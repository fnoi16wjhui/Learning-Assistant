"""Mock-first adapter for D-module intelligent applications."""

from __future__ import annotations

from typing import Any

from backend.app.models import HomeworkAssistantRequest, QARequest, SummaryRequest


class ModuleDAdapter:
    """Expose D-module contracts while real intelligent app services are pending."""

    def qa(self, request: QARequest) -> dict[str, Any]:
        course = request.course_name or "当前课程"
        return {
            "answer": f"这是关于“{course}”的 Mock 回答。真实 RAG 问答、引用溯源和多轮对话由 D 模块补齐。",
            "citations": [
                {
                    "title": "Mock 引用",
                    "source": "D module Mock",
                    "snippet": "真实引用应来自 C 模块检索结果。",
                }
            ],
            "source_module": "D",
            "status": "mock",
        }

    def summarize(self, request: SummaryRequest) -> dict[str, Any]:
        scope = request.topic or request.material_id or request.course_name or "当前资料"
        return {
            "summary": f"这是“{scope}”的 Mock 总结。真实资料总结由 D 模块基于 C 的检索能力实现。",
            "key_points": ["课程背景", "核心概念", "待复习内容"],
            "citations": [],
            "source_module": "D",
            "status": "mock",
        }

    def homework_assistant(self, request: HomeworkAssistantRequest) -> dict[str, Any]:
        return {
            "task_id": request.task_id,
            "outline": ["理解作业要求", "召回相关知识点", "形成解题步骤", "整理草稿"],
            "draft": "这是 Mock 作业助手草稿。真实题目理解、知识召回和步骤提示由 D 模块补齐。",
            "citations": [],
            "source_module": "D",
            "status": "mock",
        }
