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
    enrich_metadata_from_records,
    load_attachment_manifest,
    parse_material_paths,
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
    chunks, errors = parse_material_paths(
        inputs,
        metadata_by_path=metadata,
        chunk_chars=args.chunk_chars,
        overlap_chars=args.overlap_chars,
        strict=args.strict,
        limit=args.limit,
    )
    written = 0 if args.dry_run else write_chunks_jsonl(chunks, args.output, append=args.append)
    print(f"material_files_input={len(inputs)}")
    print(f"chunks_parsed={len(chunks)}")
    print(f"chunks_written={written}")
    print(f"output={args.output}")
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
