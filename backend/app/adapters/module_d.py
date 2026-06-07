"""D-module intelligent applications powered by LLM.

Architecture (3-stage pipeline):
  Stage 1 — Plan:    LLM analyzes question → decides search queries + data sources
  Stage 2 — Search:  Query A (tasks/emails/schedules) + C (knowledge base)
  Stage 3 — Answer:  LLM generates structured answer from combined context

Module dependencies (D → ABC):
  - A module: tasks (/api/tasks), schedules (/api/schedules), sync status
  - B module: material_chunks.jsonl (via C module's knowledge base)
  - C module: KnowledgeBase retrieval (keyword index built from B's output)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from typing import Any

import openai

from backend.app.models import HomeworkAssistantRequest, QARequest, SummaryRequest
from backend.app.settings import settings
from src.knowledge.knowledge_base import KnowledgeBase

# ---------------------------------------------------------------------------
# LLM config
# ---------------------------------------------------------------------------

def _llm_base_url() -> str:
    return os.getenv("LLM_D_BASE_URL", "https://api.deepseek.com").rstrip("/")


def _llm_api_key() -> str:
    return os.getenv("LLM_D_API_KEY", "")


def _llm_model() -> str:
    return os.getenv("LLM_D_MODEL", "deepseek-v4-pro")


def _llm_timeout() -> int:
    return int(os.getenv("LLM_D_TIMEOUT", "60"))


def _llm_max_tokens() -> int:
    return int(os.getenv("LLM_D_MAX_TOKENS", "3072"))


_client: openai.OpenAI | None = None
_client_signature: tuple[str, str] | None = None

SEMESTER_CUTOFF = settings.semester_start


def _current_time_text() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y年%m月%d日 %H:%M %A（北京时间）")

def _get_client() -> openai.OpenAI:
    global _client, _client_signature
    base_url = _llm_base_url()
    api_key = _llm_api_key()
    signature = (base_url, api_key)
    if _client is None or _client_signature != signature:
        _client = openai.OpenAI(
            api_key=api_key,
            base_url=f"{base_url}/v1/",
            timeout=_llm_timeout(),
        )
        _client_signature = signature
    return _client

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

PLAN_SYSTEM = """\
你是课程助手的查询规划器。根据用户问题和当前日期，决定需要搜索什么、搜索哪些数据源。

## 可用数据源
- knowledge_base: 课件、教材、讲义等课程学习资料（来自 C 模块）
- tasks: 作业要求、课程公告、截止日期（来自 A 模块 learn.jsonl）
- emails: 近期课程相关邮件通知（来自 A 模块 mail.jsonl）
- schedules: 考试安排、课表（来自 A 模块 jwch.jsonl）

## 决策规则
1. 问"最近有什么事"/"有什么作业"/"DDL"/"截止日期" → 重点搜 tasks + emails + schedules
2. 问"知识点"/"概念"/"原理"/"怎么做"/"如何理解" → 重点搜 knowledge_base
3. 问"作业要求"/"作业内容" → 搜 tasks（作业原题）+ knowledge_base（相关知识点）
4. 问"考试"/"什么时候考" → 搜 schedules
5. 综合问题 → 搜多个数据源
6. 如果用户没有明确指定课程，用最相关的课程名搜索

## 日期过滤
今天是 {today}。本学期为 2025-2026 春季学期（2026年2月-6月，第14-16周）。
只搜索本学期内容，过滤掉 2025-09-01 之前的数据。

请严格返回 JSON（不含 markdown 代码块）：
{{"queries": ["改写后的搜索词1", "搜索词2"], "sources": ["knowledge_base", "tasks", "emails", "schedules"], "course_hint": "提取的课程名或null"}}"""

QA_SYSTEM = """\
你是一个专业的清华本科生课程学习助手。

## 回答要求
1. **综合分析**：结合多种信息来源（课件知识、作业要求、邮件通知、考试安排），给出全面的回答。
2. **时效优先**：对于"最近"/"当前"/"这周"等时间敏感问题，优先使用邮件和作业通知中的最新信息。
3. **知识扎实**：对概念和原理问题，深入分析，解释关联，给出例题或应用场景。
4. **结构清晰**：使用标题、段落、要点组织内容，方便阅读。
5. **标注出处**：每个关键信息注明来源类型（如 [课件]、[邮件]、[作业]）和具体来源名。
6. **诚实边界**：如果信息不足以回答，说明已有信息和缺失部分。

