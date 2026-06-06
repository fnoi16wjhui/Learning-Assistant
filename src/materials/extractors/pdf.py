"""PDF material extraction."""

from __future__ import annotations

import io
import os
from pathlib import Path

from PIL import Image

from src.materials.extractors.base import MaterialExtractor, MaterialParseError
from src.materials.extractors.image import ensure_ocr_available, ocr_language, ocr_pil_image
from src.materials.models import MaterialSegment, MaterialType


PDF_TEXT_MIN_CHARS = 30
PDF_OCR_DPI = 200


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
        pages_requiring_ocr: list[int] = []
        min_chars = int(os.getenv("MATERIAL_PDF_TEXT_MIN_CHARS", str(PDF_TEXT_MIN_CHARS)))
        for index, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:
                raise MaterialParseError(f"PDF page extraction failed: path={path} page={index}") from exc
            # Attempt to extract and OCR embedded images within the page
            try:
                for img_obj in page.images:
                    try:
                        pil_image = Image.open(io.BytesIO(img_obj.data))
                        ocr_text = ocr_pil_image(pil_image)
                        if ocr_text.strip():
                            text += "\n" + ocr_text.strip()
                    except Exception:
                        pass
            except Exception:
                pass
            if len("".join(text.split())) >= min_chars:
                segments.append(
                    MaterialSegment(
                        text=text,
                        page=index,
                        metadata={"extraction_method": "pypdf"},
                    )
                )
            else:
                pages_requiring_ocr.append(index)
                if text.strip():
                    segments.append(
                        MaterialSegment(
                            text=text,
                            page=index,
                            metadata={
                                "extraction_method": "pypdf_low_text",
                                "ocr_candidate": True,
                            },
                        )
                    )

        if pages_requiring_ocr:
            segments.extend(extract_pdf_pages_with_ocr(path, pages_requiring_ocr, has_text_segments=bool(segments)))
        return segments


def extract_pdf_pages_with_ocr(
    path: Path,
    pages: list[int],
    *,
    has_text_segments: bool,
) -> list[MaterialSegment]:
    """Render selected low-text PDF pages and OCR them when local dependencies exist."""

    if os.getenv("MATERIAL_PDF_OCR", "1").lower() in {"0", "false", "no"}:
        return []
    try:
        from pdf2image import convert_from_path
    except ImportError as exc:
        if has_text_segments:
            return []
        return ocr_warning_segments(pages, f"pdf_ocr_unavailable: pdf2image is not installed: {exc}")
    try:
        ensure_ocr_available()
    except MaterialParseError as exc:
        if has_text_segments:
            return []
        return ocr_warning_segments(pages, f"pdf_ocr_unavailable: {exc}")

    dpi = int(os.getenv("MATERIAL_PDF_OCR_DPI", str(PDF_OCR_DPI)))
    ocr_segments: list[MaterialSegment] = []
    last_error: Exception | None = None
    failed_pages: list[int] = []
    for page_number in pages:
        try:
            images = convert_from_path(
                str(path),
                dpi=dpi,
                first_page=page_number,
                last_page=page_number,
                fmt="png",
                thread_count=1,
            )
            if not images:
                continue
            text = ocr_pil_image(images[0])
        except Exception as exc:
            last_error = exc
            failed_pages.append(page_number)
            continue
        if text.strip():
            ocr_segments.append(
                MaterialSegment(
                    text=text,
                    page=page_number,
                    metadata={
                        "extraction_method": "pdf_ocr",
                        "ocr_backend": "tesseract",
                        "ocr_language": ocr_language(),
                        "ocr_dpi": dpi,
                    },
                )
            )

    if failed_pages and not has_text_segments:
        ocr_segments.extend(
            ocr_warning_segments(
                failed_pages,
                f"pdf_ocr_failed: PDF contains no selectable text and OCR fallback failed: {last_error}",
            )
        )
    return ocr_segments


def ocr_warning_segments(pages: list[int], warning: str) -> list[MaterialSegment]:
    return [
        MaterialSegment(
            text="",
            page=page,
            metadata={
                "extraction_method": "pdf_ocr_failed",
                "ocr_candidate": True,
                "warning": warning,
            },
        )
        for page in pages
    ]
