# Project Tasks

## Goal
Provide a course information collector that fetches raw data from Learn, Mail, and JWCH/Info, parses it into stable records, and exports new records for downstream assistant workflows.

## Boundaries
- Keep credentials, cookies, trust-device material, SQLite files, JSONL exports, attachments, and logs out of Git.
- Preserve the four-layer split: adapters fetch raw payloads, parsers clean and normalize payloads, models define contracts, and pipeline handles state/output.
- Default commands must avoid network access unless the operator explicitly passes `--allow-network`.
- Scripts and errors must not print passwords, authorization codes, full cookies, or tokens.

## Task List
- Maintain repository-level architecture notes and per-directory task files.
- Keep `README.md` aligned with implemented collector behavior and local setup.
- Ensure `scripts/run_harness.py` passes before relying on real-account probes.
- Validate real sync one channel at a time before running `--channel all`.
- Keep local runtime artifacts under ignored `storage/` and `logs/` paths.

## Acceptance Harness
- Run `python scripts/run_harness.py` from the repository root.
- Run real sync only after `.env` and any required trust-device files are configured locally.
- A successful channel sync prints `[OK]` with raw, parsed, fresh, and output counts.

## Risks
- External school systems can change login forms, app IDs, or JSON field names without notice.
- First login may require double authentication and trusted-device setup.
- A full Learn sync can take time because it loops through every current-semester course and multiple business endpoints per course.
