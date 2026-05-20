"""Schedule output contract."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from src.models.base import Attachment, CollectorRecord


class ScheduleType(StrEnum):
    """Kinds of calendar-like course records."""

    CLASS = "class"
    EXAM = "exam"
    OFFICE_HOUR = "office_hour"
    OTHER = "other"


class ScheduleItem(CollectorRecord):
    """Normalized schedule item for classes, exams, or course events."""

    schedule_type: ScheduleType = Field(..., description="Schedule category.")
    starts_at: datetime = Field(..., description="Event start time.")
    ends_at: datetime | None = Field(default=None, description="Event end time.")
    location: str | None = Field(default=None, description="Classroom or venue.")
    teacher: str | None = Field(default=None, description="Instructor name.")
    attachments: list[Attachment] = Field(default_factory=list)

