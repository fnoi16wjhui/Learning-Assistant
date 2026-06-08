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
    upload_texts: list[str] = Field(default_factory=list)


class LocalSettingsRequest(BaseModel):
    learn_username: str | None = None
    learn_password: str | None = None
    mail_username: str | None = None
    mail_password: str | None = None
    jwch_username: str | None = None
    jwch_password: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_timeout: int | None = None
    semester_start: str | None = None


class MaterialExportRequest(BaseModel):
    source: Literal["learn", "mail", "both"] = "learn"
    limit: int = Field(default=120, ge=1, le=500)
    include_homework: bool = False
    include_notices: bool = True
    prefer_course_files: bool = True


class MaterialParseRequest(BaseModel):
    incremental: bool = True
    limit: int | None = Field(default=None, ge=1, le=500)


class MaterialPipelineRequest(BaseModel):
    export_limit: int = Field(default=200, ge=1, le=500)
    include_homework: bool = False
    include_notices: bool = True
    prefer_course_files: bool = True
    parse_limit: int | None = Field(default=None, ge=1, le=500)
    force_rebuild: bool = True
    pdf_ocr: bool = False


class KnowledgeRebuildRequest(BaseModel):
    force: bool = True
