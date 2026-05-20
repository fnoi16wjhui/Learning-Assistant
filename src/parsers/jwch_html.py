"""Pure JWCH HTML parsing helpers."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, time, timedelta
from html import unescape
from html.parser import HTMLParser
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from src.models import ScheduleItem


LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
ANCHOR_MONDAY = date(1970, 1, 5)
EXAM_SLOT_TIMES = {
    "上午": (time(9, 0), time(11, 0)),
    "下午": (time(14, 0), time(16, 0)),
    "晚上": (time(19, 0), time(21, 0)),
}
CLASS_PERIOD_TIMES = {
    1: (time(8, 0), time(8, 45)),
    2: (time(8, 50), time(9, 35)),
    3: (time(9, 50), time(10, 35)),
    4: (time(10, 40), time(11, 25)),
    5: (time(13, 30), time(14, 15)),
    6: (time(14, 20), time(15, 5)),
    7: (time(15, 20), time(16, 5)),
    8: (time(16, 10), time(16, 55)),
    9: (time(17, 5), time(17, 50)),
    10: (time(19, 20), time(20, 5)),
    11: (time(20, 10), time(20, 55)),
    12: (time(21, 0), time(21, 45)),
}
WEEKDAY_OFFSETS = {
    "星期一": 0,
    "星期二": 1,
    "星期三": 2,
    "星期四": 3,
    "星期五": 4,
    "星期六": 5,
    "星期日": 6,
    "星期天": 6,
}


class _TableParser(HTMLParser):
    """Collect readable text from HTML tables."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._current_table: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered == "table":
            self._current_table = []
        elif lowered == "tr" and self._current_table is not None:
            self._current_row = []
        elif lowered in {"td", "th"} and self._current_row is not None:
            self._current_cell = []
        elif lowered == "br" and self._current_cell is not None:
            self._current_cell.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"td", "th"} and self._current_cell is not None and self._current_row is not None:
            self._current_row.append(normalize_text(" ".join(self._current_cell)))
            self._current_cell = None
        elif lowered == "tr" and self._current_table is not None and self._current_row is not None:
            if any(cell for cell in self._current_row):
                self._current_table.append(self._current_row)
            self._current_row = None
        elif lowered == "table" and self._current_table is not None:
            self.tables.append(self._current_table)
            self._current_table = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)


class JwchHtmlParser:
    """Parse raw JWCH exam and schedule HTML into schedule records."""

    source = "jwch"

    def parse(
        self,
        raw: str | bytes,
        metadata: Mapping[str, Any] | None = None,
    ) -> list[ScheduleItem]:
        """Parse one JWCH raw HTML payload without network or file IO."""

        meta = dict(metadata or {})
        text = raw.decode(meta.get("encoding", "utf-8"), errors="replace") if isinstance(raw, bytes) else raw
        tables = extract_tables(text)
        target_hint = " ".join(
            str(value)
            for value in (meta.get("raw_id"), meta.get("url"), meta.get("endpoint"), meta.get("target"))
            if value
        )
        if "bks_ksSearch" in target_hint or has_exam_table(tables):
            return parse_exam_tables(tables, text, meta)
        return parse_js_schedule_records(text, meta) + parse_schedule_tables(tables, meta)


def extract_tables(html: str) -> list[list[list[str]]]:
    parser = _TableParser()
    parser.feed(html)
    return parser.tables


def has_exam_table(tables: list[list[list[str]]]) -> bool:
    return any(table and normalize_header_row(table[0]) == exam_headers() for table in tables)


def parse_exam_tables(
    tables: list[list[list[str]]],
    html: str,
    metadata: Mapping[str, Any],
) -> list[ScheduleItem]:
    year = int(metadata.get("exam_year") or infer_exam_year(html))
    records: list[ScheduleItem] = []
    for table in tables:
        if not table or normalize_header_row(table[0]) != exam_headers():
            continue
        headers = normalize_header_row(table[0])
        for row_index, row in enumerate(table[1:], start=1):
            item = parse_exam_row(headers, row, year, row_index, metadata)
            if item is not None:
                records.append(item)
    return records


