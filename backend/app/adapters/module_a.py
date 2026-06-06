"""Adapter for A-module collector outputs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from backend.app.adapters.jsonl import read_jsonl
from backend.app.settings import BackendSettings, settings


TASK_TYPES = {"homework", "notice", "file", "questionnaire", "discussion", "exam"}
SCHEDULE_TYPES = {"class", "exam", "office_hour", "other"}
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


class ModuleAAdapter:
    """Read A-module JSONL outputs without reimplementing collector logic."""

    def __init__(self, config: BackendSettings = settings) -> None:
        self.config = config

    def task_records(self) -> list[dict[str, Any]]:
        records = self._all_records()
        tasks = [record for record in records if record.get("task_type") in TASK_TYPES]
        return sorted([self._with_source(record) for record in tasks], key=self._task_sort_key)

    def schedule_records(self) -> list[dict[str, Any]]:
        records = self._all_records()
        schedules = [record for record in records if record.get("schedule_type") in SCHEDULE_TYPES]
        return [self._with_source(record) for record in schedules]

    def sync_status(self) -> dict[str, Any]:
        paths = {
            "collector": self.config.collector_jsonl,
            "learn": self.config.learn_jsonl,
            "mail": self.config.mail_jsonl,
            "jwch": self.config.jwch_jsonl,
        }
        items: list[dict[str, Any]] = []
        for channel, path in paths.items():
            exists = path.exists()
            items.append(
                {
                    "channel": channel,
                    "status": "ready" if exists else "missing",
                    "record_count": len(read_jsonl(path)) if exists else 0,
                    "last_synced_at": self._modified_at(path) if exists else None,
                    "message": f"Read from {path}" if exists else f"No JSONL output found at {path}",
                }
            )
        return {"items": items, "source_module": "A", "status": "ready"}

    def _all_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path in (
            self.config.collector_jsonl,
            self.config.learn_jsonl,
            self.config.mail_jsonl,
            self.config.jwch_jsonl,
        ):
            records.extend(read_jsonl(path))
        if not records:
            records.extend(read_jsonl(self.config.demo_collector_jsonl))
        return records

    @staticmethod
    def _with_source(record: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(record)
        enriched["source_module"] = "A"
        return enriched

    @staticmethod
    def _task_sort_key(record: dict[str, Any]) -> tuple[int, float, str]:
        now = datetime.now(LOCAL_TIMEZONE)
        ddl = parse_datetime(record.get("ddl"))
        if ddl is not None:
            if ddl >= now:
                return (0, ddl.timestamp(), str(record.get("title") or ""))
            return (2, -ddl.timestamp(), str(record.get("title") or ""))
        created_at = parse_datetime(record.get("created_at"))
        if created_at is not None:
            return (1, -created_at.timestamp(), str(record.get("title") or ""))
        return (1, 0.0, str(record.get("title") or ""))

    @staticmethod
    def _modified_at(path: Any) -> str:
        timestamp = path.stat().st_mtime
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=LOCAL_TIMEZONE)
    return parsed.astimezone(LOCAL_TIMEZONE)