请严格返回 JSON（不含 markdown 代码块）：
{"answer": "结构化的详细回答", "citations": [{"title": "来源标题", "source": "数据来源类型与路径", "snippet": "引用片段"}]}"""

SUMMARY_SYSTEM = """\
你是一个专业的课程学习助手。根据多来源资料生成结构化总结。

## 总结要求
1. 综合课件知识、作业练习、邮件通知等内容，提炼最核心的课程要点。
2. 按知识体系组织，标注 4-7 个关键要点。
3. 每个重要概念标明出处。

请严格返回 JSON：
{"summary": "详细总结", "key_points": ["要点1", ...], "citations": [...]}"""

DOCUMENT_ANALYSIS_SYSTEM = """\
你是一个专业的课程资料分析助手。你的任务是深度分析用户指定的**单个文件**，而不是泛泛地总结课程。

## 分析要求
1. **聚焦此文**：只分析这份文件的内容，不要引入其他资料或课程信息。
2. **深度解读**：提炼文件的核心论点、关键概念、论证逻辑和结论。
3. **结构拆解**：说明文件的结构组织（章节/段落如何展开），帮助读者快速建立认知框架。
4. **要点标注**：给出 4-7 个关键要点，直接对应文件中的具体内容。
5. **难点提示**：如果文件中有复杂概念或数学推导，指出并简要解释。

请严格返回 JSON：
{"summary": "对这份文件的深度分析", "key_points": ["关键要点1", ...], "citations": [{"title": "文件名", "source": "文件路径", "snippet": "引用片段"}]}"""

HOMEWORK_SYSTEM = """\
你是一个课程作业辅导助手。

## 辅导要求
1. **解读作业**：分析考察点、能力目标。
2. **知识关联**：指出需要用到的课程知识点，标明出处。
3. **思路引导**：给出解题步骤大纲（引导思考，不直接给答案）。
4. **常见坑点**：指出易错点和易混淆概念。
5. **自查清单**：给出完成后可验证的检查项。

