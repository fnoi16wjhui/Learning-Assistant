"""Pipeline helpers for fingerprinting, deduplication, and JSON output."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from src.models import CourseTask, ScheduleItem

PipelineRecord = CourseTask | ScheduleItem
TRecord = TypeVar("TRecord", bound=PipelineRecord)


class PipelineError(RuntimeError):
    """Raised when persistence or serialization fails with local context."""


class DeduplicationStore:
    """SQLite-backed state store for incremental collector output."""

    def __init__(self, db_path: str | Path = "storage/app.db") -> None:
        self.db_path = Path(db_path)
        self._memory_connection: sqlite3.Connection | None = None
        if str(db_path) == ":memory:":
            self._memory_connection = sqlite3.connect(":memory:")
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        if self._memory_connection is not None:
            return self._memory_connection
        return sqlite3.connect(self.db_path)

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS fingerprints (
                    fingerprint TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    raw_id TEXT NOT NULL,
                    record_type TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_state (
                    source TEXT NOT NULL,
                    state_key TEXT NOT NULL,
                    state_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (source, state_key)
                )
                """
            )

    def seen_or_add(self, record: PipelineRecord, fingerprint: str) -> bool:
        """Return True when the fingerprint already exists, otherwise persist it."""

        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO fingerprints (
                        fingerprint, source, raw_id, record_type, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        fingerprint,
                        str(record.source),
                        record.raw_id,
                        record.__class__.__name__,
                        datetime.now().isoformat(),
                    ),
                )
                return cursor.rowcount == 0
        except sqlite3.Error as exc:
            raise PipelineError(
                f"SQLite deduplication failed for source={record.source} "
                f"raw_id={record.raw_id} db_path={self.db_path}"
            ) from exc

    def get_state(self, source: str, key: str, default: str | None = None) -> str | None:
        """Read a source-specific cursor or lightweight sync state value."""

        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT state_value FROM sync_state
                    WHERE source = ? AND state_key = ?
                    """,
                    (source, key),
                ).fetchone()
                return str(row[0]) if row else default
        except sqlite3.Error as exc:
            raise PipelineError(
                f"SQLite state read failed for source={source} key={key} db_path={self.db_path}"
            ) from exc

    def set_state(self, source: str, key: str, value: str | int) -> None:
        """Persist a source-specific cursor after successful processing."""

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO sync_state (source, state_key, state_value, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(source, state_key) DO UPDATE SET
                        state_value = excluded.state_value,
                        updated_at = excluded.updated_at
                    """,
                    (source, key, str(value), datetime.now().isoformat()),
                )
        except sqlite3.Error as exc:
            raise PipelineError(
                f"SQLite state write failed for source={source} key={key} db_path={self.db_path}"
            ) from exc

    def get_int_state(self, source: str, key: str, default: int = 0) -> int:
        """Read an integer cursor with contextual validation errors."""

        value = self.get_state(source, key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError as exc:
            raise PipelineError(f"Invalid integer sync state for source={source} key={key}") from exc


def fingerprint_for(source: str, raw_id: str) -> str:
    """Build a stable SHA-256 fingerprint from source and upstream raw ID."""

    canonical = f"{source.strip()}:{raw_id.strip()}".encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def record_to_json(record: BaseModel) -> str:
    """Serialize a Pydantic record into deterministic UTF-8 JSON."""

    return record.model_dump_json(by_alias=True, exclude_none=True)


def write_jsonl(records: Iterable[PipelineRecord], output_path: str | Path) -> int:
    """Append validated records to a JSONL file and return the written count."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("a", encoding="utf-8") as stream:
        for record in records:
            stream.write(record_to_json(record))
            stream.write("\n")
            count += 1
    return count


def upsert_jsonl_by_raw_id(records: Iterable[PipelineRecord], output_path: str | Path) -> int:
    """Merge records into a JSONL file by (source, raw_id), keeping the richest row."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_jsonl_dicts(path)
    incoming = [_record_to_dict(record) for record in records]
    existing_keys = {_record_key(item) for item in existing}
    merged = _merge_records_by_raw_id([*existing, *incoming])
    _write_jsonl_dicts(path, merged)
    return sum(1 for record in incoming if _record_key(record) not in existing_keys)


def compact_jsonl_by_raw_id(output_path: str | Path) -> int:
    """Remove duplicate (source, raw_id) rows from an existing JSONL file."""

    path = Path(output_path)
    existing = _read_jsonl_dicts(path)
    merged = _merge_records_by_raw_id(existing)
    if len(merged) != len(existing):
        _write_jsonl_dicts(path, merged)
    return len(existing) - len(merged)


def filter_new_records(
    records: Iterable[TRecord],
    store: DeduplicationStore | None = None,
) -> list[TRecord]:
    """Validate fingerprint uniqueness and keep only new records."""

    dedup_store = store or DeduplicationStore()
    fresh: list[TRecord] = []
    for record in records:
        fingerprint = fingerprint_for(str(record.source), record.raw_id)
        if not dedup_store.seen_or_add(record, fingerprint):
            fresh.append(record)
    return fresh


def _record_to_dict(record: BaseModel) -> dict[str, Any]:
    return record.model_dump(mode="json", by_alias=True, exclude_none=True)


def _read_jsonl_dicts(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            logging.warning("skipping invalid jsonl line in %s", path)
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _write_jsonl_dicts(path: Path, records: list[dict[str, Any]]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")
    tmp_path.replace(path)


def _merge_records_by_raw_id(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for record in records:
        key = _record_key(record)
        if not key[1]:
            order.append((key[0], f"__missing_raw_id_{len(order)}"))
            merged[order[-1]] = record
            continue
        current = merged.get(key)
        if current is None:
            order.append(key)
            merged[key] = record
        else:
            merged[key] = _better_record(current, record)
    return [merged[key] for key in order if key in merged]


def _record_key(record: dict[str, Any]) -> tuple[str, str]:
    return (str(record.get("source") or ""), str(record.get("raw_id") or ""))


def _better_record(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return right if _record_quality(right) >= _record_quality(left) else left


def _record_quality(record: dict[str, Any]) -> tuple[int, int, int, int, str]:
    attachments = record.get("attachments")
    attachment_count = len(attachments) if isinstance(attachments, list) else 0
    content = record.get("content")
    content_length = len(content.strip()) if isinstance(content, str) else 0
    known_fields = sum(
        1
        for key in ("ddl", "published_at", "status", "completed", "starts_at", "ends_at", "location", "teacher")
        if record.get(key) not in (None, "")
    )
    created_at = str(record.get("created_at") or "")
    return (known_fields, attachment_count, content_length, len(record), created_at)


def load_records_from_json(
    payload: str,
    model: type[TRecord],
    *,
    context: str = "inline-json",
) -> list[TRecord]:
    """Load offline JSON payloads into typed records for harness or tests."""

    try:
        data: Any = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise PipelineError(f"Invalid JSON payload in {context}") from exc

    items = data if isinstance(data, list) else [data]
    try:
        return [model.model_validate(item) for item in items]
    except ValidationError as exc:
        raise PipelineError(f"Schema validation failed in {context}") from exc


def configure_logging(log_path: str | Path = "logs/collector.log") -> None:
    """Configure local file logging without leaking environment variables."""

    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
