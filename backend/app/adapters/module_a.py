"""Adapter for A-module collector outputs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from backend.app.adapters.jsonl import read_jsonl
from backend.app.data_source import is_demo_record
from backend.app.settings import BackendSettings, settings


TASK_TYPES = {"homework", "notice", "file", "questionnaire", "discussion", "exam"}
SCHEDULE_TYPES = {"class", "exam", "office_hour", "other"}
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


class ModuleAAdapter:
    """Read A-module JSONL outputs without reimplementing collector logic."""

    def __init__(self, config: BackendSettings = settings) -> None:
        self.config = config

    def task_records(self, *, include_all_semesters: bool = False, use_demo_fallback: bool = False) -> list[dict[str, Any]]:
        records = self._all_records(use_demo_fallback=use_demo_fallback)
        tasks = [
            record
            for record in records
            if record.get("task_type") in TASK_TYPES
            and (include_all_semesters or self._is_current_semester(record))
        ]
        return sorted([self._with_source(record) for record in tasks], key=self._task_sort_key)

    def schedule_records(self, *, use_demo_fallback: bool = False) -> list[dict[str, Any]]:
        records = self._all_records(use_demo_fallback=use_demo_fallback)
        schedules = [
            record
            for record in records
            if record.get("schedule_type") in SCHEDULE_TYPES and self._is_current_semester(record)
        ]
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
            records = read_jsonl(path) if exists else []
            demo_count = sum(1 for record in records if is_demo_record(record))
            if not exists:
                status = "missing"
                message = f"No JSONL output found at {path}"
            elif channel == "collector" and records and demo_count == len(records):
                status = "demo"
                message = "collector.jsonl exists but still contains demo records"
            else:
                status = "ready"
                message = f"Read from {path}"
            items.append(
                {
                    "channel": channel,
                    "status": status,
                    "record_count": len(records),
                    "demo_record_count": demo_count,
                    "last_synced_at": self._modified_at(path) if exists else None,
                    "message": message,
                }
            )
        payload = {
            "items": items,
            "source_module": "A",
            "status": "ready",
            "last_errors": _recent_sync_errors(),
        }
        return payload

    def _all_records(self, *, use_demo_fallback: bool = False) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path in (
            self.config.collector_jsonl,
            self.config.learn_jsonl,
            self.config.mail_jsonl,
            self.config.jwch_jsonl,
        ):
            records.extend(read_jsonl(path))
        if not records and use_demo_fallback:
            records.extend(read_jsonl(self.config.demo_collector_jsonl))
        real_records = [record for record in records if not is_demo_record(record)]
        if real_records:
            return real_records
        return records

    def _with_source(self, record: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(record)
        enriched["source_module"] = "A"
        enriched["course_name"] = normalize_course_name(enriched)
        enriched["data_quality_warnings"] = data_quality_warnings(enriched)
        enriched["data_quality_tag"] = data_quality_tag(enriched)
        if not self._is_current_semester(record):
            enriched["data_quality_tag"] = "old_semester"
            enriched["data_quality_warnings"] = list(enriched["data_quality_warnings"]) + ["旧学期记录，演示模式下默认隐藏。"]
        return enriched

    def _is_current_semester(self, record: dict[str, Any]) -> bool:
        cutoff = parse_datetime(self.config.semester_start)
        if cutoff is None:
            return True
        candidates = [
            parse_datetime(record.get("ddl")),
            parse_datetime(record.get("starts_at")),
            parse_datetime(record.get("created_at")),
        ]
        dated = [candidate for candidate in candidates if candidate is not None]
        if not dated:
            return str(record.get("source") or "") != "mail"
        return max(dated) >= cutoff

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


def data_quality_warnings(record: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if not str(record.get("title") or "").strip():
        warnings.append("缺少 title，已使用默认展示。")
    if not str(record.get("task_type") or "").strip():
        warnings.append("缺少 task_type。")
    if not str(record.get("created_at") or "").strip():
        warnings.append("缺少 created_at。")
    course = str(record.get("course_name") or "").strip()
    if not course or course == "Unknown Course":
        warnings.append("课程名缺失或为 Unknown Course，已尝试从标题提取。")
    if not str(record.get("ddl") or "").strip():
        warnings.append("无 DDL。")
    return warnings


def data_quality_tag(record: dict[str, Any]) -> str | None:
    course = str(record.get("course_name") or "").strip()
    if not course or course == "Unknown Course":
        return "unknown_course"
    if not str(record.get("ddl") or "").strip():
        return "no_ddl"
    return None


def _recent_sync_errors() -> list[dict[str, str]]:
    log_path = settings.project_root / "logs" / "collector.log"
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-200:]
    except OSError:
        return []

    errors: list[dict[str, str]] = []
    for line in lines:
        if "channel sync failed: channel=" not in line:
            continue
        channel = ""
        if "channel=learn" in line:
            channel = "learn"
        elif "channel=mail" in line:
            channel = "mail"
        elif "channel=jwch" in line:
            channel = "jwch"
        if not channel:
            continue
        message = _friendly_sync_error(channel, line.split("error=", 1)[-1].strip())
        if errors and errors[-1]["channel"] == channel:
            errors[-1]["message"] = message
        else:
            errors.append({"channel": channel, "message": message})
    return errors[-3:]


def _friendly_sync_error(channel: str, raw_message: str) -> str:
    lowered = raw_message.lower()
    if channel == "mail" and "login failed" in lowered:
        return "邮箱 IMAP 登录失败：请确认 MAIL_PASSWORD 是客户端专用密码，不是网页登录密码。"
    if channel == "jwch" and ("未登录" in raw_message or "roaming url" in lowered):
        return "教务系统未登录：请先确保学堂/Info 登录态可用，必要时配置 trust device。"
    if channel == "learn" and ("jsondecodeerror" in lowered or "non-json" in lowered or "login html" in lowered):
        return "学堂接口返回登录页或非 JSON：请检查 LEARN 账号密码，或是否存在二次验证/信任设备限制。"
    return raw_message


def normalize_course_name(record: dict[str, Any]) -> str:
    course_name = str(record.get("course_name") or "").strip()
    title = str(record.get("title") or "").strip()
    if course_name and course_name != "Unknown Course":
        return course_name
    if title.startswith("[") and "]" in title:
        candidate = title[1 : title.index("]")].strip()
        if candidate:
            return candidate
    if "——" in title:
        candidate = title.split("——", 1)[0].strip()
        if candidate:
            return candidate
    return course_name or "Unknown Course"
