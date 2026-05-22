"""Extractor lookup for supported local material formats."""

from __future__ import annotations

from pathlib import Path

from src.materials.extractors.base import MaterialExtractor, MaterialParseError
from src.materials.extractors.image import ImageOcrExtractor
from src.materials.extractors.media import AudioAsrExtractor, VideoAsrExtractor
from src.materials.extractors.office import DocxExtractor, PptxExtractor
from src.materials.extractors.pdf import PdfExtractor
from src.materials.extractors.text import MarkdownExtractor, TextExtractor


EXTRACTORS: tuple[MaterialExtractor, ...] = (
    TextExtractor(),
    MarkdownExtractor(),
    PdfExtractor(),
    DocxExtractor(),
    PptxExtractor(),
    ImageOcrExtractor(),
    AudioAsrExtractor(),
    VideoAsrExtractor(),
)


def extractor_for_path(path: str | Path) -> MaterialExtractor:
    file_path = Path(path)
    for extractor in EXTRACTORS:
        if extractor.supports(file_path):
            return extractor
    raise MaterialParseError(f"unsupported material type: path={file_path}")


def supported_suffixes() -> set[str]:
    suffixes: set[str] = set()
    for extractor in EXTRACTORS:
        suffixes.update(extractor.suffixes)
    return suffixes
