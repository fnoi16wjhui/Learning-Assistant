"""FastAPI entry point for the E-module integration backend."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Load .env before any adapter imports so env vars are available at module level
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.adapters.module_a import ModuleAAdapter
from backend.app.adapters.module_b import ModuleBAdapter
from backend.app.adapters.module_c import ModuleCAdapter
from backend.app.adapters.module_d import ModuleDAdapter
from backend.app.models import HomeworkAssistantRequest, QARequest, RetrievalRequest, SummaryRequest, SyncRequest
from backend.app.settings import settings


app = FastAPI(title="Learning Assistant E Module API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
static_dir = settings.project_root / "frontend_static"
if static_dir.exists():
    app.mount("/app", StaticFiles(directory=static_dir, html=True), name="app")

module_a = ModuleAAdapter()
module_b = ModuleBAdapter()
module_c = ModuleCAdapter()
module_d = ModuleDAdapter()


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "modules": {
            "A": module_a.sync_status()["status"],
            "B": module_b.parse_status()["status"],
            "C": module_c.status()["status"],
            "D": module_d.status(),
            "E": "ready",
        },
    }


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    tasks = module_a.task_records()
    schedules = module_a.schedule_records()
    materials = module_b.materials()
    homework = [item for item in tasks if item.get("task_type") == "homework"]
    return {
        "stats": {
            "task_count": len(tasks),
            "schedule_count": len(schedules),
            "material_count": len(materials),
            "pending_homework_count": len(homework),
        },
        "recent_tasks": tasks[:5],
        "sync_status": module_a.sync_status()["items"],
        "material_status": module_b.parse_status(),
        "knowledge_status": module_c.status(),
        "source_module": "E",
        "status": "ready",
    }


@app.get("/api/tasks")
def tasks(
    course_name: str | None = None,
    task_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    records = module_a.task_records()
    if course_name:
        records = [record for record in records if record.get("course_name") == course_name]
    if task_type:
        records = [record for record in records if record.get("task_type") == task_type]
    return {"items": records[:limit], "total": len(records), "source_module": "A", "status": "ready"}


@app.get("/api/schedules")
def schedules(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    records = module_a.schedule_records()
    return {"items": records[:limit], "total": len(records), "source_module": "A", "status": "ready"}


@app.post("/api/sync/run")
def run_sync(request: SyncRequest) -> dict[str, Any]:
    if not request.allow_network:
        return {
            "status": "queued",
            "message": "Dry-run sync accepted. Set allow_network=true to run real collector sync.",
            "source_module": "A",
        }

    command = [
        sys.executable,
        str(settings.project_root / "main.py"),
        "--channel",
        request.channel,
        "--allow-network",
        "--output",
        str(settings.collector_jsonl),
    ]
    subprocess.Popen(command, cwd=settings.project_root)
    return {"status": "queued", "message": "Network sync started in background.", "source_module": "A"}


@app.get("/api/sync/status")
def sync_status() -> dict[str, Any]:
    return module_a.sync_status()


@app.get("/api/materials")
def materials(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    records = module_b.materials()
    return {"items": records[:limit], "total": len(records), "source_module": "B", "status": module_b.parse_status()["status"]}


@app.post("/api/materials/upload")
def upload_material() -> dict[str, Any]:
    return {
        "status": "accepted",
        "message": "Upload endpoint is reserved for B module parser integration.",
        "source_module": "B",
    }


@app.get("/api/materials/parse-status")
def parse_status() -> dict[str, Any]:
    return module_b.parse_status()


@app.get("/api/knowledge/status")
def knowledge_status() -> dict[str, Any]:
    return module_c.status()


@app.post("/api/retrieval/search")
def retrieval_search(request: RetrievalRequest) -> dict[str, Any]:
    return module_c.search(request)


@app.post("/api/qa")
def qa(request: QARequest) -> dict[str, Any]:
    return module_d.qa(request)


@app.post("/api/summaries")
def summaries(request: SummaryRequest) -> dict[str, Any]:
    return module_d.summarize(request)


@app.post("/api/homework-assistant")
def homework_assistant(request: HomeworkAssistantRequest) -> dict[str, Any]:
    return module_d.homework_assistant(request)


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Learning Assistant E Module API", "docs": "/docs"}
