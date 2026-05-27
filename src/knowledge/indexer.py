"""Index builder: reads B-module JSONL and builds keyword + vector indexes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.knowledge.keyword_index import KeywordIndex
from src.knowledge.models import KnowledgeChunk, KnowledgeStatus
from src.knowledge.vector_index import VectorIndex


def build_index_from_jsonl(
    jsonl_path: str | Path,
    index_dir: str | Path,
) -> KnowledgeStatus:
    """Read B-module MaterialChunk JSONL and build all indexes.

    Args:
        jsonl_path: Path to B-module output JSONL (material_chunks.jsonl).
        index_dir: Root directory to persist indexes (knowledge_index/).

    Returns:
        KnowledgeStatus reflecting build result.
    """
    chunks = _load_chunks_from_jsonl(jsonl_path)
    if not chunks:
        return KnowledgeStatus(
            status="missing",
            indexed_chunks=0,
            message=f"No valid chunks found in {jsonl_path}.",
            index_dir=str(index_dir),
        )

    # Build keyword index (always available)
    kw_index = KeywordIndex()
    kw_index.build(chunks)
    kw_index.save(Path(index_dir) / "keyword")

    # Build vector index (optional, depends on sentence-transformers)
    vec_index = VectorIndex()
    vec_index.build(chunks)
    if vec_index.is_available():
        vec_index.save(Path(index_dir) / "vector")

    index_types = ["keyword"]
    if vec_index.is_available():
        index_types.append("vector")
        index_types.append("hybrid")

    return KnowledgeStatus(
        status="ready",
        indexed_chunks=len(chunks),
        index_types=index_types,
        filters=["course_name", "material_type"],
        index_dir=str(index_dir),
        message=f"Indexed {len(chunks)} chunks from {jsonl_path}.",
    )


def load_index(index_dir: str | Path) -> tuple[KeywordIndex, VectorIndex, KnowledgeStatus]:
    """Load previously built indexes from disk.

    Returns:
        (keyword_index, vector_index, status)
    """
    index_path = Path(index_dir)
    kw_index = KeywordIndex()
    vec_index = VectorIndex()

    kw_ok = False
    vec_ok = False

    if (index_path / "keyword" / "meta.json").exists():
        try:
            kw_index.load(index_path / "keyword")
            kw_ok = True
        except Exception:
            kw_ok = False

    if (index_path / "vector" / "embeddings.npy").exists():
        try:
            vec_index.load(index_path / "vector")
            vec_ok = vec_index.is_built()
        except Exception:
            vec_ok = False

    chunk_count = kw_index.chunk_count or vec_index.chunk_count
    index_types = ["keyword"]
    if vec_ok:
        index_types.append("vector")
        index_types.append("hybrid")

    if not kw_ok and not vec_ok:
        return kw_index, vec_index, KnowledgeStatus(
            status="missing",
            indexed_chunks=0,
            message="No valid index found on disk. Run build_index_from_jsonl().",
            index_dir=str(index_dir),
        )

    return kw_index, vec_index, KnowledgeStatus(
        status="ready",
        indexed_chunks=chunk_count,
        index_types=index_types,
        filters=["course_name", "material_type"],
        index_dir=str(index_dir),
        message=f"Loaded index with {chunk_count} chunks.",
    )


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _load_chunks_from_jsonl(jsonl_path: str | Path) -> list[KnowledgeChunk]:
    """Read JSONL and convert MaterialChunk records to KnowledgeChunks."""
    path = Path(jsonl_path)
    if not path.exists():
        return []

    chunks: list[KnowledgeChunk] = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue

            chunk_id = _stable_chunk_id(raw)
            chunks.append(
                KnowledgeChunk(
                    chunk_id=chunk_id,
                    source_file=raw.get("source_file", ""),
                    file_hash=raw.get("file_hash", ""),
                    material_type=raw.get("material_type", "unknown"),
                    course_name=raw.get("course_name", "Unknown Course"),
                    title=raw.get("title", "Untitled"),
                    page=raw.get("page"),
                    slide=raw.get("slide"),
                    section_title=raw.get("section_title"),
                    chunk_index=raw.get("chunk_index", 0),
                    text=raw.get("text", ""),
                    metadata=raw.get("metadata", {}),
                    created_at=raw.get("created_at"),
                )
            )
    return chunks


def _stable_chunk_id(raw: dict[str, Any]) -> str:
    """Generate a stable chunk ID from source metadata."""
    source = raw.get("source_file", "")
    idx = raw.get("chunk_index", 0)
    title = raw.get("title", "")
    text_preview = (raw.get("text") or "")[:100]
    digest = hashlib.sha256(f"{source}:{idx}:{title}:{text_preview}".encode()).hexdigest()[:16]
    return f"chunk_{digest}"
