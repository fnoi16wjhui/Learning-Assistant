"""Shared adapter primitives for raw IO boundaries."""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


class AdapterError(RuntimeError):
    """Raised when an adapter cannot authenticate or fetch raw data."""


@dataclass(frozen=True)
class RawPayload:
    """Raw source payload passed to parsers without cleaning."""

    source: str
    raw_id: str
    content: str | bytes
    content_type: str
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AdapterConfig:
    """Adapter configuration loaded from explicit values and env vars."""

    source: str
    base_url: str | None = None
    username: str | None = None
    password: str | None = None
    token: str | None = None
    data_path: Path | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(
        cls,
        source: str,
        *,
        prefix: str,
        defaults: Mapping[str, Any] | None = None,
    ) -> "AdapterConfig":
        """Build config from environment variables with optional defaults."""

        values = dict(defaults or {})
        data_path = os.getenv(f"{prefix}_DATA_PATH") or values.get("data_path")
        extra_raw = os.getenv(f"{prefix}_EXTRA_JSON") or values.get("extra_json")
        extra: dict[str, Any] = {}

        if extra_raw:
            try:
                parsed = json.loads(str(extra_raw))
            except json.JSONDecodeError as exc:
                raise AdapterError(f"Invalid {prefix}_EXTRA_JSON: {exc}") from exc
            if not isinstance(parsed, dict):
                raise AdapterError(f"{prefix}_EXTRA_JSON must be a JSON object")
            extra = parsed

        return cls(
            source=source,
            base_url=os.getenv(f"{prefix}_BASE_URL") or values.get("base_url"),
            username=os.getenv(f"{prefix}_USERNAME") or values.get("username"),
            password=os.getenv(f"{prefix}_PASSWORD") or values.get("password"),
            token=os.getenv(f"{prefix}_TOKEN") or values.get("token"),
            data_path=Path(data_path) if data_path else None,
            extra=extra,
        )


class BaseAdapter(ABC):
    """Base class for raw source adapters."""

    source: str
    env_prefix: str

    def __init__(self, config: AdapterConfig | None = None) -> None:
        self.config = config or AdapterConfig.from_env(
            self.source,
            prefix=self.env_prefix,
        )

    @abstractmethod
    def authenticate(self) -> None:
        """Prepare credentials, sessions, or clients for raw fetching."""

    @abstractmethod
    def fetch_raw(self, **kwargs: Any) -> list[RawPayload]:
        """Fetch raw records without parsing or normalizing business fields."""

    def require(self, field_name: str) -> str:
        """Return a required config value with contextual errors."""

        value = getattr(self.config, field_name)
        if not value:
            raise AdapterError(f"{self.source}_adapter missing required config: {field_name}")
        return str(value)

    def payload(
        self,
        *,
        raw_id: str,
        content: str | bytes,
        content_type: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> RawPayload:
        """Create a raw payload for pipeline handoff."""

        return RawPayload(
            source=self.source,
            raw_id=raw_id,
            content=content,
            content_type=content_type,
            metadata=dict(metadata or {}),
        )
