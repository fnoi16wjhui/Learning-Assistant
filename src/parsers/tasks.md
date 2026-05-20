# Parsers Tasks

## Goal

Build the pure computation layer that converts raw Learn HTML/JSON and raw Mail MIME into validated `CourseTask` records. Parsers must be deterministic and offline-testable: the same input bytes or string plus metadata should produce the same normalized fields.

## Boundary

- Allowed: HTML tag stripping, JSON field extraction, MIME traversal, plain-text conversion, attachment metadata extraction, explicit time-string parsing, and construction of Pydantic models from parsed fields.
- Forbidden: `requests`, `imaplib`, browser automation, filesystem reads/writes, environment variable reads, credential handling, deduplication, persistence, or direct network access.
- Parsers may import `src.models` because models are the shared data contract; they must not import adapters or pipeline.

## Inputs

- `LearnHtmlParser.parse(raw, metadata)` receives raw HTML/JSON as `str | bytes`.
- `MailMimeParser.parse(raw, metadata)` receives one raw RFC822 message as `str | bytes`.
- Optional metadata may include `raw_id`, `course_name`, `task_type`, `base_url`, `encoding`, and `content_type`.

## Outputs

- `list[CourseTask]` with normalized `source`, `task_type`, `raw_id`, `course_name`, `title`, `content`, optional `ddl`, and `attachments`.
- Attachments are represented by the shared `Attachment` model. Mail attachments use `imap://...` placeholder URIs until a safe attachment persistence strategy is defined.

## Task List

- [x] Keep `LearnHtmlParser.parse()` callable with raw `str | bytes` plus metadata.
- [x] Support Learn JSON records with title, content, course name, deadline, and attachments.
- [x] Support Learn HTML fallback with title extraction, text extraction, links, and deadline hints.
- [x] Keep `MailMimeParser.parse()` callable with raw RFC822 `str | bytes` plus metadata.
- [x] Extract mail subject, body text, HTML fallback text, deadline hints, and attachment names.
- [x] Return Pydantic `CourseTask` records instead of parser-local dataclasses.
- [x] Add JWCH exam table parser that returns `ScheduleItem(schedule_type="exam")`.
- [x] Add JWCH weekly schedule grid and inline-JS parser that returns `ScheduleItem(schedule_type="class")` for non-empty cells.
- [ ] Add fixture-driven unit tests covering HTML-only, JSON API, plain MIME, HTML MIME, and attachment MIME.
- [ ] Add source-specific cleanup rules for school footers and repeated mail signatures after enough real samples are anonymized.

## Acceptance Harness

- `python scripts/run_harness.py` must pass.
- Parser modules must not import adapter modules, `requests`, `imaplib`, `os`, or database libraries.
- Learn parser should return stable `raw_id` fallback values based on SHA-256, never Python process `hash()`.
- Mail parser should prefer `Message-ID` or metadata UID for `raw_id`; fallback must also be SHA-256 based.
- Deadline parsing should only accept explicit date hints and should attach `Asia/Shanghai` timezone when it can parse a value.

## Risks

- Learn page structure may vary across announcements, homework, files, quizzes, and discussions.
- Deadline text can be ambiguous; parser should only parse explicit time hints and let downstream logic decide uncertain cases.
- MIME messages can include nested multiparts and non-UTF encodings; decoding should preserve content with replacement rather than crashing.
- Attachment download URLs from mail are placeholders until an adapter-owned attachment fetch/persistence design exists.
