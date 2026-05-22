"""Pydantic contracts for standardized course material chunks."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field


LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def local_now() -> datetime:
    return datetime.now(LOCAL_TIMEZONE)


class MaterialType(StrEnum):
    """Supported material file categories."""

    TEXT = "text"
    MARKDOWN = "markdown"
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    UNKNOWN = "unknown"


class MaterialModel(BaseModel):
    """Strict JSON-friendly base for B-module outputs."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class MaterialSegment(MaterialModel):
    """Extractor-level text span before final chunking."""

    text: str = Field(..., description="Cleanable extracted text.")
    page: int | None = Field(default=None, description="One-based page number when available.")
    slide: int | None = Field(default=None, description="One-based slide number when available.")
    section_title: str | None = Field(default=None, description="Nearest heading or section hint.")
    metadata: dict[str, Any] = Field(default_factory=dict)


class MaterialChunk(MaterialModel):
    """Standardized chunk passed from B to C for indexing."""

    source_file: str = Field(..., description="Original local file path.")
    file_hash: str = Field(..., description="SHA-256 hash of the file bytes.")
    material_type: MaterialType = Field(..., description="Normalized source file type.")
    course_name: str = Field(default="Unknown Course")
    title: str = Field(..., description="File or document title.")
    page: int | None = None
    slide: int | None = None
    section_title: str | None = None
    chunk_index: int = Field(..., ge=0)
    text: str = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=local_now)


def material_type_for_path(path: str | Path) -> MaterialType:
    """Infer material type from file suffix."""

    suffix = Path(path).suffix.lower()
    if suffix in {".txt"}:
        return MaterialType.TEXT
    if suffix in {".md", ".markdown"}:
        return MaterialType.MARKDOWN
    if suffix == ".pdf":
        return MaterialType.PDF
    if suffix == ".docx":
        return MaterialType.DOCX
    if suffix == ".pptx":
        return MaterialType.PPTX
    if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}:
        return MaterialType.IMAGE
    if suffix in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}:
        return MaterialType.AUDIO
    if suffix in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
        return MaterialType.VIDEO
    return MaterialType.UNKNOWN
