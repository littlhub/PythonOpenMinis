"""Tool executor events — SessionEvent emitters for the tool lifecycle.

Ported from: packages/core/src/tool/executor/events.ts
Original package: @zcode/core (tool/executor)

# PORT: ``@zcode/contracts`` 的 ``SessionEventType`` / ``TraceContext`` / ``ToolResultDisplayPayload`` /
# ``ToolSideEffectScope`` / ``ToolExecutionTelemetry`` / ``SkillTelemetryMetadata`` / ``PermissionContext``
# 等尚未移植；按既有兄弟文件先例，事件对象以 dict 字面量构造（camelCase 键以保协议兼容），
# 这些类型保留为 ``Any``。``emitEvent`` 经由 ``deps.emitEvent``（``Callable[[Any], Awaitable[None]]``）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from ...contracts.tracing import TraceContext, trace_context_to_log_context  # type: ignore
from ...contracts.events import SessionEventType  # type: ignore
from ..types import ExecutableToolCall, ToolExecutionResult
from .types import ToolExecutorDeps
from .utils import summarize_input

__all__ = [
    "emit_tool_call_started",
    "emit_tool_call_result",
    "emit_tool_call_error",
    "emit_skipped_tool_error",
    "emit_permission_requested",
    "emit_permission_resolved",
    "emit_permission_denied",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _random_id() -> str:
    # PORT: TS ``crypto.randomUUID()``。
    return uuid.uuid4().hex


async def emit_tool_call_started(
    deps: ToolExecutorDeps,
    tool_call: ExecutableToolCall,
    trace_context: TraceContext,
    turn_id: Optional[Any],
    start_time: int,
    display: Any = None,
    # 解析后的副作用能力（与权限判定同源）：订阅者据此在工具动手前知道「要写了」。
    capability: Any = None,
) -> None:
    payload: dict[str, Any] = {
        "toolCallId": tool_call.id,
        "toolName": tool_call.name,
        "startedAt": _utc_now(),
    }
    if display is not None:
        payload["display"] = display
    if capability is not None and capability.get("readOnly") is not None:
        payload["readOnly"] = capability["readOnly"]
    if capability is not None and capability.get("sideEffectScope") is not None:
        payload["sideEffectScope"] = capability["sideEffectScope"]
    await deps.emit_event(
        {
            "id": _random_id(),
            "sessionId": deps.session_id,
            "turnId": turn_id,
            "type": SessionEventType.TOOL_CALL_STARTED,
            "timestamp": _utc_now(),
            "traceId": trace_context.trace_id,
            "sequenceNumber": 0,
            "payload": payload,
        }
    )


async def emit_tool_call_result(
    deps: ToolExecutorDeps,
    tool_call: ExecutableToolCall,
    trace_context: TraceContext,
    turn_id: Optional[Any],
    serialization: Any,
    duration_ms: int,
    display: Any,
    perf: Any,
    skill_metadata: Any,
) -> None:
    result_payload: dict[str, Any] = {
        "success": True,
        "content": serialization.content,
        "display": display,
        "perf": perf,
        "truncated": serialization.truncated,
        "originalBytes": serialization.original_bytes,
        "returnedBytes": serialization.returned_bytes,
        "budgetStrategy": serialization.budget_strategy,
        "artifactPath": serialization.artifact_path,
    }
    if skill_metadata is not None:
        result_payload["skillMetadata"] = skill_metadata
    await deps.emit_event(
        {
            "id": _random_id(),
            "sessionId": deps.session_id,
            "turnId": turn_id,
            "type": SessionEventType.TOOL_CALL_RESULT,
            "timestamp": _utc_now(),
            "traceId": trace_context.trace_id,
            "sequenceNumber": 0,
            "payload": {
                "toolCallId": tool_call.id,
                "result": result_payload,
                "duration": duration_ms,
            },
        }
    )


async def emit_tool_call_error(
    deps: ToolExecutorDeps,
    tool_call_id: str,
    trace_context: TraceContext,
    turn_id: Optional[Any],
    error: Any,
    skill_metadata: Any = None,
) -> None:
    payload: dict[str, Any] = {
        "toolCallId": tool_call_id,
        "error": error,
    }
    if skill_metadata is not None:
        payload["skillMetadata"] = skill_metadata
    await deps.emit_event(
        {
            "id": _random_id(),
            "sessionId": deps.session_id,
            "turnId": turn_id,
            "type": SessionEventType.TOOL_CALL_ERROR,
            "timestamp": _utc_now(),
            "traceId": trace_context.trace_id,
            "sequenceNumber": 0,
            "payload": payload,
        }
    )


async def emit_skipped_tool_error(
    deps: ToolExecutorDeps,
    result: ToolExecutionResult,
    trace_context: Optional[TraceContext] = None,
) -> None:
    event_trace_context = (
        trace_context
        or deps.trace_context
        or _create_root_trace_context(deps.session_id, deps.turn_id)
    )
    turn_id = event_trace_context.turn_id or deps.turn_id

    await emit_tool_call_error(deps, result.tool_call_id, event_trace_context, turn_id, result.error)

    if deps.logger is not None:
        deps.logger.warn(
            "Tool call skipped after blocking failure",
            {
                **trace_context_to_log_context(event_trace_context),
                "event": "tool.call.skipped",
                "module": "core.tool.executor",
                "status": "failed",
                "toolCallId": result.tool_call_id,
                "toolName": result.tool_name,
            },
        )


async def emit_permission_requested(
    deps: ToolExecutorDeps,
    tool_call: ExecutableToolCall,
    input: Any,
    request_id: str,
    risk_level: Any,
    reason: Optional[str],
    suggested_permission_updates: Any,
    trace_context: TraceContext,
    approval: Any = None,
) -> None:
    payload: dict[str, Any] = {
        "requestId": request_id,
        "toolCallId": tool_call.id,
        "toolName": tool_call.name,
        "riskLevel": risk_level,
        "reason": reason or f"Tool {tool_call.name} requires approval",
        "input": input,
        "suggestedPermissionUpdates": suggested_permission_updates,
    }
    if approval is not None and approval.get("display") is not None:
        payload["display"] = approval["display"]
    if approval is not None and approval.get("optionsPolicy") is not None:
        payload["optionsPolicy"] = approval["optionsPolicy"]
    if deps.session_mode_port is not None and getattr(deps.session_mode_port, "supports_permission_full_access", None) is not None and deps.session_mode_port.supports_permission_full_access():
        payload["fullAccessSupported"] = True
    await deps.emit_event(
        {
            "id": _random_id(),
            "sessionId": deps.session_id,
            "turnId": trace_context.turn_id or deps.turn_id,
            "type": SessionEventType.PERMISSION_REQUESTED,
            "timestamp": _utc_now(),
            "traceId": trace_context.trace_id,
            "sequenceNumber": 0,
            "payload": payload,
        }
    )


async def emit_permission_resolved(
    deps: ToolExecutorDeps,
    tool_call: ExecutableToolCall,
    request_id: str,
    decision: Any,
    trace_context: TraceContext,
) -> None:
    await deps.emit_event(
        {
            "id": _random_id(),
            "sessionId": deps.session_id,
            "turnId": trace_context.turn_id or deps.turn_id,
            "type": SessionEventType.PERMISSION_RESOLVED,
            "timestamp": _utc_now(),
            "traceId": trace_context.trace_id,
            "sequenceNumber": 0,
            "payload": {
                "requestId": request_id,
                "toolCallId": tool_call.id,
                "decision": decision.decision,
                "reason": decision.reason,
                "modifiedInput": decision.modified_input,
            },
        }
    )


async def emit_permission_denied(
    deps: ToolExecutorDeps,
    tool_call: ExecutableToolCall,
    reason: Optional[str],
    trace_context: TraceContext,
) -> None:
    await deps.emit_event(
        {
            "id": _random_id(),
            "sessionId": deps.session_id,
            "turnId": trace_context.turn_id or deps.turn_id,
            "type": SessionEventType.PERMISSION_DENIED,
            "timestamp": _utc_now(),
            "traceId": trace_context.trace_id,
            "sequenceNumber": 0,
            "payload": {
                "toolCallId": tool_call.id,
                "toolName": tool_call.name,
                "reason": reason or f"Permission denied for {tool_call.name}",
                "inputSummary": summarize_input(tool_call.input),
            },
        }
    )


def _create_root_trace_context(session_id: Any, turn_id: Any) -> Any:
    # PORT: ``createRootTraceContext`` from ``@zcode/contracts`` (tracing)，未移植；
    # 仅构造最小 trace 上下文对象供 logger 透传使用。
    return type("TraceContext", (), {"trace_id": None, "turn_id": turn_id, "session_id": session_id})()
