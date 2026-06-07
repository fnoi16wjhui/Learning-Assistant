"""Local .env read/write with masking — never returns plaintext secrets to clients."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from backend.app.settings import settings

# Known keys the settings API may read or incrementally update.
KNOWN_ENV_KEYS = [
    "LEARN_USERNAME",
    "LEARN_PASSWORD",
    "LEARN_BASE_URL",
    "LEARN_EXTRA_JSON",
    "MAIL_USERNAME",
    "MAIL_PASSWORD",
    "MAIL_BASE_URL",
    "MAIL_EXTRA_JSON",
    "JWCH_USERNAME",
    "JWCH_PASSWORD",
    "JWCH_BASE_URL",
    "JWCH_EXAM_URL",
    "JWCH_SCHEDULE_URL",
    "JWCH_EXTRA_JSON",
    "LLM_D_BASE_URL",
    "LLM_D_API_KEY",
    "LLM_D_MODEL",
    "LLM_D_TIMEOUT",
    "CURRENT_SEMESTER_START",
    "LEARNING_ASSISTANT_ROOT",
]

SECRET_KEYS = {
    "LEARN_PASSWORD",
    "MAIL_PASSWORD",
    "JWCH_PASSWORD",
    "LLM_D_API_KEY",
}

# Fill missing adapter endpoints from the project template.
DEFAULT_ENV_VALUES = {
    "LEARN_BASE_URL": "https://learn.tsinghua.edu.cn",
    "LEARN_EXTRA_JSON": '{"login_url":"https://learn.tsinghua.edu.cn","username_field":"i_user","password_field":"i_pass"}',
    "MAIL_BASE_URL": "mails.tsinghua.edu.cn",
    "MAIL_EXTRA_JSON": '{"imap_port":993,"use_ssl":true,"timeout_seconds":20}',
    "JWCH_BASE_URL": "https://zhjw.cic.tsinghua.edu.cn",
    "JWCH_EXAM_URL": "https://zhjw.cic.tsinghua.edu.cn/jxmh.do?url=/jxmh.do&m=bks_ksSearch",
    "JWCH_SCHEDULE_URL": "https://zhjw.cic.tsinghua.edu.cn/portal3rd.do?url=/portal3rd.do&m=bks_yjkbSearch",
    "JWCH_EXTRA_JSON": '{"login_url":"https://info.tsinghua.edu.cn","username_field":"i_user","password_field":"i_pass","trust_path":"storage/learn_trust_device.json","exam_app_id":"81008AA5A89C20D5BDBBDF719D5F0A94","schedule_app_id":"287C0C6D90ABB364CD5FDF1495199962","timeout_seconds":20}',
    "LLM_D_BASE_URL": "https://api.deepseek.com",
    "LLM_D_MODEL": "deepseek-v4-pro",
    "LLM_D_TIMEOUT": "60",
}


def env_file_path() -> Path:
    return settings.project_root / ".env"


def mask_secret(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if len(value) <= 4:
        return "****"
    return f"{value[:2]}****{value[-4:]}"


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, rest = line.partition("=")
        key = key.strip()
        value = rest.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        values[key] = value
    return values


def ensure_env_defaults(path: Path | None = None) -> list[str]:
    """Backfill known default env values without overwriting user settings."""
    target = path or env_file_path()
    existing = parse_env_file(target)
    updates: dict[str, str | None] = {}
    for key, value in DEFAULT_ENV_VALUES.items():
        if not str(existing.get(key, "")).strip():
            updates[key] = value
    if not updates:
        return []
    write_env_incremental(updates, path=target)
    return list(updates.keys())


def write_env_incremental(updates: dict[str, str | None], path: Path | None = None) -> dict[str, Any]:
    """Merge known keys into .env without overwriting unspecified fields."""
    target = path or env_file_path()
    existing = parse_env_file(target)
    changed: list[str] = []

    for key, value in updates.items():
        if key not in KNOWN_ENV_KEYS:
            continue
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        existing[key] = str(value).strip()
        changed.append(key)

    lines: list[str] = []
    if target.exists():
        for raw_line in target.read_text(encoding="utf-8").splitlines():
            stripped = raw_line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in existing and key in changed:
                    lines.append(f"{key}={existing[key]}")
                    changed.remove(key)
                    continue
            lines.append(raw_line)
    else:
        lines.append("# Learning Assistant local configuration")
        lines.append("# Auto-managed by E module settings API — do not commit")

    for key in changed:
        lines.append(f"{key}={existing[key]}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Refresh process env for keys we just wrote
    for key in updates:
        if key in existing:
            os.environ[key] = existing[key]

    return {"written": list(updates.keys()), "path": str(target)}


def settings_status() -> dict[str, Any]:
    """Return configuration status with masked values only."""
    path = env_file_path()
    file_values = parse_env_file(path)
    fields: dict[str, Any] = {}

    for key in KNOWN_ENV_KEYS:
        env_value = os.getenv(key, file_values.get(key, ""))
        configured = bool(str(env_value or "").strip())
        entry: dict[str, Any] = {"configured": configured}
        if key in SECRET_KEYS:
            entry["masked"] = mask_secret(str(env_value)) if configured else ""
        elif configured:
            entry["value"] = str(env_value)
        fields[key] = entry

    data_paths = {
        "collector_jsonl": settings.collector_jsonl.exists(),
        "material_chunks_jsonl": settings.material_chunks_jsonl.exists(),
        "knowledge_index_dir": settings.knowledge_index_dir.exists(),
        "demo_collector_jsonl": settings.demo_collector_jsonl.exists(),
        "demo_material_chunks_jsonl": settings.demo_material_chunks_jsonl.exists(),
    }

    return {
        "env_file_exists": path.exists(),
        "env_file_path": str(path),
        "fields": fields,
        "data_paths": data_paths,
        "semester_start": settings.semester_start,
        "source_module": "E",
        "status": "ready",
    }


def bootstrap_from_txt_files() -> dict[str, Any]:
    """Best-effort bootstrap from local credential txt files (never committed)."""
    root = settings.project_root
    updates: dict[str, str | None] = {}
    imported: list[str] = []

    learn_txt = root / "网络学堂账号和密码.txt"
    if learn_txt.exists():
        text = learn_txt.read_text(encoding="utf-8")
        user_match = re.search(r"(?:账号|用户名|username)\s*[:：]\s*(\S+)", text, re.I)
        pass_match = re.search(r"(?:密码|password)\s*[:：]\s*(\S+)", text, re.I)
        if user_match:
            updates["LEARN_USERNAME"] = user_match.group(1)
        if pass_match:
            updates["LEARN_PASSWORD"] = pass_match.group(1)
        if updates:
            imported.append(str(learn_txt.name))

    api_txt = root / "ds-for-cursor.txt"
    if api_txt.exists():
        text = api_txt.read_text(encoding="utf-8").strip()
        key_match = re.search(r"(?:sk-[A-Za-z0-9_-]+)", text)
        if key_match:
            updates["LLM_D_API_KEY"] = key_match.group(0)
            imported.append(str(api_txt.name))
        elif text and not text.startswith("#"):
            updates["LLM_D_API_KEY"] = text.splitlines()[0].strip()
            imported.append(str(api_txt.name))

    if not updates:
        filled = ensure_env_defaults()
        return {
            "imported_from": [],
            "message": "No local txt credential files found.",
            "defaults_filled": filled,
            "status": "ready",
        }

    write_env_incremental(updates)
    filled = ensure_env_defaults()
    return {
        "imported_from": imported,
        "message": f"Imported {len(imported)} local credential file(s) into .env.",
        "defaults_filled": filled,
        "status": "ready",
        "source_module": "E",
    }