def parse_exam_row(
    headers: list[str],
    row: list[str],
    year: int,
    row_index: int,
    metadata: Mapping[str, Any],
) -> ScheduleItem | None:
    if len(row) < len(headers):
        return None
    values = dict(zip(headers, row, strict=False))
    course_name = values.get("课程名", "").strip()
    date_text = values.get("考试日期", "").strip()
    if not course_name or not date_text:
        return None

    starts_at, ends_at = parse_exam_datetime(year, date_text)
    location = values.get("考场") or None
    teacher = values.get("教师") or None
    content = normalize_text(
        "\n".join(
            f"{label}: {values.get(label, '')}"
            for label in headers
            if values.get(label, "")
        )
    )
    raw_id = stable_raw_id(
        "exam",
        metadata.get("raw_id"),
        values.get("课程号"),
        values.get("课序号"),
        course_name,
        date_text,
        location,
        row_index,
    )
    return ScheduleItem(
        source="jwch",
        schedule_type="exam",
        raw_id=raw_id,
        course_name=course_name,
        title=f"{course_name} 考试安排",
        content=content,
        starts_at=starts_at,
        ends_at=ends_at,
        location=location,
        teacher=teacher,
    )


def parse_schedule_tables(tables: list[list[list[str]]], metadata: Mapping[str, Any]) -> list[ScheduleItem]:
    records: list[ScheduleItem] = []
    anchor = parse_anchor_monday(metadata.get("anchor_monday") or metadata.get("week_start_date"))
    for table in tables:
        if not table or not looks_like_week_grid(table):
            continue
        weekdays = table[0][1:]
        for row in table[1:]:
            if not row:
                continue
            period = parse_period_number(row[0])
            if period is None:
                continue
            starts_at, ends_at = class_period_datetimes(anchor, period)
            for day_index, cell in enumerate(row[1:], start=0):
                if not cell.strip():
                    continue
                day_label = weekdays[day_index] if day_index < len(weekdays) else ""
                day_offset = WEEKDAY_OFFSETS.get(day_label, day_index)
                record_start = starts_at + timedelta(days=day_offset)
                record_end = ends_at + timedelta(days=day_offset)
                course_name = infer_schedule_course_name(cell)
                records.append(
                    ScheduleItem(
                        source="jwch",
                        schedule_type="class",
                        raw_id=stable_raw_id("schedule", metadata.get("raw_id"), day_label, period, cell),
                        course_name=course_name,
                        title=f"{course_name} 第{period}节",
                        content=cell,
                        starts_at=record_start,
                        ends_at=record_end,
                        location=infer_location(cell),
                    )
                )
    return records


def parse_js_schedule_records(html: str, metadata: Mapping[str, Any]) -> list[ScheduleItem]:
    """Parse course cells built by JWCH inline JavaScript."""

    records: list[ScheduleItem] = []
    anchor = parse_anchor_monday(metadata.get("anchor_monday") or metadata.get("week_start_date"))
    pattern = re.compile(
        r'strHTML\s*=\s*"";(?P<block>.*?)document\.getElementById\([\'"]a(?P<period>\d+)_(?P<day>\d+)[\'"]\)',
        flags=re.DOTALL,
    )
    for match in pattern.finditer(html):
        block = match.group("block")
        period = int(match.group("period"))
        day = int(match.group("day"))
        course_name = extract_js_course_name(block)
        if not course_name:
            continue
        info_parts = [clean_js_text(value) for value in re.findall(r'strHTML1\s*\+=\s*"；([^"]*)"', block)]
        info_parts = [value for value in info_parts if value]
        starts_at, ends_at = class_period_datetimes(anchor, period)
        day_offset = max(day - 1, 0)
        starts_at = starts_at + timedelta(days=day_offset)
        ends_at = ends_at + timedelta(days=day_offset)
        location = info_parts[-1] if info_parts else infer_location(" ".join(info_parts))
        teacher = info_parts[0] if info_parts else None
        content = normalize_text("\n".join([course_name, *info_parts]))
        course_id = extract_js_course_id(block)
        records.append(
            ScheduleItem(
                source="jwch",
                schedule_type="class",
                raw_id=stable_raw_id("schedule", metadata.get("raw_id"), course_id, day, period, content),
                course_name=course_name,
                title=f"{course_name} 第{period}节",
                content=content,
                starts_at=starts_at,
                ends_at=ends_at,
                location=location,
                teacher=teacher,
            )
        )
    return records


