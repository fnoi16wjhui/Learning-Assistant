"""Pure Learn HTML and JSON parsing helpers."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from html.parser import HTMLParser
from typing import Any, Mapping
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from src.models import Attachment, CourseTask


LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


class _TextAndLinkParser(HTMLParser):
    """Extract readable text and anchor attachments from Learn HTML."""

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__()
        self.base_url = base_url
        self.text_parts: list[str] = []
        self.links: list[Attachment] = []
        self._active_href: str | None = None
        self._active_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "p", "div", "li", "tr"}:
            self.text_parts.append("\n")
        if tag == "a":
            attr_map = {key: value for key, value in attrs}
            self._active_href = attr_map.get("href")
            self._active_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._active_href:
            name = normalize_text(" ".join(self._active_text)) or self._active_href
            url = urljoin(self.base_url or "", self._active_href)
            self.links.append(Attachment(name=name, download_url=url))
            self._active_href = None
            self._active_text = []
        if tag in {"p", "div", "li", "tr"}:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)
        if self._active_href is not None:
            self._active_text.append(data)


class LearnHtmlParser:
    """Parse raw Learn HTML or JSON into validated course task records."""

    source = "learn"

    def parse(
        self,
        raw: str | bytes,
        metadata: Mapping[str, Any] | None = None,
    ) -> list[CourseTask]:
        """Parse raw Learn content without network or file IO."""

        meta = dict(metadata or {})
        text = raw.decode(meta.get("encoding", "utf-8"), errors="replace") if isinstance(raw, bytes) else raw
        content_type = str(meta.get("content_type", "")).lower()
        stripped = text.lstrip()
        if "json" in content_type or stripped.startswith(("{", "[")):
            return self.parse_json(text, meta)
        return [self.parse_html(text, meta)]

    def parse_html(self, html: str, metadata: Mapping[str, Any] | None = None) -> CourseTask:
        """Extract plain text, links, and explicit time hints from Learn HTML."""

        meta = dict(metadata or {})
        parser = _TextAndLinkParser(base_url=as_optional_str(meta.get("base_url")))
        parser.feed(html)
        content = normalize_text(" ".join(parser.text_parts))
        title = first_non_empty(
            as_optional_str(meta.get("title")),
            extract_title(html),
            first_line(content),
            "Untitled Learn Item",
        )
        ddl_text = first_non_empty(as_optional_str(meta.get("ddl")), find_deadline_text(content))
        return CourseTask(
            source=self.source,
            task_type=as_optional_str(meta.get("task_type")) or infer_task_type(content, title),
            raw_id=as_optional_str(meta.get("raw_id")) or stable_raw_id(title, content),
            course_name=as_optional_str(meta.get("course_name")) or "Unknown Course",
            title=title,
            content=content,
            ddl=parse_datetime_hint(ddl_text),
            attachments=parser.links,
        )

    def parse_json(self, raw_json: str, metadata: Mapping[str, Any] | None = None) -> list[CourseTask]:
        """Parse common Learn API JSON objects while preserving core fields."""

        meta = dict(metadata or {})
        data = json.loads(raw_json)
        records = data if isinstance(data, list) else extract_json_records(data)
        if isinstance(records, dict):
            records = [records]

        tasks: list[CourseTask] = []
        for index, item in enumerate(records):
            item = normalize_learn_record(item, meta)
            if not isinstance(item, dict):
                continue
            title = first_non_empty(
                as_optional_str(item.get("title")),
                as_optional_str(item.get("name")),
                as_optional_str(item.get("bt")),
                as_optional_str(item.get("wjmc")),
                as_optional_str(item.get("zt")),
                as_optional_str(item.get("bqmc")),
                f"Learn Item {index + 1}",
            )
            content = normalize_text(
                first_non_empty(
                    as_optional_str(item.get("content")),
                    as_optional_str(item.get("description")),
                    as_optional_str(item.get("body")),
                    as_optional_str(item.get("zynrStr")),
                    as_optional_str(item.get("zynrstr")),
                    as_optional_str(item.get("zynr")),
                    as_optional_str(item.get("ggnrStr")),
                    as_optional_str(item.get("ggnr")),
                    as_optional_str(item.get("ggnrMini")),
                    as_optional_str(item.get("bznr")),
                    as_optional_str(item.get("bz")),
                    as_optional_str(item.get("pynr")),
                    as_optional_str(item.get("nr")),
                    "",
                )
            )
            attachments = parse_attachment_items(item.get("attachments"), as_optional_str(meta.get("base_url")))
            attachments.extend(parse_inline_attachment_fields(item, meta))
            item_raw_id = (
                as_optional_str(item.get("id"))
                or as_optional_str(item.get("zyid"))
                or as_optional_str(item.get("xszyid"))
                or as_optional_str(item.get("wjid"))
                or as_optional_str(item.get("tlid"))
                or as_optional_str(item.get("raw_id"))
                or stable_raw_id(title, content)
            )
            raw_id_prefix = as_optional_str(meta.get("raw_id_prefix"))
            raw_id = f"{raw_id_prefix}_{item_raw_id}" if raw_id_prefix else item_raw_id
            tasks.append(
                CourseTask(
                    source=self.source,
                    task_type=(
                        as_optional_str(meta.get("task_type"))
                        or as_optional_str(item.get("task_type"))
                        or infer_task_type(content, title)
                    ),
                    raw_id=raw_id,
                    course_name=(
                        as_optional_str(item.get("course_name"))
                        or as_optional_str(meta.get("course_name"))
                        or "Unknown Course"
                    ),
                    title=title,
                    content=content,
                    ddl=parse_learn_deadline(item, content, meta),
                    attachments=attachments,
                )
            )
        return tasks


def parse_learn_deadline(item: Mapping[str, Any], content: str, metadata: Mapping[str, Any]) -> datetime | None:
    """Only homework-like Learn records should expose a downstream DDL."""

    task_type = str(item.get("task_type") or metadata.get("task_type") or infer_task_type(content, ""))
    if task_type != "homework":
        return None
    return parse_datetime_hint(
        as_optional_str(item.get("ddl"))
        or as_optional_str(item.get("deadline"))
        or as_optional_str(item.get("jzsjStr"))
        or as_optional_str(item.get("bjjzsjStr"))
        or find_deadline_text(content)
    )


def extract_json_records(data: Any) -> Any:
    """Find common list containers, including Learn DataTables JSON."""

    if not isinstance(data, dict):
        return [data]
    for key in ("data", "items", "records", "resultList"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    obj = data.get("object")
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for key in ("aaData", "data", "items", "records", "resultList"):
            value = obj.get(key)
            if isinstance(value, list):
                return value
    return [data]


def normalize_learn_record(item: Any, metadata: Mapping[str, Any]) -> Any:
    """Normalize Learn's array-shaped courseware rows into dict records."""

    if not isinstance(item, list):
        return item
    if str(metadata.get("task_type") or "") != "file" or len(item) < 8:
        return item
    file_id = as_optional_str(item[7])
    title = first_non_empty(as_optional_str(item[1]), "Course File")
    description = first_non_empty(as_optional_str(item[5]), "")
    download_template = as_optional_str(metadata.get("download_url_template"))
    download_url = download_template.format(file_id=file_id) if download_template and file_id else ""
    return {
        "id": as_optional_str(item[0]) or file_id,
        "bt": title,
        "bznr": description,
        "attachments": [{"name": title, "url": download_url}] if download_url else [],
    }


