# Models Layer Tasks

## Goal
Define the shared Pydantic v2 contracts used by collectors and downstream consumers. Models should normalize heterogeneous source records into stable Python objects and JSON payloads.

## Boundaries
- Allowed: validation rules, enum definitions, field descriptions, and typed relationships between records.
- Forbidden: network requests, IMAP access, HTML parsing, MIME parsing, SQLite writes, or reading credentials.
- Models should accept already-clean plain text from parser outputs; they do not clean source data themselves.

## Task List
- Maintain `Attachment` for downloadable or offline fixture references.
- Maintain `CourseTask` for homework, notices, files, questionnaires, discussions, and exams.
- Maintain `ScheduleItem` for classes, exams, office hours, and other calendar-like records.
- Keep source identifiers aligned with `learn`, `mail`, and `jwch`.
- Add schema migrations only after updating architecture documentation.

## Acceptance Harness
- Pydantic imports must compile with v2 syntax.
- `python scripts/run_harness.py` must reject use of `requests`, `imaplib`, or parser imports inside this layer.
- Offline fixture JSON should validate through `CourseTask` or `ScheduleItem` once parser tests are added.

## Risks
- Overly permissive fields can leak noisy upstream structure downstream.
- Overly strict fields can make incremental adapters brittle before real campus data is fully understood.
- Storing credential-bearing URLs or cookies in attachments would violate the security boundary.

