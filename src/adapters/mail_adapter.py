"""Mail adapter for raw IMAP MIME fetching."""

from __future__ import annotations

import imaplib
from pathlib import Path
from typing import Any, Protocol

from .base_adapter import AdapterConfig, AdapterError, BaseAdapter, RawPayload


class ImapClient(Protocol):
    """Small IMAP surface used by MailAdapter and fake harness clients."""

    def login(self, user: str, password: str) -> Any: ...

    def select(self, mailbox: str, readonly: bool = True) -> Any: ...

    def uid(self, command: str, *args: str) -> Any: ...

    def logout(self) -> Any: ...


class MailAdapter(BaseAdapter):
    """Fetch raw RFC822 email bytes without MIME parsing."""

    source = "mail"
    env_prefix = "MAIL"

    def __init__(
        self,
        config: AdapterConfig | None = None,
        *,
        imap_client: ImapClient | None = None,
    ) -> None:
        super().__init__(config)
        self._client = imap_client
        self._authenticated = False

    def authenticate(self) -> None:
        """Validate credentials and establish IMAP session when needed."""

        if self.config.data_path:
            self._authenticated = True
            return
        username = self.require("username")
        password = self.require("password")
        if self._client is None:
            self._client = self._connect()
        self._login_with_candidates(username, password)
        self._authenticated = True

    def fetch_raw(
        self,
        *,
        mailbox: str = "INBOX",
        criteria: str = "UNSEEN",
        limit: int = 20,
        since_uid: int | None = None,
        **_: Any,
    ) -> list[RawPayload]:
        """Fetch raw MIME messages using stable IMAP UID incremental logic."""

        if not self._authenticated:
            self.authenticate()

        if self.config.data_path:
            return self._fetch_from_path(self.config.data_path, limit=limit)

        if self._client is None:
            raise AdapterError("mail_adapter authenticated without an IMAP client")
        return self._fetch_from_imap(
            self._client,
            mailbox=mailbox,
            criteria=criteria,
            limit=limit,
            since_uid=since_uid,
        )

    def close(self) -> None:
        """Close the IMAP session when the caller finishes a sync cycle."""

        if self._client is None:
            return
        try:
            self._client.logout()
        finally:
            self._client = None
            self._authenticated = False

    def _fetch_from_path(self, path: Path, *, limit: int) -> list[RawPayload]:
        if not path.exists():
            raise AdapterError(f"mail_adapter data path does not exist: {path}")
        files = [path] if path.is_file() else sorted(path.glob("*.eml"))
        payloads: list[RawPayload] = []
        for file_path in files[:limit]:
            payloads.append(
                self.payload(
                    raw_id=f"mail_fixture_{file_path.stem}",
                    content=file_path.read_bytes(),
                    content_type="message/rfc822",
                    metadata={"path": str(file_path)},
                )
            )
        return payloads

    def _connect(self) -> ImapClient:
        host = self.require("base_url")
        port = int(self.config.extra.get("imap_port", 993))
        use_ssl = bool(self.config.extra.get("use_ssl", True))
        timeout = float(self.config.extra.get("timeout_seconds", 20))
        try:
            if use_ssl:
                return imaplib.IMAP4_SSL(host, port, timeout=timeout)
            return imaplib.IMAP4(host, port, timeout=timeout)
        except Exception as exc:
            raise AdapterError(f"mail_adapter connect failed: host={host} port={port}") from exc

    def _login_with_candidates(self, username: str, password: str) -> None:
        errors: list[str] = []
        for candidate in candidate_usernames(username):
            try:
                status, _ = self._client.login(candidate, password) if self._client is not None else ("NO", [])
            except Exception as exc:  # IMAP libraries raise several typed errors.
                errors.append(type(exc).__name__)
                continue
            if normalize_status(status) == "OK":
                return
            errors.append(normalize_status(status))
        raise AdapterError(
            "mail_adapter login failed: check MAIL_USERNAME/MAIL_PASSWORD "
            f"candidate_count={len(candidate_usernames(username))} errors={','.join(errors[:3])}"
        )

    def _fetch_from_imap(
        self,
        client: ImapClient,
        *,
        mailbox: str,
        criteria: str,
        limit: int,
        since_uid: int | None,
    ) -> list[RawPayload]:
        try:
            status, _ = client.select(mailbox, readonly=True)
        except Exception as exc:
            raise AdapterError(f"mail_adapter mailbox select failed: mailbox={mailbox}") from exc
        if normalize_status(status) != "OK":
            raise AdapterError(f"mail_adapter mailbox select failed: mailbox={mailbox}")

        search_query = build_uid_search_query(criteria, since_uid=since_uid)
        try:
            status, data = client.uid("SEARCH", None, search_query)
        except Exception as exc:
            raise AdapterError(f"mail_adapter uid search failed: mailbox={mailbox}") from exc
        if normalize_status(status) != "OK":
            raise AdapterError(f"mail_adapter uid search failed: mailbox={mailbox}")

        uids = parse_uid_search(data)
        selected_uids = uids[: max(limit, 0)]
        payloads: list[RawPayload] = []
        for uid in selected_uids:
            try:
                status, fetched = client.uid("FETCH", str(uid), "(RFC822)")
            except Exception as exc:
                raise AdapterError(f"mail_adapter uid fetch failed: mailbox={mailbox} uid={uid}") from exc
            if normalize_status(status) != "OK":
                raise AdapterError(f"mail_adapter uid fetch failed: mailbox={mailbox} uid={uid}")
            raw_message = extract_rfc822_bytes(fetched)
            payloads.append(
                self.payload(
                    raw_id=f"mail_uid_{uid}",
                    content=raw_message,
                    content_type="message/rfc822",
                    metadata={"mailbox": mailbox, "uid": uid, "criteria": criteria},
                )
            )
        return payloads


def build_uid_search_query(criteria: str, *, since_uid: int | None) -> str:
    """Build an IMAP UID search query without relying on volatile sequence IDs."""

    base = criteria.strip() or "ALL"
    if since_uid is None or since_uid < 1:
        return base
    return f"UID {since_uid + 1}:* {base}"


def parse_uid_search(data: Any) -> list[int]:
    """Parse SEARCH response bytes into ascending integer UIDs."""

    if not data:
        return []
    first = data[0]
    if isinstance(first, bytes):
        text = first.decode("ascii", errors="ignore")
    else:
        text = str(first)
    uids = [int(part) for part in text.split() if part.isdigit()]
    return sorted(set(uids))


def extract_rfc822_bytes(data: Any) -> bytes:
    """Extract raw message bytes from common imaplib FETCH response shapes."""

    if not data:
        raise AdapterError("mail_adapter uid fetch failed: empty FETCH response")
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
        if isinstance(item, bytes) and item.startswith(b"Subject:"):
            return item
    raise AdapterError("mail_adapter uid fetch failed: RFC822 bytes missing")


def normalize_status(status: Any) -> str:
    if isinstance(status, bytes):
        return status.decode("ascii", errors="ignore").upper()
    return str(status).upper()


def candidate_usernames(username: str) -> list[str]:
    """Generate Coremail username candidates without logging their values."""

    candidates: list[str] = []
    local_part = username.split("@", 1)[0]
    for item in (
        username,
        f"{local_part}@mails.tsinghua.edu.cn",
        f"{local_part}@tsinghua.edu.cn",
        f"{local_part}@mail.tsinghua.edu.cn",
    ):
        if item and item not in candidates:
            candidates.append(item)
    return candidates
