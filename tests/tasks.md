# Tests Tasks

## Goal
Hold offline fixtures and future tests for parser, model, and pipeline behavior. Tests should make collector behavior reproducible without contacting campus services.

## Boundaries
- Allowed: synthetic raw HTML, MIME-like text, JSON, and expected normalized outputs.
- Forbidden: real course announcements, real mailbox messages, real usernames, cookies, passwords, or student IDs.
- Fixtures should preserve shape, not private content.

## Task List
- Add parser tests that transform fixtures into `CourseTask` and `ScheduleItem`.
- Add pipeline tests for fingerprint stability and SQLite deduplication.
- Add CLI tests for dry-run behavior and missing environment variables.
- Keep fixtures small and readable.

## Acceptance Harness
- `python scripts/run_harness.py` must confirm `tests/tasks.md` exists.
- Harness must scan fixtures for sensitive-looking material.
- Future parser tests should run without network access.

## Risks
- Over-simplified fixtures can hide real-world parsing failures.
- Realistic fixtures can accidentally contain private classroom or mailbox data if copied from production.

