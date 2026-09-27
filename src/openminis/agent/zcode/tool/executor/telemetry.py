"""Tool call telemetry — wraps execution with the agent telemetry span.

Ported from: packages/core/src/tool/executor/telemetry.ts
Original package: @zcode/core (tool/executor)

# PORT: ``ToolExecutionSpanWriter`` 来自 ``@zcode/contracts``（未移植），保留为 ``Any``。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..types import ExecutableToolCall, ToolExecutionResult
from .types import ToolExecuteOptions, ToolExecutorDeps

__all__ = ["run_tool_call_with_telemetry"]


async def run_tool_call_with_telemetry(
    deps: ToolExecutorDeps,
    tool_call: ExecutableToolCall,
    options: Optional[ToolExecuteOptions],
    execute: Callable[[Any], Any],
) -> ToolExecutionResult:
    scope = (
        deps.agent_telemetry.start_tool(
            {
                "registeredToolName": tool_call.name,
                "toolCallId": tool_call.id,
            }
        )
        if deps.agent_telemetry is not None
        else None
    )
    if not scope:
        return await execute(None)

    async def _run() -> ToolExecutionResult:
        try:
            return await execute(scope)
        except Exception as error:  # noqa: BLE001
            # 业务执行器会在所有正常返回分支按事实收口；这里只负责连 ToolCallStarted/Error
            # 事件发布都直接抛出的非结构化异常，避免 Span 最终只能标记为 missing_terminal。
            if options is not None and options.signal is not None and options.signal.aborted:
                scope.finish_cancelled("abort_signal")
            else:
                scope.finish_failed("unhandled", "unknown", error)
            raise

    return await scope.run(_run)  # type: ignore[attr-defined]
