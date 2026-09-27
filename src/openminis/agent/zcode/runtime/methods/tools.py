"""Tool scheduling and execution methods.

Ported from: packages/core/src/runtime/methods/tools.ts
Original package: @zcode/core (runtime/methods)
"""

from __future__ import annotations

from typing import Any, Awaitable, Optional

from ..deps import (
    MessageId,
    SessionEvent,
    SessionEventType,
    ToolCall,
    ToolCallId,
    TraceContext,
)

__all__ = [
    "schedule_tools",
    "execute_tools",
    "emit_tool_scheduled_events",
    "emit_file_mutation_checkpoint",
    "emit_permission_request",
    "resolve_permission",
]


async def schedule_tools(
    runtime: Any,
    tool_calls: list[ToolCall],
) -> Any:
    """Mirrors ``scheduleTools`` — schedules tool calls for execution.

    Builds tool dependencies from registry metadata and schedules using
    the tool scheduler.

    Args:
        runtime: The AgentRuntimeInternal instance
        tool_calls: The tool calls to schedule

    Returns:
        A ToolSchedule with parallel groups and execution order
    """
    dependencies = []
    for tc in tool_calls:
        entry = runtime.registry.get(tc.name) if runtime.registry else None
        metadata = getattr(entry, "metadata", None) if entry else None

        side_effect_scope = getattr(entry, "permission", None)
        if side_effect_scope is None and metadata is not None:
            side_effect_scope = getattr(metadata, "sideEffectScope", None)

        read_only = getattr(metadata, "readOnly", None)
        if read_only is not None and side_effect_scope == "none":
            read_only = read_only and True  # enforce readOnly with no side effects

        destructive = getattr(metadata, "destructive", None)
        concurrent_safe = getattr(metadata, "concurrentSafe", None)

        dependencies.append({
            "toolCallId": tc.id,
            "toolName": tc.name,
            "dependsOn": [],
            "readOnly": read_only,
            "destructive": destructive,
            "concurrentSafe": concurrent_safe,
            "sideEffectScope": side_effect_scope,
        })

    return await runtime.tool_scheduler.schedule(dependencies)