def normalize_text(value: str) -> str:
    """Collapse repeated whitespace while keeping readable line breaks."""

    value = re.sub(r"\r\n?", "\n", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n", value)
    return value.strip()


def as_optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def first_non_empty(*values: str | None) -> str:
    for value in values:
        if value and value.strip():
            return value.strip()
    return ""


def first_line(value: str) -> str | None:
    return next((line.strip() for line in value.splitlines() if line.strip()), None)


def extract_title(html: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    return normalize_text(match.group(1)) if match else None


def find_deadline_text(content: str) -> str | None:
    pattern = r"(?:ddl|deadline|due|截止时间|截止日期|截止)[:：\s]*([0-9]{4}[-/年.][^\n]+)"
    match = re.search(pattern, content, flags=re.IGNORECASE)
    return match.group(1).strip() if match else None


def parse_datetime_hint(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = (
        value.strip()
        .replace("年", "-")
        .replace("月", "-")
        .replace("日", " ")
        .replace("/", "-")
        .replace(".", "-")
    )
    match = re.search(r"(\d{4}-\d{1,2}-\d{1,2})(?:\s+(\d{1,2}:\d{2})(?::\d{2})?)?", normalized)
    if not match:
        return None
    date_part = match.group(1)
    time_part = match.group(2) or "23:59"
    try:
        parsed = datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return parsed.replace(tzinfo=LOCAL_TIMEZONE)


def infer_task_type(content: str, title: str) -> str:
    haystack = f"{title}\n{content}".lower()
    if any(word in haystack for word in ("homework", "作业", "ddl", "deadline", "due")):
        return "homework"
    if any(word in haystack for word in ("file", "课件", "附件", "resource", "slides")):
        return "file"
    if any(word in haystack for word in ("问卷", "questionnaire", "survey")):
        return "questionnaire"
    if any(word in haystack for word in ("讨论", "discussion")):
        return "discussion"
    return "notice"


def stable_raw_id(title: str, content: str) -> str:
    digest = hashlib.sha256(f"{title}\n{content[:500]}".encode("utf-8")).hexdigest()
    return f"learn-{digest[:24]}"


def parse_attachment_items(value: Any, base_url: str | None) -> list[Attachment]:
    if not isinstance(value, list):
        return []
    attachments: list[Attachment] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = first_non_empty(as_optional_str(item.get("name")), as_optional_str(item.get("fileName")), "attachment")
        url = first_non_empty(as_optional_str(item.get("download_url")), as_optional_str(item.get("url")), "")
        if url:
            attachments.append(Attachment(name=name, download_url=urljoin(base_url or "", url)))
    return attachments


def parse_inline_attachment_fields(item: Mapping[str, Any], metadata: Mapping[str, Any]) -> list[Attachment]:
    """Map Learn business JSON attachment id/name fields into Attachment records."""

    base_url = as_optional_str(metadata.get("base_url")) or ""
    attachments: list[Attachment] = []
    task_type = str(metadata.get("task_type") or "")
    field_specs = {
        "homework": (("zyfjid", "wjmc", "homework_attachment_url_template"),),
        "notice": (("fjid", "fjmc", "notice_attachment_url_template"),),
        "file": (("wjid", "wjmc", "file_attachment_url_template"),),
    }.get(task_type, ())
    for field_id, field_name, template_key in field_specs:
        attachment_id = as_optional_str(item.get(field_id))
        if not attachment_id:
            continue
        name = first_non_empty(as_optional_str(item.get(field_name)), as_optional_str(item.get("bt")), "attachment")
        template = as_optional_str(metadata.get(template_key))
        if not template:
            continue
        format_values = {key: str(value or "") for key, value in item.items() if isinstance(key, str)}
        format_values["file_id"] = attachment_id
        attachments.append(Attachment(name=name, download_url=urljoin(base_url, template.format(**format_values))))
    return attachments
