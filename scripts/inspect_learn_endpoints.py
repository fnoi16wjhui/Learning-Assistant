"""Discover Learn page endpoint hints without printing credentials or cookies."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adapters.learn_adapter import LearnAdapter
from src.env_loader import load_project_env
from src.parsers.learn_html import extract_title


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect Learn endpoint hints from authenticated pages.")
    parser.add_argument("--endpoint", default="/f/wlxt/index/course/student/")
    parser.add_argument("--max", type=int, default=80)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    load_project_env()
    adapter = LearnAdapter()
    payload = adapter.fetch_raw(endpoint=args.endpoint, raw_id="learn_inspect")[0]
    text = payload.content if isinstance(payload.content, str) else payload.content.decode("utf-8", errors="replace")
    print(f"final_path={safe_path(str(payload.metadata.get('url') or ''))}")
    print(f"title={extract_title(text) or '(no title)'}")
    print(f"bytes={len(text.encode('utf-8'))}")
    print("scripts:")
    for item in discover_script_paths(text, str(payload.metadata.get("url") or ""), args.max):
        print(item)
    print("endpoint_hints:")
    for item in discover_endpoint_hints(text, str(payload.metadata.get("url") or ""), args.max):
        print(item)
    return 0


def discover_script_paths(html: str, current_url: str, limit: int) -> list[str]:
    values: list[str] = []
    for src in re.findall(r'<script[^>]+src=["\']([^"\']+)', html, flags=re.IGNORECASE):
        path = safe_path(urljoin(current_url, src))
        if path not in values:
            values.append(path)
    return values[:limit]


def discover_endpoint_hints(html: str, current_url: str, limit: int) -> list[str]:
    candidates = re.findall(r'["\']([^"\']*(?:/b/|/f/|/api/|/wlxt/|kczy|zy|gg|wj|tl)[^"\']*)["\']', html)
    values: list[str] = []
    for candidate in candidates:
        if any(marker in candidate.lower() for marker in ("password", "token", "ticket", "cookie")):
            continue
        path = safe_path(urljoin(current_url, candidate))
        if path not in values:
            values.append(path)
    return values[:limit]


def safe_path(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.hostname:
        return parsed.path
    return f"{parsed.hostname}{parsed.path}"


if __name__ == "__main__":
    raise SystemExit(main())
