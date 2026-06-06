"""CLI entry point for Course Agent Collector sync jobs."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src.adapters.base_adapter import AdapterError, RawPayload
from src.adapters.jwch_adapter import JwchAdapter
from src.adapters.learn_adapter import LearnAdapter
from src.adapters.mail_adapter import MailAdapter
from src.env_loader import load_project_env
from src.models import CourseTask, ScheduleItem
from src.parsers.jwch_html import JwchHtmlParser
from src.parsers.learn_html import (
    LearnHtmlParser,
    extract_json_records,
    normalize_learn_content,
    parse_learn_homework_detail_attachments,
)
from src.parsers.mail_mime import MailMimeParser, looks_course_related
from src.pipeline import DeduplicationStore, configure_logging, filter_new_records, write_jsonl


NETWORK_CHANNELS = {"learn", "mail", "jwch"}
LEARN_LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
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
    parser.add_argument("--semester-id", help="Learn semester ID override, for example 2025-2026-2.")
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

    fresh = records if channel == "learn" else filter_new_records(records, store)
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
        return sync_learn_business(adapter, parser, semester_id_override=args.semester_id)

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
    semester_id_override: str | None = None,
) -> tuple[list[RawPayload], list[PipelineRecord]]:
    """Sync Learn course business JSON for courses, homework, files, questionnaires, and discussions."""

    raw_payloads: list[RawPayload] = []
    records: list[PipelineRecord] = []
    semester_id = semester_id_override
    if not semester_id:
        semester_payload, semester_data = fetch_learn_json(
            adapter,
            "/b/kc/zhjw_v_code_xnxq/getCurrentAndNextSemester",
            raw_id="learn_current_semester",
        )
        raw_payloads.append(semester_payload)
        semester_id = ((semester_data.get("result") or {}) if isinstance(semester_data, dict) else {}).get("xnxq")
        if not isinstance(semester_id, str) or not semester_id:
            raise ValueError("Learn current semester response missing result.xnxq")
    semester_start = learn_semester_start(semester_id)

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
            payload, payload_data = fetch_learn_json(
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
                "course_id": course_id,
                "course_name": course_name,
                "task_type": spec["task_type"],
                "task_status": spec.get("task_status"),
                "completed": spec.get("completed"),
                "require_course_id_match": bool(spec.get("require_course_id_match")),
                "base_url": adapter.require("base_url"),
                "homework_attachment_url_template": "/b/wlxt/kczy/zy/student/downloadFile/{wlkcid}/{file_id}",
                "notice_attachment_url_template": "/b/wlxt/kcgg/wlkc_ggb/student/downloadFile/{file_id}",
                "file_attachment_url_template": "/b/wlxt/kj/wlkc_kjxxb/student/downloadFile?sfgk=0&wjid={file_id}",
            }
            parser_metadata.update(spec.get("metadata") or {})
            parsed_records = parser.parse(
                payload.content,
                parser_metadata,
            )
            filtered_records = filter_learn_records_for_semester(parsed_records, semester_start)
            if spec["task_type"] == "homework" and filtered_records:
                detail_payloads, filtered_records = enrich_learn_homework_records(
                    adapter,
                    filtered_records,
                    payload_data,
                )
                raw_payloads.extend(detail_payloads)
            records.extend(filtered_records)
        courseware_payloads, courseware_records = sync_learn_courseware_files(adapter, parser, course_id, course_name)
        raw_payloads.extend(courseware_payloads)
        records.extend(filter_learn_records_for_semester(courseware_records, semester_start))
    return raw_payloads, records


def enrich_learn_homework_records(
    adapter: LearnAdapter,
    records: list[PipelineRecord],
    list_data: dict[str, Any],
) -> tuple[list[RawPayload], list[PipelineRecord]]:
    """Load homework instructions and teacher attachments hidden behind the detail page."""

    raw_items = extract_json_records(list_data)
    if not isinstance(raw_items, list):
        return [], records
    items_by_id = {
        str(item.get("zyid")): item
        for item in raw_items
        if isinstance(item, dict) and item.get("zyid")
    }
    base_url = adapter.require("base_url")
    raw_payloads: list[RawPayload] = []
    enriched: list[PipelineRecord] = []
    for record in records:
        if not isinstance(record, CourseTask) or record.task_type != "homework":
            enriched.append(record)
            continue
        homework_id = record.raw_id.rsplit("_", 1)[-1]
        item = items_by_id.get(homework_id)
        if not item:
            enriched.append(record)
            continue

        content = record.content
        detail_payload, detail_data = fetch_learn_json(
            adapter,
            "/b/wlxt/kczy/zy/student/detail",
            raw_id=f"learn_homework_detail_{homework_id}",
            data={"id": homework_id},
            method="post",
        )
        raw_payloads.append(detail_payload)
        detail_message = detail_data.get("msg")
        if isinstance(detail_message, str) and detail_message.strip():
            content = normalize_learn_content(detail_message)

        attachments = list(record.attachments)
        course_id = as_optional_str(item.get("wlkcid"))
        student_homework_id = as_optional_str(item.get("xszyid"))
        if course_id and student_homework_id:
            view_endpoint = (
                "/f/wlxt/kczy/zy/student/viewZy"
                f"?wlkcid={course_id}&sfgq=0&zyid={homework_id}&xszyid={student_homework_id}"
            )
            view_payload = adapter.fetch_endpoint(
                view_endpoint,
                raw_id=f"learn_homework_view_{homework_id}",
                content_type="text/html",
            )
            raw_payloads.append(view_payload)
            attachments.extend(
                parse_learn_homework_detail_attachments(
                    str(view_payload.content),
                    base_url=base_url,
                )
            )

        deduplicated = {}
        for attachment in attachments:
            deduplicated[str(attachment.download_url)] = attachment
        enriched.append(
            record.model_copy(
                update={
                    "content": content,
                    "attachments": list(deduplicated.values()),
                }
            )
        )
    return raw_payloads, enriched


def learn_semester_start(semester_id: str) -> datetime | None:
    """Infer a conservative current-term start from Learn's xnxq value."""

    parts = semester_id.split("-")
    if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    first_year = int(parts[0])
    second_year = int(parts[1])
    term = parts[2].strip()
    if term == "1":
        return datetime(first_year, 9, 1, tzinfo=LEARN_LOCAL_TIMEZONE)
    if term == "2":
        return datetime(second_year, 2, 1, tzinfo=LEARN_LOCAL_TIMEZONE)
    if term in {"3", "summer", "Summer"}:
        return datetime(second_year, 7, 1, tzinfo=LEARN_LOCAL_TIMEZONE)
    return None


