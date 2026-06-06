"""Repair material course metadata from Learn attachment download URLs."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Repair manifest and material chunk course metadata.")
    parser.add_argument("--records", default="storage/learn_fresh.jsonl")
    parser.add_argument("--manifest", default="storage/attachments/manifest.jsonl")
    parser.add_argument("--chunks", default="storage/material_chunks.jsonl")
    parser.add_argument("--catalog", default="config/course_catalog.json")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    catalog = load_json_object(Path(args.catalog))
    records = load_jsonl(Path(args.records))
    corrections = build_corrections(records, catalog)

    manifest_path = Path(args.manifest)
    manifest = load_jsonl(manifest_path)
    repaired_manifest = repair_manifest(manifest, corrections)
    write_jsonl(manifest_path, repaired_manifest)

    chunks_path = Path(args.chunks)
    chunks = load_jsonl(chunks_path)
    repaired_chunks = repair_chunks(chunks, corrections)
    write_jsonl(chunks_path, repaired_chunks)

    print(f"course_corrections={len(corrections)}")
    print(f"manifest_records={len(repaired_manifest)}")
    print(f"chunk_records={len(repaired_chunks)}")
    return 0


def build_corrections(
    records: list[dict[str, Any]],
    catalog: dict[str, Any],
) -> dict[str, dict[str, str]]:
    corrections: dict[str, dict[str, str]] = {}
    conflicts: set[str] = set()
    for record in records:
        raw_id = str(record.get("raw_id") or "")
        item_id = raw_id.rsplit("_", 1)[-1]
        for attachment in record.get("attachments") or []:
            if not isinstance(attachment, dict):
                continue
            course_id = download_course_id(str(attachment.get("download_url") or ""))
            if not course_id:
                continue
            correction = {
                "course_id": course_id,
                "course_name": str(catalog.get(course_id) or f"历史课程（{course_id}）"),
                "source_title": str(record.get("title") or ""),
                "source_task_type": str(record.get("task_type") or ""),
            }
            existing = corrections.get(item_id)
            if existing and existing["course_id"] != course_id:
                conflicts.add(item_id)
                continue
            corrections[item_id] = correction
    for item_id in conflicts:
        corrections.pop(item_id, None)
    return corrections


def repair_manifest(
    records: list[dict[str, Any]],
    corrections: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    for record in records:
        correction = correction_for_record(record, corrections)
        if correction:
            record.update(correction)
    return records


def repair_chunks(
    records: list[dict[str, Any]],
    corrections: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    for record in records:
        correction = correction_for_record(record.get("metadata") or {}, corrections)
        if not correction:
            continue
        record["course_name"] = correction["course_name"]
        metadata = dict(record.get("metadata") or {})
        metadata.update(correction)
        record["metadata"] = metadata
    return records


def correction_for_record(
    record: dict[str, Any],
    corrections: dict[str, dict[str, str]],
) -> dict[str, str] | None:
    raw_id = str(record.get("record_raw_id") or "")
    return corrections.get(raw_id.rsplit("_", 1)[-1])


def download_course_id(url: str) -> str | None:
    match = re.search(r"/downloadFile/([^/]+)/", urlparse(url).path)
    return match.group(1) if match else None


def load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            records.append(payload)
    return records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    content = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
    path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
