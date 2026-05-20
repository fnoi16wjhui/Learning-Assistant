"""Manual IMAP UID probe for MailAdapter.

The script is dry-run by default. It only connects to the real mailbox when
--allow-network is provided.
"""

from __future__ import annotations

import argparse
import imaplib
import os
import socket
import ssl
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adapters.mail_adapter import MailAdapter
from src.env_loader import load_project_env
from src.pipeline import DeduplicationStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe Tsinghua mail IMAP UID sync.")
    parser.add_argument("--allow-network", action="store_true", help="Connect to the configured IMAP server.")
    parser.add_argument("--mailbox", default="INBOX", help="IMAP mailbox to select.")
    parser.add_argument("--criteria", default="UNSEEN", help="IMAP UID SEARCH criteria.")
    parser.add_argument("--limit", type=int, default=10, help="Maximum messages to fetch.")
    parser.add_argument("--db-path", default="storage/app.db", help="SQLite sync state path.")
    parser.add_argument("--since-uid", type=int, help="Override stored mail:last_uid for this probe.")
    parser.add_argument(
        "--diagnose-login",
        action="store_true",
        help="Run non-sensitive IMAP login diagnostics before fetching mail.",
    )
    parser.add_argument(
        "--commit-cursor",
        action="store_true",
        help="Persist max fetched UID as mail:last_uid after the probe succeeds.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    load_project_env()

    if args.diagnose_login:
        return diagnose_login()

    if not args.allow_network:
        last_uid = args.since_uid if args.since_uid is not None else 0
        print(
            "Dry run. Would run IMAP UID sync with "
            f"mailbox={args.mailbox} criteria={args.criteria} since_uid={last_uid} limit={args.limit}."
        )
        return 0

    store = DeduplicationStore(args.db_path)
    last_uid = args.since_uid if args.since_uid is not None else store.get_int_state("mail", "last_uid", default=0)

    adapter = MailAdapter()
    try:
        payloads = adapter.fetch_raw(
            mailbox=args.mailbox,
            criteria=args.criteria,
            limit=args.limit,
            since_uid=last_uid,
        )
    finally:
        adapter.close()

    fetched_uids = [int(payload.metadata["uid"]) for payload in payloads if "uid" in payload.metadata]
    print(f"Fetched {len(payloads)} raw MIME message(s).")
    if fetched_uids:
        max_uid = max(fetched_uids)
        print(f"UID range: {min(fetched_uids)}..{max_uid}")
        if args.commit_cursor:
            store.set_state("mail", "last_uid", max_uid)
            print(f"Committed mail:last_uid={max_uid}")
    return 0


def diagnose_login() -> int:
    """Diagnose IMAP login without printing usernames, passwords, or mail data."""

    host = os.getenv("MAIL_BASE_URL") or "mails.tsinghua.edu.cn"
    username = os.getenv("MAIL_USERNAME") or ""
    password = os.getenv("MAIL_PASSWORD") or ""
    port = 993

    print(f"host={host}")
    print(f"username_present={bool(username)} length={len(username)} has_at={'@' in username}")
    print(f"password_present={bool(password)} length={len(password)}")

    try:
        with socket.create_connection((host, port), timeout=10) as sock:
            print("tcp_connect=True")
            with ssl.create_default_context().wrap_socket(sock, server_hostname=host) as ssock:
                print("tls=True")
                print(f"tls_version={ssock.version()}")
    except Exception as exc:
        print(f"connect_error={type(exc).__name__}: {str(exc)[:100]}")
        return 2

    try:
        client = imaplib.IMAP4_SSL(host, port, timeout=20)
        print(
            "capabilities="
            + " ".join(cap.decode(errors="ignore") if isinstance(cap, bytes) else str(cap) for cap in client.capabilities)
        )
        client.logout()
    except Exception as exc:
        print(f"capability_error={type(exc).__name__}: {str(exc)[:100]}")

    candidates = candidate_usernames(username)
    success = False
    for index, candidate in enumerate(candidates, start=1):
        client = None
        try:
            client = imaplib.IMAP4_SSL(host, port, timeout=20)
            status, _ = client.login(candidate, password)
            print(f"candidate_{index}_status={status}")
            success = status == "OK"
            if success:
                break
        except Exception as exc:
            print(f"candidate_{index}_error={type(exc).__name__}: {str(exc)[:100]}")
        finally:
            try:
                if client is not None:
                    client.logout()
            except Exception:
                pass
    return 0 if success else 2


def candidate_usernames(username: str) -> list[str]:
    """Generate common Coremail username forms without logging the values."""

    candidates: list[str] = []
    for item in (
        username,
        f"{username}@mails.tsinghua.edu.cn",
        f"{username}@tsinghua.edu.cn",
        f"{username}@mail.tsinghua.edu.cn",
    ):
        if item and item not in candidates:
            candidates.append(item)
    return candidates


if __name__ == "__main__":
    raise SystemExit(main())
