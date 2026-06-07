"""FastAPI entry point for the E-module integration backend."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Load .env before any adapter imports so env vars are available at module level
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.adapters.module_a import ModuleAAdapter
from backend.app.adapters.module_b import ModuleBAdapter
from backend.app.adapters.module_c import ModuleCAdapter
from backend.app.adapters.module_d import ModuleDAdapter
from backend.app.data_source import material_data_source, task_data_source
from backend.app.debug_api import debug_data_source, debug_sync_errors
from backend.app.env_manager import bootstrap_from_txt_files, ensure_env_defaults, settings_status, write_env_incremental
from backend.app.sync_jobs import get_job, get_latest_job, start_sync_job
from backend.app.models import (
    HomeworkAssistantRequest,
    KnowledgeRebuildRequest,
    LocalSettingsRequest,
    MaterialExportRequest,
    MaterialParseRequest,
    QARequest,
    RetrievalRequest,
    SummaryRequest,
    SyncRequest,
)
from backend.app.response_utils import module_response, normalize_list_items, safe_call
from backend.app.settings import settings
from src.materials.extractors import MaterialParseError, extractor_for_path


app = FastAPI(title="Learning Assistant E Module API", version="0.2.0")
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


def _run_background(command: list[str], cwd: Path | None = None) -> dict[str, Any]:
    subprocess.Popen(command, cwd=cwd or settings.project_root)
    return {"status": "queued", "command": " ".join(command)}


@app.get("/api/health")
def health() -> dict[str, Any]:
    d_status = module_d.status()
    return {
        "status": "ok",
        "modules": {
            "A": module_a.sync_status()["status"],
            "B": module_b.parse_status()["status"],
            "C": module_c.status()["status"],
            "D": d_status.get("status", "missing"),
            "E": "ready",
        },
        "module_details": {
            "D": d_status,
        },
        "source_module": "E",
    }


@app.get("/api/settings/status")
def get_settings_status() -> dict[str, Any]:
    return settings_status()


@app.post("/api/settings/local")
def save_local_settings(request: LocalSettingsRequest) -> dict[str, Any]:
    updates = {
        "LEARN_USERNAME": request.learn_username,
        "LEARN_PASSWORD": request.learn_password,
        "MAIL_USERNAME": request.mail_username,
        "MAIL_PASSWORD": request.mail_password,
        "JWCH_USERNAME": request.jwch_username,
        "JWCH_PASSWORD": request.jwch_password,
        "LLM_D_BASE_URL": request.llm_base_url,
        "LLM_D_API_KEY": request.llm_api_key,
        "LLM_D_MODEL": request.llm_model,
        "LLM_D_TIMEOUT": str(request.llm_timeout) if request.llm_timeout is not None else None,
        "CURRENT_SEMESTER_START": request.semester_start,
    }
    result = write_env_incremental(updates)
    defaults_filled = ensure_env_defaults()
    masked = settings_status()
    return {
        "status": "ready",
        "message": "本地配置已保存（仅增量更新已填写字段）。",
        "written_keys": result["written"],
        "defaults_filled": defaults_filled,
        "fields": masked["fields"],
        "source_module": "E",
    }


@app.post("/api/settings/bootstrap")
def bootstrap_settings() -> dict[str, Any]:
    return bootstrap_from_txt_files()


@app.get("/api/system/check")
def system_check() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    cfg = settings_status()

    learn_ok = cfg["fields"]["LEARN_USERNAME"]["configured"] and cfg["fields"]["LEARN_PASSWORD"]["configured"]
    checks.append(
        {
            "name": "learn_credentials",
            "level": "PASS" if learn_ok else "WARN",
            "message": "学堂账号已配置" if learn_ok else "学堂账号未配置，无法真实同步。",
        }
    )
    llm_ok = cfg["fields"]["LLM_D_API_KEY"]["configured"]
    checks.append(
        {
            "name": "llm_api_key",
            "level": "PASS" if llm_ok else "BLOCKED",
            "message": "LLM API Key 已配置" if llm_ok else "LLM API Key 未配置，智能应用不可用。",
        }
    )
    collector_ok = settings.collector_jsonl.exists() or settings.demo_collector_jsonl.exists()
    checks.append(
        {
            "name": "task_data",
            "level": "PASS" if collector_ok else "WARN",
            "message": "任务数据文件存在" if collector_ok else "缺少任务数据，请先同步或使用 Demo。",
        }
    )
    chunks_ok = settings.material_chunks_jsonl.exists() or settings.demo_material_chunks_jsonl.exists()
    checks.append(
        {
            "name": "material_chunks",
            "level": "PASS" if chunks_ok else "WARN",
            "message": "资料分块文件存在" if chunks_ok else "缺少 material_chunks.jsonl，请先解析资料。",
        }
    )
    kb = module_c.status()
    checks.append(
        {
            "name": "knowledge_index",
            "level": "PASS" if kb.get("status") == "ready" else "WARN",
            "message": kb.get("message", "知识库状态未知"),
        }
    )

    overall = "PASS"
    if any(item["level"] == "BLOCKED" for item in checks):
        overall = "BLOCKED"
    elif any(item["level"] == "WARN" for item in checks):
        overall = "WARN"

    return {
        "status": "ready",
        "overall": overall,
        "checks": checks,
        "semester_start": settings.semester_start,
        "source_module": "E",
    }


@app.get("/api/dashboard")
def dashboard(demo_mode: bool = Query(default=True)) -> dict[str, Any]:
    def _build() -> dict[str, Any]:
        tasks = module_a.task_records(use_demo_fallback=demo_mode)
        schedules = module_a.schedule_records(use_demo_fallback=demo_mode)
        materials = module_b.materials(high_priority_only=demo_mode, use_demo_fallback=demo_mode)
        homework = [item for item in tasks if item.get("task_type") == "homework"]
        sync = module_a.sync_status()
        material_status = module_b.parse_status()
        knowledge_status = module_c.status()
        recommendations = _recommended_actions(sync, material_status, knowledge_status)

        recent_tasks, task_warnings = normalize_list_items(tasks, max_items=5, demo_mode=demo_mode)
        task_source = task_data_source()
        material_source = material_data_source()
        data_warnings: list[str] = []
        if task_source["source"] != "real":
            data_warnings.append(task_source["label"])
        if material_source["source"] != "real":
            data_warnings.append(material_source["label"])
        if sync.get("items") and not any(item.get("record_count", 0) > 0 for item in sync["items"] if item.get("channel") == "collector"):
            data_warnings.append("尚未完成真实同步，Dashboard 可能仍显示 Demo 或空数据。")

        latest_job = get_latest_job()
        learn_task_count = sum(1 for item in tasks if item.get("source") == "learn")
        mail_task_count = sum(1 for item in tasks if item.get("source") == "mail")
        if latest_job:
            failed_channels = latest_job.get("failed_channels") or []
            if "learn" in failed_channels:
                data_warnings.append(
                    "学堂（Learn）同步失败，本学期作业/公告不会出现。请运行 scripts/probe_learn_double_auth.py 完成设备信任后重试。"
                )
        if learn_task_count == 0 and mail_task_count > 0:
            data_warnings.append(
                "当前任务主要来自邮箱历史邮件，不是本学期雨课堂数据。建议先「清空已收集任务」，再重新同步。"
            )

        return {
            "stats": {
                "task_count": len(tasks),
                "schedule_count": len(schedules),
                "material_count": len(materials),
                "pending_homework_count": len(homework),
            },
            "recent_tasks": recent_tasks,
            "sync_status": sync["items"],
            "material_status": material_status,
            "knowledge_status": knowledge_status,
            "recommended_actions": recommendations,
            "demo_mode": demo_mode,
            "semester_start": settings.semester_start,
            "data_sources": {
                "tasks": task_source,
                "materials": material_source,
            },
            "data_source_status": "real" if task_source.get("source") == "real" and material_source.get("source") == "real" else "demo",
            "latest_sync_job": latest_job,
            "source_module": "E",
            "status": "ready",
            "warnings": task_warnings + data_warnings,
            "errors": [],
        }

    return safe_call("E", _build, user_message="Dashboard 暂时不可用，其他页面仍可访问。")


def _recommended_actions(sync: dict[str, Any], material_status: dict[str, Any], knowledge_status: dict[str, Any]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    cfg = settings_status()
    if not cfg["fields"]["LEARN_USERNAME"]["configured"]:
        actions.append({"action": "configure_learn", "label": "配置学堂账号", "target": "设置"})
    task_source = task_data_source()
    if task_source.get("source") != "real":
        actions.append({"action": "sync", "label": "同步真实学堂/邮箱数据", "target": "Dashboard"})
    elif not any(item.get("record_count", 0) > 0 for item in sync.get("items", [])):
        actions.append({"action": "sync", "label": "同步最新任务", "target": "Dashboard"})
    if material_status.get("status") != "ready":
        actions.append({"action": "parse_materials", "label": "解析课程资料", "target": "资料页"})
    if knowledge_status.get("status") != "ready":
        actions.append({"action": "rebuild_index", "label": "重建知识库索引", "target": "资料页"})
    if not cfg["fields"]["LLM_D_API_KEY"]["configured"]:
        actions.append({"action": "configure_llm", "label": "配置 LLM API Key", "target": "设置"})
    return actions


@app.get("/api/tasks")
def tasks(
    course_name: str | None = None,
    task_type: str | None = None,
    ddl_status: str | None = Query(default=None, description="upcoming|overdue|no_ddl"),
    demo_mode: bool = Query(default=True),
    include_all_semesters: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, Any]:
    def _build() -> dict[str, Any]:
        records = module_a.task_records(include_all_semesters=include_all_semesters, use_demo_fallback=demo_mode)
        if course_name:
            records = [record for record in records if record.get("course_name") == course_name]
        if task_type:
            records = [record for record in records if record.get("task_type") == task_type]
        if ddl_status:
            records = [record for record in records if _ddl_status(record.get("ddl")) == ddl_status]
        items, warnings = normalize_list_items(records, max_items=limit, demo_mode=demo_mode)
        return module_response(source_module="A", items=items, total=len(records), warnings=warnings)

    return safe_call("A", _build, user_message="任务中心暂时不可用。")


def _ddl_status(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return "no_ddl"
    from datetime import datetime

    try:
        ddl = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return "no_ddl"
    return "overdue" if ddl.timestamp() < datetime.now(ddl.tzinfo or None).timestamp() else "upcoming"


@app.get("/api/schedules")
def schedules(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    def _build() -> dict[str, Any]:
        records = module_a.schedule_records()
        items, warnings = normalize_list_items(records, max_items=limit, demo_mode=False)
        return module_response(source_module="A", items=items, total=len(records), warnings=warnings)

    return safe_call("A", _build, user_message="课表数据暂时不可用。")


@app.post("/api/sync/run")
def run_sync(request: SyncRequest) -> dict[str, Any]:
    if not request.allow_network:
        return module_response(
            source_module="A",
            status="queued",
            message="Dry-run sync accepted. Set allow_network=true to run real collector sync.",
        )

    output = settings.collector_jsonl
    output.parent.mkdir(parents=True, exist_ok=True)
    job = start_sync_job(channel=request.channel, output_path=output)
    return module_response(
        source_module="A",
        status="running",
        message=f"已启动 {request.channel} 同步，产物写入 {output.name}。请稍后查看同步结果。",
        channel=request.channel,
        output_path=str(output),
        job_id=job["job_id"],
        sync_job=job,
    )


@app.post("/api/tasks/clear-collected")
def clear_collected_tasks() -> dict[str, Any]:
    removed: list[str] = []
    for path in [settings.collector_jsonl, settings.learn_jsonl, settings.mail_jsonl, settings.jwch_jsonl]:
        if path.exists():
            path.unlink()
            removed.append(str(path))
    db_path = settings.project_root / "storage" / "app.db"
    if db_path.exists():
        with sqlite3.connect(db_path) as connection:
            connection.execute("DELETE FROM fingerprints")
            connection.execute("DELETE FROM sync_state")
    return module_response(
        source_module="A",
        message="已清空本地收集任务和同步状态。Demo 数据不会被删除。",
        removed=removed,
    )


@app.get("/api/sync/status")
def sync_status() -> dict[str, Any]:
    def _build() -> dict[str, Any]:
        payload = module_a.sync_status()
        payload["latest_job"] = get_latest_job()
        return payload

    return safe_call("A", _build, user_message="同步状态暂时不可用。")


@app.get("/api/sync/jobs/latest")
def sync_job_latest() -> dict[str, Any]:
    job = get_latest_job()
    if job is None:
        return module_response(source_module="A", status="missing", message="暂无同步任务记录。")
    return module_response(source_module="A", status=job.get("status", "unknown"), sync_job=job)


@app.get("/api/sync/jobs/{job_id}")
def sync_job_detail(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if job is None:
        return module_response(source_module="A", status="missing", message=f"未找到同步任务 {job_id}")
    return module_response(source_module="A", status=job.get("status", "unknown"), sync_job=job)


@app.get("/api/debug/data-source")
def api_debug_data_source() -> dict[str, Any]:
    return debug_data_source()


@app.get("/api/debug/sync-errors")
def api_debug_sync_errors() -> dict[str, Any]:
    return debug_sync_errors()


@app.get("/api/materials")
def materials(
    high_priority_only: bool = Query(default=False),
    demo_mode: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    def _build() -> dict[str, Any]:
        records = module_b.materials(high_priority_only=high_priority_only or demo_mode, use_demo_fallback=demo_mode)
        items, warnings = normalize_list_items(records, max_items=limit, demo_mode=demo_mode)
        return module_response(source_module="B", items=items, total=len(records), status=module_b.parse_status()["status"], warnings=warnings)

    return safe_call("B", _build, user_message="资料页暂时不可用。")


@app.post("/api/materials/export-attachments")
def export_attachments(request: MaterialExportRequest) -> dict[str, Any]:
    sources = ["learn", "mail"] if request.source == "both" else [request.source]
    commands: list[str] = []
    for source in sources:
        command = [
            sys.executable,
            str(settings.project_root / "scripts" / "export_attachments.py"),
            "--source",
            source,
            "--limit",
            str(request.limit),
        ]
        if source == "learn":
            jsonl = settings.collector_jsonl if settings.collector_jsonl.exists() else settings.demo_collector_jsonl
            command.extend(["--jsonl", str(jsonl)])
        _run_background(command)
        commands.append(" ".join(command))
    return module_response(
        source_module="B",
        status="queued",
        message=f"已启动 {len(sources)} 个附件导出任务。",
        sources=sources,
        commands=commands,
    )


@app.post("/api/materials/parse")
def parse_materials(request: MaterialParseRequest) -> dict[str, Any]:
    command = [
        sys.executable,
        str(settings.project_root / "scripts" / "parse_materials.py"),
        "--incremental",
        "--records-jsonl",
        str(settings.collector_jsonl if settings.collector_jsonl.exists() else settings.demo_collector_jsonl),
    ]
    if request.limit is not None:
        command.extend(["--limit", str(request.limit)])
    _run_background(command)
    return module_response(
        source_module="B",
        status="queued",
        message="资料解析已在后台启动，完成后请刷新资料页。",
        output_path=str(settings.material_chunks_jsonl),
    )


@app.post("/api/materials/upload")
async def upload_material(file: UploadFile = File(...)) -> dict[str, Any]:
    return await _extract_uploaded_file(file)


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)) -> dict[str, Any]:
    return await _extract_uploaded_file(file)


async def _extract_uploaded_file(file: UploadFile) -> dict[str, Any]:
    suffix = Path(file.filename or "upload").suffix.lower()
    if not suffix:
        raise HTTPException(status_code=400, detail="上传文件缺少扩展名，无法判断格式。")
    payload = await file.read()
    if len(payload) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="上传文件超过 20MB，请压缩后再试。")

    with tempfile.TemporaryDirectory(prefix="learning-assistant-upload-") as temp_dir:
        path = Path(temp_dir) / f"upload{suffix}"
        path.write_bytes(payload)
        try:
            if suffix in {".txt", ".md"}:
                text = path.read_text(encoding="utf-8", errors="ignore")
                methods = ["plain_text"]
            else:
                extractor = extractor_for_path(path)
                segments = extractor.extract(path)
                text = "\n\n".join(segment.text for segment in segments if segment.text.strip())
                methods = sorted(
                    {
                        str(method)
                        for segment in segments
                        if (method := segment.metadata.get("extraction_method"))
                    }
                )
        except MaterialParseError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"文件解析失败：{type(exc).__name__}") from exc

    if not text.strip():
        raise HTTPException(status_code=422, detail="文件已上传，但未提取到可用文字。")
    return module_response(
        source_module="B",
        status="ready",
        filename=file.filename,
        text=text[:50000],
        text_length=len(text),
        extraction_methods=methods,
    )


@app.get("/api/materials/parse-status")
def parse_status() -> dict[str, Any]:
    return safe_call("B", module_b.parse_status, user_message="资料解析状态暂时不可用。")


@app.get("/api/knowledge/status")
def knowledge_status() -> dict[str, Any]:
    return safe_call("C", module_c.status, user_message="知识库状态暂时不可用。")


@app.post("/api/knowledge/rebuild")
def knowledge_rebuild(request: KnowledgeRebuildRequest) -> dict[str, Any]:
    return safe_call(
        "C",
        lambda: module_c.rebuild(force=request.force),
        user_message="知识库重建失败，其他页面仍可使用。",
    )


@app.post("/api/retrieval/search")
def retrieval_search(request: RetrievalRequest) -> dict[str, Any]:
    return safe_call("C", lambda: module_c.search(request), user_message="检索暂时不可用。")


@app.post("/api/qa")
def qa(request: QARequest) -> dict[str, Any]:
    return safe_call("D", lambda: module_d.qa(request), user_message="问答服务暂时不可用。")


@app.post("/api/summaries")
def summaries(request: SummaryRequest) -> dict[str, Any]:
    return safe_call("D", lambda: module_d.summarize(request), user_message="总结服务暂时不可用。")


@app.post("/api/homework-assistant")
def homework_assistant(request: HomeworkAssistantRequest) -> dict[str, Any]:
    return safe_call("D", lambda: module_d.homework_assistant(request), user_message="作业助手暂时不可用。")


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Learning Assistant E Module API", "docs": "/docs"}
