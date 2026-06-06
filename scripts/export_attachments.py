"""Export Learn or Mail attachments to local files with a manifest."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adapters.learn_adapter import LearnAdapter
from src.adapters.mail_adapter import MailAdapter
from src.env_loader import load_project_env


DOCUMENT_EXTENSIONS = {".pdf", ".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar"}
COURSE_CATALOG_PATH = ROOT / "config" / "course_catalog.json"
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export course attachment files to local storage.")
    parser.add_argument("--source", choices=("learn", "mail"), required=True)
    parser.add_argument("--jsonl", help="Collector JSONL path for Learn attachment URLs.")
    parser.add_argument("--output-dir", default="storage/attachments")
    parser.add_argument("--manifest", default="storage/attachments/manifest.jsonl")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--semester-start",
        help="Only export Learn records from this semester (YYYY-MM-DD). Defaults to the current term.",
    )
    parser.add_argument(
        "--append-manifest",
        action="store_true",
        help="Append to the manifest instead of replacing stale Learn entries.",
    )
    parser.add_argument("--mailbox", default="INBOX")
    parser.add_argument("--criteria", default="ALL")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    load_project_env()
    output_dir = Path(args.output_dir) / args.source
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = Path(args.manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    if args.source == "learn":
        if not args.jsonl:
            raise SystemExit("--jsonl is required for Learn attachment export.")
        semester_start = parse_date(args.semester_start) if args.semester_start else current_semester_start()
        count = export_learn_attachments(
            Path(args.jsonl),
            output_dir,
            manifest_path,
            args.limit,
            semester_start=semester_start,
            append_manifest=args.append_manifest,
        )
    else:
        count = export_mail_attachments(output_dir, manifest_path, args.limit, args.mailbox, args.criteria)
    print(f"exported_attachments={count}")
    print(f"output_dir={output_dir}")
    print(f"manifest={manifest_path}")
    return 0


def export_learn_attachments(
    jsonl_path: Path,
    output_dir: Path,
    manifest_path: Path,
    limit: int,
    *,
    semester_start: datetime | None = None,
    append_manifest: bool = False,
) -> int:
    adapter = LearnAdapter()
    adapter.authenticate()
    if adapter._session is None:
        raise RuntimeError("Learn adapter authenticated without a session")
    course_catalog = load_course_catalog()
    exported = 0
    records = [
        record
        for record in iter_jsonl(jsonl_path)
        if is_material_candidate(record, semester_start=semester_start)
    ]
    records.sort(key=material_record_sort_key)
    mode = "a" if append_manifest else "w"
    with manifest_path.open(mode, encoding="utf-8") as manifest:
        for record in records:
            for index, attachment in enumerate(record.get("attachments") or []):
                if exported >= limit:
                    return exported
                url = str(attachment.get("download_url") or "")
                name = str(attachment.get("name") or f"attachment_{index + 1}")
                if not should_keep_document(name, url):
                    continue
                course_id = download_course_id(url)
                record_course_id = raw_id_course_id(str(record.get("raw_id") or ""))
                course_name = str(record.get("course_name") or "")
                if course_id and course_id != record_course_id:
                    course_name = course_catalog.get(course_id, f"历史课程（{course_id}）")
                response = adapter._session.get(url, headers=adapter._request_headers(), timeout=30)
                response.raise_for_status()
                filename = safe_filename(filename_from_response(response.headers.get("Content-Disposition")) or name)
                target = unique_path(output_dir / f"{safe_filename(record.get('raw_id', 'learn'))}_{index + 1}_{filename}")
                target.write_bytes(response.content)
                write_manifest(
                    manifest,
                    source="learn",
                    record_raw_id=str(record.get("raw_id") or ""),
                    attachment_name=name,
                    local_path=target,
                    bytes_written=len(response.content),
                    content_type=response.headers.get("Content-Type", "").split(";")[0],
                    course_id=course_id,
                    course_name=course_name,
                    source_title=str(record.get("title") or ""),
                    source_task_type=str(record.get("task_type") or ""),
                    published_at=str(record.get("published_at") or ""),
                    ddl=str(record.get("ddl") or ""),
                    task_status=str(record.get("status") or ""),
                    completed=record.get("completed") if isinstance(record.get("completed"), bool) else None,
                )
                exported += 1
    return exported


def export_mail_attachments(
    output_dir: Path,
    manifest_path: Path,
    limit: int,
    mailbox: str,
    criteria: str,
) -> int:
    adapter = MailAdapter()
    payloads = adapter.fetch_raw(mailbox=mailbox, criteria=criteria, limit=limit, since_uid=None)
    adapter.close()
    exported = 0
    with manifest_path.open("a", encoding="utf-8") as manifest:
        for payload in payloads:
            message = BytesParser(policy=policy.default).parsebytes(
                payload.content if isinstance(payload.content, bytes) else payload.content.encode("utf-8")
            )
            for part in message.walk():
                filename = part.get_filename()
                if not filename or not should_keep_document(filename, filename):
                    continue
                body = part.get_payload(decode=True)
                if not body:
                    continue
                target = unique_path(output_dir / f"{safe_filename(payload.raw_id)}_{safe_filename(filename)}")
                target.write_bytes(body)
                write_manifest(
                    manifest,
                    source="mail",
                    record_raw_id=payload.raw_id,
                    attachment_name=filename,
                    local_path=target,
                    bytes_written=len(body),
                    content_type=part.get_content_type(),
                )
                exported += 1
                if exported >= limit:
                    return exported
    return exported


def iter_jsonl(path: Path):
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def current_semester_start(now: datetime | None = None) -> datetime:
    current = now or datetime.now(LOCAL_TIMEZONE)
    if 2 <= current.month <= 7:
        return datetime(current.year, 2, 1, tzinfo=LOCAL_TIMEZONE)
    if current.month >= 8:
        return datetime(current.year, 9, 1, tzinfo=LOCAL_TIMEZONE)
    return datetime(current.year - 1, 9, 1, tzinfo=LOCAL_TIMEZONE)


def parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=LOCAL_TIMEZONE)


def parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=LOCAL_TIMEZONE)
    return parsed.astimezone(LOCAL_TIMEZONE)


def is_material_candidate(
    record: dict[str, object],
    *,
    semester_start: datetime | None,
    now: datetime | None = None,
) -> bool:
    task_type = str(record.get("task_type") or "")
    if task_type not in {"homework", "file"}:
        return False
    current = now or datetime.now(LOCAL_TIMEZONE)
    start = semester_start or current_semester_start(current)
    if task_type == "file":
        published_at = parse_datetime(record.get("published_at"))
        return published_at is None or start <= published_at <= current

    ddl = parse_datetime(record.get("ddl"))
    published_at = parse_datetime(record.get("published_at"))
    if ddl is not None and ddl < start:
        return False
    if ddl is None and published_at is not None and published_at < start:
        return False
    completed = record.get("completed")
    if not isinstance(completed, bool):
        completed = infer_completed(record)
    return completed is False or (ddl is not None and ddl >= current)


def infer_completed(record: dict[str, object]) -> bool | None:
    status = str(record.get("status") or "")
    raw_id = str(record.get("raw_id") or "")
    if status == "unsubmitted" or "_homework_unsubmitted_" in raw_id:
        return False
    if status in {"submitted_ungraded", "graded"}:
        return True
    if "_homework_submitted_ungraded_" in raw_id or "_homework_graded_" in raw_id:
        return True
    return None


def material_record_sort_key(record: dict[str, object]) -> tuple[int, float, str]:
    task_type = str(record.get("task_type") or "")
    if task_type == "homework":
        ddl = parse_datetime(record.get("ddl"))
        return (0, ddl.timestamp() if ddl else float("inf"), str(record.get("title") or ""))
    published_at = parse_datetime(record.get("published_at"))
    return (1, -(published_at.timestamp() if published_at else 0.0), str(record.get("title") or ""))


def load_course_catalog() -> dict[str, str]:
    if not COURSE_CATALOG_PATH.exists():
        return {}
    payload = json.loads(COURSE_CATALOG_PATH.read_text(encoding="utf-8"))
    return {
        str(course_id): str(course_name)
        for course_id, course_name in payload.items()
        if course_id and course_name
    }


def download_course_id(url: str) -> str | None:
    match = re.search(r"/downloadFile/([^/]+)/", urlparse(url).path)
    return match.group(1) if match else None


def raw_id_course_id(raw_id: str) -> str | None:
    match = re.search(r"_(\d{4}-\d{4}-\d+)_", raw_id)
    return match.group(1) if match else None


def should_keep_document(name: str, url: str) -> bool:
    lowered = f"{name} {urlparse(url).path}".lower()
    return any(ext in lowered for ext in DOCUMENT_EXTENSIONS)


def filename_from_response(content_disposition: str | None) -> str | None:
    if not content_disposition:
        return None
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', content_disposition, flags=re.IGNORECASE)
    return match.group(1).strip() if match else None


def safe_filename(value: object) -> str:
    text = str(value or "attachment")
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:120] or "attachment"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 10000):
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not allocate unique path for {path}")


def write_manifest(
    stream,
    *,
    source: str,
    record_raw_id: str,
    attachment_name: str,
    local_path: Path,
    bytes_written: int,
    content_type: str,
    course_id: str | None = None,
    course_name: str = "",
    source_title: str = "",
    source_task_type: str = "",
    published_at: str = "",
    ddl: str = "",
    task_status: str = "",
    completed: bool | None = None,
) -> None:
    stream.write(
        json.dumps(
            {
                "source": source,
                "record_raw_id": record_raw_id,
                "attachment_name": attachment_name,
                "local_path": str(local_path),
                "bytes": bytes_written,
                "content_type": content_type,
                "course_id": course_id,
                "course_name": course_name,
                "source_title": source_title,
                "source_task_type": source_task_type,
                "published_at": published_at,
                "ddl": ddl,
                "task_status": task_status,
                "completed": completed,
            },
            ensure_ascii=False,
        )
    )
    stream.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
