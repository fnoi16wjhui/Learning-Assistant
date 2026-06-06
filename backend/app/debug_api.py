"""Sanitized debug helpers — no secrets in responses."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.app.adapters.jsonl import read_jsonl
from backend.app.adapters.module_a import _recent_sync_errors
from backend.app.data_source import is_demo_record, material_data_source, task_data_source
from backend.app.settings import settings
from backend.app.sync_jobs import get_latest_job, list_recent_jobs


def _file_probe(path: Path) -> dict[str, Any]:
    exists = path.exists()
    records = read_jsonl(path) if exists else []
    first_ids = [
        str(record.get("raw_id") or record.get("id") or "unknown")
        for record in records[:3]
    ]
    demo_count = sum(1 for record in records if is_demo_record(record))
    return {
        "path": str(path),
        "exists": exists,
        "record_count": len(records),
        "demo_count": demo_count,
        "all_demo": bool(records) and demo_count == len(records),
        "first_raw_ids": first_ids,
        "last_modified": path.stat().st_mtime if exists else None,
    }


def debug_data_source() -> dict[str, Any]:
    return {
        "source_module": "E",
        "status": "ready",
        "tasks": task_data_source(),
        "materials": material_data_source(),
        "files": {
            "collector_jsonl": _file_probe(settings.collector_jsonl),
            "learn_jsonl": _file_probe(settings.learn_jsonl),
            "mail_jsonl": _file_probe(settings.mail_jsonl),
            "jwch_jsonl": _file_probe(settings.jwch_jsonl),
            "material_chunks_jsonl": _file_probe(settings.material_chunks_jsonl),
            "demo_collector_jsonl": _file_probe(settings.demo_collector_jsonl),
            "demo_material_chunks_jsonl": _file_probe(settings.demo_material_chunks_jsonl),
        },
        "latest_sync_job": get_latest_job(),
    }


def debug_sync_errors() -> dict[str, Any]:
    return {
        "source_module": "E",
        "status": "ready",
        "collector_log_errors": _recent_sync_errors(),
        "latest_sync_job": get_latest_job(),
        "recent_jobs": list_recent_jobs(limit=5),
    }
