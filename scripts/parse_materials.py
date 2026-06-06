"""Parse local course material files into standardized JSONL chunks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.materials.pipeline import (
    DEFAULT_CHUNK_CHARS,
    DEFAULT_OVERLAP_CHARS,
    build_parse_report,
    enrich_metadata_from_records,
    load_existing_file_hashes,
    load_attachment_manifest,
    load_report_file_hashes,
    parse_material_paths_with_report,
    write_parse_report,
    write_chunks_jsonl,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Course material parser for B-module outputs.")
    parser.add_argument(
        "--input",
        action="append",
        dest="inputs",
        help="File or directory to parse. Can be repeated.",
    )
    parser.add_argument(
        "--manifest",
        default="storage/attachments/manifest.jsonl",
        help="Attachment export manifest from scripts/export_attachments.py.",
    )
    parser.add_argument(
        "--records-jsonl",
        action="append",
        default=[],
        help="A-module CourseTask JSONL used to enrich course metadata. Can be repeated.",
    )
    parser.add_argument("--output", default="storage/material_chunks.jsonl")
    parser.add_argument("--chunk-chars", type=int, default=DEFAULT_CHUNK_CHARS)
    parser.add_argument("--overlap-chars", type=int, default=DEFAULT_OVERLAP_CHARS)
    parser.add_argument("--limit", type=int, help="Maximum files to parse.")
    parser.add_argument("--strict", action="store_true", help="Stop at the first parse error.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report counts without writing output.")
    parser.add_argument("--append", action="store_true", help="Append chunks instead of replacing the output file.")
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Skip files whose file_hash already exists in the output JSONL and append new chunks.",
    )
    parser.add_argument(
        "--no-dedupe",
        action="store_true",
        help="Allow duplicate chunk IDs when appending. Not recommended.",
    )
    parser.add_argument(
        "--report",
        default="storage/material_parse_report.json",
        help="Write a JSON parse report with per-file status and extraction metrics.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    inputs = [Path(item) for item in (args.inputs or [])]
    manifest_metadata = load_attachment_manifest(args.manifest)
    if not inputs:
        if manifest_metadata:
            inputs = [Path(metadata_key) for metadata_key in manifest_metadata]
        else:
            inputs = [Path("storage/attachments")]

    metadata = enrich_metadata_from_records(
        manifest_metadata,
        args.records_jsonl or default_existing_record_paths(),
    )
    output_path = Path(args.output)
    incremental = bool(args.incremental)
    skip_hashes = (
        load_existing_file_hashes(output_path) | load_report_file_hashes(args.report)
        if incremental
        else set()
    )
    chunks, errors, file_reports = parse_material_paths_with_report(
        inputs,
        metadata_by_path=metadata,
        chunk_chars=args.chunk_chars,
        overlap_chars=args.overlap_chars,
        strict=args.strict,
        limit=args.limit,
        skip_file_hashes=skip_hashes,
    )
    append = bool(args.append or incremental)
    dedupe = not args.no_dedupe
    written = 0 if args.dry_run else write_chunks_jsonl(chunks, output_path, append=append, dedupe=dedupe)
    report = build_parse_report(
        inputs=inputs,
        output=output_path,
        chunk_chars=args.chunk_chars,
        overlap_chars=args.overlap_chars,
        incremental=incremental,
        chunks_parsed=len(chunks),
        chunks_written=written,
        errors=errors,
        file_reports=file_reports,
    )
    if not args.dry_run:
        write_parse_report(report, args.report)
    print(f"material_files_input={len(inputs)}")
    print(f"chunks_parsed={len(chunks)}")
    print(f"chunks_written={written}")
    print(f"output={args.output}")
    print(f"report={args.report if not args.dry_run else '(dry-run skipped)'}")
    if incremental:
        print(f"files_skipped_unchanged={report.files_skipped_unchanged}")
    if report.files_skipped_unsupported:
        print(f"files_skipped_unsupported={report.files_skipped_unsupported}")
    if errors:
        print(f"errors={len(errors)}")
        for error in errors[:10]:
            print(f"  - {error}")
        return 1 if args.strict else 0
    return 0


def default_existing_record_paths() -> list[Path]:
    candidates = [
        Path("storage/learn.jsonl"),
        Path("storage/mail.jsonl"),
        Path("storage/collector.jsonl"),
    ]
    return [path for path in candidates if path.exists()]


if __name__ == "__main__":
    raise SystemExit(main())
