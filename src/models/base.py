"""Shared Pydantic contracts used by collector outputs."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def local_now() -> datetime:
    """Return timezone-aware local collector time."""

    return datetime.now(LOCAL_TIMEZONE)


class SourceKind(StrEnum):
    """Supported upstream source identifiers."""

    LEARN = "learn"
    MAIL = "mail"
    JWCH = "jwch"
    HARNESS = "harness"


class CollectorModel(BaseModel):
    """Base model with strict, JSON-friendly validation defaults."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class Attachment(CollectorModel):
    """Normalized attachment metadata without local credential material."""

    name: str = Field(..., min_length=1, description="Display file name.")
    download_url: HttpUrl | str = Field(
        ...,
        description="Original download URL or offline fixture URI.",
    )


class CollectorRecord(CollectorModel):
    """Common fields needed for fingerprinting and downstream routing."""

    source: SourceKind = Field(..., description="Origin channel.")
    raw_id: str = Field(..., min_length=1, description="Stable upstream ID.")
    course_name: str = Field(..., min_length=1, description="Course name.")
    title: str = Field(..., min_length=1, description="Human-readable title.")
    content: str = Field(..., description="Clean plain text content.")
    created_at: datetime = Field(
        default_factory=local_now,
        description="Local discovery time. Callers should use Asia/Shanghai.",
    )
