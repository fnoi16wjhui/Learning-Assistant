"""Office document material extraction."""

from __future__ import annotations

from pathlib import Path

from src.materials.extractors.base import MaterialExtractor, MaterialParseError
from src.materials.models import MaterialSegment, MaterialType


class DocxExtractor(MaterialExtractor):
    material_type = MaterialType.DOCX
    suffixes = {".docx"}

    def extract(self, path: Path) -> list[MaterialSegment]:
        try:
            from docx import Document
        except ImportError as exc:
            raise MaterialParseError("DOCX parsing requires python-docx. Run: pip install python-docx") from exc

        try:
            document = Document(str(path))
        except Exception as exc:
            raise MaterialParseError(f"DOCX open failed: path={path}") from exc

        parts: list[str] = []
        for paragraph in document.paragraphs:
            if paragraph.text.strip():
                parts.append(paragraph.text)
        for table in document.tables:
            for row in table.rows:
                values = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if values:
                    parts.append(" | ".join(values))
        return [MaterialSegment(text="\n".join(parts))] if parts else []


class PptxExtractor(MaterialExtractor):
    material_type = MaterialType.PPTX
    suffixes = {".pptx"}

    def extract(self, path: Path) -> list[MaterialSegment]:
        try:
            from pptx import Presentation
        except ImportError as exc:
            raise MaterialParseError("PPTX parsing requires python-pptx. Run: pip install python-pptx") from exc

        try:
            presentation = Presentation(str(path))
        except Exception as exc:
            raise MaterialParseError(f"PPTX open failed: path={path}") from exc

        segments: list[MaterialSegment] = []
        for index, slide in enumerate(presentation.slides, start=1):
            texts: list[str] = []
            for shape in slide.shapes:
                text = getattr(shape, "text", "")
                if isinstance(text, str) and text.strip():
                    texts.append(text)
                if getattr(shape, "has_table", False):
                    for row in shape.table.rows:
                        values = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                        if values:
                            texts.append(" | ".join(values))
            if texts:
                segments.append(MaterialSegment(text="\n".join(texts), slide=index))
        return segments
