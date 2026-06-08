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
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adapters.learn_adapter import LearnAdapter
from src.adapters.mail_adapter import MailAdapter
from src.env_loader import load_project_env


DOCUMENT_EXTENSIONS = {".pdf", ".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export course attachment files to local storage.")
    parser.add_argument("--source", choices=("learn", "mail"), required=True)
    parser.add_argument("--jsonl", help="Collector JSONL path for Learn attachment URLs.")
    parser.add_argument("--output-dir", default="storage/attachments")
    parser.add_argument("--manifest", default="storage/attachments/manifest.jsonl")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--include-homework",
        action="store_true",
        help="Include pending homework attachments in addition to course files.",
    )
    parser.add_argument(
        "--include-notices",
        action="store_true",
        default=True,
        help="Include notice attachments (enabled by default).",
    )
    parser.add_argument(
        "--exclude-notices",
        action="store_true",
        help="Exclude notice attachments.",
    )
    parser.add_argument(
        "--prefer-course-files",
        action="store_true",
        default=True,
        help="Prioritize task_type=file records when limit is reached (enabled by default).",
    )
    parser.add_argument(
        "--no-prefer-course-files",
        action="store_true",
        help="Disable course-file prioritization and keep source order.",
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
    include_notices = bool(args.include_notices and not args.exclude_notices)
    prefer_course_files = bool(args.prefer_course_files and not args.no_prefer_course_files)

    if args.source == "learn":
        if not args.jsonl:
            raise SystemExit("--jsonl is required for Learn attachment export.")
        count = export_learn_attachments(
            Path(args.jsonl),
            output_dir,
            manifest_path,
            args.limit,
            include_homework=args.include_homework,
            include_notices=include_notices,
            prefer_course_files=prefer_course_files,
        )
    else:
        count = export_mail_attachments(output_dir, manifest_path, args.limit, args.mailbox, args.criteria)
    print(f"exported_attachments={count}")
    print(f"output_dir={output_dir}")
    print(f"manifest={manifest_path}")
    return 0


def load_exported_keys(manifest_path: Path) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    if not manifest_path.exists():
        return keys
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if str(entry.get("source") or "") != "learn":
            continue
        keys.add((str(entry.get("record_raw_id") or ""), str(entry.get("attachment_name") or "")))
    return keys


def export_learn_attachments(
    jsonl_path: Path,
    output_dir: Path,
    manifest_path: Path,
    limit: int,
    *,
    include_homework: bool,
    include_notices: bool,
    prefer_course_files: bool,
) -> int:
    adapter = LearnAdapter()
    adapter.authenticate()
    if adapter._session is None:
        raise RuntimeError("Learn adapter authenticated without a session")
    base_url = adapter.require("base_url")
    exported = 0
    already_exported = load_exported_keys(manifest_path)
    candidates = build_learn_candidates(
        iter_jsonl(jsonl_path),
        base_url=base_url,
        already_exported=already_exported,
        include_homework=include_homework,
        include_notices=include_notices,
        prefer_course_files=prefer_course_files,
    )
    with manifest_path.open("a", encoding="utf-8") as manifest:
        for candidate in candidates:
            if exported >= limit:
                return exported
            response = adapter._session.get(candidate["url"], headers=adapter._request_headers(), timeout=30)
            response.raise_for_status()
            filename = safe_filename(
                filename_from_response(response.headers.get("Content-Disposition")) or candidate["attachment_name"]
            )
            target = unique_path(output_dir / build_learn_target_name(candidate, filename))
            target.write_bytes(response.content)
            write_manifest(
                manifest,
                source="learn",
                record_raw_id=candidate["record_raw_id"],
                attachment_name=candidate["attachment_name"],
                local_path=target,
                bytes_written=len(response.content),
                content_type=response.headers.get("Content-Type", "").split(";")[0],
                course_name=candidate.get("course_name"),
                task_type=candidate.get("task_type"),
                title=candidate.get("title"),
                published_at=candidate.get("published_at"),
                ddl=candidate.get("ddl"),
                status=candidate.get("status"),
            )
            already_exported.add((candidate["record_raw_id"], candidate["attachment_name"]))
            exported += 1
    return exported


def build_learn_candidates(
    records: list[dict] | tuple[dict, ...] | object,
    *,
    base_url: str,
    already_exported: set[tuple[str, str]],
    include_homework: bool,
    include_notices: bool,
    prefer_course_files: bool,
) -> list[dict]:
    candidates: list[dict] = []
    for record_index, record in enumerate(records):
        if not isinstance(record, dict) or not is_learn_record(record):
            continue
        task_type = str(record.get("task_type") or "").lower()
        if task_type == "homework":
            if not include_homework or not should_include_homework(record):
                continue
        elif task_type == "notice":
            if not include_notices:
                continue
        elif task_type == "file":
            pass
        else:
            continue
        record_raw_id = str(record.get("raw_id") or "")
        if not record_raw_id:
            continue
        for attachment_index, attachment in enumerate(record.get("attachments") or []):
            if not isinstance(attachment, dict):
                continue
            name = str(attachment.get("name") or f"attachment_{attachment_index + 1}")
            if (record_raw_id, name) in already_exported:
                continue
            url = resolve_download_url(str(attachment.get("download_url") or ""), base_url)
            if not url or not is_http_url(url):
                continue
            if not should_keep_document(name, url):
                continue
            candidates.append(
                {
                    "record_raw_id": record_raw_id,
                    "attachment_name": name,
                    "attachment_index": attachment_index,
                    "url": url,
                    "task_type": task_type or None,
                    "course_name": record.get("course_name"),
                    "title": record.get("title"),
                    "published_at": record.get("published_at"),
                    "ddl": record.get("ddl"),
                    "status": record.get("status"),
                    "record_index": record_index,
                }
            )
    if not prefer_course_files:
        return sorted(candidates, key=lambda item: (int(item["record_index"]), int(item["attachment_index"])))
    return sorted(
        candidates,
        key=lambda item: (
            task_type_priority(str(item.get("task_type") or "")),
            -published_timestamp(item.get("published_at")),
            int(item["record_index"]),
            int(item["attachment_index"]),
        ),
    )


def should_include_homework(record: dict) -> bool:
    status = str(record.get("status") or "").lower()
    if status in {"graded", "submitted_ungraded", "submitted", "submitted_graded"}:
        return False
    if record.get("completed") is True:
        return False
    ddl = parse_time(record.get("ddl"))
    if ddl is not None and ddl < datetime.now(ddl.tzinfo):
        return False
    return True


def task_type_priority(task_type: str) -> int:
    if task_type == "file":
        return 0
    if task_type == "notice":
        return 1
    if task_type == "homework":
        return 2
    return 9


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def published_timestamp(value: object) -> float:
    parsed = parse_time(value)
    if parsed is None:
        return 0.0
    return parsed.timestamp()


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


def is_learn_record(record: dict) -> bool:
    """Only export HTTP attachments that belong to Learn records."""

    return str(record.get("source") or "").lower() == "learn"


def is_http_url(url: str) -> bool:
    return urlparse(url).scheme in {"http", "https"}


def resolve_download_url(url: str, base_url: str) -> str:
    url = url.strip()
    if not url:
        return ""
    if is_http_url(url):
        return url
    if url.startswith("/"):
        return urljoin(base_url.rstrip("/") + "/", url.lstrip("/"))
    return ""


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


def build_learn_target_name(candidate: dict, filename: str) -> str:
    """Build a short, stable learn attachment filename to avoid Windows path limits."""

    source_name = Path(filename).name
    extension = Path(source_name).suffix
    stem = Path(source_name).stem
    short_stem = safe_filename(stem)[:60]
    short_raw = safe_filename(candidate.get("record_raw_id", "learn"))[:64]
    index = int(candidate.get("attachment_index", 0)) + 1
    if extension:
        return f"{short_raw}_{index}_{short_stem}{extension}"
    return f"{short_raw}_{index}_{short_stem}"


def write_manifest(
    stream,
    *,
    source: str,
    record_raw_id: str,
    attachment_name: str,
    local_path: Path,
    bytes_written: int,
    content_type: str,
    course_name: str | None = None,
    task_type: str | None = None,
    title: str | None = None,
    published_at: str | None = None,
    ddl: str | None = None,
    status: str | None = None,
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
                "course_name": course_name,
                "task_type": task_type,
                "title": title,
                "published_at": published_at,
                "ddl": ddl,
                "status": status,
            },
            ensure_ascii=False,
        )
    )
    stream.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
