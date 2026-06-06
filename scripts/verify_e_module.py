"""Smoke checks for the E-module backend API."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from backend.app.main import app


def main() -> int:
    client = TestClient(app)
    checks = [
        ("GET", "/api/health", None),
        ("GET", "/api/dashboard", None),
        ("GET", "/api/settings/status", None),
        ("GET", "/api/system/check", None),
        ("GET", "/api/tasks", None),
        ("GET", "/api/materials", None),
        ("GET", "/api/knowledge/status", None),
        ("POST", "/api/retrieval/search", {"query": "demo", "top_k": 3, "mode": "keyword"}),
        ("POST", "/api/qa", {"question": "Demo question"}),
        ("POST", "/api/summaries", {"topic": "Demo topic"}),
        ("POST", "/api/homework-assistant", {"question": "Demo homework"}),
    ]
    for method, path, body in checks:
        response = client.request(method, path, json=body)
        print(f"{method} {path} -> {response.status_code}")
        if response.status_code >= 400:
            print(response.text)
            return 1
    print(json.dumps(client.get("/api/health").json(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
