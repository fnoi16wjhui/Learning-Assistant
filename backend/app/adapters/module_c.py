"""Adapter for C-module knowledge base and retrieval.

Reads B-module MaterialChunk JSONL, builds/loads indexes,
and provides keyword/vector/hybrid search through a unified interface.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from typing import Any

from backend.app.data_source import material_data_source
from backend.app.models import RetrievalRequest
from backend.app.settings import BackendSettings, settings
from src.knowledge.knowledge_base import KnowledgeBase


class ModuleCAdapter:
    """Expose C-module knowledge base and retrieval through E-module contracts."""

    def __init__(self, config: BackendSettings = settings) -> None:
        self._config = config
        self._kb = KnowledgeBase(
            index_dir=config.knowledge_index_dir,
            chunks_jsonl=config.material_chunks_jsonl,
        )
        self._initialised = False
        self._last_built_at: str | None = None

    def _ensure_index(self) -> None:
        """Lazy-build or load the knowledge base on first access."""
        if self._initialised:
            return
        try:
            self._kb.build_if_needed()
        except Exception:
            pass
        self._initialised = True

    def status(self) -> dict[str, Any]:
        """Return knowledge base status."""
        try:
            self._ensure_index()
            payload = self._kb.status()
            payload["last_built_at"] = self._last_built_at
            payload.setdefault("warnings", [])
            payload.setdefault("errors", [])
            material_source = material_data_source()
            if material_source.get("source") == "demo":
                payload["status"] = "demo"
                payload["warnings"].append("知识库索引基于 Demo 资料分块构建，请解析真实资料后重建。")
            return payload
        except Exception as exc:
            return {
                "status": "blocked",
                "indexed_chunks": 0,
                "index_types": ["keyword", "vector", "hybrid"],
                "filters": ["course_name", "material_type"],
                "message": f"知识库状态读取失败：{exc}",
                "source_module": "C",
                "warnings": [],
                "errors": [
                    {
                        "error_code": "knowledge_status_failed",
                        "user_message": "知识库暂时不可用，不影响任务和资料页。",
                        "detail": str(exc),
                        "retryable": True,
                    }
                ],
            }

    def rebuild(self, *, force: bool = True) -> dict[str, Any]:
        """Rebuild knowledge index from material_chunks.jsonl."""
        chunks_path = self._config.material_chunks_jsonl
        if not chunks_path.exists():
            demo = self._config.demo_material_chunks_jsonl
            if demo.exists():
                return {
                    "status": "blocked",
                    "message": "未找到真实 material_chunks.jsonl，请先解析资料。",
                    "source_module": "C",
                    "warnings": ["可使用 Demo 数据继续演示其他页面。"],
                    "errors": [],
                }
            return {
                "status": "blocked",
                "message": "缺少 material_chunks.jsonl，无法重建知识库。",
                "source_module": "C",
                "warnings": [],
                "errors": [
                    {
                        "error_code": "missing_chunks",
                        "user_message": "请先导出附件并解析资料。",
                        "retryable": False,
                    }
                ],
            }

        index_dir = self._config.knowledge_index_dir
        previous_ready = self._kb.is_built()
        if force and index_dir.exists():
            shutil.rmtree(index_dir, ignore_errors=True)

        self._initialised = False
        self._kb = KnowledgeBase(index_dir=index_dir, chunks_jsonl=chunks_path)
        try:
            build_status = self._kb.build()
            self._initialised = True
            self._last_built_at = datetime.now(timezone.utc).isoformat()
            return {
                "status": build_status.status,
                "indexed_chunks": build_status.indexed_chunks,
                "message": build_status.message,
                "source_module": "C",
                "last_built_at": self._last_built_at,
                "warnings": [],
                "errors": [],
            }
        except Exception as exc:
            self._initialised = False
            if previous_ready:
                self._kb.load()
                self._initialised = True
                return {
                    "status": "ready",
                    "message": "重建失败，已保留上一版可用索引。",
                    "source_module": "C",
                    "warnings": [str(exc)],
                    "errors": [
                        {
                            "error_code": "rebuild_failed",
                            "user_message": "索引重建失败，仍可使用旧索引。",
                            "detail": str(exc),
                            "retryable": True,
                        }
                    ],
                }
            return {
                "status": "blocked",
                "message": f"知识库重建失败：{exc}",
                "source_module": "C",
                "warnings": [],
                "errors": [
                    {
                        "error_code": "rebuild_failed",
                        "user_message": "知识库不可用，请先确保资料已解析。",
                        "detail": str(exc),
                        "retryable": True,
                    }
                ],
            }

    def search(self, request: RetrievalRequest) -> dict[str, Any]:
        """Search the knowledge base and return results in E-module format."""
        self._ensure_index()
        warnings: list[str] = []
        mode = request.mode
        top_k = min(request.top_k, 5)

        if not self._kb.is_built():
            return {
                "query": request.query,
                "items": [],
                "source_module": "C",
                "status": "missing",
                "warnings": ["知识库未建立，请先解析资料并重建索引。"],
                "errors": [],
                "mode_used": mode,
            }

        items: list[dict[str, Any]] = []
        try:
            items = self._kb.search(
                query=request.query,
                course_name=request.course_name,
                top_k=top_k,
                mode=mode,
            )
        except Exception:
            if mode == "hybrid":
                warnings.append("混合检索不可用，已降级为关键词检索。")
                mode = "keyword"
                try:
                    items = self._kb.search(
                        query=request.query,
                        course_name=request.course_name,
                        top_k=top_k,
                        mode=mode,
                    )
                except Exception as exc:
                    return {
                        "query": request.query,
                        "items": [],
                        "source_module": "C",
                        "status": "blocked",
                        "warnings": warnings,
                        "errors": [
                            {
                                "error_code": "search_failed",
                                "user_message": "检索失败，请稍后重试或重建知识库。",
                                "detail": str(exc),
                                "retryable": True,
                            }
                        ],
                        "mode_used": mode,
                    }

        normalized = [_normalize_hit(item) for item in items[:5]]
        return {
            "query": request.query,
            "items": normalized,
            "source_module": "C",
            "status": "ready" if normalized else "missing",
            "warnings": warnings,
            "errors": [],
            "mode_used": mode,
        }


def _normalize_hit(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": item.get("chunk_id") or "unknown",
        "title": item.get("title") or "未命名片段",
        "course_name": item.get("course_name") or "未知课程",
        "text": (item.get("text") or "")[:500],
        "score": item.get("score") if item.get("score") is not None else 0.0,
        "citation": item.get("citation") or item.get("source") or "未知来源",
    }
