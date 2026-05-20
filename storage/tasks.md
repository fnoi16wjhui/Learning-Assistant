# Storage Tasks

## Goal
Reserve a local directory for lightweight runtime state such as SQLite deduplication databases, sync cursors, and JSONL exports. Runtime data should stay local and out of version control.

## Boundaries
- Allowed: `.gitkeep`, documentation, and local generated databases ignored by Git.
- Forbidden in commits: real course data, mailbox content, authentication cookies, student IDs, or exported credentials.
- Allowed locally and ignored by Git: short-lived probe session files needed to finish user-driven two-factor authentication.
- The pipeline may create `storage/app.db` locally, including `fingerprints` and `sync_state` tables, but that file must not be committed.

## Task List
- Keep `.gitkeep` so the directory exists in fresh clones.
- Store only generated local state during development.
- Use `sync_state` for cursors such as `mail:last_uid`.
- Keep `*session*.json` and `*trust*.json` local; they may contain login session material.
- Keep sample data under `tests/fixtures/` and ensure it is synthetic.

## Acceptance Harness
- `python scripts/run_harness.py` must confirm this `tasks.md` exists.
- Git ignore rules must exclude SQLite database and journal files in this directory.

## Risks
- Accidentally committing runtime databases can leak course names, deadlines, raw IDs, or personal records.
- Sharing local deduplication state across machines may hide missing parser or adapter bugs.
