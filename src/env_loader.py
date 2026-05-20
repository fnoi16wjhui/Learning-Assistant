"""Environment loading helpers that never print credential values."""

from __future__ import annotations

from pathlib import Path


def load_project_env(path: str | Path = ".env") -> str | None:
    """Load .env with common Windows-safe encodings.

    Returns the encoding that succeeded, or None when python-dotenv is absent
    or no .env file exists.
    """

    try:
        from dotenv import load_dotenv
    except ImportError:
        return None

    env_path = Path(path)
    if not env_path.exists():
        return None

    last_error: UnicodeDecodeError | None = None
    for encoding in ("utf-8", "utf-8-sig", "gbk", "cp936"):
        try:
            load_dotenv(env_path, encoding=encoding)
            return encoding
        except UnicodeDecodeError as exc:
            last_error = exc

    if last_error is not None:
        raise last_error
    return None
