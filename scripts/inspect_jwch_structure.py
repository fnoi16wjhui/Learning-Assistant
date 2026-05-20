"""Print redacted JWCH table structure for parser development."""

from __future__ import annotations

import argparse
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adapters.jwch_adapter import JwchAdapter
from src.env_loader import load_project_env


class TableShapeParser(HTMLParser):
    """Collect table rows without preserving links, cookies, or scripts."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._current_table: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None
        self._cell_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "table":
            self._current_table = []
        elif tag.lower() == "tr" and self._current_table is not None:
            self._current_row = []
        elif tag.lower() in {"td", "th"} and self._current_row is not None:
            self._current_cell = []
            self._cell_depth += 1
        elif tag.lower() == "br" and self._current_cell is not None:
            self._current_cell.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"td", "th"} and self._current_cell is not None and self._current_row is not None:
            self._current_row.append(normalize_text(" ".join(self._current_cell)))
            self._current_cell = None
            self._cell_depth = max(0, self._cell_depth - 1)
        elif tag.lower() == "tr" and self._current_table is not None and self._current_row is not None:
            if any(cell for cell in self._current_row):
                self._current_table.append(self._current_row)
            self._current_row = None
        elif tag.lower() == "table" and self._current_table is not None:
            self.tables.append(self._current_table)
            self._current_table = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect JWCH table shapes without printing secrets.")
    parser.add_argument("--target", choices=("exam", "schedule"), required=True)
    parser.add_argument("--rows", type=int, default=4)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    load_project_env()
    url = target_url(args.target)
    payload = JwchAdapter().fetch_raw(url=url, raw_id=f"jwch_{args.target}")[0]
    text = payload.content if isinstance(payload.content, str) else payload.content.decode("utf-8", errors="replace")
    parser = TableShapeParser()
    parser.feed(text)
    print(f"target={args.target}")
    print(f"final_path={safe_path(str(payload.metadata.get('url') or ''))}")
    print(f"bytes={len(text.encode('utf-8'))}")
    print(f"table_count={len(parser.tables)}")
    for index, table in enumerate(parser.tables[:8], start=1):
        widths = sorted({len(row) for row in table})
        print(f"table_{index}_rows={len(table)} widths={widths[:8]}")
        for row in table[: args.rows]:
            print(" | ".join(redact_cell(cell) for cell in row[:12]))
    return 0


def target_url(target: str) -> str:
    import os

    if target == "exam":
        return os.getenv("JWCH_EXAM_URL") or "https://zhjw.cic.tsinghua.edu.cn/jxmh.do?url=/jxmh.do&m=bks_ksSearch"
    return (
        os.getenv("JWCH_SCHEDULE_URL")
        or "https://zhjw.cic.tsinghua.edu.cn/portal3rd.do?url=/portal3rd.do&m=bks_yjkbSearch"
    )


def normalize_text(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def redact_cell(value: str) -> str:
    value = re.sub(r"(?i)(ticket|token|csrf|sessionid)=\S+", r"\1=<redacted>", value)
    return value[:80] + ("..." if len(value) > 80 else "")


def safe_path(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return f"{parsed.hostname}{parsed.path}"


if __name__ == "__main__":
    raise SystemExit(main())
