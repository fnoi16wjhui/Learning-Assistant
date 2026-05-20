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
