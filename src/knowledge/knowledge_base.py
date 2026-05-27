"""Knowledge base manager: unified entry point for C-module operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.knowledge.indexer import build_index_from_jsonl, load_index
from src.knowledge.models import KnowledgeStatus
from src.knowledge.retriever import Retriever


DEFAULT_INDEX_DIR = "storage/knowledge_index"
DEFAULT_CHUNKS_JSONL = "storage/material_chunks.jsonl"


class KnowledgeBase:
    """Unified knowledge base for building, loading, and searching indexes."""

    def __init__(
        self,
        index_dir: str | Path = DEFAULT_INDEX_DIR,
        chunks_jsonl: str | Path = DEFAULT_CHUNKS_JSONL,
    ) -> None:
        self._index_dir = Path(index_dir)
        self._chunks_jsonl = Path(chunks_jsonl)
        self._retriever: Retriever | None = None
        self._status: KnowledgeStatus | None = None

    # ------------------------------------------------------------------
    # Build / Load
    # ------------------------------------------------------------------
    def build(self) -> KnowledgeStatus:
        """Build indexes from the chunks JSONL file.

        Returns:
            KnowledgeStatus with build results.
        """
        status = build_index_from_jsonl(self._chunks_jsonl, self._index_dir)
        self._status = status

        if status.status == "ready":
            kw_idx, vec_idx, _ = load_index(self._index_dir)
            self._retriever = Retriever(kw_idx, vec_idx)

        return self._status

    def load(self) -> bool:
        """Load previously built indexes from disk.

        Returns:
            True if at least keyword index was loaded successfully.
        """
        kw_idx, vec_idx, status = load_index(self._index_dir)
        self._status = status

        if kw_idx.is_built():
            self._retriever = Retriever(kw_idx, vec_idx)
            return True

        return False

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        *,
        course_name: str | None = None,
        top_k: int = 10,
        mode: str = "hybrid",
    ) -> list[dict[str, Any]]:
        """Search the knowledge base.

        Args:
            query: Free-text search query.
            course_name: Optional course name filter.
            top_k: Maximum results.
            mode: "keyword", "vector", or "hybrid".

        Returns:
            List of result dicts compatible with E-module API format.
        """
        if self._retriever is None or not self._retriever.is_ready():
            return []

        results = self._retriever.search(
            query,
            course_name=course_name,
            top_k=top_k,
            mode=mode,
        )
        return [
            {
                "chunk_id": r.chunk_id,
                "title": r.title,
                "course_name": r.course_name,
                "text": r.text,
                "score": r.score,
                "citation": r.source,
            }
            for r in results
        ]

    def status(self) -> dict[str, Any]:
        """Return knowledge base status in E-module format."""
        if self._status is None:
            return {
                "status": "missing",
                "indexed_chunks": 0,
                "index_types": ["keyword", "vector", "hybrid"],
                "filters": ["course_name", "material_type"],
                "message": "Knowledge base not built. Run build() first.",
                "source_module": "C",
            }

        return {
            "status": self._status.status,
            "indexed_chunks": self._status.indexed_chunks,
            "index_types": self._status.index_types,
            "filters": self._status.filters,
            "message": self._status.message,
            "source_module": "C",
        }

    def is_built(self) -> bool:
        return self._retriever is not None and self._retriever.is_ready()

    def build_if_needed(self) -> KnowledgeStatus:
        """Load existing index or build if not present."""
        if self.load():
            return self._status  # type: ignore[return-value]
        return self.build()
