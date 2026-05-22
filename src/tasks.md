# src Layer Tasks

## Goal
Provide the package boundary for the collector code owned by the data acquisition team. This layer should expose stable contracts and orchestration helpers while keeping network IO, parsing, validation, and persistence in separate modules.

## Boundaries
- `src/models/` owns Pydantic v2 data contracts only.
- `src/materials/` owns local file extraction, text cleaning, and chunking for B-module course materials.
- `src/pipeline.py` owns fingerprinting, SQLite deduplication, JSON serialization, and local logging setup.
- This package must not hardcode student IDs, passwords, mailbox authorization codes, cookies, or API keys.
- This package must not collapse the four-layer architecture from `Rules.md`: adapters fetch raw data, parsers clean raw data, models define contracts, and pipeline persists/deduplicates typed records.

## Task List
- Keep public imports small and predictable.
- Preserve strict model validation so downstream Agent code receives stable data.
- Keep runtime side effects out of package import time.
- Add future modules only after their architectural layer is documented.

## Acceptance Harness
- `python scripts/run_harness.py` must compile all Python files.
- Harness must confirm `src/tasks.md` and child `tasks.md` files exist.
- Harness must scan for obvious sensitive placeholders or real-looking secrets.

## Risks
- Cross-layer shortcuts can make login, parsing, and persistence impossible to test independently.
- Import-time side effects can accidentally access network or local credentials.
- Expanding schemas without documenting downstream impact can break the content understanding team.

