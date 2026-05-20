"""List Info portal app IDs from a search page without printing cookies."""

from __future__ import annotations

import argparse
import re
import sys
from html import unescape
from pathlib import Path
from urllib.parse import quote, urljoin

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adapters.jwch_adapter import JwchAdapter
from src.env_loader import load_project_env


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect Info portal app IDs by search keyword.")
    parser.add_argument("keyword")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    load_project_env()
    adapter = JwchAdapter()
    session = adapter._build_session()
    adapter._session = session
    adapter._load_info_cookies()
    base = "https://info.tsinghua.edu.cn"
    url = base + "/f/info/portal_fg/common/yyfwsearch?searchParam=" + quote(args.keyword)
    response = session.get(url, timeout=float(adapter.config.extra.get("timeout_seconds", 20)))
    response.raise_for_status()
    text = response.text
    print(f"keyword={args.keyword}")
    print(f"status={response.status_code}")
    print(f"bytes={len(text.encode('utf-8'))}")
    apps = discover_apps(text)
    print(f"app_count={len(apps)}")
    for name, app_id in apps[:30]:
        print(f"{name} | {app_id}")
    if not apps:
        print(f"yyfwid_occurrences={len(re.findall(r'yyfwid', text, flags=re.IGNORECASE))}")
        print(f"online_redirect_occurrences={len(re.findall(r'onlineAppRedirect', text, flags=re.IGNORECASE))}")
    return 0


def discover_apps(text: str) -> list[tuple[str, str]]:
    apps: list[tuple[str, str]] = []
    pattern = re.compile(r"yyfwid=([A-Fa-f0-9]{32})", flags=re.IGNORECASE)
    for match in pattern.finditer(text):
        app_id = match.group(1)
        window = text[max(0, match.start() - 300) : match.end() + 300]
        name = infer_name(window)
        item = (name, app_id)
        if item not in apps:
            apps.append(item)
    jsonish = re.compile(r'"yyfwid"\s*:\s*"([A-Fa-f0-9]{32})"', flags=re.IGNORECASE)
    for match in jsonish.finditer(text):
        app_id = match.group(1)
        window = text[max(0, match.start() - 400) : match.end() + 400]
        name = infer_name(window)
        item = (name, app_id)
        if item not in apps:
            apps.append(item)
    return apps


def infer_name(window: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", unescape(window))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    candidates = re.findall(r"[\u4e00-\u9fffA-Za-z0-9（）()_\-]{2,40}", cleaned)
    for candidate in candidates:
        if "yyfwid" not in candidate and "onlineAppRedirect" not in candidate:
            return candidate
    return "(unknown)"


if __name__ == "__main__":
    raise SystemExit(main())
