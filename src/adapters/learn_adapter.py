"""Network Learn adapter for raw HTML and JSON fetching."""

from __future__ import annotations

import re
import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from .base_adapter import AdapterConfig, AdapterError, BaseAdapter, RawPayload


class _LoginFormParser(HTMLParser):
    """Collect the first HTML form and its input defaults."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "form":
            self._current = {
                "action": attr_map.get("action", ""),
                "method": attr_map.get("method", "post").lower(),
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


class LearnAdapter(BaseAdapter):
    """Fetch raw Learn pages or API responses without parsing them."""

    source = "learn"
    env_prefix = "LEARN"

    def __init__(self, config: AdapterConfig | None = None, *, session: Any | None = None) -> None:
        super().__init__(config)
        self._session = session
        self._authenticated = False

    def authenticate(self) -> None:
        """Validate credentials and prepare a Learn HTTP session."""

        if self.config.token:
            self._session = self._session or self._build_session()
            self._session.headers.update({"Authorization": f"Bearer {self.config.token}"})
            self._authenticated = True
            return
        if self.config.username and self.config.password:
            self._session = self._session or self._build_session()
            self._login_with_password()
            self._authenticated = True
            return
        if self.config.data_path:
            self._authenticated = True
            return
        raise AdapterError("learn_adapter missing credentials or LEARN_DATA_PATH")

    def fetch_raw(
        self,
        *,
        endpoint: str | None = None,
        raw_id: str | None = None,
        content_type: str | None = None,
        **_: Any,
    ) -> list[RawPayload]:
        """Fetch raw Learn content from an offline path or HTTP endpoint."""

        if not self._authenticated:
            self.authenticate()

        if self.config.data_path:
            return [self._fetch_from_path(self.config.data_path, raw_id=raw_id)]

        if not endpoint:
            raise AdapterError("learn_adapter requires endpoint when LEARN_DATA_PATH is absent")
        return [
            self.fetch_endpoint(
                endpoint,
                raw_id=raw_id,
                content_type=content_type,
            )
        ]

    def fetch_endpoint(
        self,
        endpoint: str,
        *,
        raw_id: str | None = None,
        content_type: str | None = None,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        method: str = "get",
    ) -> RawPayload:
        """Fetch one raw Learn endpoint with the authenticated session."""

        if not self._authenticated:
            self.authenticate()
        if self._session is None:
            raise AdapterError("learn_adapter authenticated without a session")

        base_url = self.require("base_url")
        url = urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))
        timeout = float(self.config.extra.get("timeout_seconds", 20))
        try:
            if method.lower() == "post":
                response = self._session.post(url, params=params, data=data, headers=self._request_headers(), timeout=timeout)
            else:
                response = self._session.get(url, params=params, headers=self._request_headers(), timeout=timeout)
            response.raise_for_status()
        except Exception as exc:
            raise AdapterError(f"learn_adapter endpoint fetch failed: endpoint={endpoint}") from exc

        detected_type = content_type or response.headers.get("Content-Type", "text/html").split(";")[0]
        return self.payload(
            raw_id=raw_id or stable_endpoint_raw_id(endpoint, params),
            content=response.text,
            content_type=detected_type,
            metadata={
                "endpoint": endpoint,
                "method": method.lower(),
                "url": response.url,
                "status_code": response.status_code,
            },
        )

    def _request_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"X-Requested-With": "XMLHttpRequest"}
        csrf_token = self._csrf_token()
        if csrf_token:
            headers["X-XSRF-TOKEN"] = csrf_token
        return headers

    def _csrf_token(self) -> str | None:
        if self._session is None:
            return None
        token = self._session.cookies.get("XSRF-TOKEN", domain="learn.tsinghua.edu.cn")
        if token:
            return str(token)
        for cookie in self._session.cookies:
            if cookie.name == "XSRF-TOKEN" and cookie.value:
                return str(cookie.value)
        return None

    def _fetch_from_path(self, path: Path, *, raw_id: str | None) -> RawPayload:
        if not path.exists():
            raise AdapterError(f"learn_adapter data path does not exist: {path}")
        content = path.read_text(encoding="utf-8")
        suffix = path.suffix.lower()
        guessed_type = "application/json" if suffix == ".json" else "text/html"
        return self.payload(
            raw_id=raw_id or path.stem,
            content=content,
            content_type=guessed_type,
            metadata={"path": str(path)},
        )

    def _build_session(self) -> Any:
        try:
            import requests
        except ImportError as exc:
            raise AdapterError("learn_adapter requires requests for network access") from exc

        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": self.config.extra.get(
                    "user_agent",
                    "CourseAgentCollector/0.1 (+https://learn.tsinghua.edu.cn)",
                )
            }
        )
        return session

    def _login_with_password(self) -> None:
        if self._session is None:
            raise AdapterError("learn_adapter login failed: session is not initialized")

        base_url = self.require("base_url")
        login_url = str(self.config.extra.get("login_url") or base_url)
        timeout = float(self.config.extra.get("timeout_seconds", 20))
        try:
            login_page = self._session.get(login_url, timeout=timeout)
            login_page.raise_for_status()
        except Exception as exc:
            raise AdapterError("learn_adapter login page fetch failed") from exc

        username_field = str(self.config.extra.get("username_field") or "i_user")
        password_field = str(self.config.extra.get("password_field") or "i_pass")
        form_page_url, form_page_html = self._resolve_login_form_page(login_page.url, login_page.text)
        form = select_login_form(
            form_page_html,
            username_field=username_field,
            password_field=password_field,
        )
        action = form.get("action") or login_url
        method = str(form.get("method") or "post").lower()
        payload = dict(form.get("inputs", {}))
        payload[username_field] = self.require("username")
        payload[password_field] = self._prepare_password(form_page_url, form_page_html)
        payload.update(load_trusted_device_fields(self.config.extra.get("trust_path")))
        submit_url = urljoin(form_page_url, str(action))

        try:
            if method == "get":
                result = self._session.get(submit_url, params=payload, timeout=timeout)
            else:
                result = self._session.post(submit_url, data=payload, timeout=timeout)
            result.raise_for_status()
        except Exception as exc:
            raise AdapterError("learn_adapter login submit failed") from exc

        failure_markers = self.config.extra.get("failure_markers", ["invalid", "error", "密码错误"])
        page_text = result.text.lower()
        if any(str(marker).lower() in page_text for marker in failure_markers):
            raise AdapterError("learn_adapter login failed: server returned a failure marker")

        roaming_url = discover_learn_roaming_url(result.url, result.text)
        if roaming_url:
            try:
                roaming = self._session.get(roaming_url, timeout=timeout)
                roaming.raise_for_status()
            except Exception as exc:
                raise AdapterError("learn_adapter login failed: roaming entry fetch failed") from exc

    def _resolve_login_form_page(self, current_url: str, html: str) -> tuple[str, str]:
        username_field = str(self.config.extra.get("username_field") or "i_user")
        password_field = str(self.config.extra.get("password_field") or "i_pass")
        if form_has_fields(html, username_field=username_field, password_field=password_field):
            return current_url, html

        auth_url = discover_auth_form_url(current_url, html)
        if not auth_url:
            return current_url, html

        timeout = float(self.config.extra.get("timeout_seconds", 20))
        try:
            response = self._session.get(auth_url, timeout=timeout)
            response.raise_for_status()
        except Exception as exc:
            raise AdapterError("learn_adapter auth form fetch failed") from exc
        return response.url, response.text

    def _prepare_password(self, form_page_url: str, form_page_html: str) -> str:
        public_key = extract_sm2_public_key(form_page_html)
        if not public_key:
            return self.require("password")

        sm2_url = discover_sm2_script_url(form_page_url, form_page_html)
        if not sm2_url:
            raise AdapterError("learn_adapter login failed: SM2 script URL missing")

        timeout = float(self.config.extra.get("timeout_seconds", 20))
        try:
            sm2_response = self._session.get(sm2_url, timeout=timeout)
            sm2_response.raise_for_status()
        except Exception as exc:
            raise AdapterError("learn_adapter login failed: SM2 script fetch failed") from exc
        return encrypt_password_with_node_sm2(
            password=self.require("password"),
            public_key=public_key,
            sm2_javascript=sm2_response.text,
        )


def select_login_form(
    html: str,
    *,
    username_field: str = "i_user",
    password_field: str = "i_pass",
) -> dict[str, Any]:
    """Return the form containing credential fields, falling back to first form."""

    parser = _LoginFormParser()
    parser.feed(html)
    if not parser.forms:
        return {"action": "", "method": "post", "inputs": {}}
    for form in parser.forms:
        inputs = form.get("inputs", {})
        if username_field in inputs and password_field in inputs:
            return form
    return parser.forms[0]


def form_has_fields(html: str, *, username_field: str, password_field: str) -> bool:
    form = select_login_form(
        html,
        username_field=username_field,
        password_field=password_field,
    )
    inputs = form.get("inputs", {})
    return username_field in inputs and password_field in inputs


def discover_auth_form_url(current_url: str, html: str) -> str | None:
    """Find a linked Tsinghua ID auth form URL from the Learn login page."""

    candidates: list[str] = []
    for attr in ("href", "src", "action"):
        candidates.extend(re.findall(attr + r"=[\"']([^\"']+)", html, flags=re.IGNORECASE))
    for candidate in candidates:
        absolute = urljoin(current_url, candidate)
        lowered = absolute.lower()
        if "id.tsinghua.edu.cn" in lowered and "/auth/login/form/" in lowered:
            return absolute
    return None


def extract_sm2_public_key(html: str) -> str | None:
    match = re.search(r'id=["\']sm2publicKey["\'][^>]*>([^<]+)', html, flags=re.IGNORECASE)
    return match.group(1).strip() if match else None


def discover_sm2_script_url(current_url: str, html: str) -> str | None:
    for src in re.findall(r'<script[^>]+src=["\']([^"\']+)', html, flags=re.IGNORECASE):
        if "sm2Util.js" in src:
            return urljoin(current_url, src)
    return None


def discover_learn_roaming_url(current_url: str, html: str) -> str | None:
    """Find the post-auth Learn roaming entry carrying the temporary ticket."""

    for href in re.findall(r'href=["\']([^"\']+)', html, flags=re.IGNORECASE):
        absolute = urljoin(current_url, href)
        if "learn.tsinghua.edu.cn" in absolute and "j_spring_security_thauth_roaming_entry" in absolute:
            return absolute
    return None


def encrypt_password_with_node_sm2(*, password: str, public_key: str, sm2_javascript: str) -> str:
    """Run the official page SM2 helper in Node without exposing secrets in argv."""

    node_code = """
const fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
eval(input.sm2Javascript);
const encrypted = sm2Util.doEncryptStr(input.password, input.publicKey);
process.stdout.write(encrypted);
"""
    try:
        completed = subprocess.run(
            ["node", "-e", node_code],
            input=json.dumps(
                {
                    "password": password,
                    "publicKey": public_key,
                    "sm2Javascript": sm2_javascript,
                }
            ),
            text=True,
            capture_output=True,
            timeout=20,
            check=True,
        )
    except FileNotFoundError as exc:
        raise AdapterError("learn_adapter login failed: Node.js is required for SM2 encryption") from exc
    except subprocess.SubprocessError as exc:
        raise AdapterError("learn_adapter login failed: SM2 encryption subprocess failed") from exc

    encrypted = completed.stdout.strip()
    if not encrypted:
        raise AdapterError("learn_adapter login failed: SM2 encryption returned empty output")
    return encrypted


def load_trusted_device_fields(path_value: Any = None) -> dict[str, str]:
    """Load locally saved double-auth trust material for future logins."""

    trust_path = Path(path_value or "storage/learn_trust_device.json")
    if not trust_path.exists():
        return {}
    try:
        data = json.loads(trust_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    trusted_value = data.get("object")
    if not isinstance(trusted_value, str) or not trusted_value:
        return {}
    return {
        "fingerGenPrint": trusted_value,
        "deviceName": "CourseAgentCollector",
    }


def stable_endpoint_raw_id(endpoint: str, params: dict[str, str] | None) -> str:
    """Build a stable raw ID for endpoint probes when upstream IDs are absent."""

    endpoint_key = endpoint.strip("/").replace("/", "_") or "root"
    if not params:
        return f"learn_endpoint_{endpoint_key}"
    encoded_params = "_".join(f"{key}={value}" for key, value in sorted(params.items()))
    return f"learn_endpoint_{endpoint_key}_{encoded_params}"
