"""Manual JWCH/Info probe for exam and schedule pages."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adapters.jwch_adapter import JwchAdapter
from src.adapters.learn_adapter import discover_auth_form_url, select_login_form
from src.env_loader import load_project_env
from src.parsers.jwch_html import JwchHtmlParser
from src.parsers.learn_html import extract_title


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe JWCH exam and schedule raw pages.")
    parser.add_argument("--allow-network", action="store_true", help="Connect to Info/JWCH.")
    parser.add_argument(
        "--target",
        choices=("exam", "schedule", "both"),
        default="both",
        help="Which JWCH page to fetch.",
    )
    parser.add_argument("--diagnose-auth", action="store_true", help="Print redacted Info auth diagnostics.")
    parser.add_argument("--parse", action="store_true", help="Parse fetched JWCH HTML and print record counts.")
    parser.add_argument("--anchor-monday", help="YYYY-MM-DD Monday used for weekly schedule parse timestamps.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    load_project_env()

    targets = selected_targets(args.target)
    if not args.allow_network:
        for name, url in targets:
            print(f"Dry run. Would fetch {name}: {url}")
        print("JWCH login is expected to enter through Info onlineAppRedirect, then fetch zhjw.cic pages.")
        return 0

    adapter = JwchAdapter()
    if args.diagnose_auth:
        return diagnose_auth(adapter)
    for name, url in targets:
        payload = adapter.fetch_raw(url=url, raw_id=f"jwch_{name}")[0]
        text = payload.content if isinstance(payload.content, str) else payload.content.decode("utf-8", errors="replace")
        print(f"{name}_raw_id={payload.raw_id}")
        print(f"{name}_url={payload.metadata.get('url')}")
        print(f"{name}_content_type={payload.content_type}")
        print(f"{name}_title={extract_title(text) or '(no title)'}")
        print(f"{name}_looks_login={looks_like_login(text)}")
        print(f"{name}_bytes={len(text.encode('utf-8'))}")
        if args.parse:
            records = JwchHtmlParser().parse(
                text,
                {
                    "raw_id": payload.raw_id,
                    "url": payload.metadata.get("url"),
                    "anchor_monday": args.anchor_monday,
                },
            )
            print(f"{name}_parsed_count={len(records)}")
            for record in records[:3]:
                print(
                    f"{name}_record={record.schedule_type} "
                    f"{record.starts_at.isoformat()} "
                    f"{record.course_name[:40]} "
                    f"{(record.location or '')[:40]}"
                )
    return 0


def diagnose_auth(adapter: JwchAdapter) -> int:
    session = adapter._build_session()
    adapter._session = session
    login_url = adapter._portal_login_url()
    entry = session.get(login_url, timeout=float(adapter.config.extra.get("timeout_seconds", 20)))
    auth_url = discover_auth_form_url(entry.url, entry.text)
    form_page_url = auth_url or entry.url
    form_page = session.get(auth_url, timeout=float(adapter.config.extra.get("timeout_seconds", 20))) if auth_url else entry
    form = select_login_form(form_page.text, username_field="i_user", password_field="i_pass")
    inputs = form.get("inputs", {})
    print(f"entry_host={host_and_path(entry.url)}")
    print(f"entry_title={extract_title(entry.text) or '(no title)'}")
    print(f"entry_id_links={discover_safe_hosts(entry.url, entry.text, 'id.tsinghua.edu.cn')}")
    print(f"entry_info_links={discover_safe_hosts(entry.url, entry.text, 'info.tsinghua.edu.cn')[:5]}")
    print(f"auth_form_host={host_and_path(form_page_url) if auth_url else '(none)'}")
    print(f"form_page_host={host_and_path(form_page.url)}")
    print(f"form_has_i_user={'i_user' in inputs}")
    print(f"form_has_i_pass={'i_pass' in inputs}")
    print(f"form_action_host={host_and_path(str(form.get('action') or form_page.url))}")
    adapter._login_with_password(login_url)
    portal_base = str(adapter.config.extra.get("portal_base_url") or "https://info.tsinghua.edu.cn").rstrip("/")
    referer = str(
        adapter.config.extra.get("portal_referer")
        or portal_base + "/f/info/portal_fg/common/yyfwsearch?searchParam=%E8%80%83%E8%AF%95"
    )
    response = session.get(referer, timeout=float(adapter.config.extra.get("timeout_seconds", 20)))
    cookie_names = sorted({cookie.name for cookie in session.cookies if cookie.domain.endswith("tsinghua.edu.cn")})
    cookie_scopes = sorted(
        f"{cookie.domain}{cookie.path}:{cookie.name}"
        for cookie in session.cookies
        if cookie.domain.endswith("tsinghua.edu.cn")
    )
    print(f"login_entry_host={host_and_path(login_url)}")
    print(f"portal_context_host={host_and_path(response.url)}")
    print(f"portal_context_status={response.status_code}")
    print(f"portal_context_title={extract_title(response.text) or '(no title)'}")
    print(f"portal_context_looks_login={looks_like_login(response.text)}")
    print(f"cookie_names={cookie_names}")
    print(f"cookie_scopes={cookie_scopes}")
    print(f"xsrf_cookie_present={'XSRF-TOKEN' in cookie_names}")
    print(f"info_session_cookie_present={'JSESSIONID' in cookie_names}")
    return 0


def host_and_path(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.hostname}{parsed.path}"


def discover_safe_hosts(current_url: str, text: str, host: str) -> list[str]:
    values: list[str] = []
    candidates = re.findall(r'["\']([^"\']+)["\']', text)
    for candidate in candidates:
        if host not in candidate:
            continue
        absolute = urljoin(current_url, candidate)
        parsed = urlparse(absolute)
        values.append(f"{parsed.hostname}{parsed.path}")
    return sorted(set(values))


def selected_targets(target: str) -> list[tuple[str, str]]:
    exam_url = os.getenv("JWCH_EXAM_URL") or "https://zhjw.cic.tsinghua.edu.cn/jxmh.do?url=/jxmh.do&m=bks_ksSearch"
    schedule_url = (
        os.getenv("JWCH_SCHEDULE_URL")
        or "https://zhjw.cic.tsinghua.edu.cn/portal3rd.do?url=/portal3rd.do&m=bks_yjkbSearch"
    )
    if target == "exam":
        return [("exam", exam_url)]
    if target == "schedule":
        return [("schedule", schedule_url)]
    return [("exam", exam_url), ("schedule", schedule_url)]


def looks_like_login(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in ("i_user", "i_pass", "login", "统一认证", "登录"))


if __name__ == "__main__":
    raise SystemExit(main())
