# Scripts Tasks

## Goal
Provide local developer utilities that verify architecture constraints and allow explicit manual probes for real adapters.

## Boundaries
- `run_harness.py` must use only the Python standard library.
- `run_harness.py` must not perform network requests or read `.env`.
- `probe_learn.py` and `probe_mail.py` are dry-run by default and may access network only with `--allow-network`.
- Scripts must not print credential material.

## Task List
- Check required `tasks.md` files exist.
- Compile Python files to catch syntax errors.
- Scan parser files for network libraries such as `requests` and `imaplib`.
- Scan adapter files for parser imports to protect the four-layer boundary.
- Scan tracked source-like files for sensitive placeholders and likely hardcoded secrets.
- Verify MailAdapter UID incremental behavior through a fake IMAP client.
- Verify pipeline sync state contract is present without importing non-stdlib dependencies.
- Provide manual Learn SSO/endpoint probe.
- Provide manual Learn double-auth probe with local ignored session and trust storage.
- Provide manual Mail IMAP UID probe with optional cursor commit.
- Provide non-sensitive Mail login diagnostics for TCP/TLS/capabilities/common username forms.
- Provide manual JWCH/Info exam and schedule raw page probes.
- Provide manual JWCH/auth.cic double-auth probe with local ignored session and trust storage.

## Acceptance Harness
- Run `python scripts/run_harness.py` from the repository root.
- A passing run prints each check and exits with code `0`.
- A failing run prints actionable messages and exits with code `1`.
- `python scripts/probe_learn.py` and `python scripts/probe_mail.py` must dry-run without network.

## Risks
- Harness checks are guardrails, not a replacement for tests with realistic offline fixtures or sanctioned real-account probes.
- Pattern-based secret scanning can miss context-specific leaks or flag false positives.
