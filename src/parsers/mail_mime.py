"""Pure MIME parsing helpers for course emails."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser, Parser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Mapping
from urllib.parse import quote

from src.models import Attachment, CourseTask
from src.models.base import local_now
from src.parsers.learn_html import parse_datetime_hint


class _HtmlTextParser(HTMLParser):
    """Convert HTML email body to plain text."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


class MailMimeParser:
    """Parse raw RFC822 MIME bytes into validated course task records."""

    source = "mail"

    def parse(
        self,
        raw: str | bytes,
        metadata: Mapping[str, Any] | None = None,
    ) -> list[CourseTask]:
        """Parse one raw MIME message without network or file IO."""

        meta = dict(metadata or {})
        message = parse_message(raw)
        text_body, html_body = extract_bodies(message)
        html_text = html_to_text(html_body) if html_body else None
        content = clean_mail_noise(normalize_text(text_body or html_text or ""))
        subject = first_non_empty(message.get("Subject"), "(no subject)")
        raw_id = stable_message_id(
            as_optional_str(meta.get("raw_id")),
            message.get("Message-ID"),
            subject,
            message.get("Date"),
            content,
        )

        sent_at = parse_mail_date(message.get("Date"))
        return [
            CourseTask(
                source=self.source,
                task_type=as_optional_str(meta.get("task_type")) or infer_task_type(subject, content),
                raw_id=raw_id,
                course_name=as_optional_str(meta.get("course_name")) or infer_course_name(subject) or "Unknown Course",
                title=subject,
                content=content,
                created_at=sent_at or local_now(),
                ddl=parse_deadline(content),
                attachments=extract_attachments(message, raw_id),
            )
        ]


def parse_message(raw: str | bytes) -> Message:
    if isinstance(raw, bytes):
        return BytesParser(policy=policy.default).parsebytes(raw)
    return Parser(policy=policy.default).parsestr(raw)


def extract_bodies(message: Message) -> tuple[str | None, str | None]:
    text_body: str | None = None
    html_body: str | None = None

    if isinstance(message, EmailMessage):
        text_part = message.get_body(preferencelist=("plain",))
        html_part = message.get_body(preferencelist=("html",))
        text_body = decode_part(text_part) if text_part else None
        html_body = decode_part(html_part) if html_part else None
        return text_body, html_body

    if message.is_multipart():
        for part in message.walk():
            if part.get_content_disposition() == "attachment":
                continue
            content_type = part.get_content_type()
            if content_type == "text/plain" and text_body is None:
                text_body = decode_part(part)
            elif content_type == "text/html" and html_body is None:
                html_body = decode_part(part)
    else:
        content_type = message.get_content_type()
        if content_type == "text/html":
            html_body = decode_part(message)
        else:
            text_body = decode_part(message)
    return text_body, html_body


def extract_attachments(message: Message, raw_id: str) -> list[Attachment]:
    attachments: list[Attachment] = []
    for part in message.walk() if message.is_multipart() else []:
        filename = part.get_filename()
        disposition = part.get_content_disposition()
        if disposition != "attachment" and not filename:
            continue
        safe_name = filename or "attachment"
        attachments.append(
            Attachment(
                name=safe_name,
                download_url=f"imap://{quote(raw_id, safe='')}/{quote(safe_name)}",
            )
        )
    return attachments


def decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    charset = part.get_content_charset() or "utf-8"
    if payload is None:
        raw_payload = part.get_payload()
        return raw_payload if isinstance(raw_payload, str) else ""
    return payload.decode(charset, errors="replace")


def html_to_text(html: str) -> str:
    parser = _HtmlTextParser()
    parser.feed(html)
    return normalize_text(" ".join(parser.parts))


def normalize_text(value: str) -> str:
    lines = [" ".join(line.split()) for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line).strip()


def clean_mail_noise(value: str) -> str:
    """Remove common automated mail footers without guessing course semantics."""

    if not value:
        return value
    stop_markers = (
        "此邮件由系统自动发送",
        "本邮件由系统自动发送",
        "请勿直接回复",
        "do not reply",
        "please do not reply",
        "unsubscribe",
        "退订",
        "清华大学信息门户",
        "Tsinghua University",
    )
    kept: list[str] = []
    for line in value.splitlines():
        lowered = line.lower()
        if any(marker.lower() in lowered for marker in stop_markers):
            break
        if is_repeated_separator(line):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def is_repeated_separator(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and len(stripped) >= 6 and len(set(stripped)) <= 2


def parse_mail_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_deadline(content: str) -> datetime | None:
    pattern = r"(?:ddl|deadline|due|截止时间|截止日期|截止)[:：\s]*([0-9]{4}[-/年.][^\n]+)"
    match = re_search(pattern, content)
    return parse_datetime_hint(match) if match else None


def re_search(pattern: str, content: str) -> str | None:
    match = re.search(pattern, content, flags=re.IGNORECASE)
    return match.group(1).strip() if match else None


def infer_task_type(subject: str, content: str) -> str:
    haystack = f"{subject}\n{content}".lower()
    if any(word in haystack for word in ("homework", "作业", "ddl", "deadline", "due")):
        return "homework"
    if any(word in haystack for word in ("file", "课件", "附件", "resource", "slides")):
        return "file"
    if any(word in haystack for word in ("exam", "考试", "期中", "期末")):
        return "exam"
    return "notice"


def infer_course_name(subject: str) -> str | None:
    match = re_search(r"[【\[]([^】\]]+)[】\]]", subject)
    return match


def looks_course_related(record: str) -> bool:
    """Heuristic used by orchestration to suppress obvious non-course mail."""

    haystack = record.lower()
    keywords = (
        "课程",
        "作业",
        "考试",
        "课堂",
        "教学",
        "课件",
        "助教",
        "老师",
        "ddl",
        "homework",
        "assignment",
        "exam",
        "course",
        "lecture",
    )
    return any(keyword.lower() in haystack for keyword in keywords)


def stable_message_id(
    metadata_id: str | None,
    message_id: str | None,
    subject: str,
    date_value: str | None,
    content: str,
) -> str:
    if metadata_id:
        return metadata_id
    if message_id and message_id.strip():
        return message_id.strip()
    digest = hashlib.sha256(f"{subject}\n{date_value or ''}\n{content[:500]}".encode("utf-8")).hexdigest()
    return f"mail-{digest[:24]}"


def first_non_empty(*values: str | None) -> str:
    for value in values:
        if value and value.strip():
            return value.strip()
    return ""


def as_optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
