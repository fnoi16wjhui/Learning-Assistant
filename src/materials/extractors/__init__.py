"""Extractor registry for course material files."""

from src.materials.extractors.base import MaterialParseError
from src.materials.extractors.registry import extractor_for_path, supported_suffixes

__all__ = ["MaterialParseError", "extractor_for_path", "supported_suffixes"]
