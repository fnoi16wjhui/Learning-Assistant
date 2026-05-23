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


class HomeworkAssistantRequest(BaseModel):
    task_id: str | None = None
    question: str = Field(..., min_length=1)
