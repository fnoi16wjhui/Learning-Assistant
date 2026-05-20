"""Public data contracts for the collector pipeline."""

from src.models.base import Attachment, CollectorRecord, SourceKind
from src.models.course_task import CourseTask, TaskType
from src.models.schedule import ScheduleItem, ScheduleType

__all__ = [
    "Attachment",
    "CollectorRecord",
    "CourseTask",
    "ScheduleItem",
    "ScheduleType",
    "SourceKind",
    "TaskType",
]

