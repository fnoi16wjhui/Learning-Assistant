"""Pydantic models for C-module knowledge base internals."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field


LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


class SearchMode(StrEnum):
    """Supported retrieval modes."""

    KEYWORD = "keyword"
    VECTOR = "vector"
    HYBRID = "hybrid"


class KnowledgeModel(BaseModel):
    """Strict JSON-friendly base for C-module outputs."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class KnowledgeChunk(KnowledgeModel):
    """A normalized knowledge chunk stored in the knowledge base."""

    chunk_id: str = Field(..., min_length=1, description="Stable unique identifier.")
    source_file: str = Field(..., description="Original local file path.")
    file_hash: str = Field(..., description="SHA-256 hash of the source file.")
    material_type: str = Field(..., description="Normalized source file type.")
    course_name: str = Field(default="Unknown Course")
    title: str = Field(..., description="File or document title.")
    page: int | None = None
    slide: int | None = None
    section_title: str | None = None
    chunk_index: int = Field(..., ge=0)
    text: str = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class SearchResult(KnowledgeModel):
    """Single retrieval result returned to the caller."""

    chunk_id: str
    title: str
    course_name: str
    text: str
    score: float = Field(..., ge=0.0, le=1.0)
    source: str = Field(default="", description="Source file path or identifier.")


class KnowledgeStatus(KnowledgeModel):
    """Knowledge base health and statistics."""

    status: str = "missing"
    indexed_chunks: int = 0
    index_types: list[str] = Field(default_factory=lambda: ["keyword"])
    filters: list[str] = Field(default_factory=lambda: ["course_name", "material_type"])
    index_dir: str | None = None
    message: str = ""
    source_module: str = "C"