请严格返回 JSON：
{"outline": ["步骤1", ...], "draft": "思路分析", "pitfalls": ["易错点", ...], "checklist": ["自查项", ...], "citations": [...]}"""

# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return json.loads(text)


def _classify_llm_error(exc: Exception) -> dict[str, Any]:
    message = str(exc).lower()
    if not _llm_api_key().strip():
        return {
            "error_code": "missing_api_key",
            "user_message": "未配置 LLM API Key，请在设置页保存 LLM_D_API_KEY。",
            "retryable": False,
        }
    if "401" in message or "403" in message or "invalid" in message and "key" in message:
        return {
            "error_code": "invalid_api_key",
            "user_message": "API Key 无效或已过期，请检查设置页配置。",
            "retryable": False,
        }
    if "429" in message or "rate" in message or "limit" in message:
        return {
            "error_code": "rate_limited",
            "user_message": "LLM 请求被限流，请稍后重试。",
            "retryable": True,
        }
    if "timeout" in message or "timed out" in message:
        return {
            "error_code": "timeout",
            "user_message": "LLM 请求超时，请缩短问题或稍后重试。",
            "retryable": True,
        }
    if "balance" in message or "quota" in message or "insufficient" in message or "余额" in message:
        return {
            "error_code": "insufficient_balance",
            "user_message": "API 余额不足，请更换 Key 或充值后重试。",
            "retryable": False,
        }
    if "model" in message and ("not found" in message or "does not exist" in message):
        return {
            "error_code": "model_not_found",
            "user_message": "模型不存在，请检查 LLM_D_MODEL 配置。",
            "retryable": False,
        }
    if "500" in message or "502" in message or "503" in message:
        return {
            "error_code": "upstream_error",
            "user_message": "LLM 服务暂时不可用，请稍后重试。",
            "retryable": True,
        }
    return {
        "error_code": "llm_error",
        "user_message": "LLM 调用失败，请检查网络与 API 配置。",
        "retryable": True,
    }


def _blocked_payload(exc: Exception, *, retrieved: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    error = _classify_llm_error(exc)
    payload: dict[str, Any] = {
        "source_module": "D",
        "status": "blocked",
        "error_code": error["error_code"],
        "user_message": error["user_message"],
        "retryable": error["retryable"],
        "errors": [{**error, "detail": str(exc)}],
        "warnings": [],
    }
    if retrieved is not None:
        payload["retrieved"] = retrieved
    payload.update(extra)
    return payload


def _llm_chat(messages: list[dict[str, str]], temperature: float = 0.5) -> dict[str, Any]:
    if not _llm_api_key().strip():
        raise RuntimeError("LLM_D_API_KEY is not configured")
    client = _get_client()
    response = client.chat.completions.create(
        model=_llm_model(),
        messages=messages,
        temperature=temperature,
        max_tokens=_llm_max_tokens(),
        response_format={"type": "json_object"},
    )
    raw = response.choices[0].message.content or ""
    try:
        return _extract_json(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"模型返回格式异常：{exc}") from exc

# ---------------------------------------------------------------------------
# A-module data loaders (D 调用 A 模块)
# ---------------------------------------------------------------------------

_TASK_CACHE: list[dict[str, Any]] | None = None
_TASK_CACHE_TIME: datetime | None = None


def _load_all_records() -> list[dict[str, Any]]:
    """Load all A-module records (learn + mail + jwch).  [D → A]"""
    global _TASK_CACHE, _TASK_CACHE_TIME
    now = datetime.now(timezone.utc)
    if _TASK_CACHE is not None and _TASK_CACHE_TIME is not None:
        if (now - _TASK_CACHE_TIME).total_seconds() < 300:
            return _TASK_CACHE

    records: list[dict[str, Any]] = []
    paths = [settings.collector_jsonl, settings.learn_jsonl, settings.mail_jsonl, settings.jwch_jsonl]
    seen_ids: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        if not isinstance(rec, dict):
                            continue
                        dedupe_key = str(rec.get("raw_id") or rec.get("id") or f"{path}:{line[:80]}")
                        if dedupe_key in seen_ids:
                            continue
                        seen_ids.add(dedupe_key)
                        if "schedule_type" in rec:
                            rec["_source"] = "schedules"
                        elif path.name == "mail.jsonl" or rec.get("source") == "mail":
                            rec["_source"] = "emails"
                        else:
                            rec["_source"] = "tasks"
                        records.append(rec)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue

    _TASK_CACHE = records
    _TASK_CACHE_TIME = now
    return records


def _search_a_module(
    query: str,
    sources: list[str],
    course_hint: str | None,
    max_results: int = 10,
) -> list[dict[str, Any]]:
    """Keyword-search A-module records, filtered by source type and semester.  [D → A]"""
    records = _load_all_records()
    results: list[dict[str, Any]] = []
    query_lower = query.lower()
    cutoff = datetime.fromisoformat(SEMESTER_CUTOFF).replace(tzinfo=timezone(timedelta(hours=8)))

    for rec in records:
        if rec.get("_source") not in sources:
            continue

        # Semester filter: skip old records
        created = rec.get("created_at", "")
        if created:
            try:
                created_dt = datetime.fromisoformat(created)
                if created_dt < cutoff:
                    continue
            except (ValueError, TypeError):
                pass

        # Score by keyword match
        title = (rec.get("title") or "").lower()
        content = (rec.get("content") or "").lower()
        course = (rec.get("course_name") or "").lower()
        score = 0
        for term in query_lower.split():
            if term in title:
                score += 3
            if term in content:
                score += 2
            if term in course:
                score += 2

        # Boost if course_hint matches; explicit course selection should still surface records.
        if course_hint and course_hint.lower() in course:
            score += 5
            if rec.get("task_type") == "homework" and any(
                token in f"{query_lower} {title}" for token in ("作业", "homework", "ddl", "截止", "提交")
            ):
                score += 3

        if score > 0:
            results.append({**rec, "_score": score})

    results.sort(key=lambda r: r["_score"], reverse=True)
    return results[:max_results]


def _is_homework_deadline_query(question: str) -> bool:
    return any(token in question for token in ("作业", "DDL", "ddl", "截止", "提交", "要交"))


def _course_matches(record_course: Any, course_hint: str | None) -> bool:
    if not course_hint:
        return True
    left = _normalize_course_text(str(record_course or ""))
    right = _normalize_course_text(course_hint)
    return bool(left and right and (left == right or left in right or right in left))


def _normalize_course_text(value: str) -> str:
    return (
        value.lower()
        .replace(" ", "")
        .replace("（", "(")
        .replace("）", ")")
        .replace("Ⅱ", "ii")
        .replace("ⅱ", "ii")
    )


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone(timedelta(hours=8)))
    return parsed.astimezone(timezone(timedelta(hours=8)))


def _search_pending_homework(course_hint: str | None, max_results: int = 10) -> list[dict[str, Any]]:
    """Deterministic lookup for deadline questions; avoids schedules and expired homework."""

    now = datetime.now(timezone(timedelta(hours=8)))
    results: list[dict[str, Any]] = []
    for rec in _load_all_records():
        if rec.get("task_type") != "homework":
            continue
        if not _course_matches(rec.get("course_name"), course_hint):
            continue
        if rec.get("completed") is True or rec.get("status") in {"submitted_ungraded", "graded", "submitted"}:
            continue
        ddl = _parse_datetime(rec.get("ddl"))
        if ddl is not None and ddl < now:
            continue
        score = 10
        if rec.get("completed") is False or rec.get("status") == "unsubmitted":
            score += 5
        if ddl is not None:
            score += 3
        results.append({**rec, "_score": score})
    results.sort(
        key=lambda r: (
            _parse_datetime(r.get("ddl")) is None,
            (_parse_datetime(r.get("ddl")) or datetime.max.replace(tzinfo=timezone(timedelta(hours=8)))).timestamp(),
            str(r.get("title") or ""),
        )
    )
    return results[:max_results]


# ---------------------------------------------------------------------------
# C-module search (D 调用 C 模块)
# ---------------------------------------------------------------------------

def _search_c_module(
    kb: KnowledgeBase,
    queries: list[str],
    course_hint: str | None,
    top_k: int = 8,
) -> list[dict[str, Any]]:
    """Search knowledge base with multiple queries, deduplicate.  [D → C]"""
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for q in queries:
        try:
            hits = kb.search(query=q, course_name=course_hint, top_k=top_k, mode="keyword")
        except Exception:
            continue
        for h in hits:
            cid = h.get("chunk_id", "")
            if cid and cid not in seen:
                seen.add(cid)
                h["_source_type"] = "课件/资料"
                results.append(h)
    return results


# ---------------------------------------------------------------------------
# B-module material lookup (D → B)
# ---------------------------------------------------------------------------

_MATERIAL_CACHE: list[dict[str, Any]] | None = None


def _load_material_chunks() -> list[dict[str, Any]]:
    """Load all material chunks from B-module output.  [D → B]"""
    global _MATERIAL_CACHE
    if _MATERIAL_CACHE is not None:
        return _MATERIAL_CACHE
    chunks: list[dict[str, Any]] = []
    path = settings.material_chunks_jsonl
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        if isinstance(rec, dict):
                            chunks.append(rec)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
    _MATERIAL_CACHE = chunks
    return chunks


def _find_material_chunks(material_id: str) -> list[dict[str, Any]]:
    """Find all chunks belonging to a specific material.  [D → B]

    material_id can be: file_hash, source_file, or a material_id field.
    """
    all_chunks = _load_material_chunks()
    matched: list[dict[str, Any]] = []
    mid = material_id.strip()
    for c in all_chunks:
        if (c.get("file_hash") == mid
                or c.get("source_file") == mid
                or c.get("material_id") == mid
                or str(c.get("source_file", "")).endswith(mid)
                or str(c.get("source_file", "")).endswith(mid.replace("/", "\\"))):
            matched.append(c)
    # Sort by page/slide then chunk_index
    matched.sort(key=lambda c: (c.get("page") or c.get("slide") or 0, c.get("chunk_index", 0)))
    return matched


# ---------------------------------------------------------------------------
# Context building
# ---------------------------------------------------------------------------

def _format_a_results(records: list[dict[str, Any]]) -> str:
    """Format A-module results into prompt context.  [D → A data]"""
    if not records:
        return ""
    parts: list[str] = []
    for i, r in enumerate(records, 1):
        src = r.get("_source", "unknown")
        src_label = {"tasks": "作业/公告", "emails": "邮件", "schedules": "考试/课表"}.get(src, src)
        title = r.get("title", "无标题")
        course = r.get("course_name", "未知课程")
        content = r.get("content", "")
        meta_lines = []
        if r.get("task_type"):
            meta_lines.append(f"类型: {r.get('task_type')}")
        if r.get("ddl"):
            meta_lines.append(f"截止时间: {r.get('ddl')}")
        if r.get("status"):
            meta_lines.append(f"提交状态: {r.get('status')}")
        if r.get("completed") is not None:
            meta_lines.append(f"是否完成: {r.get('completed')}")
        if r.get("published_at"):
            meta_lines.append(f"发布时间: {r.get('published_at')}")
        meta = "\n".join(meta_lines)
        body = "\n".join(part for part in (meta, content) if part)
        parts.append(f"[A{i}] [{src_label}]《{title}》({course})\n{body}")
    return "\n\n".join(parts)


def _format_c_results(chunks: list[dict[str, Any]]) -> str:
    """Format C-module results into prompt context.  [D → C data]"""
    if not chunks:
        return ""
    parts: list[str] = []
    for i, c in enumerate(chunks, 1):
        title = c.get("title", "无标题")
        course = c.get("course_name", "未知")
        text = c.get("text", "")
        citation = c.get("citation", "")
        parts.append(f"[C{i}] [课件/资料]《{title}》({course})\n{text}\n文件: {citation}")
    return "\n\n---\n\n".join(parts)


def _merge_citations(a_records: list[dict], c_chunks: list[dict]) -> list[dict]:
    """Build citation list from both A and C results."""
    citations: list[dict] = []
    for i, r in enumerate(a_records[:5]):
        snippet_parts = []
        if r.get("ddl"):
            snippet_parts.append(f"截止时间: {r.get('ddl')}")
        if r.get("status"):
            snippet_parts.append(f"提交状态: {r.get('status')}")
        if r.get("completed") is not None:
            snippet_parts.append(f"是否完成: {r.get('completed')}")
        if r.get("content"):
            snippet_parts.append(str(r.get("content"))[:200])
        citations.append({
            "title": r.get("title", "无标题"),
            "source": f"A模块-{r.get('_source', 'unknown')}",
            "snippet": "\n".join(snippet_parts),
        })
    for i, c in enumerate(c_chunks[:5]):
        citations.append({
            "title": c.get("title", "无标题"),
            "source": c.get("citation", "C模块"),
            "snippet": c.get("text", "")[:200],
        })
    return citations


# ---------------------------------------------------------------------------
# Stage 1: Query planning (D → LLM)
# ---------------------------------------------------------------------------

def _fallback_plan(question: str, course_name: str | None) -> dict[str, Any]:
    """Rule-based search plan when LLM planning is unavailable."""
    sources = ["tasks", "emails", "schedules", "knowledge_base"]
    haystack = f"{question} {course_name or ''}"
    if any(token in haystack for token in ("考试", "课表", "期中", "期末")):
        sources = ["schedules", "tasks", "emails", "knowledge_base"]
    elif any(token in haystack for token in ("概念", "原理", "知识点", "怎么", "如何")):
        sources = ["knowledge_base", "tasks"]
    return {
        "queries": [question, course_name or question],
        "sources": sources,
        "course_hint": course_name,
    }


def _plan_query(question: str, course_name: str | None) -> dict[str, Any]:
    """Stage 1: LLM analyzes question → search plan."""
    planning_prompt = PLAN_SYSTEM.format(today=_current_time_text())

    messages = [
        {"role": "system", "content": planning_prompt},
        {"role": "user", "content": f"课程: {course_name or '未指定'}\n问题: {question}"},
    ]
    try:
        return _llm_chat(messages, temperature=0.2)
    except Exception:
        return _fallback_plan(question, course_name)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class ModuleDAdapter:
    """D-module: QA, summarization, homework assistant.

    Module dependencies:
      D → A: _load_all_records(), _search_a_module()  — 任务/邮件/考试数据
      D → C: _search_c_module()                       — 知识库检索
      D → LLM: _plan_query(), _llm_chat()              — 规划与生成
    """

    def __init__(self) -> None:
        self._kb: KnowledgeBase | None = None
        self._ready: bool = False

    def _get_kb(self) -> KnowledgeBase:
        """Lazy-init C-module knowledge base.  [D → C]"""
        if self._kb is None:
            self._kb = KnowledgeBase(
                index_dir=settings.knowledge_index_dir,
                chunks_jsonl=settings.material_chunks_jsonl,
            )
            self._kb.build_if_needed()
            self._ready = self._kb.is_built()
        return self._kb

    def status(self) -> dict[str, Any]:
        has_key = bool(_llm_api_key().strip())
        task_count = len(_load_all_records())
        self._get_kb()
        kb_ready = self._ready
        if not has_key:
            return {
                "status": "missing",
                "llm_configured": False,
                "knowledge_ready": kb_ready,
                "task_records": task_count,
                "message": "未配置 LLM_D_API_KEY",
                "source_module": "D",
            }
        return {
            "status": "ready" if kb_ready or task_count > 0 else "missing",
            "llm_configured": True,
            "knowledge_ready": kb_ready,
            "task_records": task_count,
            "model": _llm_model(),
            "message": "LLM 已配置" if kb_ready else "LLM 已配置，但知识库未建立",
            "source_module": "D",
        }

    def status_badge(self) -> str:
        return str(self.status().get("status", "missing"))

    # ---- Stage 1+2: Plan + Multi-source Search ---------------------------

    def _execute_search(self, plan: dict[str, Any], *, question: str, course_name: str | None) -> dict[str, Any]:
        kb = self._get_kb()
        queries = plan.get("queries", [question])
        sources = plan.get("sources", ["knowledge_base", "tasks", "emails", "schedules"])
        course_hint = course_name or plan.get("course_hint")
        question_text = f"{question} {' '.join(str(q) for q in queries)}"
        deadline_query = _is_homework_deadline_query(question_text)
        if deadline_query:
            sources = list(dict.fromkeys(["tasks", *sources]))
            queries = list(dict.fromkeys([question, *(str(q) for q in queries if q)]))

        a_results: list[dict] = []
        c_results: list[dict] = []

        a_sources = [s for s in sources if s in ("tasks", "emails", "schedules")]
        if deadline_query:
            a_results = _search_pending_homework(course_hint)
            sources = ["tasks", *[s for s in sources if s == "knowledge_base"]]
        elif a_sources:
            combined_query = " ".join(str(q) for q in queries if q)
            a_results = _search_a_module(combined_query, a_sources, course_hint)

        if "knowledge_base" in sources:
            c_results = _search_c_module(kb, queries, course_hint)

        return {
            "queries": queries,
            "sources": sources,
            "course_hint": course_hint,
            "a_results": a_results,
            "c_results": c_results,
        }

    def _search(self, question: str, course_name: str | None) -> dict[str, Any]:
        """Execute plan → A+C search → return combined context.  [D → LLM → A + C]"""
        plan = _plan_query(question, course_name)
        return self._execute_search(plan, question=question, course_name=course_name)

    # ---- QA  [D → LLM plan → A + C search → LLM answer] ------------------

    def qa(self, request: QARequest) -> dict[str, Any]:
        try:
            ctx = self._search(request.question, request.course_name)
        except Exception:
            ctx = self._execute_search(
                _fallback_plan(request.question, request.course_name),
                question=request.question,
                course_name=request.course_name,
            )

        a_ctx = _format_a_results(ctx["a_results"])
        c_ctx = _format_c_results(ctx["c_results"])

        context_blocks = []
        if a_ctx:
            context_blocks.append(f"【A 模块 — 任务/邮件/考试】\n{a_ctx}")
        if c_ctx:
            context_blocks.append(f"【C 模块 — 课件/资料】\n{c_ctx}")
        combined = "\n\n========\n\n".join(context_blocks) if context_blocks else "暂无相关资料。"

        messages = [
            {"role": "system", "content": QA_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"【当前时间】\n{_current_time_text()}\n\n"
                    f"【多源参考资料】\n{combined}\n\n"
                    f"【问题】\n{request.question}"
                ),
            },
        ]

        try:
            parsed = _llm_chat(messages, temperature=0.5)
            return {
                "answer": parsed.get("answer", ""),
                "citations": _merge_citations(ctx["a_results"], ctx["c_results"])
                or parsed.get("citations", []),
                "retrieved": {
                    "a_module": len(ctx["a_results"]),
                    "c_module": len(ctx["c_results"]),
                    "queries_used": ctx.get("queries", []),
                    "sources_used": ctx.get("sources", []),
                },
                "source_module": "D",
                "status": "ready",
            }
        except Exception as exc:
            return _blocked_payload(
                exc,
                answer=f"无法生成回答：{_classify_llm_error(exc)['user_message']}",
                citations=[],
                retrieved={
                    "a_module": len(ctx["a_results"]),
                    "c_module": len(ctx["c_results"]),
                    "queries_used": ctx.get("queries", []),
                    "sources_used": ctx.get("sources", []),
                },
            )

    # ---- Summaries  [D → LLM plan → A + C search → LLM summary] -----------
    #
    #  Two modes:
    #    a) material_id is set → single-file deep analysis  [D → B]
    #    b) material_id empty   → full-course multi-source summary  [D → A + C]

    def summarize(self, request: SummaryRequest) -> dict[str, Any]:
        # --- Mode A: single-file analysis  [D → B] -------------------------
        if request.material_id:
            material_chunks = _find_material_chunks(request.material_id)
            if not material_chunks:
                return {
                    "summary": f"未找到指定文件（material_id={request.material_id}），请重新选择。",
                    "key_points": [],
                    "citations": [],
                    "retrieved": {"a_module": 0, "c_module": 0, "material_chunks": 0},
                    "source_module": "D",
                    "status": "missing",
                }

            file_title = material_chunks[0].get("title", "未知文件")
            file_source = material_chunks[0].get("source_file", "")
            file_course = material_chunks[0].get("course_name", "")
            chunk_texts = [c.get("text", "") for c in material_chunks if c.get("text", "").strip()]
            combined = "\n\n---\n\n".join(chunk_texts)
            user_topic = request.topic or f"分析《{file_title}》的核心内容"

            messages = [
                {"role": "system", "content": DOCUMENT_ANALYSIS_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"【文件信息】\n"
                        f"文件名：{file_title}\n"
                        f"所属课程：{file_course}\n"
                        f"文件路径：{file_source}\n\n"
                        f"【文件内容】\n{combined}\n\n"
                        f"【分析请求】\n{user_topic}"
                    ),
                },
            ]

            try:
                parsed = _llm_chat(messages, temperature=0.4)
                return {
                    "summary": parsed.get("summary", ""),
                    "key_points": parsed.get("key_points", []),
                    "citations": parsed.get("citations", [])
                    or [{"title": file_title, "source": file_source, "snippet": chunk_texts[0][:200] if chunk_texts else ""}],
                    "retrieved": {"a_module": 0, "c_module": 0, "material_chunks": len(material_chunks)},
                    "source_module": "D",
                    "status": "ready",
                }
            except Exception as exc:
                return _blocked_payload(
                    exc,
                    summary=f"无法分析文件：{_classify_llm_error(exc)['user_message']}",
                    key_points=[],
                    citations=[],
                    retrieved={"a_module": 0, "c_module": 0, "material_chunks": len(material_chunks)},
                )

        # --- Mode B: full-course multi-source summary  [D → A + C] -----------
        topic = request.topic or request.course_name or "课程内容总结"
        question = f"总结：{topic}"

        try:
            ctx = self._search(question, request.course_name)
        except Exception:
            ctx = {"a_results": [], "c_results": [], "queries": [], "sources": []}

        c_ctx = _format_c_results(ctx["c_results"])
        a_ctx = _format_a_results(ctx["a_results"])
        context_blocks = []
        if c_ctx:
            context_blocks.append(f"【课件资料】\n{c_ctx}")
        if a_ctx:
            context_blocks.append(f"【相关任务与通知】\n{a_ctx}")
        combined = "\n\n========\n\n".join(context_blocks) or "暂无相关资料。"

        scope = request.topic or request.course_name or "当前课程"
        messages = [
            {"role": "system", "content": SUMMARY_SYSTEM},
            {
                "role": "user",
                "content": f"【当前时间】\n{_current_time_text()}\n\n【多源参考资料】\n{combined}\n\n【总结主题】\n{scope}",
            },
        ]

        try:
            parsed = _llm_chat(messages, temperature=0.4)
            return {
                "summary": parsed.get("summary", ""),
                "key_points": parsed.get("key_points", []),
                "citations": parsed.get("citations", [])
                or _merge_citations(ctx["a_results"], ctx["c_results"]),
                "retrieved": {
                    "a_module": len(ctx["a_results"]),
                    "c_module": len(ctx["c_results"]),
                },
                "source_module": "D",
                "status": "ready",
            }
        except Exception as exc:
            return _blocked_payload(
                exc,
                summary=f"无法生成总结：{_classify_llm_error(exc)['user_message']}",
                key_points=[],
                citations=[],
                retrieved={
                    "a_module": len(ctx["a_results"]),
                    "c_module": len(ctx["c_results"]),
                },
            )

    # ---- Homework Assistant  [D → A (task lookup) → LLM plan → A+C search → LLM] -

    def homework_assistant(self, request: HomeworkAssistantRequest) -> dict[str, Any]:
        # Stage 0: Look up task from A module  [D → A]
        task = None
        if request.task_id:
            for rec in _load_all_records():
                if rec.get("raw_id") == request.task_id:
                    task = rec
                    break

        task_title = task.get("title", "") if task else ""
        task_content = task.get("content", "") if task else ""
        task_course = task.get("course_name", "") if task else ""

        question = f"作业：{task_title}。{request.question}"
        try:
            ctx = self._search(question, task_course)
        except Exception:
            ctx = {"a_results": [], "c_results": [], "queries": [], "sources": []}

        c_ctx = _format_c_results(ctx["c_results"])
        a_ctx = _format_a_results(ctx["a_results"])
        context_blocks = []
        if c_ctx:
            context_blocks.append(f"【课件资料】\n{c_ctx}")
        if a_ctx:
            context_blocks.append(f"【相关任务】\n{a_ctx}")
        combined = "\n\n========\n\n".join(context_blocks) or "暂无相关资料。"

        upload_context = "\n\n".join(text.strip() for text in request.upload_texts if text.strip())
        upload_block = f"\n\n【用户上传材料】\n{upload_context}" if upload_context else ""
        user_msg = (
            f"【当前时间】\n{_current_time_text()}\n\n"
            f"【作业信息 — A 模块】\n标题：{task_title}\n要求：{task_content or '（无详细描述）'}\n\n"
            f"【多源参考资料】\n{combined}\n\n"
            f"{upload_block}\n\n"
            f"【学生问题】\n{request.question}"
        )
        messages = [
            {"role": "system", "content": HOMEWORK_SYSTEM},
            {"role": "user", "content": user_msg},
        ]

        try:
            parsed = _llm_chat(messages, temperature=0.5)
            return {
                "task_id": request.task_id,
                "outline": parsed.get("outline", []),
                "draft": parsed.get("draft", ""),
                "pitfalls": parsed.get("pitfalls", []),
                "checklist": parsed.get("checklist", []),
                "citations": parsed.get("citations", [])
                or _merge_citations(ctx["a_results"], ctx["c_results"]),
                "retrieved": {
                    "a_module": len(ctx["a_results"]),
                    "c_module": len(ctx["c_results"]),
                },
                "source_module": "D",
                "status": "ready",
            }
        except Exception as exc:
            return _blocked_payload(
                exc,
                task_id=request.task_id,
                outline=[],
                draft=f"无法生成作业建议：{_classify_llm_error(exc)['user_message']}",
                citations=[],
                retrieved={
                    "a_module": len(ctx["a_results"]),
                    "c_module": len(ctx["c_results"]),
                },
            )
