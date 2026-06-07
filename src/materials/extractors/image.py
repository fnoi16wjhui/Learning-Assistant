"""Image OCR material extraction."""

from __future__ import annotations

import os
from typing import Any
from pathlib import Path

from src.materials.extractors.base import MaterialExtractor, MaterialParseError
from src.materials.models import MaterialSegment, MaterialType


DEFAULT_OCR_LANG = "chi_sim+eng"


class ImageOcrExtractor(MaterialExtractor):
    material_type = MaterialType.IMAGE
    suffixes = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}

    def extract(self, path: Path) -> list[MaterialSegment]:
        try:
            from PIL import Image
        except ImportError as exc:
            raise MaterialParseError(
                "Image OCR requires pillow, pytesseract, and a local Tesseract binary."
            ) from exc

        try:
            with Image.open(path) as image:
                text = ocr_pil_image(image)
        except Exception as exc:
            raise MaterialParseError(f"image OCR failed: path={path}") from exc
        return [
            MaterialSegment(
                text=text,
                metadata={
                    "extraction_method": "ocr",
                    "ocr_backend": "tesseract",
                    "ocr_language": ocr_language(),
                },
            )
        ] if text.strip() else []


def ocr_language() -> str:
    return os.getenv("MATERIAL_OCR_LANG", DEFAULT_OCR_LANG)


def ocr_pil_image(image: Any, *, lang: str | None = None) -> str:
    """Extract text from an in-memory PIL image through local Tesseract."""

    pytesseract = ensure_ocr_available()
    requested_lang = lang or ocr_language()
    available = available_ocr_languages(pytesseract)
    effective_lang = choose_ocr_language(requested_lang, available)
    try:
        return pytesseract.image_to_string(image, lang=effective_lang)
    except Exception as exc:
        raise MaterialParseError(
            f"OCR failed with language '{effective_lang}'. "
            f"Available languages: {', '.join(sorted(available)) or 'none'}."
        ) from exc


def ensure_ocr_available() -> Any:
    """Return pytesseract when the Python package and local binary are both available."""

    try:
        import pytesseract
    except ImportError as exc:
        raise MaterialParseError(
            "OCR fallback requires pytesseract and a local Tesseract binary."
        ) from exc
    configure_windows_tesseract(pytesseract)
    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:
        raise MaterialParseError(
            "OCR fallback requires a local Tesseract binary available on PATH."
        ) from exc
    return pytesseract


def configure_windows_tesseract(pytesseract: Any) -> None:
    """Use common Windows install/user tessdata locations when PATH is stale."""

    explicit_cmd = os.getenv("TESSERACT_CMD")
    if explicit_cmd:
        pytesseract.pytesseract.tesseract_cmd = explicit_cmd
    elif os.name != "nt":
        return
    if not os.getenv("TESSDATA_PREFIX"):
        local_appdata = os.getenv("LOCALAPPDATA")
        if local_appdata:
            user_tessdata = Path(local_appdata) / "LearningAssistantTools" / "tessdata"
            if user_tessdata.exists():
                os.environ["TESSDATA_PREFIX"] = str(user_tessdata)
    current_cmd = getattr(pytesseract.pytesseract, "tesseract_cmd", "")
    if current_cmd and Path(current_cmd).exists():
        return
    for candidate in (
        Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
        Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
        Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
    ):
        if candidate.exists():
            pytesseract.pytesseract.tesseract_cmd = str(candidate)
            return


def available_ocr_languages(pytesseract: Any | None = None) -> set[str]:
    """Return installed Tesseract language IDs, excluding orientation/script data."""

    if pytesseract is None:
        pytesseract = ensure_ocr_available()
    try:
        languages = pytesseract.get_languages(config="")
    except Exception as exc:
        raise MaterialParseError(f"Unable to list Tesseract OCR languages: {exc}") from exc
    return {language for language in languages if language and language != "osd"}


def choose_ocr_language(requested: str, available: set[str]) -> str:
    """Choose the best OCR language string supported by the local Tesseract install."""

    requested_parts = [part for part in requested.split("+") if part]
    matched = [part for part in requested_parts if part in available]
    if matched:
        return "+".join(matched)
    if "eng" in available:
        return "eng"
    if available:
        return sorted(available)[0]
    raise MaterialParseError(
        "No Tesseract OCR language data is installed. "
        "Install chi_sim.traineddata and eng.traineddata, or set TESSDATA_PREFIX."
    )
