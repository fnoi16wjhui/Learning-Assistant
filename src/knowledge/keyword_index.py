"""Keyword-based index using jieba tokenizer and TF-IDF scoring."""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from src.knowledge.models import KnowledgeChunk, SearchResult


class KeywordIndex:
    """Inverted index with TF-IDF scoring for keyword search."""

    def __init__(self) -> None:
        self._chunks: dict[str, KnowledgeChunk] = {}
        self._index: dict[str, dict[str, float]] = {}  # term -> {chunk_id: tfidf}
        self._doc_count: int = 0
        self._built = False

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------
    def build(self, chunks: list[KnowledgeChunk]) -> None:
        """Tokenize and index all chunks."""
        import jieba

        jieba.initialize()  # warm-up

        self._chunks = {c.chunk_id: c for c in chunks}
        self._index = {}
        self._doc_count = len(chunks)

        doc_freq: Counter[str] = Counter()
        term_doc_tf: dict[str, list[tuple[str, float]]] = {}

        for chunk in chunks:
            tokens = self._tokenize(chunk.text)
            if not tokens:
                continue
            tf = Counter(tokens)
            total = len(tokens)
            for term, count in tf.items():
                term_doc_tf.setdefault(term, []).append((chunk.chunk_id, count / total))
            for term in set(tokens):
                doc_freq[term] += 1

        for term, postings in term_doc_tf.items():
            idf = math.log((self._doc_count + 1) / (doc_freq[term] + 1)) + 1
            self._index[term] = {cid: tf_val * idf for cid, tf_val in postings}

        self._built = True

    def search(self, query: str, *, top_k: int = 10) -> list[SearchResult]:
        """Search by keyword tokens and return scored results."""
        if not self._built or not query.strip():
            return []

        import jieba

        query_terms = list(jieba.cut(query.strip().lower()))
        scores: dict[str, float] = Counter()

        for term in query_terms:
            if not term.strip() or len(term) == 1:
                continue
            postings = self._index.get(term)
            if postings is None:
                continue
            for chunk_id, tfidf in postings.items():
                scores[chunk_id] += tfidf

        if not scores:
            return []

        ranked = scores.most_common(top_k)
        results: list[SearchResult] = []
        max_score = ranked[0][1] if ranked else 1.0

        for rank, (chunk_id, score) in enumerate(ranked):
            chunk = self._chunks.get(chunk_id)
            if chunk is None:
                continue
            results.append(
                SearchResult(
                    chunk_id=chunk_id,
                    title=chunk.title,
                    course_name=chunk.course_name,
                    text=chunk.text[:300],
                    score=score / max_score if max_score > 0 else 0.0,
                    source=chunk.source_file,
                )
            )
        return results

    def is_built(self) -> bool:
        return self._built

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def save(self, directory: str | Path) -> None:
        """Persist index and chunk metadata to disk."""
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)

        # Serialise index (only term -> {chunk_id: tfidf} mapping)
        index_data: dict[str, dict[str, float]] = {}
        for term, postings in self._index.items():
            index_data[term] = dict(sorted(postings.items(), key=lambda x: -x[1])[:200])

        (path / "keyword_index.json").write_text(
            json.dumps(index_data, ensure_ascii=False), encoding="utf-8"
        )

        # Serialise chunk metadata
        chunks_data = [
            chunk.model_dump(mode="json", exclude_none=True) for chunk in self._chunks.values()
        ]
        (path / "chunks.json").write_text(
            json.dumps(chunks_data, ensure_ascii=False), encoding="utf-8"
        )

        # Serialise doc count
        (path / "meta.json").write_text(
            json.dumps({"doc_count": self._doc_count}, ensure_ascii=False), encoding="utf-8"
        )

    def load(self, directory: str | Path) -> None:
        """Load previously persisted index from disk."""
        path = Path(directory)

        meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
        self._doc_count = meta["doc_count"]

        index_data: dict[str, dict[str, float]] = json.loads(
            (path / "keyword_index.json").read_text(encoding="utf-8")
        )
        self._index = index_data

        chunks_data: list[dict[str, Any]] = json.loads(
            (path / "chunks.json").read_text(encoding="utf-8")
        )
        self._chunks = {}
        for item in chunks_data:
            chunk = KnowledgeChunk(**item)
            self._chunks[chunk.chunk_id] = chunk

        self._built = True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _tokenize(text: str) -> list[str]:
        import jieba

        tokens = list(jieba.cut(text.strip().lower()))
        # Remove single characters, whitespace, and punctuation-only tokens
        return [t for t in tokens if len(t) > 1 and any(c.isalpha() for c in t)]
