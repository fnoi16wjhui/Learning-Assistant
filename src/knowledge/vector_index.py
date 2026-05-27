"""Vector-based index using sentence-transformers and numpy cosine similarity.

This module is optional. When sentence-transformers or numpy is not installed,
vector search degrades gracefully (is_available() returns False).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.knowledge.models import KnowledgeChunk, SearchResult


class VectorIndex:
    """Dense vector index with cosine similarity search."""

    def __init__(self) -> None:
        self._chunks: dict[str, KnowledgeChunk] = {}
        self._chunk_ids: list[str] = []
        self._embeddings: Any = None  # np.ndarray when built
        self._model: Any = None  # SentenceTransformer when loaded
        self._built = False
        self._available = False

    def build(self, chunks: list[KnowledgeChunk]) -> None:
        """Generate embeddings and index chunks.

        Requires sentence-transformers and numpy installed.
        Silently returns without building when dependencies are missing.
        """
        try:
            import numpy as np
            from sentence_transformers import SentenceTransformer
        except ImportError:
            self._available = False
            return

        self._available = True
        model_name = "shibing624/text2vec-base-chinese"
        self._model = SentenceTransformer(model_name)

        self._chunks = {c.chunk_id: c for c in chunks}
        self._chunk_ids = [c.chunk_id for c in chunks]

        texts = [f"{c.title}\n{c.text}" for c in chunks]
        self._embeddings = self._model.encode(texts, show_progress_bar=False, convert_to="numpy")
        self._built = True

    def search(self, query: str, *, top_k: int = 10) -> list[SearchResult]:
        """Search by vector similarity and return scored results."""
        if not self._built or not self._available or not query.strip():
            return []

        import numpy as np

        query_vec = self._model.encode([query], show_progress_bar=False, convert_to="numpy")
        scores = np.dot(self._embeddings, query_vec.T).flatten()
        top_indices = np.argsort(scores)[::-1][:top_k]

        results: list[SearchResult] = []
        for idx in top_indices:
            if scores[idx] <= 0:
                continue
            chunk = self._chunks.get(self._chunk_ids[idx])
            if chunk is None:
                continue
            results.append(
                SearchResult(
                    chunk_id=chunk.chunk_id,
                    title=chunk.title,
                    course_name=chunk.course_name,
                    text=chunk.text[:300],
                    score=float(scores[idx]),
                    source=chunk.source_file,
                )
            )
        return results

    def is_built(self) -> bool:
        return self._built

    def is_available(self) -> bool:
        return self._available

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def save(self, directory: str | Path) -> None:
        """Persist embeddings and chunk ordering to disk."""
        if not self._built:
            return
        import numpy as np

        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)

        np.save(str(path / "embeddings.npy"), self._embeddings)
        (path / "chunk_ids.json").write_text(
            json.dumps(self._chunk_ids, ensure_ascii=False), encoding="utf-8"
        )

    def load(self, directory: str | Path) -> None:
        """Load previously persisted embeddings from disk."""
        try:
            import numpy as np
            from sentence_transformers import SentenceTransformer
        except ImportError:
            self._available = False
            return

        path = Path(directory)
        emb_path = path / "embeddings.npy"
        ids_path = path / "chunk_ids.json"

        if not emb_path.exists() or not ids_path.exists():
            return

        self._embeddings = np.load(str(emb_path))
        self._chunk_ids = json.loads(ids_path.read_text(encoding="utf-8"))

        # Rebuild chunks dict from keyword index chunk metadata
        keyword_chunks_path = Path(directory).parent / "keyword" / "chunks.json"
        if keyword_chunks_path.exists():
            chunks_data: list[dict[str, Any]] = json.loads(
                keyword_chunks_path.read_text(encoding="utf-8")
            )
            self._chunks = {}
            for item in chunks_data:
                chunk = KnowledgeChunk(**item)
                self._chunks[chunk.chunk_id] = chunk

        self._model = SentenceTransformer("shibing624/text2vec-base-chinese")
        self._available = True
        self._built = True
