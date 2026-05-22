"""PDF material extraction."""

from __future__ import annotations

from pathlib import Path

from src.materials.extractors.base import MaterialExtractor, MaterialParseError
from src.materials.models import MaterialSegment, MaterialType


class PdfExtractor(MaterialExtractor):
    material_type = MaterialType.PDF
    suffixes = {".pdf"}

    def extract(self, path: Path) -> list[MaterialSegment]:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise MaterialParseError("PDF parsing requires pypdf. Run: pip install pypdf") from exc

        try:
            reader = PdfReader(str(path))
        except Exception as exc:
            raise MaterialParseError(f"PDF open failed: path={path}") from exc

        segments: list[MaterialSegment] = []
        for index, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:
                raise MaterialParseError(f"PDF page extraction failed: path={path} page={index}") from exc
            if text.strip():
                segments.append(MaterialSegment(text=text, page=index))
        return segments
