"""Adapter for B-module material parsing outputs."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from backend.app.adapters.jsonl import read_jsonl
from backend.app.data_source import is_demo_record
from backend.app.settings import BackendSettings, settings


LOW_VALUE_KEYWORDS = ("实验报告", "作业实验报告", "我的作业", "作业提交", "上机作业实验报告")
TEACHER_MATERIAL_KEYWORDS = ("课件", "ppt", "pptx", "讲义", "lecture", "slide", "教材", "课程资料")


class ModuleBAdapter:
    """Read standardized material chunks produced by B module."""

    def __init__(self, config: BackendSettings = settings) -> None:
        self.config = config

    def materials(self, *, high_priority_only: bool = False, use_demo_fallback: bool = False) -> list[dict[str, Any]]:
        records = read_jsonl(self.config.material_chunks_jsonl)
        if not records and use_demo_fallback:
            records = read_jsonl(self.config.demo_material_chunks_jsonl)
        enriched = [self._with_source(record) for record in records]
        if high_priority_only:
            enriched = [record for record in enriched if record.get("material_priority", 1) < 2]
        return sorted(enriched, key=material_sort_key)

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
            if record.get("material_priority", 1) > files[source_file].get("material_priority", 1):
                files[source_file]["material_priority"] = record.get("material_priority")
            if record.get("data_quality_tag"):
                files[source_file]["data_quality_tag"] = record.get("data_quality_tag")
                files[source_file]["display_hint"] = record.get("display_hint")

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
        records = self.materials(use_demo_fallback=False)
        if not records:
            records = self.materials(use_demo_fallback=True)
        files = Counter(str(record.get("source_file", "unknown")) for record in records)
        low_value = sum(1 for record in records if is_low_value_material(record))
        report = _load_parse_report(self.config.project_root / "storage" / "material_parse_report.json")
        failed_files = report.get("failed_files", 0) if report else 0
        items = [
            {
                "source_file": source_file,
                "chunk_count": chunk_count,
                "status": "parsed",
                "error": None,
            }
            for source_file, chunk_count in files.items()
        ]
        for entry in report.get("file_reports", []) if report else []:
            if entry.get("status") == "failed":
                items.append(
                    {
                        "source_file": entry.get("path", "unknown"),
                        "chunk_count": 0,
                        "status": "failed",
                        "error": entry.get("error") or "parse failed",
                    }
                )
        real_records = self.materials(use_demo_fallback=False)
        if not records:
            status = "missing"
        elif not real_records and records:
            status = "demo"
        elif real_records and all(is_demo_record(record) for record in real_records):
            status = "demo"
        else:
            status = "ready"
        return {
            "total": len(items),
            "parsed": sum(1 for item in items if item["status"] == "parsed"),
            "failed": failed_files or sum(1 for item in items if item["status"] == "failed"),
            "low_value_chunks": low_value,
            "chunk_count": len(records),
            "items": items[:50],
            "source_module": "B",
            "status": status,
            "warnings": [],
            "errors": [],
        }

    @staticmethod
    def _with_source(record: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(record)
        enriched["source_module"] = "B"
        enriched["material_priority"] = material_priority(enriched)
        enriched["display_hint"] = (
            "可能是学生作业/实验报告，已降低展示优先级"
            if is_low_value_material(enriched)
            else "优先参考资料"
        )
        enriched["data_quality_tag"] = "low_priority" if is_low_value_material(enriched) else None
        text = str(enriched.get("text") or "")
        enriched["text_preview"] = text[:280] + ("…" if len(text) > 280 else "")
        return enriched


def _load_parse_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def material_sort_key(record: dict[str, Any]) -> tuple[int, str, int]:
    return (
        material_priority(record),
        str(record.get("course_name") or ""),
        int(record.get("chunk_index") or 0),
    )


def material_priority(record: dict[str, Any]) -> int:
    text = material_text(record)
    if any(keyword.lower() in text for keyword in TEACHER_MATERIAL_KEYWORDS):
        return 0
    if is_low_value_material(record):
        return 2
    return 1


def is_low_value_material(record: dict[str, Any]) -> bool:
    metadata = record.get("metadata")
    if isinstance(metadata, dict) and metadata.get("source_task_type") == "homework":
        return True
    text = material_text(record)
    return any(keyword.lower() in text for keyword in LOW_VALUE_KEYWORDS)


def material_text(record: dict[str, Any]) -> str:
    parts = [
        record.get("title"),
        record.get("source_file"),
        record.get("section_title"),
        record.get("text"),
    ]
    return " ".join(str(part or "") for part in parts).lower()
