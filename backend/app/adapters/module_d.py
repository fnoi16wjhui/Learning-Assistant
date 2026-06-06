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

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import jieba
import openai

from backend.app.models import HomeworkAssistantRequest, QARequest, SummaryRequest
from backend.app.settings import settings
from src.knowledge.knowledge_base import KnowledgeBase

# ---------------------------------------------------------------------------
# LLM config (reloadable — file overrides env, API can update at runtime)
# ---------------------------------------------------------------------------

_LLM_CONFIG_FILE = Path(__file__).resolve().parents[3] / "storage" / "llm_config.json"
_LLM_CONFIG_DEFAULTS: dict[str, Any] = {
    "base_url": "https://llmapi.paratera.com",
    "api_key": "",
    "model": "deepseek-chat",
    "timeout": 60,
    "max_tokens": 3072,
}
_llm_config: dict[str, Any] = {}
_client: openai.OpenAI | None = None

# Current semester cutoff: ignore content from before 2025-09-01 (上学期 + 上上学期)
SEMESTER_CUTOFF = "2025-09-01"


def _load_llm_config() -> dict[str, Any]:
    """Load LLM config from file, falling back to env vars and defaults."""
    cfg: dict[str, Any] = {}
    if _LLM_CONFIG_FILE.exists():
        try:
            saved = json.loads(_LLM_CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                cfg.update(saved)
        except (json.JSONDecodeError, OSError):
            pass
    cfg.setdefault("base_url", os.getenv("LLM_D_BASE_URL", _LLM_CONFIG_DEFAULTS["base_url"]).rstrip("/"))
    cfg.setdefault("api_key", os.getenv("LLM_D_API_KEY", _LLM_CONFIG_DEFAULTS["api_key"]))
    cfg.setdefault("model", os.getenv("LLM_D_MODEL", _LLM_CONFIG_DEFAULTS["model"]))
    cfg.setdefault("timeout", int(os.getenv("LLM_D_TIMEOUT", str(_LLM_CONFIG_DEFAULTS["timeout"]))))
    cfg.setdefault("max_tokens", int(os.getenv("LLM_D_MAX_TOKENS", str(_LLM_CONFIG_DEFAULTS["max_tokens"]))))
    return cfg


def _save_llm_config(cfg: dict[str, Any]) -> None:
    _LLM_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _LLM_CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_llm_config() -> dict[str, Any]:
    """Return current LLM config (API key masked)."""
    cfg = dict(_llm_config)
    key = cfg.get("api_key", "")
    if key:
        cfg["api_key"] = key[:4] + "****" + key[-4:] if len(key) > 8 else "****"
    return cfg


def update_llm_config(updates: dict[str, Any]) -> dict[str, Any]:
    """Update LLM config at runtime, persist to file, recreate client."""
    global _client, _llm_config
    allowed = {"base_url", "api_key", "model", "timeout", "max_tokens"}
    for key in updates:
        if key in allowed and updates[key]:
            _llm_config[key] = updates[key] if key == "api_key" else updates[key]
    if "base_url" in updates and updates["base_url"]:
        _llm_config["base_url"] = str(updates["base_url"]).rstrip("/")
    if "timeout" in updates:
        try:
            _llm_config["timeout"] = int(updates["timeout"])
        except (ValueError, TypeError):
            pass
    if "max_tokens" in updates:
        try:
            _llm_config["max_tokens"] = int(updates["max_tokens"])
        except (ValueError, TypeError):
            pass
    _save_llm_config(_llm_config)
    _client = None  # force recreate
    return get_llm_config()


# Load at import time
_llm_config = _load_llm_config()


def _get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        _client = openai.OpenAI(
            api_key=_llm_config["api_key"],
            base_url=f"{_llm_config['base_url']}/v1/",
            timeout=_llm_config["timeout"],
        )
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
你是一个专业的课程学习助手。根据用户指定的资料生成结构化总结或回答针对资料的问题。

## 回答要求
1. 用户要求总结时，提炼资料中最核心的课程要点；用户提出具体问题时，直接根据资料回答。
2. 按知识体系组织，标注 4-7 个关键要点。
3. 每个重要概念标明出处。
4. 指定文件时只能使用该文件的内容，不要混入其他资料。

请严格返回 JSON：
{"summary": "详细总结", "key_points": ["要点1", ...], "citations": [{"title": "来源标题", "source": "数据来源类型与路径", "snippet": "引用片段"}]}"""

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


# ---------------------------------------------------------------------------
# LLM response cache
# ---------------------------------------------------------------------------

_LLM_CACHE_DIR = Path(__file__).resolve().parents[3] / "storage" / "llm_cache"
_LLM_CACHE_TTL = int(os.getenv("LLM_D_CACHE_TTL", "86400"))  # 24 hours


def _cache_key(model: str, temperature: float, messages: list[dict[str, str]]) -> str:
    payload = json.dumps(
        {"model": model, "temperature": temperature, "messages": messages},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> dict[str, Any] | None:
    cache_file = _LLM_CACHE_DIR / f"{key}.json"
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    created = data.get("_created", 0)
    if time.time() - created > _LLM_CACHE_TTL:
        return None
    return data.get("response")


def _cache_set(key: str, response: dict[str, Any]) -> None:
    _LLM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _LLM_CACHE_DIR / f"{key}.json"
    cache_file.write_text(
        json.dumps(
            {"_key": key, "_created": time.time(), "response": response},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _llm_chat(
    messages: list[dict[str, str]],
    temperature: float = 0.5,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    key = _cache_key(_llm_config["model"], temperature, messages)
    if use_cache:
        cached = _cache_get(key)
        if cached is not None:
            return cached

    client = _get_client()
    response = client.chat.completions.create(
        model=_llm_config["model"],
        messages=messages,
        temperature=temperature,
        max_tokens=_llm_config["max_tokens"],
        response_format={"type": "json_object"},
    )
    result = _extract_json(response.choices[0].message.content or "")

    if use_cache:
        _cache_set(key, result)
    return result

# ---------------------------------------------------------------------------
# A-module data loaders (D 调用 A 模块)
# ---------------------------------------------------------------------------

_TASK_CACHE: list[dict[str, Any]] | None = None
_TASK_CACHE_TIME: datetime | None = None


def _load_all_records() -> list[dict[str, Any]]:
    """Load all A-module records (learn + mail + jwch).  [D → A]

    Schedule records (jwch.jsonl) are filtered to only include courses
    that appear in learn.jsonl, since jwch currently contains all-school data.
    """
    global _TASK_CACHE, _TASK_CACHE_TIME
    now = datetime.now(timezone.utc)
    if _TASK_CACHE is not None and _TASK_CACHE_TIME is not None:
        if (now - _TASK_CACHE_TIME).total_seconds() < 300:
            return _TASK_CACHE

    records: list[dict[str, Any]] = []
    user_courses: set[str] = set()

    # Phase 1: load learn + mail → discover user's enrolled courses
    for path in [settings.learn_jsonl, settings.mail_jsonl]:
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
                    except json.JSONDecodeError:
                        continue
                    source = "emails" if path.name == "mail.jsonl" else "tasks"
                    rec["_source"] = source
                    records.append(rec)
                    course = str(rec.get("course_name") or "").strip()
                    if course and course != "Unknown Course":
                        user_courses.add(course)
        except OSError:
            continue

    # Phase 2: load schedules → only keep matching courses
    if settings.jwch_jsonl.exists():
        try:
            with settings.jwch_jsonl.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        if not isinstance(rec, dict):
                            continue
                    except json.JSONDecodeError:
                        continue
                    course = str(rec.get("course_name") or "").strip()
                    if course in user_courses:
                        rec["_source"] = "schedules"
                        records.append(rec)
        except OSError:
            pass

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
        for term in set(jieba.cut(query_lower)):
            term = term.strip()
            if not term or len(term) < 2:
                continue
            if term in title:
                score += 3
            if term in content:
                score += 2
            if term in course:
                score += 2

        # Boost homework-type records when query mentions 作业/提交/截止
        if rec.get("task_type") == "homework":
            score += 5

        # Boost if course_hint matches
        if course_hint and course_hint.lower() in course:
            score += 5

        if score > 0:
            results.append({**rec, "_score": score})

    results.sort(key=lambda r: r["_score"], reverse=True)
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
            hits = kb.search(query=q, course_name=course_hint, top_k=top_k, mode="hybrid")
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
        parts.append(f"[A{i}] [{src_label}]《{title}》({course})\n{content}")
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
        citations.append({
            "title": r.get("title", "无标题"),
            "source": f"任务与通知-{r.get('_source', 'unknown')}",
            "snippet": (r.get("content") or "")[:200],
        })
    for i, c in enumerate(c_chunks[:5]):
        citations.append({
            "title": c.get("title", "无标题"),
            "source": c.get("citation", "课程资料"),
            "snippet": c.get("text", "")[:200],
        })
    return citations


def _load_material_chunks(material_id: str, query: str) -> list[dict[str, Any]]:
    """Load and rank chunks belonging to one exact material file."""

    if not settings.material_chunks_jsonl.exists():
        return []
    records: list[dict[str, Any]] = []
    with settings.material_chunks_jsonl.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            if material_id not in {
                str(item.get("file_hash") or ""),
                str(item.get("source_file") or ""),
            }:
                continue
            enriched = dict(item)
            enriched["citation"] = str(item.get("source_file") or item.get("title") or "课程资料")
            enriched["_source_type"] = "指定文件"
            records.append(enriched)
    return _select_material_chunks(records, query)


def _select_material_chunks(
    records: list[dict[str, Any]],
    query: str,
    *,
    max_chunks: int = 20,
    max_chars: int = 24000,
) -> list[dict[str, Any]]:
    """Select relevant chunks while keeping broad summaries representative."""

    if not records:
        return []
    ordered = sorted(records, key=lambda item: int(item.get("chunk_index") or 0))
    terms = _query_terms(query)
    generic_terms = {"总结", "概括", "梳理", "主要内容", "核心内容", "知识点"}
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in ordered:
        text = str(item.get("text") or "").lower()
        score = sum(text.count(term) for term in terms if term not in generic_terms)
        scored.append((score, item))

    if any(score > 0 for score, _ in scored):
        selected = [
            item
            for _, item in sorted(
                scored,
                key=lambda pair: (-pair[0], int(pair[1].get("chunk_index") or 0)),
            )[:max_chunks]
        ]
        selected.sort(key=lambda item: int(item.get("chunk_index") or 0))
    elif len(ordered) <= max_chunks:
        selected = ordered
    else:
        indexes = {
            round(index * (len(ordered) - 1) / (max_chunks - 1))
            for index in range(max_chunks)
        }
        selected = [ordered[index] for index in sorted(indexes)]

    limited: list[dict[str, Any]] = []
    used_chars = 0
    for item in selected:
        text = str(item.get("text") or "")
        if not text:
            continue
        remaining = max_chars - used_chars
        if remaining <= 0:
            break
        copy = dict(item)
        copy["text"] = text[:remaining]
        limited.append(copy)
        used_chars += len(copy["text"])
    return limited


def _query_terms(query: str) -> set[str]:
    lowered = query.lower()
    terms = {
        match
        for match in re.findall(r"[a-z0-9_+-]{2,}|[\u4e00-\u9fff]{2,}", lowered)
        if match
    }
    for chinese in re.findall(r"[\u4e00-\u9fff]{3,}", lowered):
        terms.update(chinese[index : index + 2] for index in range(len(chinese) - 1))
    return terms


# ---------------------------------------------------------------------------
# Stage 1: Query planning (D → LLM)
# ---------------------------------------------------------------------------

def _plan_query(question: str, course_name: str | None) -> dict[str, Any]:
    """Stage 1: LLM analyzes question → search plan."""
    tz = timezone(timedelta(hours=8))
    today = datetime.now(tz).strftime("%Y年%m月%d日 %A")
    planning_prompt = PLAN_SYSTEM.format(today=today)

    messages = [
        {"role": "system", "content": planning_prompt},
        {"role": "user", "content": f"课程: {course_name or '未指定'}\n问题: {question}"},
    ]
    return _llm_chat(messages, temperature=0.2, use_cache=False)


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

    def status(self) -> str:
        self._get_kb()
        return "ready" if self._ready else "missing"

    # ---- Stage 1+2: Plan + Multi-source Search ---------------------------

    def _search(self, question: str, course_name: str | None) -> dict[str, Any]:
        """Execute plan → A+C search → return combined context.  [D → LLM → A + C]"""
        kb = self._get_kb()

        # Stage 1: Plan  [D → LLM]
        plan = _plan_query(question, course_name)
        queries = plan.get("queries", [question])
        sources = plan.get("sources", ["knowledge_base", "tasks", "emails", "schedules"])
        course_hint = plan.get("course_hint") or course_name

        # Stage 2: Search
        a_results: list[dict] = []
        c_results: list[dict] = []

        # A-module sources
        a_sources = [s for s in sources if s in ("tasks", "emails", "schedules")]
        if a_sources:
            combined_query = " ".join(q for q in queries if q.strip())
            a_results = _search_a_module(combined_query, a_sources, course_hint)

        # C-module source  [D → C]
        if "knowledge_base" in sources:
            c_results = _search_c_module(kb, queries, course_hint)

        return {
            "queries": queries,
            "sources": sources,
            "course_hint": course_hint,
            "a_results": a_results,
            "c_results": c_results,
        }

    # ---- QA  [D → LLM plan → A + C search → LLM answer] ------------------

    def qa(self, request: QARequest) -> dict[str, Any]:
        try:
            ctx = self._search(request.question, request.course_name)
        except Exception:
            ctx = {"a_results": [], "c_results": [], "queries": [], "sources": []}

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
            {"role": "user", "content": f"【多源参考资料】\n{combined}\n\n【问题】\n{request.question}"},
        ]

        try:
            parsed = _llm_chat(messages, temperature=0.5)
            return {
                "answer": parsed.get("answer", ""),
                "citations": parsed.get("citations", [])
                or _merge_citations(ctx["a_results"], ctx["c_results"]),
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
            return {
                "answer": f"LLM 调用失败：{exc}",
                "citations": [],
                "retrieved": {"a_module": len(ctx["a_results"]), "c_module": len(ctx["c_results"])},
                "source_module": "D",
                "status": "blocked",
            }

    # ---- Summaries  [D → LLM plan → A + C search → LLM summary] -----------

    def summarize(self, request: SummaryRequest) -> dict[str, Any]:
        topic = request.topic or request.course_name or "课程内容总结"
        question = topic if request.material_id else f"总结：{topic}"

        if request.material_id:
            c_results = _load_material_chunks(request.material_id, question)
            # Filter by page/slide if requested
            if request.page is not None:
                c_results = [c for c in c_results if c.get("page") == request.page]
            if request.slide is not None:
                c_results = [c for c in c_results if c.get("slide") == request.slide]
            if not c_results:
                scope = f"第{request.page}页" if request.page else f"第{request.slide}张幻灯片" if request.slide else "所选文件"
                return {
                    "summary": f"未找到{scope}的可用解析内容。",
                    "key_points": [],
                    "citations": [],
                    "retrieved": {
                        "a_module": 0,
                        "c_module": 0,
                        "material_id": request.material_id,
                    },
                    "source_module": "D",
                    "status": "blocked",
                }
            ctx = {
                "a_results": [],
                "c_results": c_results,
                "queries": [question],
                "sources": ["selected_material"],
            }
        else:
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
        if request.material_id and request.page:
            material_instruction = f"仅使用上方指定文件第 {request.page} 页的内容回答。"
        elif request.material_id and request.slide:
            material_instruction = f"仅使用上方指定文件第 {request.slide} 张幻灯片的内容回答。"
        elif request.material_id:
            material_instruction = "仅使用上方指定文件回答。"
        else:
            material_instruction = "综合使用上方多源资料回答。"
        messages = [
            {"role": "system", "content": SUMMARY_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"【参考资料】\n{combined}\n\n"
                    f"【用户要求或问题】\n{scope}\n\n"
                    f"【范围约束】\n{material_instruction}"
                ),
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
                    "material_id": request.material_id,
                },
                "source_module": "D",
                "status": "ready",
            }
        except Exception as exc:
            return {
                "summary": f"LLM 调用失败：{exc}",
                "key_points": [],
                "citations": [],
                "source_module": "D",
                "status": "blocked",
            }

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

        upload_context = ""
        if request.upload_texts:
            upload_blocks = []
            for i, text in enumerate(request.upload_texts):
                if text.strip():
                    upload_blocks.append(f"[上传文件 {i + 1}]\n{text.strip()}")
            if upload_blocks:
                upload_context = "【学生上传的补充材料】\n" + "\n\n".join(upload_blocks) + "\n\n"

        user_msg = (
            f"【作业信息 — A 模块】\n标题：{task_title}\n要求：{task_content or '（无详细描述）'}\n\n"
            f"{upload_context}"
            f"【多源参考资料】\n{combined}\n\n"
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
            return {
                "task_id": request.task_id,
                "outline": [],
                "draft": f"LLM 调用失败：{exc}",
                "citations": [],
                "source_module": "D",
                "status": "blocked",
            }