async def execute_tools(
    runtime: Any,
    tool_calls: list[ToolCall],
    schedule: Any,
    options: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Mirrors ``executeTools`` — executes scheduled tool calls.

    Uses the tool executor to run the scheduled tool calls, yielding batch
    start/complete events as tools are executed.

    Args:
        runtime: The AgentRuntimeInternal instance
        tool_calls: The tool calls to execute
        schedule: The pre-computed schedule
        options: Execution options (signal, model, etc.)

    Returns:
        ExecuteToolsResult with results and events
    """
    options = options or {}

    executable_calls = [
        {"id": tc.id, "name": tc.name, "input": tc.input}
        for tc in tool_calls
    ]

    trace_context = options.get("traceContext") or getattr(runtime, "root_trace_context", None)
    events: list[SessionEvent] = []

    logger = getattr(runtime, "logger", None)
    if logger:
        logger.debug(
            "executeTools: executing schedule",
            tool_calls=len(executable_calls),
            parallel_groups=len(getattr(schedule, "parallel_groups", [])),
        )

    # Get the trace context for callbacks
    trace_ctx = trace_context if trace_context else None

    # Build executor options
    exec_options = {
        "automationTurn": options.get("automationTurn", False),
        "offPeakTurn": options.get("offPeakTurn", False),
        "signal": options.get("signal"),
        "traceContext": trace_ctx,
        "subagentModelOverride": options.get("subagentModelOverride"),
        "model": options.get("model"),
    }

    generator = runtime.executor.execute_schedule(executable_calls, schedule, exec_options)
    results: list[Any] = []

    while True:
        try:
            next_result = await generator.__anext__()
        except StopAsyncIteration:
            break

        if next_result is None:
            continue

        # Check result type
        result_type = getattr(next_result, "type", None)

        if result_type == "batch_start":
            # Notify batch start callback
            batch_callback = options.get("onBatchStart")
            if batch_callback:
                await batch_callback(next_result.tool_call_ids)
            continue

        if result_type == "batch_complete":
            # Create and append batch complete event
            batch_results = next_result.results
            success_count = sum(1 for r in batch_results if getattr(r, "success", False))
            error_count = len(batch_results) - success_count

            batch_complete_event = runtime.create_event(
                SessionEventType.TOOL_BATCH_COMPLETE,
                {
                    "toolCallIds": [r.tool_call_id for r in batch_results],
                    "successCount": success_count,
                    "errorCount": error_count,
                },
                trace_ctx or runtime.root_trace_context,
            )
            await runtime.append_event(batch_complete_event, trace_ctx or runtime.root_trace_context)
            events.append(batch_complete_event)
            continue

        # Assume it's the final results
        results = next_result if isinstance(next_result, list) else []

    if logger:
        logger.debug("executeTools: completed", result_count=len(results))

    return {"results": results, "events": events}


async def emit_tool_scheduled_events(
    runtime: Any,
    tool_calls: list[ToolCall],
    schedule: Any,
    assistant_message_id: MessageId,
    trace_context: TraceContext,
) -> list[SessionEvent]:
    """Mirrors ``emitToolScheduledEvents`` — emits ToolCallScheduled events.

    Args:
        runtime: The AgentRuntimeInternal instance
        tool_calls: The scheduled tool calls
        schedule: The schedule with dependency info
        assistant_message_id: The assistant message ID
        trace_context: The trace context

    Returns:
        List of emitted session events
    """
    events: list[SessionEvent] = []
    tool_call_by_id = {tc.id: tc for tc in tool_calls}

    for item in getattr(schedule, "items", []):
        tool_call = tool_call_by_id.get(str(item.tool_call_id))
        if not tool_call:
            continue

        # Skip empty-named tool calls
        if tool_call.name is not None and len(tool_call.name.strip()) == 0:
            continue

        event = runtime.create_event(
            SessionEventType.TOOL_CALL_SCHEDULED,
            {
                "toolCallId": item.tool_call_id,
                "assistantMessageId": assistant_message_id,
                "toolName": tool_call.name,
                "input": tool_call.input,
                "dependencies": getattr(item, "dependencies", []),
                "parallelGroupIndex": _find_parallel_group_index(schedule, item.tool_call_id),
                "canRunParallel": getattr(item, "canRunParallel", False),
                "display": None,  # PORT: MCP display info
                "schedule": {
                    "parallelGroups": getattr(schedule, "parallel_groups", []),
                    "executionOrder": getattr(schedule, "execution_order", []),
                },
            },
            trace_context,
        )
        await runtime.append_event(event, trace_context)
        events.append(event)

    return events


def _find_parallel_group_index(schedule: Any, tool_call_id: Any) -> int:
    """Find the parallel group index for a tool call."""
    # PORT: stub - would search through schedule.parallelGroups
    return 0


async def emit_file_mutation_checkpoint(
    runtime: Any,
    options: dict[str, Any],
) -> None:
    """Mirrors ``emitFileMutationCheckpoint`` — creates checkpoint after file mutation.

    Args:
        runtime: The AgentRuntimeInternal instance
        options: Checkpoint options (result, messageId, traceContext, etc.)
    """
    # PORT: stub - full implementation when checkpoint persistence is ported
    pass


async def emit_permission_request(
    runtime: Any,
    tool_call_id: ToolCallId,
    tool_name: str,
    risk_level: str,
) -> None:
    """Mirrors ``emitPermissionRequest`` — emits a permission requested event.

    Args:
        runtime: The AgentRuntimeInternal instance
        tool_call_id: The tool call ID
        tool_name: The tool name
        risk_level: The risk level string
    """
    trace_context = getattr(runtime, "root_trace_context", None)

    event = runtime.create_event(
        SessionEventType.PERMISSION_REQUESTED,
        {
            "toolCallId": tool_call_id,
            "toolName": tool_name,
            "riskLevel": risk_level,
            "reason": f"Tool {tool_name} requires approval",
            "input": {},
        },
        trace_context,
    )
    await runtime.append_event(event, trace_context)


async def resolve_permission(
    runtime: Any,
    tool_call_id: ToolCallId,
    decision: Any,  # PermissionDecisionResult
) -> None:
    """Mirrors ``resolvePermission`` — resolves a permission request.

    Args:
        runtime: The AgentRuntimeInternal instance
        tool_call_id: The tool call ID
        decision: The permission decision
    """
    broker_result = {
        "decision": "allow" if decision.allowed else "deny",
        "reason": decision.reason,
        "modifiedInput": getattr(decision, "modified_input", None),
        "permissionUpdates": getattr(decision, "permission_updates", None),
        "resolvedAt": None,  # Would be new Date()
    }

    # Try to resolve via permission broker
    broker = getattr(runtime, "permission_broker", None)
    if broker is not None and hasattr(broker, "resolve_permission"):
        resolved = broker.resolve_permission(tool_call_id, broker_result)
        if resolved:
            return

    # Fall back to emitting event
    trace_context = getattr(runtime, "root_trace_context", None)

    event = runtime.create_event(
        SessionEventType.PERMISSION_RESOLVED,
        {
            "toolCallId": tool_call_id,
            "decision": broker_result["decision"],
            "reason": broker_result["reason"],
            "modifiedInput": broker_result["modifiedInput"],
        },
        trace_context,
    )
    await runtime.append_event(event, trace_context)