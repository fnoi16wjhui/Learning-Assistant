"""Adapter for B-module material parsing outputs."""

from __future__ import annotations

from collections import Counter
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
