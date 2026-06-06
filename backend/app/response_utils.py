"""Unified E-module response helpers for failure isolation."""

from __future__ import annotations

from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def module_response(
    *,
    source_module: str,
    status: str = "ready",
    items: list[dict[str, Any]] | None = None,
    total: int | None = None,
    warnings: list[str] | None = None,
    errors: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source_module": source_module,
        "status": status,
        "warnings": warnings or [],
        "errors": errors or [],
    }
    if items is not None:
        payload["items"] = items
    if total is not None:
        payload["total"] = total
    payload.update(extra)
    return payload


def safe_call(
    source_module: str,
    fn: Callable[[], dict[str, Any]],
    *,
    fallback_status: str = "blocked",
    user_message: str = "模块暂时不可用，请稍后重试。",
) -> dict[str, Any]:
    try:
        result = fn()
        if "source_module" not in result:
            result["source_module"] = source_module
        result.setdefault("warnings", [])
        result.setdefault("errors", [])
        return result
    except Exception as exc:
        return module_response(
            source_module=source_module,
            status=fallback_status,
            warnings=[],
            errors=[
                {
                    "error_code": "module_exception",
                    "user_message": user_message,
                    "detail": str(exc),
                    "retryable": True,
                }
            ],
        )


def normalize_list_items(
    items: list[dict[str, Any]],
    *,
    max_items: int,
    demo_mode: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Apply display limits and demo-mode filtering; return warnings."""
    warnings: list[str] = []
    filtered = items

    if demo_mode:
        filtered = [
            item
            for item in items
            if not item.get("data_quality_tag") or item.get("data_quality_tag") not in ("old_semester", "low_priority", "parse_failed")
        ]
        if len(filtered) < len(items):
            warnings.append(f"演示模式已隐藏 {len(items) - len(filtered)} 条低质量/旧学期记录。")

    if len(filtered) > max_items:
        warnings.append(f"仅展示前 {max_items} 条，共 {len(filtered)} 条可用。")

    return filtered[:max_items], warnings
