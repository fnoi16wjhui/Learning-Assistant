"""Course task output contract."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from src.models.base import Attachment, CollectorRecord


class TaskType(StrEnum):
    """Normalized course event categories."""

    HOMEWORK = "homework"
    NOTICE = "notice"
    FILE = "file"
    QUESTIONNAIRE = "questionnaire"
    DISCUSSION = "discussion"
    EXAM = "exam"


class CourseTask(CollectorRecord):
    """Unified task object delivered by adapters and parsers through pipeline."""

    task_type: TaskType = Field(..., description="Normalized task category.")
    ddl: datetime | None = Field(
        default=None,
        description="Deadline for homework-like tasks, if provided.",
    )
    attachments: list[Attachment] = Field(
        default_factory=list,
        description="Normalized attachments associated with this task.",
    )

