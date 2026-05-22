"""Plain text and Markdown material extraction."""

from __future__ import annotations

from pathlib import Path

from src.materials.extractors.base import MaterialExtractor, MaterialParseError
from src.materials.models import MaterialSegment, MaterialType


class TextExtractor(MaterialExtractor):
    material_type = MaterialType.TEXT
    suffixes = {".txt"}

    def extract(self, path: Path) -> list[MaterialSegment]:
        return [MaterialSegment(text=read_text_with_fallbacks(path))]


class MarkdownExtractor(MaterialExtractor):
    material_type = MaterialType.MARKDOWN
    suffixes = {".md", ".markdown"}

    def extract(self, path: Path) -> list[MaterialSegment]:
        return [MaterialSegment(text=read_text_with_fallbacks(path))]


def read_text_with_fallbacks(path: Path) -> str:
    """Read course text files using common Windows and UTF encodings."""

    last_error: UnicodeDecodeError | None = None
    for encoding in ("utf-8", "utf-8-sig", "gbk", "cp936"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error is not None:
        raise MaterialParseError(f"text decode failed: path={path}") from last_error
    return path.read_text(encoding="utf-8", errors="replace")
