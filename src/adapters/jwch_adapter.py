"""JWCH adapter for raw exam and schedule HTML fetching."""

from __future__ import annotations

import html
import json
import os
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse

from .base_adapter import AdapterConfig, AdapterError, BaseAdapter, RawPayload
from .learn_adapter import (
    discover_auth_form_url,
    discover_sm2_script_url,
    encrypt_password_with_node_sm2,
    extract_sm2_public_key,
    load_trusted_device_fields,
    select_login_form,
)


class _FormCollector(HTMLParser):
    """Collect HTML forms for post-auth auto-submit pages."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "form":
            self._current = {
                "action": attr_map.get("action", ""),
                "method": attr_map.get("method", "get").lower(),
                "inputs": {},
            }
        elif tag.lower() == "input" and self._current is not None:
            name = attr_map.get("name")
            if name:
                self._current["inputs"][name] = attr_map.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "form" and self._current is not None:
            self.forms.append(self._current)
            self._current = None


class JwchAdapter(BaseAdapter):
    """Fetch raw JWCH pages without parsing exam or schedule semantics."""

    source = "jwch"
    env_prefix = "JWCH"
    default_exam_app_id = "81008AA5A89C20D5BDBBDF719D5F0A94"
    default_schedule_app_id = "287C0C6D90ABB364CD5FDF1495199962"

    def __init__(self, config: AdapterConfig | None = None, *, session: Any | None = None) -> None:
        super().__init__(config)
        self._session = session
        self._authenticated = False
        self._zhjw_session_ready = False

    def authenticate(self, *, start_url: str | None = None) -> None:
        """Prepare an authenticated session through the Info portal app redirect."""

        if self.config.data_path:
            self._authenticated = True
            return
        self._session = self._session or self._build_session()
        self._load_info_cookies()
        login_url = self._portal_login_url()
        if not self._has_info_session_cookie():
            self._login_with_password(login_url)
        self._bootstrap_zhjw_session(start_url)
        self._authenticated = True

    def fetch_raw(
        self,
        *,
        endpoint: str | None = None,
        url: str | None = None,
        raw_id: str | None = None,
        **_: Any,
    ) -> list[RawPayload]:
        """Fetch raw JWCH content from an offline path, endpoint, or full URL."""

        if self.config.data_path:
            if not self._authenticated:
                self.authenticate()
            return [self._fetch_from_path(self.config.data_path)]

        target_url = url or self._endpoint_to_url(endpoint)
        if not self._authenticated:
            self.authenticate(start_url=target_url)
        return [self.fetch_url(target_url, raw_id=raw_id)]

    def fetch_url(self, url: str, *, raw_id: str | None = None) -> RawPayload:
        """Fetch one raw JWCH page with the authenticated session."""

        if not self._authenticated:
            self.authenticate(start_url=url)
        if self._session is None:
            raise AdapterError("jwch_adapter authenticated without a session")

        timeout = float(self.config.extra.get("timeout_seconds", 20))
        try:
            response = self._session.get(url, timeout=timeout)
            response.raise_for_status()
        except Exception as exc:
            raise AdapterError(f"jwch_adapter page fetch failed: url={redact_query(url)}") from exc

        content_type = response.headers.get("Content-Type", "text/html").split(";")[0]
        return self.payload(
            raw_id=raw_id or stable_jwch_raw_id(url),
            content=response.text,
            content_type=content_type,
            metadata={"url": response.url, "status_code": response.status_code},
        )

    def _endpoint_to_url(self, endpoint: str | None) -> str:
        if not endpoint:
            raise AdapterError("jwch_adapter requires endpoint or url when JWCH_DATA_PATH is absent")
        base_url = self.require("base_url")
        return urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))

    def _fetch_from_path(self, path: Path) -> RawPayload:
        if not path.exists():
            raise AdapterError(f"jwch_adapter data path does not exist: {path}")
        suffix = path.suffix.lower()
        content_type = {
            ".ics": "text/calendar",
            ".json": "application/json",
        }.get(suffix, "text/html")
        content: str | bytes = path.read_bytes() if suffix == ".ics" else path.read_text(encoding="utf-8")
        return self.payload(
            raw_id=path.stem,
            content=content,
            content_type=content_type,
            metadata={"path": str(path)},
        )

    def _build_session(self) -> Any:
        try:
            import requests
        except ImportError as exc:
            raise AdapterError("jwch_adapter requires requests for network access") from exc

        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": self.config.extra.get(
                    "user_agent",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
                ),
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        return session

    def _portal_login_url(self) -> str:
        explicit = self.config.extra.get("portal_login_url") or self.config.extra.get("info_login_url")
        if explicit:
            return str(explicit)
        legacy_login_url = str(self.config.extra.get("login_url") or "")
        if legacy_login_url and "auth.cic.tsinghua.edu.cn" not in legacy_login_url:
            return legacy_login_url
        return "https://info.tsinghua.edu.cn"

    def _load_info_cookies(self) -> None:
        if self._session is None:
            raise AdapterError("jwch_adapter cookie load failed: session is not initialized")
        cookie_path_value = self.config.extra.get("info_cookie_path") or os.getenv("JWCH_INFO_COOKIE_PATH")
        if not cookie_path_value:
            return
        cookie_path = Path(str(cookie_path_value))
        if not cookie_path.exists():
            raise AdapterError(f"jwch_adapter info cookie path does not exist: {cookie_path}")
        try:
            data = json.loads(cookie_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AdapterError("jwch_adapter info cookie file is not valid JSON") from exc

        if isinstance(data, dict):
            items = [
                {"name": name, "value": value, "domain": "info.tsinghua.edu.cn", "path": "/"}
                for name, value in data.items()
            ]
        elif isinstance(data, list):
            items = data
        else:
            raise AdapterError("jwch_adapter info cookie file must be a JSON object or list")

        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            value = item.get("value")
            if not isinstance(name, str) or not isinstance(value, str):
                continue
            self._session.cookies.set(
                name,
                value,
                domain=str(item.get("domain") or "info.tsinghua.edu.cn"),
                path=str(item.get("path") or "/"),
            )

    def _has_info_session_cookie(self) -> bool:
        if self._session is None:
            return False
        for cookie in self._session.cookies:
            if cookie.domain.endswith("info.tsinghua.edu.cn") and cookie.name == "JSESSIONID":
                return True
        return False

    def _login_with_password(self, start_url: str) -> None:
        if self._session is None:
            raise AdapterError("jwch_adapter login failed: session is not initialized")

        timeout = float(self.config.extra.get("timeout_seconds", 20))
        try:
            entry_page = self._session.get(start_url, timeout=timeout)
            entry_page.raise_for_status()
        except Exception as exc:
            raise AdapterError("jwch_adapter login entry fetch failed") from exc

        auth_url = discover_auth_form_url(entry_page.url, entry_page.text)
        if auth_url:
            try:
                auth_page = self._session.get(auth_url, timeout=timeout)
                auth_page.raise_for_status()
            except Exception as exc:
                raise AdapterError("jwch_adapter auth form fetch failed") from exc
            form_page_url = auth_page.url
            form_page_html = auth_page.text
        else:
            form_page_url = entry_page.url
            form_page_html = entry_page.text

        username_field = str(
            self.config.extra.get("portal_username_field")
            or self.config.extra.get("info_username_field")
            or "i_user"
        )
        password_field = str(
            self.config.extra.get("portal_password_field")
            or self.config.extra.get("info_password_field")
            or "i_pass"
        )
        form = select_login_form(
            form_page_html,
            username_field=username_field,
            password_field=password_field,
        )
        payload = dict(form.get("inputs", {}))
        payload[username_field] = self._credential("username")
        payload[password_field] = self._prepare_password(form_page_url, form_page_html)
        payload.update(load_trusted_device_fields(self.config.extra.get("trust_path")))

        submit_url = urljoin(form_page_url, str(form.get("action") or ""))
        try:
            result = self._session.post(submit_url, data=payload, timeout=timeout)
            result.raise_for_status()
        except Exception as exc:
            raise AdapterError("jwch_adapter login submit failed") from exc

        post_auth_form = discover_post_auth_form(
            result.url,
            result.text,
            allowed_hosts={"info.tsinghua.edu.cn", "zhjw.cic.tsinghua.edu.cn"},
        )
        if post_auth_form:
            method, action_url, inputs = post_auth_form
            try:
                if method == "post":
                    follow = self._session.post(action_url, data=inputs, timeout=timeout)
                else:
                    follow = self._session.get(action_url, params=inputs, timeout=timeout)
                follow.raise_for_status()
            except Exception as exc:
                raise AdapterError("jwch_adapter post-auth form submit failed") from exc
            return

        post_auth_url = discover_post_auth_url(
            result.url,
            result.text,
            allowed_hosts={"zhjw.cic.tsinghua.edu.cn", "info.tsinghua.edu.cn"},
        )
        if post_auth_url:
            try:
                follow = self._session.get(post_auth_url, timeout=timeout)
                follow.raise_for_status()
            except Exception as exc:
                raise AdapterError("jwch_adapter post-auth entry fetch failed") from exc

    def _bootstrap_zhjw_session(self, target_url: str | None) -> None:
        """Use Info's online app redirect to mint a JWCH business session."""

        if self._session is None:
            raise AdapterError("jwch_adapter portal redirect failed: session is not initialized")
        if self._zhjw_session_ready:
            return

        app_id = self._app_id_for_target(target_url)
        if not app_id:
            return

        timeout = float(self.config.extra.get("timeout_seconds", 20))
        portal_base = str(self.config.extra.get("portal_base_url") or "https://info.tsinghua.edu.cn").rstrip("/")
        referer = str(
            self.config.extra.get("portal_referer")
            or portal_base + "/f/info/portal_fg/common/yyfwsearch?searchParam=%E8%80%83%E8%AF%95"
        )
        try:
            context = self._session.get(referer, timeout=timeout)
            context.raise_for_status()
        except Exception as exc:
            raise AdapterError("jwch_adapter portal context page fetch failed") from exc

        redirect_path = str(
            self.config.extra.get("online_app_redirect_path")
            or "/b/yyfw/vyyfwxx/info/portal_fg/common/onlineAppRedirect"
        )
        csrf_token = self._portal_csrf_token()
        redirect_url = portal_base + redirect_path + "?" + urlencode(
            {
                "yyfwid": app_id,
                "machine": str(self.config.extra.get("machine") or "p"),
                "_csrf": csrf_token,
            }
        )
        headers = {
            "Accept": "*/*",
            "Origin": portal_base,
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
            "X-XSRF-TOKEN": csrf_token,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

        try:
            response = self._session.post(redirect_url, headers=headers, timeout=timeout)
            response.raise_for_status()
            data = response.json()
        except json.JSONDecodeError as exc:
            raise AdapterError("jwch_adapter portal redirect returned non-JSON response") from exc
        except Exception as exc:
            raise AdapterError("jwch_adapter portal redirect request failed") from exc

        roaming_url = extract_roaming_url(data)
        if not roaming_url:
            raise AdapterError(
                "jwch_adapter portal redirect response did not include a roaming URL: "
                + describe_redirect_response(data)
            )

        try:
            entry = self._session.get(roaming_url, timeout=timeout)
            entry.raise_for_status()
        except Exception as exc:
            raise AdapterError(f"jwch_adapter JWCH roaming entry failed: url={redact_query(roaming_url)}") from exc
        self._zhjw_session_ready = True

    def _app_id_for_target(self, target_url: str | None) -> str | None:
        if target_url and "bks_yjkbSearch" in target_url:
            return str(
                self.config.extra.get("schedule_app_id")
                or self.default_schedule_app_id
            )
        return str(
            self.config.extra.get("exam_app_id")
            or self.config.extra.get("default_app_id")
            or self.default_exam_app_id
        )

    def _portal_csrf_token(self) -> str:
        if self._session is None:
            raise AdapterError("jwch_adapter csrf discovery failed: session is not initialized")
        cookie_value = self._session.cookies.get("XSRF-TOKEN", domain="info.tsinghua.edu.cn")
        if cookie_value:
            return str(cookie_value)
        for cookie in self._session.cookies:
            if cookie.name == "XSRF-TOKEN" and cookie.value:
                return str(cookie.value)
        raise AdapterError("jwch_adapter portal redirect requires XSRF-TOKEN from Info login")

    def _prepare_password(self, form_page_url: str, form_page_html: str) -> str:
        public_key = extract_sm2_public_key(form_page_html)
        if not public_key:
            return self._credential("password")

        sm2_url = discover_sm2_script_url(form_page_url, form_page_html)
        if not sm2_url:
            raise AdapterError("jwch_adapter login failed: SM2 script URL missing")

        timeout = float(self.config.extra.get("timeout_seconds", 20))
        try:
            sm2_response = self._session.get(sm2_url, timeout=timeout)
            sm2_response.raise_for_status()
        except Exception as exc:
            raise AdapterError("jwch_adapter login failed: SM2 script fetch failed") from exc
        return encrypt_password_with_node_sm2(
            password=self._credential("password"),
            public_key=public_key,
            sm2_javascript=sm2_response.text,
        )

    def _credential(self, field_name: str) -> str:
        value = getattr(self.config, field_name)
        if value:
            return str(value)
        fallback = os.getenv(f"LEARN_{field_name.upper()}")
        if fallback:
            return fallback
        raise AdapterError(f"jwch_adapter missing required config: {field_name}")


def discover_post_auth_url(current_url: str, html: str, *, allowed_hosts: set[str]) -> str | None:
    """Find a post-auth service URL carrying a temporary ticket or redirect."""

    candidates: list[str] = []
    for attr in ("href", "src", "action"):
        candidates.extend(re.findall(attr + r'=["\']([^"\']+)', html, flags=re.IGNORECASE))
    candidates.extend(re.findall(r'["\'](https?://[^"\']+)["\']', html, flags=re.IGNORECASE))
    for candidate in candidates:
        absolute = urljoin(current_url, candidate)
        parsed = urlparse(absolute)
        if parsed.hostname in allowed_hosts:
            return absolute
    return None


def discover_post_auth_form(
    current_url: str,
    html: str,
    *,
    allowed_hosts: set[str],
) -> tuple[str, str, dict[str, str]] | None:
    """Find an auto-submit form that returns the session to an allowed service."""

    parser = _FormCollector()
    parser.feed(html)
    for form in parser.forms:
        action_url = urljoin(current_url, str(form.get("action") or ""))
        if urlparse(action_url).hostname in allowed_hosts:
            return (
                str(form.get("method") or "get").lower(),
                action_url,
                dict(form.get("inputs", {})),
            )
    return None


def extract_roaming_url(data: dict[str, Any]) -> str | None:
    """Extract the one-time JWCH roaming URL from Info's redirect JSON."""

    if data.get("result") != "success":
        return None
    obj = data.get("object")
    if not isinstance(obj, dict):
        return None
    roaming_url = obj.get("roamingurl")
    if not isinstance(roaming_url, str) or not roaming_url:
        return None
    return html.unescape(roaming_url)


def describe_redirect_response(data: dict[str, Any]) -> str:
    """Return a redacted shape summary for Info redirect diagnostics."""

    obj = data.get("object")
    object_keys = sorted(obj.keys()) if isinstance(obj, dict) else []
    return (
        f"result={data.get('result')!r} "
        f"msg={redact_sensitive_text(str(data.get('msg') or ''))!r} "
        f"object_type={type(obj).__name__} "
        f"object_keys={object_keys}"
    )


def redact_sensitive_text(value: str) -> str:
    redacted = re.sub(r"(?i)(ticket|token|code|csrf|sessionid)=([^&\s]+)", r"\1=<redacted>", value)
    if len(redacted) > 120:
        return redacted[:117] + "..."
    return redacted


def stable_jwch_raw_id(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.strip("/").replace("/", "_") or "root"
    if "bks_ksSearch" in parsed.query:
        return "jwch_exam"
    if "bks_yjkbSearch" in parsed.query:
        return "jwch_schedule"
    return f"jwch_{path}"


def redact_query(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(query="<redacted>" if parsed.query else "").geturl()
