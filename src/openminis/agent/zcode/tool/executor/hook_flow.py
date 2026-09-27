"""Hook flow — tool-related hook invocations and PreToolUse permission folding.

Ported from: packages/core/src/tool/executor/hook-flow.ts
Original package: @zcode/core (tool/executor)

# PORT: 依赖尚未移植的 hooks 运行时（``core/src/hooks/*``）。本文件按 §6 映射直连其未来落点
# ``...hooks``（``HookRunResult`` / ``HookRunner``），``HookEventName`` 来自
# ``@zcode/contracts``，直连 ``...contracts.hooks``。
# PORT: ``HookInput`` / ``HookRunOptions`` 同为未移植契约，此处按原 TS 对象字面量以 dict
# 透传（键转 snake_case）。待 hooks 端口落定后按同一形状对齐即可，判定顺序不受影响。
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Any, Optional

from ...contracts.errors import CoreErrorType, is_core_error
from ...contracts.hooks import HookEventName
from ...contracts.tracing import TraceContext
from ...hooks import HookRunResult, HookRunner
from ...permission.ports import (
    CollaborationMode,
    PermissionBrokerResult,
    PermissionDecisionResult,
)
from ..compat import hook_matcher_tool_names_for_tool
from ..types import ExecutableToolCall, ToolEntry
from .types import ToolExecutorDeps
from .utils import preview_hook_value

__all__ = [
    "run_pre_tool_use_hooks",
    "run_permission_request_hooks",
    "run_post_tool_use_hooks",
    "run_post_tool_use_failure_hooks",
    "apply_pre_tool_permission_decision",
    "format_hook_additional_contexts",
]


async def run_pre_tool_use_hooks(
    deps: ToolExecutorDeps,
    tool_call: ExecutableToolCall,
    input: Any,
    entry: ToolEntry,
    mode: CollaborationMode,
    trace_context: TraceContext,
    signal: Optional[Any] = None,
) -> HookRunResult:
    """Mirrors ``runPreToolUseHooks(deps, toolCall, input, entry, mode, traceContext, signal?)``."""
    if deps.hook_runner is None:
        return HookRunResult(additional_contexts=[])
    return await deps.hook_runner.run(
        {
            "cwd": deps.get_working_directory(),
            "hook_event_name": HookEventName.PRE_TOOL_USE,
            "mode": mode,
            "risk_level": entry.metadata.risk_level,
            "session_id": deps.session_id,
            "side_effect_scope": entry.metadata.side_effect_scope,
            "timestamp": _iso_timestamp(),
            "tool_call_id": tool_call.id,
            "tool_input": input,
            "tool_name": tool_call.name,
            "trace_id": trace_context.trace_id,
            "turn_id": (
                trace_context.turn_id
                if trace_context.turn_id is not None
                else deps.turn_id
            ),
        },
        {
            "match_value": tool_call.name,
            "match_values": hook_matcher_tool_names_for_tool(tool_call.name),
            "signal": signal,
        },
    )


async def run_permission_request_hooks(
    deps: ToolExecutorDeps,
    tool_call: ExecutableToolCall,
    input: Any,
    request_id: str,
    permission_decision: PermissionDecisionResult,
    mode: CollaborationMode,
    trace_context: TraceContext,
    signal: Optional[Any] = None,
) -> Optional[PermissionBrokerResult]:
    """Mirrors ``runPermissionRequestHooks(...)``."""
    if deps.hook_runner is None:
        return None
    hook_result = await deps.hook_runner.run(
        {
            "cwd": deps.get_working_directory(),
            "hook_event_name": HookEventName.PERMISSION_REQUEST,
            "mode": mode,
            "reason": (
                permission_decision.reason
                if permission_decision.reason is not None
                else f"Tool {tool_call.name} requires approval"
            ),
            "request_id": request_id,
            "risk_level": permission_decision.risk_level,
            "session_id": deps.session_id,
            "side_effect_scope": permission_decision.side_effect_scope,
            "timestamp": _iso_timestamp(),
            "tool_call_id": tool_call.id,
            "tool_input": input,
            "tool_name": tool_call.name,
            "trace_id": trace_context.trace_id,
            "turn_id": (
                trace_context.turn_id
                if trace_context.turn_id is not None
                else deps.turn_id
            ),
        },
        {
            "match_value": tool_call.name,
            "match_values": hook_matcher_tool_names_for_tool(tool_call.name),
            "signal": signal,
        },
    )
    decision = hook_result.permission_request_result
    if hook_result.prevent_continuation:
        return PermissionBrokerResult(
            decision="deny",
            reason=(
                hook_result.stop_reason
                if hook_result.stop_reason is not None
                else "Denied by PermissionRequest hook"
            ),
        )
    if not decision:
        if hook_result.permission_behavior == "deny":
            return PermissionBrokerResult(
                decision="deny",
                reason=(
                    hook_result.stop_reason
                    if hook_result.stop_reason is not None
                    else "Denied by PermissionRequest hook"
                ),
            )
        return (
            PermissionBrokerResult(
                decision="allow",
                reason="Allowed by PermissionRequest hook",
            )
            if hook_result.permission_behavior == "allow"
            else None
        )
    if decision.behavior == "deny":
        return PermissionBrokerResult(
            decision="deny",
            reason=(
                decision.message
                if decision.message is not None
                else "Denied by PermissionRequest hook"
            ),
        )
    permission_updates = (
        decision.permission_updates
        if decision.permission_updates is not None
        else decision.updated_permissions
    )
    return (
        PermissionBrokerResult(
            decision="allow",
            permission_updates=permission_updates,
            reason="Allowed by PermissionRequest hook",
        )
        if decision.updated_input is None
        else PermissionBrokerResult(
            decision="modify",
            modified_input=decision.updated_input,
            permission_updates=permission_updates,
            reason="Allowed with modified input by PermissionRequest hook",
        )
    )


async def run_post_tool_use_hooks(
    deps: ToolExecutorDeps,
    tool_call: ExecutableToolCall,
    input: Any,
    output: Any,
    artifact_path: Optional[str],
    trace_context: TraceContext,
    signal: Optional[Any] = None,
) -> HookRunResult:
    """Mirrors ``runPostToolUseHooks(...)``."""
    if deps.hook_runner is None:
        return HookRunResult(additional_contexts=[])
    return await deps.hook_runner.run(
        {
            "artifact_refs": [artifact_path] if artifact_path else None,
            "cwd": deps.get_working_directory(),
            "hook_event_name": HookEventName.POST_TOOL_USE,
            "mode": deps.get_mode(),
            "session_id": deps.session_id,
            "timestamp": _iso_timestamp(),
            "tool_call_id": tool_call.id,
            "tool_input": input,
            "tool_name": tool_call.name,
            "tool_response": output,
            "tool_result_preview": preview_hook_value(output),
            "trace_id": trace_context.trace_id,
            "turn_id": (
                trace_context.turn_id
                if trace_context.turn_id is not None
                else deps.turn_id
            ),
        },
        {
            "match_value": tool_call.name,
            "match_values": hook_matcher_tool_names_for_tool(tool_call.name),
            "signal": signal,
        },
    )


async def run_post_tool_use_failure_hooks(
    deps: ToolExecutorDeps,
    tool_call: ExecutableToolCall,
    input: Any,
    error: Any,
    trace_context: TraceContext,
    signal: Optional[Any] = None,
) -> HookRunResult:
    """Mirrors ``runPostToolUseFailureHooks(...)``."""
    if deps.hook_runner is None:
        return HookRunResult(additional_contexts=[])
    # PORT: TS ``error instanceof Error ? error : new Error(String(error))``。
    normalized = error if isinstance(error, BaseException) else Exception(str(error))
    return await deps.hook_runner.run(
        {
            "cwd": deps.get_working_directory(),
            "error": {
                "message": str(normalized),
                "type": (
                    normalized.type
                    if is_core_error(normalized)
                    else type(normalized).__name__
                ),
            },
            "hook_event_name": HookEventName.POST_TOOL_USE_FAILURE,
            "is_interrupt": (
                is_core_error(normalized)
                and normalized.type == CoreErrorType.TOOL_CANCELLED
            ),
            "mode": deps.get_mode(),
            "session_id": deps.session_id,
            "timestamp": _iso_timestamp(),
            "tool_call_id": tool_call.id,
            "tool_input": input,
            "tool_name": tool_call.name,
            "trace_id": trace_context.trace_id,
            "turn_id": (
                trace_context.turn_id
                if trace_context.turn_id is not None
                else deps.turn_id
            ),
        },
        {
            "match_value": tool_call.name,
            "match_values": hook_matcher_tool_names_for_tool(tool_call.name),
            "signal": signal,
        },
    )


def apply_pre_tool_permission_decision(
    permission_decision: PermissionDecisionResult,
    hook_result: HookRunResult,
    mode: CollaborationMode,
) -> PermissionDecisionResult:
    """Mirrors ``applyPreToolPermissionDecision(permissionDecision, hookResult, mode)``."""
    if permission_decision.decision == "deny":
        return permission_decision
    # alwaysAsk 声明出来的确认（如 workflow 运行确认）不能被 PreToolUse hook
    # 的整体 allow 悄悄抹掉——那等于给"任何模式都要问"开了一个静默后门。自动化仍有正规出口：
    # PermissionRequest hook 可以应答这次弹窗（见 runPermissionRequestHooks）。
    if (
        hook_result.permission_behavior == "allow"
        and permission_decision.decision == "ask"
        and not permission_decision.always_ask
    ):
        return dataclasses.replace(
            permission_decision,
            allowed=True,
            decision="allow",
            escalated=False,
            mode=mode,
            reason=(
                hook_result.hook_permission_decision_reason
                if hook_result.hook_permission_decision_reason is not None
                else "Tool was allowed by PreToolUse hook"
            ),
            rule_id="hook.PreToolUse.allow",
        )
    if (
        hook_result.permission_behavior == "ask"
        and permission_decision.decision == "allow"
    ):
        return dataclasses.replace(
            permission_decision,
            allowed=False,
            decision="ask",
            escalated=True,
            mode=mode,
            reason=(
                hook_result.hook_permission_decision_reason
                if hook_result.hook_permission_decision_reason is not None
                else "Tool requires approval by PreToolUse hook"
            ),
            rule_id="hook.PreToolUse.ask",
        )
    return permission_decision


def format_hook_additional_contexts(additional_contexts: list[str]) -> str:
    """Mirrors ``formatHookAdditionalContexts(additionalContexts)``."""
    return "\n".join(
        [
            "[Hook additional context]",
            *[
                f"#{index + 1}\n{context}"
                for index, context in enumerate(additional_contexts)
            ],
        ]
    )


def _iso_timestamp() -> str:
    """Mirrors ``new Date().toISOString()``.

    # PORT: Python 的 ``isoformat()`` 出 ``+00:00`` 后缀而 JS 出 ``Z``；这里补齐成同一形状。
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
