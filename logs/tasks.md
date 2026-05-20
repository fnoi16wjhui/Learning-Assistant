# Logs Tasks

## Goal
Reserve a directory for local diagnostic logs that help identify adapter, parser, and pipeline failures without exposing secrets.

## Boundaries
- Allowed: `.gitkeep`, documentation, and local ignored `.log` files.
- Forbidden: passwords, authorization codes, cookies, raw email bodies, complete HTML dumps, or real student identifiers.
- Log messages should include safe context such as channel, source, raw ID, and operation name.

## Task List
- Keep local logs ignored by Git.
- Prefer explicit exception context over swallowed errors.
- Review future logging changes for secret leakage before committing.

## Acceptance Harness
- `python scripts/run_harness.py` must confirm this `tasks.md` exists.
- Git ignore rules must exclude `.log` files in this directory.

## Risks
- Debug logging raw responses can leak credentials or private class information.
- Silent exception handling can make collector failures look like empty successful syncs.

