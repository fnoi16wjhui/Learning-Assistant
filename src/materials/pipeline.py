"""Local material parsing, cleaning, chunking, and JSONL output."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src.materials.extractors import MaterialParseError, extractor_for_path, supported_suffixes
from src.materials.models import MaterialChunk, material_type_for_path


DEFAULT_CHUNK_CHARS = 900
DEFAULT_OVERLAP_CHARS = 120
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


@dataclass
class TextChunkSpan:
    text: str
    start_char: int | None = None
    end_char: int | None = None


@dataclass
class MaterialFileReport:
    source_file: str
    material_type: str
    status: str
    file_hash: str | None = None
    chunks: int = 0
    extracted_chars: int = 0
    segment_count: int = 0
    metadata_matched: bool = False
    extraction_methods: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class MaterialParseReport:
    created_at: str
    inputs: list[str]
    output: str
    chunk_chars: int
    overlap_chars: int
    incremental: bool
    files_input: int
    files_parsed: int
    files_skipped: int
    files_skipped_unchanged: int
    files_skipped_unsupported: int
    files_failed: int
    chunks_parsed: int
    chunks_written: int
    errors: list[str]
    files: list[MaterialFileReport]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data


def parse_material_paths(
    paths: Iterable[str | Path],
    *,
    metadata_by_path: Mapping[str, Mapping[str, Any]] | None = None,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
    strict: bool = False,
    limit: int | None = None,
) -> tuple[list[MaterialChunk], list[str]]:
    """Parse local files into standardized chunks and collect non-fatal errors."""

    chunks, errors, _ = parse_material_paths_with_report(
        paths,
        metadata_by_path=metadata_by_path,
        chunk_chars=chunk_chars,
        overlap_chars=overlap_chars,
        strict=strict,
        limit=limit,
    )
    return chunks, errors


def parse_material_paths_with_report(
    paths: Iterable[str | Path],
    *,
    metadata_by_path: Mapping[str, Mapping[str, Any]] | None = None,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
    strict: bool = False,
    limit: int | None = None,
    skip_file_hashes: set[str] | None = None,
) -> tuple[list[MaterialChunk], list[str], list[MaterialFileReport]]:
    """Parse local files and return per-file parse reports."""

    chunks: list[MaterialChunk] = []
    errors: list[str] = []
    reports: list[MaterialFileReport] = []
    metadata_map = {normalize_path_key(key): dict(value) for key, value in (metadata_by_path or {}).items()}
    suffixes = supported_suffixes()
    files = list(iter_candidate_files(paths))
    if limit is not None:
        files = files[: max(limit, 0)]

    for path in files:
        metadata_key = normalize_path_key(path)
        metadata = metadata_map.get(metadata_key, {})
        if path.suffix.lower() not in suffixes:
            reports.append(
                MaterialFileReport(
                    source_file=str(path),
                    material_type=str(material_type_for_path(path)),
                    status="skipped_unsupported",
                    metadata_matched=bool(metadata),
                    warnings=[f"unsupported_suffix: {path.suffix.lower() or '(none)'}"],
                )
            )
            continue
        try:
            file_hash = sha256_file(path)
            if skip_file_hashes and file_hash in skip_file_hashes:
                reports.append(
                    MaterialFileReport(
                        source_file=str(path),
                        material_type=str(material_type_for_path(path)),
                        status="skipped_unchanged",
                        file_hash=file_hash,
                        metadata_matched=bool(metadata),
                    )
                )
                continue
            file_chunks, file_report = parse_material_file_with_report(
                path,
                metadata=metadata,
                chunk_chars=chunk_chars,
                overlap_chars=overlap_chars,
                file_hash=file_hash,
            )
            chunks.extend(file_chunks)
            reports.append(file_report)
        except Exception as exc:
            message = f"{path}: {type(exc).__name__}: {str(exc)[:180]}"
            if strict:
                raise
            errors.append(message)
            reports.append(
                MaterialFileReport(
                    source_file=str(path),
                    material_type=str(material_type_for_path(path)),
                    status="failed",
                    metadata_matched=bool(metadata),
                    error=message,
                )
            )
    return chunks, errors, reports


def parse_material_file(
    path: str | Path,
    *,
    metadata: Mapping[str, Any] | None = None,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[MaterialChunk]:
    """Parse one local material file into standardized chunks."""

    chunks, _ = parse_material_file_with_report(
        path,
        metadata=metadata,
        chunk_chars=chunk_chars,
        overlap_chars=overlap_chars,
    )
    return chunks


def parse_material_file_with_report(
    path: str | Path,
    *,
    metadata: Mapping[str, Any] | None = None,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
    file_hash: str | None = None,
) -> tuple[list[MaterialChunk], MaterialFileReport]:
    """Parse one file and return both chunks and file-level report."""

    file_path = Path(path)
    if not file_path.is_file():
        raise MaterialParseError(f"material file does not exist: path={file_path}")

    extractor = extractor_for_path(file_path)
    digest = file_hash or sha256_file(file_path)
    meta = dict(metadata or {})
    course_name = first_non_empty(as_optional_str(meta.get("course_name")), "Unknown Course")
    title = first_non_empty(
        as_optional_str(meta.get("title")),
        as_optional_str(meta.get("attachment_name")),
        file_path.stem,
    )

    chunks: list[MaterialChunk] = []
    chunk_index = 0
    extracted_chars = 0
    segment_count = 0
    methods: set[str] = set()
    warnings: list[str] = []
    for segment in extractor.extract(file_path):
        segment_count += 1
        method = segment.metadata.get("extraction_method")
        if isinstance(method, str) and method:
            methods.add(method)
        warning = segment.metadata.get("warning")
        if isinstance(warning, str) and warning:
            warnings.append(warning)
        segment_warnings = segment.metadata.get("warnings")
        if isinstance(segment_warnings, list):
            warnings.extend(str(item) for item in segment_warnings if item)
        cleaned = clean_material_text(segment.text)
        if not cleaned:
            continue
        extracted_chars += len(cleaned)
        for span in chunk_text_spans(cleaned, max_chars=chunk_chars, overlap_chars=overlap_chars):
            text_hash = sha256_text(span.text)
            chunks.append(
                MaterialChunk(
                    chunk_id=stable_chunk_id(
                        file_hash=digest,
                        page=segment.page,
                        slide=segment.slide,
                        section_title=segment.section_title,
                        chunk_index=chunk_index,
                        text_hash=text_hash,
                    ),
                    source_file=str(file_path),
                    file_hash=digest,
                    material_type=material_type_for_path(file_path),
                    course_name=course_name,
                    title=title,
                    page=segment.page,
                    slide=segment.slide,
                    section_title=segment.section_title,
                    chunk_index=chunk_index,
                    start_char=span.start_char,
                    end_char=span.end_char,
                    text_hash=text_hash,
                    text=span.text,
                    metadata={**meta, **segment.metadata},
                )
            )
            chunk_index += 1
    if not chunks:
        warnings.append("no_text_extracted")
    warnings = unique_preserve_order(warnings)
    report = MaterialFileReport(
        source_file=str(file_path),
        material_type=str(material_type_for_path(file_path)),
        status="parsed" if chunks else "empty",
        file_hash=digest,
        chunks=len(chunks),
        extracted_chars=extracted_chars,
        segment_count=segment_count,
        metadata_matched=bool(meta),
        extraction_methods=sorted(methods),
        warnings=warnings,
    )
    return chunks, report


def unique_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def iter_material_files(paths: Iterable[str | Path]) -> Iterable[Path]:
    """Yield supported files from files or directories in stable order."""

    suffixes = supported_suffixes()
    for path in iter_candidate_files(paths):
        if path.suffix.lower() in suffixes:
            yield path


def iter_candidate_files(paths: Iterable[str | Path]) -> Iterable[Path]:
    """Yield all file candidates from files or directories in stable order."""

    for raw_path in paths:
        path = Path(raw_path)
        if path.is_file():
            yield path
            continue
        if path.is_dir():
            for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
                yield item


def clean_material_text(value: str) -> str:
    """Normalize extracted document text without removing domain content."""

    text = value.replace("\ufeff", "").replace("\x00", "")
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"([A-Za-z])-\n([A-Za-z])", r"\1\2", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = remove_repeated_short_lines(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def remove_repeated_short_lines(value: str) -> str:
    """Drop obvious repeated headers/footers from page extraction."""

    lines = value.splitlines()
    counts: dict[str, int] = {}
    for line in lines:
        key = line.strip()
        if 0 < len(key) <= 80:
            counts[key] = counts.get(key, 0) + 1
    threshold = max(3, len(lines) // 20)
    kept = [
        line
        for line in lines
        if not (0 < len(line.strip()) <= 80 and counts.get(line.strip(), 0) > threshold)
    ]
    return "\n".join(kept)


def chunk_text(value: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    """Create paragraph-aware chunks with small character overlap."""

    return [span.text for span in chunk_text_spans(value, max_chars=max_chars, overlap_chars=overlap_chars)]


def chunk_text_spans(value: str, *, max_chars: int, overlap_chars: int) -> list[TextChunkSpan]:
    """Create paragraph-aware chunks and keep approximate character spans."""

    max_chars = max(max_chars, 200)
    overlap_chars = max(min(overlap_chars, max_chars // 3), 0)
    paragraphs = paragraph_spans(value)
    chunks: list[TextChunkSpan] = []
    current = ""
    current_start: int | None = None
    current_end: int | None = None

    for paragraph, start, end in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(TextChunkSpan(current.strip(), current_start, current_end))
                current = ""
                current_start = None
                current_end = None
            chunks.extend(
                slice_long_text_spans(
                    paragraph,
                    base_start=start,
                    max_chars=max_chars,
                    overlap_chars=overlap_chars,
                )
            )
            continue
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
            current_start = start if current_start is None else current_start
            current_end = end
            continue
        if current:
            chunks.append(TextChunkSpan(current.strip(), current_start, current_end))
            current = overlap_prefix(current, overlap_chars, paragraph)
            current_start = max((current_end or start) - overlap_chars, 0) if overlap_chars else start
            current_end = end
        else:
            current = paragraph
            current_start = start
            current_end = end

    if current:
        chunks.append(TextChunkSpan(current.strip(), current_start, current_end))
    return [chunk for chunk in chunks if chunk.text]


def paragraph_spans(value: str) -> list[tuple[str, int, int]]:
    """Return non-empty paragraphs with source character offsets."""

    spans: list[tuple[str, int, int]] = []
    for match in re.finditer(r"\S(?:.*?)(?=\n\s*\n|\Z)", value, flags=re.DOTALL):
        paragraph = match.group(0).strip()
        if not paragraph:
            continue
        leading = len(match.group(0)) - len(match.group(0).lstrip())
        trailing = len(match.group(0).rstrip())
        spans.append((paragraph, match.start() + leading, match.start() + trailing))
    return spans


def slice_long_text(value: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    return [
        span.text
        for span in slice_long_text_spans(
            value,
            base_start=0,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        )
    ]


def slice_long_text_spans(
    value: str,
    *,
    base_start: int,
    max_chars: int,
    overlap_chars: int,
) -> list[TextChunkSpan]:
    chunks: list[TextChunkSpan] = []
    start = 0
    while start < len(value):
        end = choose_chunk_end(value, start, max_chars)
        chunk = value[start:end].strip()
        if chunk:
            leading = len(value[start:end]) - len(value[start:end].lstrip())
            trailing = len(value[start:end].rstrip())
            chunks.append(TextChunkSpan(chunk, base_start + start + leading, base_start + start + trailing))
        if end >= len(value):
            break
        start = max(end - overlap_chars, start + 1)
    return chunks


def choose_chunk_end(value: str, start: int, max_chars: int) -> int:
    """Prefer sentence or line boundaries when slicing long paragraphs."""

    hard_end = min(start + max_chars, len(value))
    if hard_end >= len(value):
        return hard_end
    window = value[start:hard_end]
    boundary_chars = "\n。！？；.!?;"
    for index in range(len(window) - 1, max(len(window) // 2, 0), -1):
        if window[index] in boundary_chars:
            return start + index + 1
    for index in range(len(window) - 1, max(len(window) // 2, 0), -1):
        if window[index].isspace():
            return start + index + 1
    return hard_end


def overlap_prefix(previous: str, overlap_chars: int, next_text: str) -> str:
    if overlap_chars <= 0:
        return next_text
    tail = previous[-overlap_chars:].strip()
    return f"{tail}\n\n{next_text}".strip() if tail else next_text


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_chunk_id(
    *,
    file_hash: str,
    page: int | None,
    slide: int | None,
    section_title: str | None,
    chunk_index: int,
    text_hash: str,
) -> str:
    payload = "|".join(
        [
            file_hash,
            str(page or ""),
            str(slide or ""),
            section_title or "",
            str(chunk_index),
            text_hash[:24],
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"material_{digest}"


def write_chunks_jsonl(
    records: Iterable[MaterialChunk],
    output_path: str | Path,
    *,
    append: bool = False,
    dedupe: bool = False,
) -> int:
    """Write material chunks to a JSONL file and return the written count."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    mode = "a" if append else "w"
    seen_chunk_ids = load_existing_chunk_ids(path) if append and dedupe else set()
    with path.open(mode, encoding="utf-8") as stream:
        for record in records:
            record_keys = {record.chunk_id}
            legacy_key = legacy_chunk_key(record.model_dump(mode="json"))
            if legacy_key:
                record_keys.add(legacy_key)
            if dedupe and record_keys.intersection(seen_chunk_ids):
                continue
            stream.write(record.model_dump_json(exclude_none=True))
            stream.write("\n")
            seen_chunk_ids.update(record_keys)
            count += 1
    return count


