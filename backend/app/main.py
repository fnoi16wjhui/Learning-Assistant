"""FastAPI entry point for the E-module integration backend."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Load .env before any adapter imports so env vars are available at module level
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.adapters.learn_adapter import LearnAdapter
from backend.app.adapters.module_a import ModuleAAdapter, parse_datetime
from backend.app.adapters.module_b import ModuleBAdapter
from backend.app.adapters.module_c import ModuleCAdapter
from backend.app.adapters.module_d import ModuleDAdapter, get_llm_config, update_llm_config
from backend.app.models import HomeworkAssistantRequest, QARequest, RetrievalRequest, SummaryRequest, SyncRequest
from backend.app.settings import settings
from src.knowledge.knowledge_base import KnowledgeBase


LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


app = FastAPI(title="Learning Assistant API", version="0.1.0")
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
    homework = [item for item in tasks if item.get("task_type") == "homework"]
    now = datetime.now(LOCAL_TIMEZONE)
    upcoming = [
        item
        for item in homework
        if (deadline := parse_datetime(item.get("ddl"))) is not None and deadline >= now
    ]
    courses = {
        str(item.get("course_name"))
        for item in tasks
        if item.get("course_name") and item.get("course_name") != "Unknown Course"
    }
    return {
        "stats": {
            "course_count": len(courses),
            "schedule_count": len(schedules),
            "pending_homework_count": len(homework),
            "upcoming_deadline_count": len(upcoming),
        },
        "recent_tasks": homework[:4],
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

    output_paths = {
        "learn": settings.learn_jsonl,
        "mail": settings.mail_jsonl,
        "jwch": settings.jwch_jsonl,
        "all": settings.collector_jsonl,
    }
    command = [
        sys.executable,
        str(settings.project_root / "main.py"),
        "--channel",
        request.channel,
        "--allow-network",
        "--output",
        str(output_paths[request.channel]),
    ]
    if request.semester_id and request.channel in {"learn", "all"}:
        command.extend(["--semester-id", request.semester_id])
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.Popen(command, cwd=settings.project_root, creationflags=creationflags)
    return {"status": "queued", "message": "同步已在后台启动。", "source_module": "A"}


@app.get("/api/sync/status")
def sync_status() -> dict[str, Any]:
    return module_a.sync_status()


@app.get("/api/tasks/{task_id}/attachments/{attachment_index}")
def task_attachment(task_id: str, attachment_index: int) -> FileResponse:
    task = next(
        (item for item in module_a.task_records() if str(item.get("raw_id") or item.get("id")) == task_id),
        None,
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    attachments = task.get("attachments")
    if not isinstance(attachments, list) or not 0 <= attachment_index < len(attachments):
        raise HTTPException(status_code=404, detail="Attachment not found")
    attachment = attachments[attachment_index]
    if not isinstance(attachment, dict):
        raise HTTPException(status_code=404, detail="Attachment metadata is invalid")

    name = safe_attachment_filename(str(attachment.get("name") or f"attachment-{attachment_index + 1}"))
    local_path = find_exported_attachment(task_id, name)
    media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if local_path is None:
        remote_url = str(attachment.get("download_url") or "")
        if not remote_url:
            raise HTTPException(status_code=404, detail="Attachment URL is missing")
        local_path, media_type = cache_learn_attachment(task_id, name, remote_url)
    return FileResponse(
        local_path,
        media_type=media_type,
        filename=name,
        content_disposition_type="inline",
    )


@app.get("/api/materials")
def materials(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    records = module_b.material_files()
    return {"items": records[:limit], "total": len(records), "source_module": "B", "status": module_b.parse_status()["status"]}


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)) -> dict[str, Any]:
    """Upload a file (PDF, PPTX, DOCX, image) and parse it via B-module extractors.

    Returns the extracted text that can be passed as upload_texts to the
    homework-assistant endpoint for enriched LLM context.
    """
    allowed_suffixes = {
        ".pdf", ".pptx", ".docx",
        ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp",
        ".txt", ".md",
    }
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in allowed_suffixes:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {suffix}。支持: {', '.join(sorted(allowed_suffixes))}",
        )

    upload_dir = settings.project_root / "storage" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    content = await file.read()
    file_hash = hashlib.sha256(content).hexdigest()
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", file.filename or "upload")
    dest = upload_dir / f"{file_hash[:16]}_{safe_name}"
    dest.write_bytes(content)

    # Parse with B-module extractors
    extracted_texts: list[str] = []
    extraction_method = "text"
    try:
        if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}:
            from PIL import Image
            import io as _io
            pil_img = Image.open(_io.BytesIO(content))
            from src.materials.extractors.image import ocr_pil_image
            text = ocr_pil_image(pil_img)
            if text.strip():
                extracted_texts.append(text.strip())
            extraction_method = "ocr"
        elif suffix == ".pdf":
            from src.materials.extractors.pdf import PdfExtractor
            segments = PdfExtractor().extract(dest)
            for seg in segments:
                if seg.text.strip():
                    extracted_texts.append(seg.text.strip())
        elif suffix == ".pptx":
            from src.materials.extractors.office import PptxExtractor
            segments = PptxExtractor().extract(dest)
            for seg in segments:
                if seg.text.strip():
                    extracted_texts.append(seg.text.strip())
        elif suffix == ".docx":
            from src.materials.extractors.office import DocxExtractor
            segments = DocxExtractor().extract(dest)
            for seg in segments:
                if seg.text.strip():
                    extracted_texts.append(seg.text.strip())
        else:
            text = content.decode("utf-8", errors="replace")
            if text.strip():
                extracted_texts.append(text.strip())
    except Exception as exc:
        extracted_texts = [f"[文件解析失败: {exc}]"]

    combined = "\n\n".join(extracted_texts)
    return {
        "file_hash": file_hash,
        "filename": safe_name,
        "size_bytes": len(content),
        "extraction_method": extraction_method,
        "segments": len(extracted_texts),
        "text": combined,
        "text_preview": combined[:500],
        "source_module": "B",
        "status": "ready",
    }


@app.post("/api/materials/upload")
def upload_material() -> dict[str, Any]:
    return {
        "status": "accepted",
        "message": "Upload endpoint is reserved for B module parser integration.",
        "source_module": "B",
    }


@app.get("/api/materials/{material_id}/file")
def material_file(material_id: str) -> FileResponse:
    """Serve the original material file (PDF, PPTX, etc.) so users can view it."""
    records = module_b.materials()
    matched = [
        record for record in records
        if str(record.get("file_hash") or "") == material_id
        or str(record.get("source_file") or "") == material_id
    ]
    if not matched:
        raise HTTPException(status_code=404, detail="Material not found")

    source = matched[0].get("source_file", "")
    path = Path(source)
    if not path.is_absolute():
        path = settings.project_root / path
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Original file not found on disk")

    media_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        content_disposition_type="inline",
    )


@app.get("/api/materials/parse-status")
def parse_status() -> dict[str, Any]:
    return module_b.parse_status()


@app.get("/api/materials/{material_id}/chunks")
def material_chunks(
    material_id: str,
    page: int | None = Query(default=None, ge=1),
    slide: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    """Return parsed chunks for a specific material file, optionally filtered by page or slide."""
    records = module_b.materials()
    chunks = [
        record for record in records
        if str(record.get("file_hash") or "") == material_id
        or str(record.get("source_file") or "") == material_id
    ]
    if page is not None:
        chunks = [c for c in chunks if c.get("page") == page]
    if slide is not None:
        chunks = [c for c in chunks if c.get("slide") == slide]

    # Sort by page/slide then chunk_index
    chunks.sort(key=lambda c: (c.get("page") or c.get("slide") or 0, c.get("chunk_index", 0)))

    if not chunks:
        raise HTTPException(status_code=404, detail="No chunks found for this material")

    # Build page/slide summary
    pages: dict[int, list[dict[str, Any]]] = {}
    for c in chunks:
        key = c.get("page") or c.get("slide") or 0
        pages.setdefault(key, []).append(c)

    first_meta = chunks[0].get("metadata", {}) if chunks else {}
    file_info = {
        "file_hash": chunks[0].get("file_hash", ""),
        "source_file": chunks[0].get("source_file", ""),
        "title": chunks[0].get("title", "未命名文件"),
        "course_name": chunks[0].get("course_name", "未知课程"),
        "material_type": chunks[0].get("material_type", "unknown"),
        "total_pages": len(pages),
        "total_chunks": len(chunks),
        "bytes": first_meta.get("bytes"),
        "published_at": first_meta.get("published_at"),
        "content_type": first_meta.get("content_type"),
    }

    return {
        "material_id": material_id,
        "file": file_info,
        "pages": {
            str(p): {
                "page": p,
                "chunk_count": len(page_chunks),
                "chunks": [
                    {
                        "chunk_id": c.get("chunk_id", ""),
                        "chunk_index": c.get("chunk_index", 0),
                        "text": c.get("text", ""),
                        "title": c.get("title", ""),
                        "page": c.get("page"),
                        "slide": c.get("slide"),
                        "section_title": c.get("section_title"),
                        "metadata": c.get("metadata", {}),
                    }
                    for c in page_chunks
                ],
            }
            for p, page_chunks in sorted(pages.items())
        },
        "source_module": "B",
        "status": "ready",
    }


@app.get("/api/knowledge/status")
def knowledge_status() -> dict[str, Any]:
    return module_c.status()


@app.post("/api/knowledge/rebuild")
def rebuild_knowledge() -> dict[str, Any]:
    """Rebuild the knowledge base from current material chunks.

    Runs B-module parsing (if manifest exists) followed by C-module indexing.
    This endpoint blocks until the rebuild is complete.
    """
    chunks_jsonl = settings.material_chunks_jsonl
    manifest_path = settings.project_root / "storage" / "attachments" / "manifest.jsonl"
    learn_jsonl = settings.learn_jsonl

    parse_result = None
    if manifest_path.exists():
        try:
            parse_cmd = [
                sys.executable,
                str(settings.project_root / "scripts" / "parse_materials.py"),
                "--manifest", str(manifest_path),
                "--records-jsonl", str(learn_jsonl),
                "--output", str(chunks_jsonl),
                "--incremental",
            ]
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            proc = subprocess.run(
                parse_cmd,
                cwd=settings.project_root,
                capture_output=True,
                text=True,
                creationflags=creationflags,
                timeout=300,
            )
            parse_result = {
                "return_code": proc.returncode,
                "stdout": proc.stdout[-500:] if proc.stdout else "",
            }
        except subprocess.TimeoutExpired:
            parse_result = {"error": "Parse timed out after 300 seconds"}
        except Exception as exc:
            parse_result = {"error": str(exc)}
    else:
        parse_result = {"skipped": "no manifest found, reusing existing material_chunks.jsonl"}

    module_c._initialised = False
    module_c._kb = KnowledgeBase(
        index_dir=settings.knowledge_index_dir,
        chunks_jsonl=chunks_jsonl,
    )
    status = module_c._kb.build()
    return {
        "status": status.status,
        "indexed_chunks": status.indexed_chunks,
        "index_types": status.index_types,
        "parse_phase": parse_result,
        "source_module": "E",
    }


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


@app.get("/api/settings/llm")
def get_llm_settings() -> dict[str, Any]:
    """Return current LLM configuration (API key masked)."""
    return {"config": get_llm_config(), "source_module": "D", "status": "ready"}


@app.post("/api/settings/llm")
def update_llm_settings(body: dict[str, Any]) -> dict[str, Any]:
    """Update LLM configuration at runtime."""
    return {"config": update_llm_config(body), "source_module": "D", "status": "ready"}


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Learning Assistant API", "docs": "/docs"}


def find_exported_attachment(task_id: str, attachment_name: str) -> Path | None:
    manifest = settings.project_root / "storage" / "attachments" / "manifest.jsonl"
    if not manifest.exists():
        return None
    for line in manifest.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("record_raw_id") != task_id:
            continue
        if safe_attachment_filename(str(item.get("attachment_name") or "")) != attachment_name:
            continue
        candidate = Path(str(item.get("local_path") or ""))
        if not candidate.is_absolute():
            candidate = settings.project_root / candidate
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def cache_learn_attachment(task_id: str, name: str, remote_url: str) -> tuple[Path, str]:
    base_url = os.getenv("LEARN_BASE_URL") or "https://learn.tsinghua.edu.cn"
    absolute_url = urljoin(base_url.rstrip("/") + "/", remote_url)
    base_host = urlparse(base_url).hostname
    parsed = urlparse(absolute_url)
    if parsed.scheme != "https" or parsed.hostname != base_host:
        raise HTTPException(status_code=400, detail="Attachment host is not allowed")
    clean_query = urlencode(
        [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != "_csrf"]
    )
    absolute_url = urlunparse(parsed._replace(query=clean_query))

    cache_dir = settings.project_root / "storage" / "task_attachments"
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(f"{task_id}:{absolute_url}".encode("utf-8")).hexdigest()[:20]
    target = cache_dir / f"{digest}_{name}"
    if target.exists():
        return target, mimetypes.guess_type(name)[0] or "application/octet-stream"
    adapter = LearnAdapter()
    adapter.authenticate()
    if adapter._session is None:
        raise HTTPException(status_code=502, detail="Learn session is unavailable")
    try:
        response = adapter._session.get(
            absolute_url,
            headers=adapter._request_headers(),
            timeout=30,
        )
        response.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Unable to download attachment from Learn") from exc
    target.write_bytes(response.content)
    media_type = response.headers.get("Content-Type", "").split(";")[0]
    return target, media_type or mimetypes.guess_type(name)[0] or "application/octet-stream"


def safe_attachment_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    return cleaned[:160] or "attachment"
