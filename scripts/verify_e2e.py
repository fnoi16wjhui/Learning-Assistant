"""End-to-end verification with PASS / WARN / BLOCKED grading."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from backend.app.env_manager import settings_status
from backend.app.main import app
from backend.app.settings import settings


def grade(name: str, level: str, message: str) -> dict[str, str]:
    return {"name": name, "level": level, "message": message}


def main() -> int:
    results: list[dict[str, str]] = []
    client = TestClient(app)

    cfg = settings_status()
    if cfg["env_file_exists"]:
        results.append(grade("env_file", "PASS", ".env 文件存在"))
    else:
        results.append(grade("env_file", "WARN", ".env 不存在，可使用设置页或 bootstrap 创建"))

    learn_ok = cfg["fields"]["LEARN_USERNAME"]["configured"] and cfg["fields"]["LEARN_PASSWORD"]["configured"]
    results.append(
        grade("learn_credentials", "PASS" if learn_ok else "WARN", "学堂账号已配置" if learn_ok else "学堂账号未配置")
    )

    llm_ok = cfg["fields"]["LLM_D_API_KEY"]["configured"]
    results.append(
        grade("llm_api_key", "PASS" if llm_ok else "BLOCKED:D", "LLM API Key 已配置" if llm_ok else "LLM API Key 未配置")
    )

    collector = settings.collector_jsonl.exists() or settings.demo_collector_jsonl.exists()
    results.append(
        grade("collector_jsonl", "PASS" if collector else "WARN", "任务 JSONL 可用" if collector else "缺少任务数据")
    )

    chunks = settings.material_chunks_jsonl.exists() or settings.demo_material_chunks_jsonl.exists()
    results.append(
        grade("material_chunks", "PASS" if chunks else "WARN", "资料分块可用" if chunks else "缺少 material_chunks.jsonl")
    )

    api_checks = [
        ("GET", "/api/health", None),
        ("GET", "/api/dashboard", None),
        ("GET", "/api/settings/status", None),
        ("GET", "/api/system/check", None),
        ("GET", "/api/debug/data-source", None),
        ("GET", "/api/debug/sync-errors", None),
        ("GET", "/api/sync/jobs/latest", None),
        ("GET", "/api/tasks", None),
        ("GET", "/api/materials", None),
        ("GET", "/api/knowledge/status", None),
        ("POST", "/api/retrieval/search", {"query": "课程", "top_k": 3, "mode": "keyword"}),
    ]
    for method, path, body in api_checks:
        response = client.request(method, path, json=body)
        if response.status_code >= 400:
            results.append(grade(f"api:{path}", "BLOCKED:E", f"{method} {path} -> {response.status_code}"))
        else:
            results.append(grade(f"api:{path}", "PASS", f"{method} {path} -> {response.status_code}"))

    if llm_ok:
        for path, body in [
            ("/api/qa", {"question": "Demo question"}),
            ("/api/summaries", {"topic": "Demo topic"}),
            ("/api/homework-assistant", {"question": "Demo homework"}),
        ]:
            response = client.post(path, json=body)
            payload = response.json()
            status = payload.get("status", "ready")
            if response.status_code >= 400:
                results.append(grade(f"api:{path}", "BLOCKED:D", f"HTTP {response.status_code}"))
            elif status == "blocked":
                code = payload.get("error_code", "blocked")
                results.append(grade(f"api:{path}", "BLOCKED:D", f"D 模块阻塞: {code}"))
            else:
                results.append(grade(f"api:{path}", "PASS", "D 模块响应正常"))
    else:
        results.append(grade("d_module_skipped", "BLOCKED:D", "未配置 LLM，跳过 D 智能应用验收"))

    overall = "PASS"
    if any(item["level"].startswith("BLOCKED") for item in results):
        overall = "BLOCKED"
    elif any(item["level"] == "WARN" for item in results):
        overall = "WARN"

    report = {"overall": overall, "results": results}
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if overall == "BLOCKED" and not llm_ok:
        return 0
    return 0 if overall in {"PASS", "WARN"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
