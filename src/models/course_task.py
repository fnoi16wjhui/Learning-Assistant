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
    status: str | None = Field(
        default=None,
        description="Normalized upstream status such as unsubmitted, submitted_ungraded, or graded.",
    )
    completed: bool | None = Field(
        default=None,
        description="Whether the student has completed/submitted the task when known.",
    )
    published_at: datetime | None = Field(
        default=None,
        description="Upstream open/upload time when provided by Learn.",
    )