def filter_learn_records_for_semester(
    records: list[PipelineRecord],
    semester_start: datetime | None,
    *,
    now: datetime | None = None,
) -> list[PipelineRecord]:
    if semester_start is None:
        return records
    current_time = normalize_datetime(now or datetime.now(LEARN_LOCAL_TIMEZONE))
    filtered: list[PipelineRecord] = []
    for record in records:
        if isinstance(record, CourseTask) and record.source == "learn":
            if record.task_type == "homework":
                ddl = normalize_datetime(record.ddl) if record.ddl is not None else None
                published_at = (
                    normalize_datetime(record.published_at)
                    if record.published_at is not None
                    else None
                )
                if ddl is not None and ddl < semester_start:
                    continue
                if ddl is None and published_at is not None and published_at < semester_start:
                    continue
                if record.completed is not False and (ddl is None or ddl < current_time):
                    continue
            elif record.task_type == "file" and record.published_at is not None:
                published_at = normalize_datetime(record.published_at)
                if published_at < semester_start or published_at > current_time:
                    continue
        filtered.append(record)
    return filtered


def normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=LEARN_LOCAL_TIMEZONE)
    return value.astimezone(LEARN_LOCAL_TIMEZONE)


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
    stripped = text.lstrip()
    metadata = payload.metadata if isinstance(payload.metadata, dict) else {}
    content_type = str(metadata.get("content_type") or payload.content_type or "")
    final_url = str(metadata.get("url") or "")
    preview = " ".join(stripped.split())[:200]

    if stripped.startswith("<") or "text/html" in content_type.lower():
        logging.error(
            "learn_non_json_response endpoint=%s status=%s url=%s preview=%s",
            endpoint,
            metadata.get("status_code"),
            final_url,
            preview,
        )
        if "login_timeout" in final_url.lower():
            raise AdapterError(
                "learn_adapter session expired (login_timeout). "
                "Re-run scripts/probe_learn_double_auth.py to trust this device, then sync again."
            )
        if any(token in stripped.lower() for token in ("login", "登录", "i_user", "i_pass", "password")):
            raise AdapterError(
                "learn_adapter authentication failed: endpoint returned login HTML instead of JSON. "
                "Check LEARN_USERNAME/LEARN_PASSWORD or trust-device requirements."
            )
        raise AdapterError(
            f"learn_adapter endpoint returned HTML instead of JSON: endpoint={endpoint} preview={preview[:120]}"
        )

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logging.error(
            "learn_json_decode_failed endpoint=%s status=%s url=%s preview=%s",
            endpoint,
            metadata.get("status_code"),
            final_url,
            preview,
        )
        raise AdapterError(
            f"learn_adapter endpoint returned non-JSON response: endpoint={endpoint} preview={preview[:120]}"
        ) from exc

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
            "task_status": "unsubmitted",
            "completed": False,
            "endpoint": "/b/wlxt/kczy/zy/student/zyListWj",
            "params": common_params,
            "require_course_id_match": True,
        },
        {
            "name": "homework_submitted_ungraded",
            "task_type": "homework",
            "task_status": "submitted_ungraded",
            "completed": True,
            "endpoint": "/b/wlxt/kczy/zy/student/zyListYjwg",
            "params": common_params,
            "require_course_id_match": True,
        },
        {
            "name": "homework_graded",
            "task_type": "homework",
            "task_status": "graded",
            "completed": True,
            "endpoint": "/b/wlxt/kczy/zy/student/zyListYpg",
            "params": common_params,
            "require_course_id_match": True,
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
                    "course_id": course_id,
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
