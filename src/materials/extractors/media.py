"""Audio and video ASR material extraction."""

from __future__ import annotations

import os
from pathlib import Path

from src.materials.extractors.base import MaterialExtractor, MaterialParseError
from src.materials.models import MaterialSegment, MaterialType


class AudioAsrExtractor(MaterialExtractor):
    material_type = MaterialType.AUDIO
    suffixes = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}

    def extract(self, path: Path) -> list[MaterialSegment]:
        return transcribe_with_faster_whisper(path)


class VideoAsrExtractor(MaterialExtractor):
    material_type = MaterialType.VIDEO
    suffixes = {".mp4", ".mov", ".avi", ".mkv", ".webm"}

    def extract(self, path: Path) -> list[MaterialSegment]:
        return transcribe_with_faster_whisper(path)


def transcribe_with_faster_whisper(path: Path) -> list[MaterialSegment]:
    """Transcribe local media through an optional faster-whisper backend."""

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise MaterialParseError(
            "Audio/video ASR requires optional faster-whisper. "
            "Install it separately and ensure FFmpeg is available."
        ) from exc

    model_name = os.getenv("MATERIAL_ASR_MODEL", "base")
    device = os.getenv("MATERIAL_ASR_DEVICE", "cpu")
    compute_type = os.getenv("MATERIAL_ASR_COMPUTE_TYPE", "int8")
    language = os.getenv("MATERIAL_ASR_LANGUAGE") or None
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
        segments, _ = model.transcribe(str(path), language=language)
    except Exception as exc:
        raise MaterialParseError(f"audio/video ASR failed: path={path}") from exc

    results: list[MaterialSegment] = []
    for index, segment in enumerate(segments):
        text = str(getattr(segment, "text", "") or "").strip()
        if not text:
            continue
        results.append(
            MaterialSegment(
                text=text,
                metadata={
                    "media_segment_index": index,
                    "start_seconds": getattr(segment, "start", None),
                    "end_seconds": getattr(segment, "end", None),
                    "asr_backend": "faster-whisper",
                    "asr_model": model_name,
                },
            )
        )
    return results
