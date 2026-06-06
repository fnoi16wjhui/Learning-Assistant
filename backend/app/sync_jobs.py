"""Background sync job tracking for E-module."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app.adapters.jsonl import read_jsonl
from backend.app.adapters.module_a import _friendly_sync_error
from backend.app.data_source import is_demo_record, task_data_source
from backend.app.settings import settings

JOBS_DIR = settings.project_root / "logs" / "sync_jobs"
STATE_FILE = settings.project_root / "storage" / "sync_job_state.json"
_lock = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"latest_job_id": None, "jobs": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"latest_job_id": None, "jobs": {}}


def _save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _count_records(path: Path) -> int:
    return len(read_jsonl(path)) if path.exists() else 0


def _demo_count(path: Path) -> int:
    if not path.exists():
        return 0
    records = read_jsonl(path)
    return sum(1 for record in records if is_demo_record(record))


def _parse_job_log(log_path: Path) -> dict[str, Any]:
    if not log_path.exists():
        return {
            "succeeded_channels": [],
            "failed_channels": [],
            "channel_results": [],
            "error_summary": [],
        }

    text = log_path.read_text(encoding="utf-8", errors="ignore")
    succeeded: list[str] = []
    failed: list[str] = []
    channel_results: list[dict[str, Any]] = []
    error_summary: list[str] = []

    for line in text.splitlines():
        ok_match = re.search(r"\[OK\]\s+(\w+):", line)
        if ok_match:
            channel = ok_match.group(1)
            if channel not in succeeded:
                succeeded.append(channel)
            channel_results.append({"channel": channel, "status": "success", "message": line.strip()})
            continue
        fail_match = re.search(r"\[FAIL\]\s+(\w+):", line)
        if fail_match:
            channel = fail_match.group(1)
            if channel not in failed:
                failed.append(channel)
            message = _friendly_sync_error(channel, line.split(":", 2)[-1].strip() if ":" in line else line.strip())
            channel_results.append({"channel": channel, "status": "failed", "message": message})
            error_summary.append(f"{channel}: {message}")
            continue
        if "channel sync failed: channel=" in line:
            for channel in ("learn", "mail", "jwch"):
                if f"channel={channel}" in line:
                    message = _friendly_sync_error(channel, line.split("error=", 1)[-1].strip())
                    if channel not in failed:
                        failed.append(channel)
                    channel_results.append({"channel": channel, "status": "failed", "message": message})
                    summary = f"{channel}: {message}"
                    if summary not in error_summary:
                        error_summary.append(summary)

    return {
        "succeeded_channels": succeeded,
        "failed_channels": failed,
        "channel_results": channel_results,
        "error_summary": error_summary,
    }


def _finalize_job(job_id: str, *, exit_code: int, log_path: Path, output_path: Path) -> None:
    parsed = _parse_job_log(log_path)
    record_count_after = _count_records(output_path)
    demo_after = _demo_count(output_path)
    source = task_data_source()

    succeeded = parsed["succeeded_channels"]
    failed = parsed["failed_channels"]

    with _lock:
        state = _load_state()
        existing_job = state.get("jobs", {}).get(job_id, {})
        record_count_before = existing_job.get("record_count_before", 0)

    has_new_real_records = record_count_after > record_count_before and demo_after < record_count_after
    if not succeeded:
        status = "failed"
    elif succeeded and not has_new_real_records and source.get("source") == "demo":
        status = "partial" if failed else "failed"
        if "同步完成但 collector.jsonl 仍为 Demo 内容，未产生真实记录。" not in parsed["error_summary"]:
            parsed["error_summary"].append("同步完成但 collector.jsonl 仍为 Demo 内容，未产生真实记录。")
    elif succeeded and failed:
        status = "partial"
    elif succeeded and has_new_real_records:
        status = "success" if not failed else "partial"
    elif exit_code == 0:
        status = "success"
    else:
        status = "partial"

    with _lock:
        state = _load_state()
        job = state["jobs"].get(job_id, {})
        job.update(
            {
                "status": status,
                "finished_at": _utc_now(),
                "exit_code": exit_code,
                "record_count_after": record_count_after,
                "demo_record_count_after": demo_after,
                "data_source_after": source.get("source"),
                "succeeded_channels": succeeded,
                "failed_channels": failed,
                "channel_results": parsed["channel_results"],
                "error_summary": parsed["error_summary"],
            }
        )
        state["jobs"][job_id] = job
        state["latest_job_id"] = job_id
        _save_state(state)


def _run_job(job_id: str, command: list[str], output_path: Path) -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = JOBS_DIR / f"sync_{job_id}.log"
    with log_path.open("w", encoding="utf-8") as log_file:
        proc = subprocess.run(
            command,
            cwd=settings.project_root,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=None,
        )
    _finalize_job(job_id, exit_code=proc.returncode, log_path=log_path, output_path=output_path)


def start_sync_job(*, channel: str, output_path: Path) -> dict[str, Any]:
    job_id = uuid.uuid4().hex[:12]
    record_count_before = _count_records(output_path)
    log_path = JOBS_DIR / f"sync_{job_id}.log"
    command = [
        sys.executable,
        str(settings.project_root / "main.py"),
        "--channel",
        channel,
        "--allow-network",
        "--output",
        str(output_path),
        "--limit",
        "150",
        "--criteria",
        "ALL",
    ]

    job = {
        "job_id": job_id,
        "channel": channel,
        "status": "running",
        "started_at": _utc_now(),
        "finished_at": None,
        "output_path": str(output_path),
        "log_path": str(log_path),
        "record_count_before": record_count_before,
        "record_count_after": None,
        "demo_record_count_before": _demo_count(output_path),
        "succeeded_channels": [],
        "failed_channels": [],
        "channel_results": [],
        "error_summary": [],
        "command": " ".join(command),
    }

    with _lock:
        state = _load_state()
        state.setdefault("jobs", {})[job_id] = job
        state["latest_job_id"] = job_id
        _save_state(state)

    thread = threading.Thread(target=_run_job, args=(job_id, command, output_path), daemon=True)
    thread.start()
    return job


def get_job(job_id: str | None = None) -> dict[str, Any] | None:
    state = _load_state()
    target_id = job_id or state.get("latest_job_id")
    if not target_id:
        return None
    return state.get("jobs", {}).get(target_id)


def get_latest_job() -> dict[str, Any] | None:
    return get_job(None)


def list_recent_jobs(limit: int = 5) -> list[dict[str, Any]]:
    state = _load_state()
    jobs = list(state.get("jobs", {}).values())
    jobs.sort(key=lambda item: item.get("started_at") or "", reverse=True)
    return jobs[:limit]
