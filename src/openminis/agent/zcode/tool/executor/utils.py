"""Executor utils — small pure helpers shared by the tool executor.

Ported from: packages/core/src/tool/executor/utils.ts
Original package: @zcode/core (tool/executor)
"""

from __future__ import annotations

import json
from typing import Any

__all__ = [
    "is_record",
    "summarize_input",
    "preview_hook_value",
]


def is_record(value: Any) -> bool:
    """Mirrors ``isRecord(value)``.

    # PORT: TS 的类型守卫 ``value is Record<string, unknown>`` 在 Python 里没有运行时对应；
    # 只保留判定语义（dict 视为 record，list / 其它一律不算）。
    """
    return isinstance(value, dict)


def summarize_input(input: Any) -> dict[str, Any]:
    """Mirrors ``summarizeInput(input)``."""
    if input is None:
        return {"type": "null"}
    if isinstance(input, list):
        return {"type": "array", "length": len(input)}
    if isinstance(input, dict):
        return {
            "type": "object",
            "keys": list(input.keys())[:20],
        }
    if isinstance(input, str):
        return {"type": "string", "length": len(input)}
    return {"type": _js_typeof(input)}


def preview_hook_value(value: Any) -> str:
    """Mirrors ``previewHookValue(value)``."""
    try:
        serialized = value if isinstance(value, str) else json.dumps(value, default=str)
        if not serialized:
            return ""
        if len(serialized) <= 4000:
            return serialized
        return f"{serialized[:4000]}...[truncated]"
    except Exception:
        return str(value)


def _js_typeof(value: Any) -> str:
    """Mirrors JS ``typeof`` for the primitive tail of :func:`summarize_input`.

    # PORT: Python 没有 ``undefined``/``symbol``/``bigint``；``None`` 已被 ``summarizeInput``
    的 ``input === null`` 分支提前拦截，故这里只需覆盖 number / boolean。
    """
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__
