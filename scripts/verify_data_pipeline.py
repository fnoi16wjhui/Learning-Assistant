"""Verify real-vs-demo data pipeline state for local debugging."""

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
        ("GET", "/api/debug/data-source", None),
        ("GET", "/api/debug/sync-errors", None),
        ("GET", "/api/sync/jobs/latest", None),
        ("GET", "/api/dashboard?demo_mode=false", None),
    ]
    for method, path, body in checks:
        response = client.request(method, path, json=body)
        print(f"{method} {path} -> {response.status_code}")
        if response.status_code >= 400:
            print(response.text)
            return 1

    data = client.get("/api/debug/data-source").json()
    print(json.dumps(data, ensure_ascii=False, indent=2))
    task_source = data.get("tasks", {}).get("source")
    if task_source == "real":
        print("PASS: task data source is real")
        return 0
    print(f"WARN: task data source is {task_source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
