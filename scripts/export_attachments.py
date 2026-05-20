"""Export Learn or Mail attachments to local files with a manifest."""

from __future__ import annotations

import argparse
import json
import re
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import urlparse

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
        count = export_learn_attachments(Path(args.jsonl), output_dir, manifest_path, args.limit)
    else:
        count = export_mail_attachments(output_dir, manifest_path, args.limit, args.mailbox, args.criteria)
    print(f"exported_attachments={count}")
    print(f"output_dir={output_dir}")
    print(f"manifest={manifest_path}")
    return 0


def export_learn_attachments(jsonl_path: Path, output_dir: Path, manifest_path: Path, limit: int) -> int:
    adapter = LearnAdapter()
    adapter.authenticate()
    if adapter._session is None:
        raise RuntimeError("Learn adapter authenticated without a session")
    exported = 0
    with manifest_path.open("a", encoding="utf-8") as manifest:
        for record in iter_jsonl(jsonl_path):
            for index, attachment in enumerate(record.get("attachments") or []):
                if exported >= limit:
                    return exported
                url = str(attachment.get("download_url") or "")
                name = str(attachment.get("name") or f"attachment_{index + 1}")
                if not should_keep_document(name, url):
                    continue
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
            },
            ensure_ascii=False,
        )
    )
    stream.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
