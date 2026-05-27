"""Retriever: hybrid search with RRF fusion and metadata filtering."""

from __future__ import annotations

from typing import Any

from src.knowledge.keyword_index import KeywordIndex
from src.knowledge.models import SearchResult, SearchMode
from src.knowledge.vector_index import VectorIndex


RRF_K = 60  # RRF constant for reciprocal rank fusion


class Retriever:
    """Unified retriever supporting keyword, vector, and hybrid search modes."""

    def __init__(
        self,
        keyword_index: KeywordIndex,
        vector_index: VectorIndex | None = None,
    ) -> None:
        self._kw = keyword_index
        self._vec = vector_index

    def search(
        self,
        query: str,
        *,
        course_name: str | None = None,
        material_type: str | None = None,
        top_k: int = 10,
        mode: str = "hybrid",
    ) -> list[SearchResult]:
        """Search across indexed knowledge chunks.

        Args:
            query: Free-text search query.
            course_name: Optional course name filter.
            material_type: Optional material type filter.
            top_k: Maximum results to return.
            mode: One of "keyword", "vector", "hybrid".

        Returns:
            Ranked list of SearchResult items.
        """
        if not query.strip():
            return []

        mode_enum = self._resolve_mode(mode)

        if mode_enum == SearchMode.KEYWORD:
            results = self._kw.search(query, top_k=top_k * 2)
        elif mode_enum == SearchMode.VECTOR:
            if self._vec is None or not self._vec.is_built():
                return []
            results = self._vec.search(query, top_k=top_k * 2)
        else:  # hybrid
            results = self._hybrid_search(query, top_k=top_k * 2)

        # Apply metadata filters
        results = self._apply_filters(results, course_name=course_name, material_type=material_type)

        # Normalise scores to [0, 1]
        if results:
            max_score = max(r.score for r in results)
            if max_score > 0:
                for r in results:
                    r.score = round(r.score / max_score, 4)

        return results[:top_k]

    def is_ready(self) -> bool:
        """Return True when at least keyword search is available."""
        return self._kw.is_built()

    def status_info(self) -> dict[str, Any]:
        """Return diagnostic information about the retriever state."""
        return {
            "keyword_built": self._kw.is_built(),
            "keyword_chunks": self._kw.chunk_count,
            "vector_built": self._vec is not None and self._vec.is_built(),
            "vector_chunks": self._vec.chunk_count if self._vec and self._vec.is_built() else 0,
            "vector_available": self._vec is not None and self._vec.is_available(),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_mode(mode: str) -> SearchMode:
        try:
            return SearchMode(mode.lower())
        except ValueError:
            return SearchMode.HYBRID

    def _hybrid_search(self, query: str, *, top_k: int) -> list[SearchResult]:
        """Reciprocal rank fusion (RRF) of keyword and vector results."""
        kw_results = self._kw.search(query, top_k=top_k)
        vec_results = (
            self._vec.search(query, top_k=top_k)
            if self._vec and self._vec.is_built()
            else []
        )

        if not kw_results and not vec_results:
            return []
        if not vec_results:
            return kw_results
        if not kw_results:
            return vec_results

        rrf_scores: dict[str, float] = {}
        result_map: dict[str, SearchResult] = {}

        for rank, r in enumerate(kw_results):
            rrf_scores[r.chunk_id] = rrf_scores.get(r.chunk_id, 0.0) + 1.0 / (rank + RRF_K)
            result_map[r.chunk_id] = r

        for rank, r in enumerate(vec_results):
            rrf_scores[r.chunk_id] = rrf_scores.get(r.chunk_id, 0.0) + 1.0 / (rank + RRF_K)
            result_map[r.chunk_id] = r

        ranked = sorted(rrf_scores.items(), key=lambda x: -x[1])
        results = []
        for chunk_id, score in ranked:
            original = result_map.get(chunk_id)
            if original is None:
                continue
            results.append(
                SearchResult(
                    chunk_id=original.chunk_id,
                    title=original.title,
                    course_name=original.course_name,
                    text=original.text,
                    score=score,
                    source=original.source,
                )
            )
        return results

    @staticmethod
    def _apply_filters(
        results: list[SearchResult],
        course_name: str | None = None,
        material_type: str | None = None,
    ) -> list[SearchResult]:
        if course_name:
            results = [r for r in results if r.course_name == course_name]
        if material_type:
            results = [r for r in results if material_type.lower() in r.source.lower()]
        return results
