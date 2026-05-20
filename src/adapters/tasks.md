# Adapters Tasks

## Goal

Build the raw IO layer for course data sources. Adapters own sessions, authentication entry points, retries, and raw HTML/JSON/MIME/ICS fetching. They hand `RawPayload` objects to the pipeline without cleaning, extracting, or constructing course models.

## Boundary

- Allowed: environment/config loading, credential validation, HTTP/IMAP/session setup, raw payload download, raw file fixture loading for harnesses.
- Forbidden: HTML cleanup, MIME body extraction, attachment normalization, deadline inference, `CourseTask` or `ScheduleItem` construction.
- Sensitive data must come from environment variables or injected `AdapterConfig`; never hardcode student IDs, passwords, mail auth codes, cookies, or API keys.

## Task List

- Define the shared adapter contract in `base_adapter.py`.
- Keep `RawPayload` as the only cross-layer output from adapters.
- Implement Learn, Mail, and JWCH credential entry points with clear errors.
- Add offline fixture paths through `LEARN_DATA_PATH`, `MAIL_DATA_PATH`, and `JWCH_DATA_PATH` for harness testing.
- Add a `requests.Session`-based Learn SSO/endpoint probe with configurable form field names.
- Support Learn SM2 password encryption, double-auth trust material reuse, and post-auth roaming ticket follow-up.
- Implement Mail IMAP UID incremental fetch with injectable fake clients for offline harnesses.
- Wire JWCH fetch through Info `onlineAppRedirect`, then follow the one-time roaming URL into zhjw.
- Provide a local ignored Info cookie bootstrap fallback when password login cannot establish a portal API session.
- Add logging and retry policy in adapters without leaking secrets.

## Acceptance Harness

- Instantiate each adapter with `AdapterConfig(data_path=...)` and assert `fetch_raw()` returns `RawPayload`.
- Assert missing credentials raise `AdapterError` with adapter-specific context.
- Assert raw fixture contents are unchanged by adapters.
- Assert fake IMAP UID search/fetch returns `mail_uid_<uid>` raw IDs and `message/rfc822` payloads.
- Assert trusted Learn logins can reuse local ignored trust material without parser/model involvement.
- Assert no adapter imports parser modules or model modules.
- Assert no adapter source file contains real credentials or source-specific secrets.

## Risks

- Learn SSO may change; keep `login_url`, `username_field`, and `password_field` configurable through `LEARN_EXTRA_JSON`.
- Learn double-auth trust files are sensitive local session material and must stay ignored by Git.
- IMAP full scans are expensive; UID cursors reduce scan cost but fingerprint deduplication remains the final duplicate guard.
- JWCH/ThuInfo source choice is unstable; keep raw fetch generic until parser and model contracts settle.
- Fixture paths are for harnesses only and must not become a hidden production data source.
