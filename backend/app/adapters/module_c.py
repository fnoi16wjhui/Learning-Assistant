"""Adapter for C-module knowledge base and retrieval.

Reads B-module MaterialChunk JSONL, builds/loads indexes,
and provides keyword/vector/hybrid search through a unified interface.
"""

from __future__ import annotations

from typing import Any

from backend.app.models import RetrievalRequest
from backend.app.settings import BackendSettings, settings
from src.knowledge.knowledge_base import KnowledgeBase


class ModuleCAdapter:
    """Expose C-module knowledge base and retrieval through E-module contracts."""

    def __init__(self, config: BackendSettings = settings) -> None:
        self._kb = KnowledgeBase(
            index_dir=config.knowledge_index_dir,
            chunks_jsonl=config.material_chunks_jsonl,
        )
        self._initialised = False

    def _ensure_index(self) -> None:
        """Lazy-build or load the knowledge base on first access."""
        if self._initialised:
            return
        self._kb.build_if_needed()
        self._initialised = True

    def status(self) -> dict[str, Any]:
        """Return knowledge base status."""
        self._ensure_index()
        return self._kb.status()

    def search(self, request: RetrievalRequest) -> dict[str, Any]:
        """Search the knowledge base and return results in E-module format."""
        self._ensure_index()

        items = self._kb.search(
            query=request.query,
            course_name=request.course_name,
            top_k=request.top_k,
            mode=request.mode,
        )

        status = "ready" if self._kb.is_built() else "missing"
        return {
            "query": request.query,
            "items": items,
            "source_module": "C",
            "status": status,
        }
