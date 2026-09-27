"""Input normalization — tool input JSON-string normalization + runtime schema pre-pass.

Ported from: packages/core/src/tool/input-normalization.ts
Original package: @zcode/core (tool)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from .types import ToolEntry

__all__ = [
    "RuntimeInputValidationIssue",
    "normalize_tool_execution_input",
    "prepare_initial_tool_execution_input",
]

#: PORT: upstream ``type RuntimeInputValidationIssue = Readonly<Record<string, unknown>>``.
RuntimeInputValidationIssue = dict[str, Any]

logger = logging.getLogger(__name__)


def normalize_tool_execution_input(
    entry: ToolEntry,
    input: Any,
    source: str,
    logger_: Optional[Any] = None,
) -> Any:
    """Mirrors ``normalizeToolExecutionInput({ entry, input, source, logger? })``.

    # PORT: TS 的 options 对象 → Python 具名参数（entry / input / source / logger_）。
    """
    return _prepare_tool_execution_input(entry, input, source, logger_).get("input")


def prepare_initial_tool_execution_input(
    entry: ToolEntry,
    input: Any,
    logger_: Optional[Any] = None,
) -> dict[str, Any]:
    """Mirrors ``prepareInitialToolExecutionInput`` (source fixed to ``"initial"``)."""
    out = _prepare_tool_execution_input(entry, input, "initial", logger_)
    return {
        "input": out.get("input"),
        **({"runtimeValidationIssues": out["runtimeValidationIssues"]}
           if out.get("runtimeValidationIssues") else {}),
    }


def _prepare_tool_execution_input(
    entry: ToolEntry,
    input: Any,
    source: str,
    logger_: Optional[Any],
) -> dict[str, Any]:
    json_normalized = _normalize_top_level_json_string(input, entry, source, logger_)
    runtime_schema = _as_safe_parse_schema(
        getattr(entry, "runtime_input_schema", None)
    )
    if runtime_schema is None:
        return {"input": json_normalized}

    parsed = runtime_schema.safe_parse(json_normalized)
    success = parsed.get("success") if isinstance(parsed, dict) else getattr(
        parsed, "success", None
    )
    if not success:
        # runtime schema 已经完成默认值、preprocess 和约束判断；失败时若只
        # 返回 raw input，后续 JSON Schema 会重新推导一份不完整且顺序不同的错误。
        error = parsed.get("error") if isinstance(parsed, dict) else getattr(
            parsed, "error", None
        )
        runtime_validation_issues = _read_runtime_validation_issues(error)
        out: dict[str, Any] = {"input": json_normalized}
        if runtime_validation_issues:
            out["runtimeValidationIssues"] = runtime_validation_issues
        return out

    data = parsed.get("data") if isinstance(parsed, dict) else getattr(parsed, "data", None)
    return {"input": data}


def _normalize_top_level_json_string(
    input: Any, entry: ToolEntry, source: str, logger_: Optional[Any]
) -> Any:
    """Some model adapters and hook/broker paths surface tool inputs as JSON
    strings. Normalize safely so bad input degrades into a recoverable
    validation error rather than crashing the executor before it can return
    a structured tool failure.
    """
    if not isinstance(input, str):
        return input

    try:
        return json.loads(input)
    except Exception:
        log = logger_ or logger
        warn = getattr(log, "warning", None) or getattr(log, "warn", None)
        if warn is not None:
            warn(
                "Tool execution input JSON normalization failed",
                extra={
                    "event": "tool.input.normalize_failed",
                    "inputLength": len(input),
                    "module": "core.tool.input-normalization",
                    "source": source,
                    "status": "failed",
                    "toolName": getattr(getattr(entry, "metadata", None), "name", None),
                },
            )
        return input


def _as_safe_parse_schema(value: Any) -> Optional[Any]:
    # PORT: TS structural check ``typeof value.safeParse === "function"``;
    # Python 侧保持同一鸭子判定（zod schema / 轻量 schema 替身都走这条路）。
    if value is None or not isinstance(value, (dict, list)) and not hasattr(
        value, "safe_parse"
    ):
        # dict/list 不是 schema；带 safe_parse 的对象才是。
        return None
    safe_parse = getattr(value, "safe_parse", None)
    if callable(safe_parse):
        return value
    safe_parse_fn = value.get("safeParse") if isinstance(value, dict) else None
    if callable(safe_parse_fn):
        # PORT: 上游返回只带 ``safeParse`` 的窄接口；Python 直接原样返回宿主对象
        # （调用点只读 safe_parse / safeParse），语义等价。
        return value
    return None


def _read_runtime_validation_issues(error: Any) -> list[RuntimeInputValidationIssue]:
    if not isinstance(error, dict) and not (isinstance(error, object) and not isinstance(error, (str, int, float, bool))):
        return []
    issues = error.get("issues") if isinstance(error, dict) else getattr(error, "issues", None)
    if not isinstance(issues, list):
        return []
    return [
        issue
        for issue in issues
        if isinstance(issue, dict)
    ]
