"""Image OCR material extraction."""

from __future__ import annotations

from pathlib import Path

from src.materials.extractors.base import MaterialExtractor, MaterialParseError
from src.materials.models import MaterialSegment, MaterialType


class ImageOcrExtractor(MaterialExtractor):
    material_type = MaterialType.IMAGE
    suffixes = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}

    def extract(self, path: Path) -> list[MaterialSegment]:
        try:
            import pytesseract
            from PIL import Image
        except ImportError as exc:
            raise MaterialParseError(
                "Image OCR requires pillow and pytesseract, plus a local Tesseract binary."
            ) from exc

        try:
            with Image.open(path) as image:
                text = pytesseract.image_to_string(image, lang="chi_sim+eng")
        except Exception as exc:
            raise MaterialParseError(f"image OCR failed: path={path}") from exc
        return [MaterialSegment(text=text)] if text.strip() else []
