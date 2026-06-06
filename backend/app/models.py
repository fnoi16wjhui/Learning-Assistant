"""Pydantic models for E-module public APIs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ModuleName = Literal["A", "B", "C", "D", "E"]
ModuleStatus = Literal["ready", "mock", "in_progress", "blocked", "missing"]


class ModulePayload(BaseModel):
    """Common provenance fields returned by E-module APIs."""

    source_module: ModuleName
    status: ModuleStatus = "ready"


class ListPayload(ModulePayload):
    items: list[dict[str, Any]]
    total: int


class SyncRequest(BaseModel):
    channel: Literal["learn", "mail", "jwch", "all"] = "all"
    allow_network: bool = False
    semester_id: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{4}-[123]$",
        description="Optional Learn semester ID such as 2025-2026-2.",
    )


class RetrievalRequest(BaseModel):
    query: str = Field(..., min_length=1)
    course_name: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    mode: Literal["keyword", "vector", "hybrid"] = "hybrid"


class QARequest(BaseModel):
    question: str = Field(..., min_length=1)
    course_name: str | None = None
    conversation_id: str | None = None


class SummaryRequest(BaseModel):
    course_name: str | None = None
    material_id: str | None = None
    topic: str | None = None
    page: int | None = Field(default=None, ge=1, description="Focus question on a specific page of the material")
    slide: int | None = Field(default=None, ge=1, description="Focus question on a specific slide of the material")


class HomeworkAssistantRequest(BaseModel):
    task_id: str | None = None
    question: str = Field(..., min_length=1)
    upload_texts: list[str] | None = Field(
        default=None,
        description="Parsed text from user-uploaded files/images to supplement the homework context.",
    )