def load_existing_chunk_ids(path: str | Path) -> set[str]:
    chunk_ids: set[str] = set()
    file_path = Path(path)
    if not file_path.exists():
        return chunk_ids
    for item in iter_jsonl_objects(file_path):
        chunk_id = item.get("chunk_id")
        if isinstance(chunk_id, str) and chunk_id:
            chunk_ids.add(chunk_id)
            continue
        fallback = legacy_chunk_key(item)
        if fallback:
            chunk_ids.add(fallback)
    return chunk_ids


def load_existing_file_hashes(path: str | Path) -> set[str]:
    file_hashes: set[str] = set()
    file_path = Path(path)
    if not file_path.exists():
        return file_hashes
    for item in iter_jsonl_objects(file_path):
        file_hash = item.get("file_hash")
        if isinstance(file_hash, str) and file_hash:
            file_hashes.add(file_hash)
    return file_hashes


def load_report_file_hashes(path: str | Path) -> set[str]:
    """Load file hashes from a previous parse report, including empty files."""

    file_path = Path(path)
    if not file_path.exists():
        return set()
    try:
        report = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    files = report.get("files")
    if not isinstance(files, list):
        return set()
    hashes: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            continue
        file_hash = item.get("file_hash")
        if isinstance(file_hash, str) and file_hash:
            hashes.add(file_hash)
    return hashes


