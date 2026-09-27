"""工具批量执行 / 调度（并行组、串行组、turn-stop 短路）。

Ported from: packages/core/src/tool/executor/batch-runner.ts
Original package: @zcode/core (tool/executor)
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator, Awaitable, Callable, Optional

from ...contracts.errors import CoreErrorType, create_core_error  # type: ignore
from ...contracts.tracing import (  # type: ignore
    create_root_trace_context,
    trace_context_to_log_context,
)
from ..scheduler import ToolSchedule
from ..types import ExecutableToolCall, ToolBatchEvent, ToolExecutionResult
from .errors import create_error_result
from .events import emit_tool_call_error

TOOL_CANCELLED_AFTER_TURN_STOP = "Tool cancelled because a previous tool result requested a turn stop."

ExecuteToolCall = Callable[[ExecutableToolCall, Optional[Any]], Awaitable[ToolExecutionResult]]


async def execute_tool_batch(
    deps: Any,
    execute_one: ExecuteToolCall,
    tool_calls: list[ExecutableToolCall],
    options: Optional[Any] = None,
) -> list[ToolExecutionResult]:
    """工具批量执行：并发度内直接全跑，超出则按 maxConcurrency 分片串行。"""
    max_concurrency = (
        options.max_concurrency
        if options is not None and options.max_concurrency is not None
        else deps.max_concurrency
    )

    if len(tool_calls) <= max_concurrency:
        return await asyncio.gather(*(execute_one(tc, options) for tc in tool_calls))

    results: list[ToolExecutionResult] = []
    for i in range(0, len(tool_calls), max_concurrency):
        batch = tool_calls[i : i + max_concurrency]
        batch_results = await asyncio.gather(
            *(execute_one(tc, options) for tc in batch)
        )
        results.extend(batch_results)
    return results


async def execute_tool_schedule(
    deps: Any,
    execute_batch: Callable[[list[ExecutableToolCall], Optional[Any]], Awaitable[list[ToolExecutionResult]]],
    tool_calls: list[ExecutableToolCall],
    schedule: ToolSchedule,
    options: Optional[Any] = None,
) -> AsyncGenerator[ToolBatchEvent, None]:
    """按 ``parallelGroups`` 顺序执行：组内并发、组间串行；任一结果请求 turn-stop 则短路剩余组。

    # PORT: TS ``AsyncGenerator<ToolBatchEvent, ToolExecutionResult[]>`` 的返回值（allResults）在
    # Python 异步生成器里无法 ``return`` 带出；事件经 ``yield`` 流出，``batch_complete`` 事件已携带
    # 本组结果，消费者（``executeSchedule``）只迭代事件，故返回值在此丢弃（与协议返回 ``None`` 一致）。
    """
    from ..types import ToolBatchCompleteEvent, ToolBatchStartEvent

    tool_map = {tc.id: tc for tc in tool_calls}
    all_results: list[ToolExecutionResult] = []
    max_concurrency = (
        options.max_concurrency
        if options is not None and options.max_concurrency is not None
        else deps.max_concurrency
    )

    for group_index in range(len(schedule.parallel_groups)):
        group = schedule.parallel_groups[group_index]
        group_tools = [tool_map[tool_id] for tool_id in group if tool_map.get(tool_id) is not None]

        if len(group_tools) == 0:
            continue

        yield ToolBatchStartEvent(parallel_group_index=group_index, tool_call_ids=group)

        group_results = await execute_batch(
            group_tools,
            options,
        )
        all_results.extend(group_results)

        yield ToolBatchCompleteEvent(parallel_group_index=group_index, results=group_results)

        should_stop_turn = any(
            r.turn_control is not None and r.turn_control.stop_turn_after_result for r in group_results
        )
        if should_stop_turn:
            skipped_tools = [
                tool_map[tool_id]
                for gid in schedule.parallel_groups[group_index + 1 :]
                for tool_id in gid
                if tool_map.get(tool_id) is not None
            ]
            event_trace_context = (
                options.trace_context
                if options is not None and options.trace_context is not None
                else (
                    deps.trace_context
                    if deps.trace_context is not None
                    else create_root_trace_context(session_id=deps.session_id, turn_id=deps.turn_id)
                )
            )
            turn_id = (
                event_trace_context.turn_id
                if getattr(event_trace_context, "turn_id", None) is not None
                else deps.turn_id
            )

            for tool_call in skipped_tools:
                result = create_error_result(
                    tool_call,
                    create_core_error(
                        CoreErrorType.TOOL_CANCELLED,
                        TOOL_CANCELLED_AFTER_TURN_STOP,
                        {
                            "context": {"toolCallId": tool_call.id, "toolName": tool_call.name},
                            "recoverable": True,
                        },
                    ),
                )
                all_results.append(result)
                await emit_tool_call_error(deps, result.tool_call_id, event_trace_context, turn_id, result.error)
                deps.logger and getattr(deps.logger, "warn", None) and deps.logger.warn(
                    "Tool call cancelled after turn stop",
                    {
                        **trace_context_to_log_context(event_trace_context),
                        "event": "tool.call.cancelled_after_turn_stop",
                        "module": "core.tool.executor",
                        "status": "cancelled",
                        "toolCallId": result.tool_call_id,
                        "toolName": result.tool_name,
                    },
                )

            break

    return
