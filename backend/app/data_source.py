"""Detect whether the platform is serving demo or real synced data."""

from __future__ import annotations

from typing import Any

from backend.app.adapters.jsonl import read_jsonl
from backend.app.settings import settings


def is_demo_record(record: dict[str, Any]) -> bool:
    raw_id = str(record.get("raw_id") or record.get("id") or "")
    if raw_id.startswith("demo_"):
        return True
    meta = record.get("metadata")
    if isinstance(meta, dict) and meta.get("source") == "demo":
        return True
    if str(record.get("file_hash") or "") == "demo-sha256":
        return True
    return False


def task_data_source() -> dict[str, Any]:
    paths = [
        settings.collector_jsonl,
        settings.learn_jsonl,
        settings.mail_jsonl,
        settings.jwch_jsonl,
    ]
    records: list[dict[str, Any]] = []
    for path in paths:
        if path.exists():
            records.extend(read_jsonl(path))

    if not records:
        demo_records = read_jsonl(settings.demo_collector_jsonl)
        return {
            "source": "demo",
            "label": "Demo 任务数据（未成功同步真实学堂/邮箱数据）",
            "record_count": len(demo_records),
            "using_fallback": True,
        }

    demo_count = sum(1 for record in records if is_demo_record(record))
    if demo_count == len(records):
        return {
            "source": "demo",
            "label": "Demo 任务数据（collector.jsonl 仍是演示内容）",
            "record_count": len(records),
            "using_fallback": False,
        }

    return {
        "source": "real",
        "label": "真实同步任务数据",
        "record_count": len(records),
        "using_fallback": False,
        "demo_mixed_count": demo_count,
    }


def material_data_source() -> dict[str, Any]:
    real_path = settings.material_chunks_jsonl
    records = read_jsonl(real_path) if real_path.exists() else []
    using_fallback = False

    if not records:
        records = read_jsonl(settings.demo_material_chunks_jsonl)
        using_fallback = True

    if not records:
        return {
            "source": "missing",
            "label": "无资料数据",
            "chunk_count": 0,
            "using_fallback": False,
        }

    demo_count = sum(1 for record in records if is_demo_record(record))
    if demo_count == len(records):
        return {
            "source": "demo",
            "label": "Demo 资料分块（非真实解析结果）",
            "chunk_count": len(records),
            "using_fallback": using_fallback,
        }

    return {
        "source": "real",
        "label": "真实资料解析结果",
        "chunk_count": len(records),
        "using_fallback": using_fallback,
        "demo_mixed_count": demo_count,
    }