def iter_jsonl_objects(path: Path) -> Iterable[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            yield item


def legacy_chunk_key(item: Mapping[str, Any]) -> str:
    source = str(item.get("source_file") or "")
    file_hash = str(item.get("file_hash") or "")
    chunk_index = str(item.get("chunk_index") or 0)
    text = str(item.get("text") or "")
    if not (source or file_hash or text):
        return ""
    digest = hashlib.sha256(f"{file_hash}:{source}:{chunk_index}:{text[:200]}".encode("utf-8")).hexdigest()[:20]
    return f"legacy_{digest}"


def build_parse_report(
    *,
    inputs: Iterable[str | Path],
    output: str | Path,
    chunk_chars: int,
    overlap_chars: int,
    incremental: bool,
    chunks_parsed: int,
    chunks_written: int,
    errors: list[str],
    file_reports: list[MaterialFileReport],
) -> MaterialParseReport:
    return MaterialParseReport(
        created_at=datetime.now(LOCAL_TIMEZONE).isoformat(),
        inputs=[str(item) for item in inputs],
        output=str(output),
        chunk_chars=chunk_chars,
        overlap_chars=overlap_chars,
        incremental=incremental,
        files_input=len(file_reports),
        files_parsed=sum(1 for item in file_reports if item.status in {"parsed", "empty"}),
        files_skipped=sum(1 for item in file_reports if item.status.startswith("skipped")),
        files_skipped_unchanged=sum(1 for item in file_reports if item.status == "skipped_unchanged"),
        files_skipped_unsupported=sum(1 for item in file_reports if item.status == "skipped_unsupported"),
        files_failed=sum(1 for item in file_reports if item.status == "failed"),
        chunks_parsed=chunks_parsed,
        chunks_written=chunks_written,
        errors=errors,
        files=file_reports,
    )


def write_parse_report(report: MaterialParseReport, output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def load_attachment_manifest(path: str | Path) -> dict[str, dict[str, Any]]:
    """Load A-module attachment export manifest keyed by local_path."""

    manifest_path = Path(path)
    metadata: dict[str, dict[str, Any]] = {}
    if not manifest_path.exists():
        return metadata
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        local_path = item.get("local_path")
        if not isinstance(local_path, str) or not local_path:
            continue
        metadata[normalize_path_key(local_path)] = {
            "origin": item.get("source"),
            "record_raw_id": item.get("record_raw_id"),
            "attachment_name": item.get("attachment_name"),
            "content_type": item.get("content_type"),
            "bytes": item.get("bytes"),
            "course_id": item.get("course_id"),
            "course_name": item.get("course_name"),
            "source_title": item.get("source_title"),
            "source_task_type": item.get("source_task_type"),
            "published_at": item.get("published_at"),
            "ddl": item.get("ddl"),
            "task_status": item.get("task_status"),
            "completed": item.get("completed"),
        }
    return metadata


def enrich_metadata_from_records(
    metadata_by_path: dict[str, dict[str, Any]],
    records_jsonl_paths: Iterable[str | Path],
) -> dict[str, dict[str, Any]]:
    """Attach course/title metadata from A's CourseTask JSONL when raw IDs match."""

    by_raw_id: dict[str, dict[str, Any]] = {}
    for raw_path in records_jsonl_paths:
        path = Path(raw_path)
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            raw_id = item.get("raw_id")
            if isinstance(raw_id, str):
                by_raw_id[raw_id] = {
                    "course_name": item.get("course_name"),
                    "source_title": item.get("title"),
                    "source_task_type": item.get("task_type"),
                }

    for metadata in metadata_by_path.values():
        raw_id = metadata.get("record_raw_id")
        if isinstance(raw_id, str) and raw_id in by_raw_id:
            for key, value in by_raw_id[raw_id].items():
                if value and not metadata.get(key):
                    metadata[key] = value
    return metadata_by_path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_path_key(path: str | Path) -> str:
    return str(Path(path).resolve()).lower()


def first_non_empty(*values: str | None) -> str:
    for value in values:
        if value and value.strip():
            return value.strip()
    return ""


def as_optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
