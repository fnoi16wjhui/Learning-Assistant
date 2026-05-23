"""Backend settings for the E-module integration API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class BackendSettings:
    """Paths used by adapters to consume existing module outputs."""

    project_root: Path = PROJECT_ROOT
    collector_jsonl: Path = PROJECT_ROOT / "storage" / "collector.jsonl"
    demo_collector_jsonl: Path = PROJECT_ROOT / "storage" / "demo_collector.jsonl"
    learn_jsonl: Path = PROJECT_ROOT / "storage" / "learn.jsonl"
    mail_jsonl: Path = PROJECT_ROOT / "storage" / "mail.jsonl"
    jwch_jsonl: Path = PROJECT_ROOT / "storage" / "jwch.jsonl"
    material_chunks_jsonl: Path = PROJECT_ROOT / "storage" / "material_chunks.jsonl"
    demo_material_chunks_jsonl: Path = PROJECT_ROOT / "storage" / "demo_material_chunks.jsonl"

    @classmethod
    def from_env(cls) -> "BackendSettings":
        root = Path(os.getenv("LEARNING_ASSISTANT_ROOT", PROJECT_ROOT)).resolve()
        return cls(
            project_root=root,
            collector_jsonl=Path(os.getenv("COLLECTOR_JSONL", root / "storage" / "collector.jsonl")),
            demo_collector_jsonl=Path(os.getenv("DEMO_COLLECTOR_JSONL", root / "storage" / "demo_collector.jsonl")),
            learn_jsonl=Path(os.getenv("LEARN_JSONL", root / "storage" / "learn.jsonl")),
            mail_jsonl=Path(os.getenv("MAIL_JSONL", root / "storage" / "mail.jsonl")),
            jwch_jsonl=Path(os.getenv("JWCH_JSONL", root / "storage" / "jwch.jsonl")),
            material_chunks_jsonl=Path(os.getenv("MATERIAL_CHUNKS_JSONL", root / "storage" / "material_chunks.jsonl")),
            demo_material_chunks_jsonl=Path(
                os.getenv("DEMO_MATERIAL_CHUNKS_JSONL", root / "storage" / "demo_material_chunks.jsonl")
            ),
        )


settings = BackendSettings.from_env()
