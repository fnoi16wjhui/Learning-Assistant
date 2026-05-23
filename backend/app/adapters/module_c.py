"""Mock-first adapter for C-module knowledge base and retrieval."""

from __future__ import annotations

from typing import Any

from backend.app.models import RetrievalRequest


class ModuleCAdapter:
    """Expose C-module contracts while the real retrieval service is pending."""

    def status(self) -> dict[str, Any]:
        return {
            "status": "mock",
            "indexed_chunks": 0,
            "index_types": ["keyword", "vector", "hybrid"],
            "filters": ["course_name", "material_type", "time_range"],
            "message": "C module will replace this Mock with knowledge base status.",
            "source_module": "C",
        }

    def search(self, request: RetrievalRequest) -> dict[str, Any]:
        course_name = request.course_name or "Unknown Course"
        return {
            "query": request.query,
            "items": [
                {
                    "chunk_id": "mock-c-1",
                    "title": "Mock 知识片段",
                    "course_name": course_name,
                    "text": "这里是检索 Mock 结果。真实关键词、向量和混合检索由 C 模块补齐。",
                    "score": 0.8,
                    "citation": "C module Mock",
                }
            ][: request.top_k],
            "source_module": "C",
            "status": "mock",
        }
