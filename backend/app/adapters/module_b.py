"""Adapter for B-module material parsing outputs."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from backend.app.adapters.jsonl import read_jsonl
from backend.app.settings import BackendSettings, settings


class ModuleBAdapter:
    """Read standardized material chunks produced by B module."""

    def __init__(self, config: BackendSettings = settings) -> None:
        self.config = config

    def materials(self) -> list[dict[str, Any]]:
        records = read_jsonl(self.config.material_chunks_jsonl)
        if not records:
            records = read_jsonl(self.config.demo_material_chunks_jsonl)
        return [self._with_source(record) for record in records]

    def material_files(self) -> list[dict[str, Any]]:
        files: dict[str, dict[str, Any]] = {}
        for record in self.materials():
            source_file = str(record.get("source_file") or "unknown")
            if source_file not in files:
                item = dict(record)
                item.pop("text", None)
                metadata = item.get("metadata")
                if isinstance(metadata, dict):
                    for key in ("published_at", "ddl", "task_status", "completed"):
                        if metadata.get(key) not in (None, ""):
                            item[key] = metadata[key]
                item["chunk_count"] = 0
                item["content"] = ""
                item["material_id"] = str(item.get("file_hash") or source_file)
                files[source_file] = item
            files[source_file]["chunk_count"] += 1

        for source_file, item in files.items():
            material_type = str(item.get("material_type") or "file").upper()
            chunk_count = int(item["chunk_count"])
            published_at = str(item.get("published_at") or "")
            published_hint = f" · 上传于 {published_at[:10]}" if published_at else ""
            item["content"] = f"{material_type} · {chunk_count} 个内容片段{published_hint}"
            item["file_name"] = Path(source_file).name
        return sorted(
            files.values(),
            key=lambda item: (
                str(item.get("course_name") or ""),
                str(item.get("title") or item.get("file_name") or ""),
            ),
        )

    def parse_status(self) -> dict[str, Any]:
        records = self.materials()
        files = Counter(str(record.get("source_file", "unknown")) for record in records)
        items = [
            {
                "source_file": source_file,
                "chunk_count": chunk_count,
                "status": "parsed",
                "error": None,
            }
            for source_file, chunk_count in files.items()
        ]
        status = "ready" if records else "missing"
        return {
            "total": len(items),
            "parsed": len(items),
            "failed": 0,
            "items": items,
            "source_module": "B",
            "status": status,
        }

    @staticmethod
    def _with_source(record: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(record)
        enriched["source_module"] = "B"
        return enriched
