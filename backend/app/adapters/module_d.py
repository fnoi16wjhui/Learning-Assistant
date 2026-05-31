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

LLM_BASE_URL = os.getenv("LLM_D_BASE_URL", "https://llmapi.paratera.com").rstrip("/")
LLM_API_KEY = os.getenv("LLM_D_API_KEY", "")
LLM_MODEL = os.getenv("LLM_D_MODEL", "deepseek-chat")
LLM_TIMEOUT = int(os.getenv("LLM_D_TIMEOUT", "60"))
LLM_MAX_TOKENS = int(os.getenv("LLM_D_MAX_TOKENS", "3072"))

_client: openai.OpenAI | None = None

# Current semester cutoff: ignore content from before 2025-09-01 (上学期 + 上上学期)
SEMESTER_CUTOFF = "2025-09-01"

def _get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        _client = openai.OpenAI(
            api_key=LLM_API_KEY,
            base_url=f"{LLM_BASE_URL}/v1/",
            timeout=LLM_TIMEOUT,
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
{"queries": ["改写后的搜索词1", "搜索词2"], "sources": ["knowledge_base", "tasks", "emails", "schedules"], "course_hint": "提取的课程名或null"}"""

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


def _llm_chat(messages: list[dict[str, str]], temperature: float = 0.5) -> dict[str, Any]:
    client = _get_client()
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=LLM_MAX_TOKENS,
        response_format={"type": "json_object"},
    )
    return _extract_json(response.choices[0].message.content or "")

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
    for path in [settings.learn_jsonl, settings.mail_jsonl, settings.jwch_jsonl]:
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
                        if isinstance(rec, dict):
                            # Tag source for routing
                            if "schedule_type" in rec:
                                rec["_source"] = "schedules"
                            elif path.name == "mail.jsonl":
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
            "source": f"A模块-{r.get('_source', 'unknown')}",
            "snippet": (r.get("content") or "")[:200],
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

def _plan_query(question: str, course_name: str | None) -> dict[str, Any]:
    """Stage 1: LLM analyzes question → search plan."""
    tz = timezone(timedelta(hours=8))
    today = datetime.now(tz).strftime("%Y年%m月%d日 %A")
    planning_prompt = PLAN_SYSTEM.format(today=today)

    messages = [
        {"role": "system", "content": planning_prompt},
        {"role": "user", "content": f"课程: {course_name or '未指定'}\n问题: {question}"},
    ]
    return _llm_chat(messages, temperature=0.2)


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
            combined_query = " ".join(queries)
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

        scope = request.topic or request.course_name or request.material_id or "当前课程"
        messages = [
            {"role": "system", "content": SUMMARY_SYSTEM},
            {"role": "user", "content": f"【多源参考资料】\n{combined}\n\n【总结主题】\n{scope}"},
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

        user_msg = (
            f"【作业信息 — A 模块】\n标题：{task_title}\n要求：{task_content or '（无详细描述）'}\n\n"
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
