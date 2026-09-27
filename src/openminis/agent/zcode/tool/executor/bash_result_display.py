"""Bash result display — bounded head snapshot of Bash output for the UI.

Ported from: packages/core/src/tool/executor/bash-result-display.ts
Original package: @zcode/core (tool/executor)

# PORT: ``BashOutputSchema`` / ``ToolResultDisplayPayload`` 来自 ``@zcode/contracts``（未移植）；
# 保留为 ``Any``，调用方以 ``safeParse`` 形态透传。
"""

from __future__ import annotations

from typing import Any

__all__ = ["create_bash_result_display"]

_DISPLAY_BUDGET_BYTES = 150_000


def create_bash_result_display(output: Any) -> Any:
    from ...contracts.tools import BashOutputSchema  # type: ignore

    parsed = BashOutputSchema.safe_parse(output) if hasattr(BashOutputSchema, "safe_parse") else _safe_parse_fallback(BashOutputSchema, output)
    if not parsed.success:
        return None
    result = parsed.data
    if result.status == "backgrounded" or getattr(result, "isImage", False) or (getattr(result, "structuredContent", None) is not None and len(result.structuredContent) > 0):
        return None
    output_path = getattr(result, "persistedOutputPath", None) or getattr(result, "rawOutputPath", None)
    truncated = result.stdout_truncated is True or result.stderr_truncated is True
    if not truncated and not output_path:
        return None

    # 原因：模型 envelope 会再次缩短正文且隐藏 Bash 的截断事实；Desktop 必须使用
    # 独立的有界头部展示，不能解析模型文案或把文件路径误当协议按需读取 ref。
    text = "\n".join(part for part in [getattr(result, "stdout", None), getattr(result, "stderr", None)] if part)
    exceeds_display_budget = len(text.encode("utf-8")) > _DISPLAY_BUDGET_BYTES
    if exceeds_display_budget:
        # PORT: TS ``new TextDecoder().decode(Buffer.from(text).subarray(0, 150_000), { stream: true })``
        # —— 按字节截断后用 errors="ignore" 丢弃被切半的多字节字符。
        bounded = text.encode("utf-8")[:_DISPLAY_BUDGET_BYTES].decode("utf-8", errors="ignore")
    else:
        bounded = text

    payload: dict[str, Any] = {
        "kind": "bash_output",
        "output": bounded,
        "truncated": truncated or exceeds_display_budget,
    }
    if output_path:
        payload["outputPath"] = output_path
    return payload


def _safe_parse_fallback(schema: Any, output: Any) -> Any:
    # PORT: 兜底：若 ``BashOutputSchema`` 尚未提供 ``safe_parse``，按宽松 record 校验。
    class _R:
        success = True
        data = output

    return _R()