def extract_js_course_name(block: str) -> str | None:
    match = re.search(r'onmouseout=[^>]*>([^"<]+)"', block)
    if not match:
        match = re.search(r"strHTML\s*\+=\s*\"([^\"]+)\";", block)
    if not match:
        return None
    value = clean_js_text(match.group(1))
    return value or None


def extract_js_course_id(block: str) -> str | None:
    match = re.search(r"p_id=([^'\"&<>]+)", block)
    return match.group(1).strip() if match else None


def clean_js_text(value: str) -> str:
    value = value.replace(r"\/", "/").replace(r"\'", "'").replace(r'\"', '"')
    return normalize_text(unescape(value))


def normalize_header_row(row: list[str]) -> list[str]:
    return [normalize_text(cell) for cell in row]


def exam_headers() -> list[str]:
    return ["开课系", "课程号", "课序号", "课程名", "课程分类", "教师", "人数", "考试日期", "考场"]


def infer_exam_year(html: str) -> int:
    match = re.search(r"(\d{4})-(\d{4})学年\s*(春|秋)", html)
    if not match:
        return 1970
    start_year = int(match.group(1))
    end_year = int(match.group(2))
    term = match.group(3)
    return end_year if term == "春" else start_year


def parse_exam_datetime(year: int, value: str) -> tuple[datetime, datetime | None]:
    match = re.search(r"(\d{1,2})\.(\d{1,2})", value)
    if not match:
        fallback = datetime(year, 1, 1, tzinfo=LOCAL_TIMEZONE)
        return fallback, None
    month = int(match.group(1))
    day = int(match.group(2))
    slot_match = re.search(r"(上午|下午|晚上)", value)
    slot = slot_match.group(1) if slot_match else ""
    start_time, end_time = EXAM_SLOT_TIMES.get(slot, (time(0, 0), time(23, 59)))
    start = datetime.combine(date(year, month, day), start_time, LOCAL_TIMEZONE)
    end = datetime.combine(date(year, month, day), end_time, LOCAL_TIMEZONE)
    return start, end


def looks_like_week_grid(table: list[list[str]]) -> bool:
    if not table or len(table[0]) < 2:
        return False
    header_text = " ".join(table[0])
    return "星期一" in header_text and "星期日" in header_text


def parse_period_number(value: str) -> int | None:
    match = re.search(r"第\s*(\d+)\s*节", value)
    return int(match.group(1)) if match else None


def parse_anchor_monday(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and value.strip():
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d").date()
        except ValueError:
            return ANCHOR_MONDAY
    return ANCHOR_MONDAY


def class_period_datetimes(anchor: date, period: int) -> tuple[datetime, datetime]:
    start_time, end_time = CLASS_PERIOD_TIMES.get(period, (time(0, 0), time(0, 45)))
    return (
        datetime.combine(anchor, start_time, LOCAL_TIMEZONE),
        datetime.combine(anchor, end_time, LOCAL_TIMEZONE),
    )


def infer_schedule_course_name(value: str) -> str:
    line = next((part.strip() for part in re.split(r"[\n;；]", value) if part.strip()), "")
    if not line:
        return "Unknown Course"
    return re.split(r"\s{2,}|\(|（|@", line, maxsplit=1)[0].strip() or line


def infer_location(value: str) -> str | None:
    match = re.search(r"(?:地点|教室)[:：]\s*([^\s;；]+)", value)
    if match:
        return match.group(1).strip()
    bracket = re.search(r"[（(]([^（）()]*?(?:楼|馆|教|室|厅|场|房|区)[^（）()]*)[）)]", value)
    return bracket.group(1).strip() if bracket else None


def normalize_text(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = re.sub(r"\r\n?", "\n", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n", value)
    return value.strip()


def stable_raw_id(prefix: str, *parts: Any) -> str:
    normalized = "\n".join(str(part or "") for part in parts)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"jwch_{prefix}_{digest[:24]}"
