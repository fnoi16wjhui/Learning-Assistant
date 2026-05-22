"""Shared extractor primitives for local course material files."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from src.materials.models import MaterialSegment, MaterialType


class MaterialParseError(RuntimeError):
    """Raised when a local material file cannot be parsed."""


class MaterialExtractor(ABC):
    """Local file text extraction boundary."""

    material_type: MaterialType
    suffixes: set[str]

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in self.suffixes

    @abstractmethod
    def extract(self, path: Path) -> list[MaterialSegment]:
        """Extract text segments without chunking or indexing."""
