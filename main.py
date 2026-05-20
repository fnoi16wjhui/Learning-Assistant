"""CLI entry point for Course Agent Collector sync jobs."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.adapters.base_adapter import AdapterError, RawPayload
from src.adapters.jwch_adapter import JwchAdapter
from src.adapters.learn_adapter import LearnAdapter
from src.adapters.mail_adapter import MailAdapter
from src.env_loader import load_project_env
from src.models import CourseTask, ScheduleItem
from src.parsers.jwch_html import JwchHtmlParser
from src.parsers.learn_html import LearnHtmlParser
from src.parsers.mail_mime import MailMimeParser, looks_course_related
from src.pipeline import DeduplicationStore, configure_logging, filter_new_records, write_jsonl


NETWORK_CHANNELS = {"learn", "mail", "jwch"}
PipelineRecord = CourseTask | ScheduleItem


@dataclass(frozen=True)
class SyncResult:
    """Small summary for one channel sync."""

    channel: str
    raw_count: int
    parsed_count: int
    fresh_count: int
    output_path: Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Course Agent Collector")
    parser.add_argument(
        "--channel",
        choices=sorted(NETWORK_CHANNELS | {"all", "harness"}),
        help="Source channel to sync. Use all to run channels independently.",
    )
    parser.add_argument("--allow-network", action="store_true", help="Permit network-backed adapter execution.")
    parser.add_argument("--dry-run", action="store_true", help="Validate configuration without network or writes.")
    parser.add_argument("--db-path", default="storage/app.db", help="SQLite path used for deduplication and cursors.")
    parser.add_argument("--output", default="storage/collector.jsonl", help="JSONL output path for fresh records.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum records/messages per channel fetch.")
    parser.add_argument("--mailbox", default="INBOX", help="IMAP mailbox for mail sync.")
    parser.add_argument("--criteria", default="ALL", help="IMAP search criteria for mail sync.")
    parser.add_argument("--anchor-monday", help="YYYY-MM-DD Monday used for JWCH weekly schedule timestamps.")
    parser.add_argument("--learn-endpoints-json", help="JSON array overriding configured Learn endpoint specs.")
    parser.add_argument("--retries", type=int, default=2, help="Attempts per channel before reporting failure.")
    parser.add_argument("--retry-delay", type=float, default=2.0, help="Initial retry delay in seconds.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    load_project_env()
    configure_logging()

    if not args.channel:
        print("No channel selected. Run scripts/run_harness.py for architecture checks.")
        return 0
    if args.channel == "harness":
        print("Run: python scripts/run_harness.py")
        return 0
    if args.dry_run or not args.allow_network:
        print(f"Dry run for channel={args.channel}. Add --allow-network to run a real sync.")
        return 0

    channels = sorted(NETWORK_CHANNELS) if args.channel == "all" else [args.channel]
    failures: list[str] = []
    for channel in channels:
        try:
            result = run_with_retries(channel, args)
        except Exception as exc:
            logging.exception("channel sync failed: channel=%s error=%s", channel, type(exc).__name__)
            print(f"[FAIL] {channel}: {type(exc).__name__}: {str(exc)[:160]}")
            failures.append(channel)
            continue
        print(
            f"[OK] {result.channel}: raw={result.raw_count} parsed={result.parsed_count} "
            f"fresh={result.fresh_count} output={result.output_path}"
        )
    return 1 if failures else 0


def run_with_retries(channel: str, args: argparse.Namespace) -> SyncResult:
    attempts = max(args.retries, 1)
    delay = max(args.retry_delay, 0.0)
    for attempt in range(1, attempts + 1):
        try:
            return sync_channel(channel, args)
        except AdapterError:
            if attempt >= attempts:
                raise
            logging.warning("retrying channel=%s attempt=%s/%s", channel, attempt + 1, attempts)
            time.sleep(delay)
            delay *= 2
    raise AssertionError("retry loop exhausted")


def sync_channel(channel: str, args: argparse.Namespace) -> SyncResult:
    store = DeduplicationStore(args.db_path)
    output_path = Path(args.output)
    if channel == "jwch":
        raw_payloads, records = sync_jwch(args)
    elif channel == "mail":
        raw_payloads, records = sync_mail(args, store)
    elif channel == "learn":
        raw_payloads, records = sync_learn(args)
    else:
        raise ValueError(f"unsupported channel: {channel}")

    fresh = filter_new_records(records, store)
    write_jsonl(fresh, output_path)
    return SyncResult(
        channel=channel,
        raw_count=len(raw_payloads),
        parsed_count=len(records),
        fresh_count=len(fresh),
        output_path=output_path,
    )


def sync_jwch(args: argparse.Namespace) -> tuple[list[RawPayload], list[PipelineRecord]]:
    adapter = JwchAdapter()
    parser = JwchHtmlParser()
    targets = [
        ("exam", os.getenv("JWCH_EXAM_URL") or "https://zhjw.cic.tsinghua.edu.cn/jxmh.do?url=/jxmh.do&m=bks_ksSearch"),
        (
            "schedule",
            os.getenv("JWCH_SCHEDULE_URL")
            or "https://zhjw.cic.tsinghua.edu.cn/portal3rd.do?url=/portal3rd.do&m=bks_yjkbSearch",
        ),
    ]
    raw_payloads: list[RawPayload] = []
    records: list[PipelineRecord] = []
    anchor_monday = args.anchor_monday or os.getenv("JWCH_ANCHOR_MONDAY")
    for name, url in targets:
        payload = adapter.fetch_raw(url=url, raw_id=f"jwch_{name}")[0]
        raw_payloads.append(payload)
        records.extend(
            parser.parse(
                payload.content,
                {
                    "raw_id": payload.raw_id,
                    "url": payload.metadata.get("url"),
                    "anchor_monday": anchor_monday,
                },
            )
        )
    return raw_payloads, records


def sync_mail(args: argparse.Namespace, store: DeduplicationStore) -> tuple[list[RawPayload], list[PipelineRecord]]:
    last_uid = store.get_int_state("mail", "last_uid", default=0)
    adapter = MailAdapter()
    try:
        raw_payloads = adapter.fetch_raw(
            mailbox=args.mailbox,
            criteria=args.criteria,
            limit=args.limit,
            since_uid=last_uid,
        )
    finally:
        adapter.close()

    parser = MailMimeParser()
    records: list[PipelineRecord] = []
    max_uid = last_uid
    for payload in raw_payloads:
        parsed_records = parser.parse(payload.content, payload.metadata)
        records.extend(
            record
            for record in parsed_records
            if looks_course_related(f"{record.title}\n{record.content}\n{record.course_name}")
        )
        uid = payload.metadata.get("uid")
        if isinstance(uid, int):
            max_uid = max(max_uid, uid)
    if max_uid > last_uid:
        store.set_state("mail", "last_uid", max_uid)
    return raw_payloads, records


def sync_learn(args: argparse.Namespace) -> tuple[list[RawPayload], list[PipelineRecord]]:
    adapter = LearnAdapter()
    parser = LearnHtmlParser()
    endpoint_override = args.learn_endpoints_json or os.getenv("LEARN_ENDPOINTS_JSON")
    if not endpoint_override:
        return sync_learn_business(adapter, parser)

    endpoint_specs = load_learn_endpoint_specs(endpoint_override)
    raw_payloads: list[RawPayload] = []
    records: list[PipelineRecord] = []
    for spec in endpoint_specs:
        endpoint = require_spec_value(spec, "endpoint")
        raw_id = str(spec.get("raw_id") or endpoint.strip("/").replace("/", "_") or "learn_root")
        payload = adapter.fetch_raw(
            endpoint=endpoint,
            raw_id=raw_id,
            content_type=as_optional_str(spec.get("content_type")),
        )[0]
        raw_payloads.append(payload)
        metadata = dict(spec.get("metadata") or {})
        metadata.update({"raw_id": payload.raw_id, "content_type": payload.content_type})
        records.extend(parser.parse(payload.content, metadata))
    return raw_payloads, records


def sync_learn_business(
    adapter: LearnAdapter,
    parser: LearnHtmlParser,
) -> tuple[list[RawPayload], list[PipelineRecord]]:
    """Sync Learn course business JSON for courses, homework, files, questionnaires, and discussions."""

    raw_payloads: list[RawPayload] = []
    records: list[PipelineRecord] = []
    semester_payload, semester_data = fetch_learn_json(
        adapter,
        "/b/kc/zhjw_v_code_xnxq/getCurrentAndNextSemester",
        raw_id="learn_current_semester",
    )
    raw_payloads.append(semester_payload)
    semester_id = ((semester_data.get("result") or {}) if isinstance(semester_data, dict) else {}).get("xnxq")
    if not isinstance(semester_id, str) or not semester_id:
        raise ValueError("Learn current semester response missing result.xnxq")

    locale = str(adapter.config.extra.get("locale") or "zh")
    course_payload, course_data = fetch_learn_json(
        adapter,
        f"/b/wlxt/kc/v_wlkc_xs_xkb_kcb_extend/student/loadCourseBySemesterId/{semester_id}/{locale}",
        raw_id=f"learn_courses_{semester_id}",
    )
    raw_payloads.append(course_payload)
    courses = course_data.get("resultList") if isinstance(course_data, dict) else None
    if not isinstance(courses, list):
        raise ValueError("Learn course list response missing resultList")

    for course in courses:
        if not isinstance(course, dict):
            continue
        course_id = as_optional_str(course.get("wlkcid")) or as_optional_str(course.get("id"))
        if not course_id:
            continue
        course_name = as_optional_str(course.get("kcm")) or as_optional_str(course.get("ywkcm")) or "Unknown Course"
        for spec in default_learn_business_specs(course_id):
            payload, _ = fetch_learn_json(
                adapter,
                spec["endpoint"],
                raw_id=f"learn_{spec['name']}_{course_id}",
                params=spec.get("params"),
                data=spec.get("data"),
                method=str(spec.get("method") or "get"),
            )
            raw_payloads.append(payload)
            parser_metadata = {
                "raw_id": payload.raw_id,
                "raw_id_prefix": payload.raw_id,
                "content_type": payload.content_type,
                "course_name": course_name,
                "task_type": spec["task_type"],
                "base_url": adapter.require("base_url"),
                "homework_attachment_url_template": "/b/wlxt/kczy/zy/student/downloadFile/{wlkcid}/{file_id}",
                "notice_attachment_url_template": "/b/wlxt/kcgg/wlkc_ggb/student/downloadFile/{file_id}",
                "file_attachment_url_template": "/b/wlxt/kj/wlkc_kjxxb/student/downloadFile?sfgk=0&wjid={file_id}",
            }
            parser_metadata.update(spec.get("metadata") or {})
            records.extend(
                parser.parse(
                    payload.content,
                    parser_metadata,
                )
            )
        courseware_payloads, courseware_records = sync_learn_courseware_files(adapter, parser, course_id, course_name)
        raw_payloads.extend(courseware_payloads)
        records.extend(courseware_records)
    return raw_payloads, records


def fetch_learn_json(
    adapter: LearnAdapter,
    endpoint: str,
    *,
    raw_id: str,
    params: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
    method: str = "get",
) -> tuple[RawPayload, dict[str, Any]]:
    payload = adapter.fetch_endpoint(
        endpoint,
        raw_id=raw_id,
        content_type="application/json",
        params=params,
        data=data,
        method=method,
    )
    text = payload.content if isinstance(payload.content, str) else payload.content.decode("utf-8", errors="replace")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"Learn endpoint did not return a JSON object: {endpoint}")
    return payload, data


def default_learn_business_specs(course_id: str) -> list[dict[str, Any]]:
    common_params = {"wlkcid": course_id, "aoData": "[]"}
    ao_data = json.dumps([{"name": "wlkcid", "value": course_id}], ensure_ascii=False)
    return [
        {
            "name": "notice_active",
            "task_type": "notice",
            "endpoint": "/b/wlxt/kcgg/wlkc_ggb/student/pageListXsbyWgq",
            "method": "post",
            "data": {"aoData": ao_data},
        },
        {
            "name": "notice_expired",
            "task_type": "notice",
            "endpoint": "/b/wlxt/kcgg/wlkc_ggb/student/pageListXsbyYgq",
            "method": "post",
            "data": {"aoData": ao_data},
        },
        {
            "name": "homework_unsubmitted",
            "task_type": "homework",
            "endpoint": "/b/wlxt/kczy/zy/student/zyListWj",
            "params": common_params,
        },
        {
            "name": "homework_submitted_ungraded",
            "task_type": "homework",
            "endpoint": "/b/wlxt/kczy/zy/student/zyListYjwg",
            "params": common_params,
        },
        {
            "name": "homework_graded",
            "task_type": "homework",
            "endpoint": "/b/wlxt/kczy/zy/student/zyListYpg",
            "params": common_params,
        },
        {
            "name": "questionnaire",
            "task_type": "questionnaire",
            "endpoint": "/b/wlxt/kcwj/wlkc_wjb/student/pageListWks",
            "params": common_params,
        },
        {
            "name": "discussion",
            "task_type": "discussion",
            "endpoint": "/b/wlxt/bbs/bbs_tltb/student/ybtlPageList",
            "params": common_params,
        },
    ]


def sync_learn_courseware_files(
    adapter: LearnAdapter,
    parser: LearnHtmlParser,
    course_id: str,
    course_name: str,
) -> tuple[list[RawPayload], list[PipelineRecord]]:
    category_payload, category_data = fetch_learn_json(
        adapter,
        "/b/wlxt/kj/wlkc_kjflb/student/pageList",
        raw_id=f"learn_courseware_categories_for_files_{course_id}",
        params={"wlkcid": course_id},
    )
    raw_payloads = [category_payload]
    records: list[PipelineRecord] = []
    rows = ((category_data.get("object") or {}) if isinstance(category_data, dict) else {}).get("rows")
    if not isinstance(rows, list):
        return raw_payloads, records
    for row in rows:
        if not isinstance(row, dict):
            continue
        category_id = as_optional_str(row.get("kjflid"))
        if not category_id:
            continue
        payload, _ = fetch_learn_json(
            adapter,
            f"/b/wlxt/kj/wlkc_kjxxb/student/kjxxb/{course_id}/{category_id}",
            raw_id=f"learn_courseware_files_{course_id}_{category_id}",
        )
        raw_payloads.append(payload)
        records.extend(
            parser.parse(
                payload.content,
                {
                    "raw_id": payload.raw_id,
                    "raw_id_prefix": payload.raw_id,
                    "content_type": payload.content_type,
                    "course_name": course_name,
                    "task_type": "file",
                    "base_url": adapter.require("base_url"),
                    "download_url_template": "/b/wlxt/kj/wlkc_kjxxb/student/downloadFile?sfgk=0&wjid={file_id}",
                },
            )
        )
    return raw_payloads, records


def load_learn_endpoint_specs(override_json: str | None) -> list[dict[str, Any]]:
    raw = override_json
    if not raw:
        raw = json.dumps([{"endpoint": "/", "raw_id": "learn_home"}])
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid Learn endpoints JSON: {exc}") from exc
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ValueError("Learn endpoints JSON must be an array of objects")
    return list(data)


def require_spec_value(spec: dict[str, Any], key: str) -> str:
    value = spec.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Learn endpoint spec missing {key}")
    return value


def as_optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


if __name__ == "__main__":
    raise SystemExit(main())
