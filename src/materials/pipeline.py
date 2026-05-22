"""Local material parsing, cleaning, chunking, and JSONL output."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from src.materials.extractors import MaterialParseError, extractor_for_path, supported_suffixes
from src.materials.models import MaterialChunk, material_type_for_path


DEFAULT_CHUNK_CHARS = 900
DEFAULT_OVERLAP_CHARS = 120


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

    chunks: list[MaterialChunk] = []
    errors: list[str] = []
    metadata_map = {normalize_path_key(key): dict(value) for key, value in (metadata_by_path or {}).items()}
    files = list(iter_material_files(paths))
    if limit is not None:
        files = files[: max(limit, 0)]

    for path in files:
        metadata = metadata_map.get(normalize_path_key(path), {})
        try:
            chunks.extend(
                parse_material_file(
                    path,
                    metadata=metadata,
                    chunk_chars=chunk_chars,
                    overlap_chars=overlap_chars,
                )
            )
        except Exception as exc:
            message = f"{path}: {type(exc).__name__}: {str(exc)[:180]}"
            if strict:
                raise
            errors.append(message)
    return chunks, errors


def parse_material_file(
    path: str | Path,
    *,
    metadata: Mapping[str, Any] | None = None,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[MaterialChunk]:
    """Parse one local material file into standardized chunks."""

    file_path = Path(path)
    if not file_path.is_file():
        raise MaterialParseError(f"material file does not exist: path={file_path}")

    extractor = extractor_for_path(file_path)
    file_hash = sha256_file(file_path)
    meta = dict(metadata or {})
    course_name = first_non_empty(as_optional_str(meta.get("course_name")), "Unknown Course")
    title = first_non_empty(
        as_optional_str(meta.get("title")),
        as_optional_str(meta.get("attachment_name")),
        file_path.stem,
    )

    chunks: list[MaterialChunk] = []
    chunk_index = 0
    for segment in extractor.extract(file_path):
        cleaned = clean_material_text(segment.text)
        if not cleaned:
            continue
        for text_chunk in chunk_text(cleaned, max_chars=chunk_chars, overlap_chars=overlap_chars):
            chunks.append(
                MaterialChunk(
                    source_file=str(file_path),
                    file_hash=file_hash,
                    material_type=material_type_for_path(file_path),
                    course_name=course_name,
                    title=title,
                    page=segment.page,
                    slide=segment.slide,
                    section_title=segment.section_title,
                    chunk_index=chunk_index,
                    text=text_chunk,
                    metadata={**meta, **segment.metadata},
                )
            )
            chunk_index += 1
    return chunks


def iter_material_files(paths: Iterable[str | Path]) -> Iterable[Path]:
    """Yield supported files from files or directories in stable order."""

    suffixes = supported_suffixes()
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_file():
            if path.suffix.lower() in suffixes:
                yield path
            continue
        if path.is_dir():
            for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
                if item.suffix.lower() in suffixes:
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

    max_chars = max(max_chars, 200)
    overlap_chars = max(min(overlap_chars, max_chars // 3), 0)
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", value) if part.strip()]
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(slice_long_text(paragraph, max_chars=max_chars, overlap_chars=overlap_chars))
            continue
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current.strip())
            current = overlap_prefix(current, overlap_chars, paragraph)
        else:
            current = paragraph

    if current:
        chunks.append(current.strip())
    return [chunk for chunk in chunks if chunk]


def slice_long_text(value: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(value):
        end = min(start + max_chars, len(value))
        chunk = value[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(value):
            break
        start = max(end - overlap_chars, start + 1)
    return chunks


def overlap_prefix(previous: str, overlap_chars: int, next_text: str) -> str:
    if overlap_chars <= 0:
        return next_text
    tail = previous[-overlap_chars:].strip()
    return f"{tail}\n\n{next_text}".strip() if tail else next_text


def write_chunks_jsonl(
    records: Iterable[MaterialChunk],
    output_path: str | Path,
    *,
    append: bool = False,
) -> int:
    """Write material chunks to a JSONL file and return the written count."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8") as stream:
        for record in records:
            stream.write(record.model_dump_json(exclude_none=True))
            stream.write("\n")
            count += 1
    return count


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
            metadata.update({key: value for key, value in by_raw_id[raw_id].items() if value})
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
