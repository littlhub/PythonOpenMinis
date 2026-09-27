"""Permission flow — resolve whether a tool call is allowed, denied, or needs approval.

Ported from: packages/core/src/tool/executor/permission-flow.ts
Original package: @zcode/core (tool/executor)

# PORT: ``@zcode/contracts`` 的 ``CoreErrorType`` / ``createCoreError`` / ``isCoreError`` /
# ``traceContextToLogContext`` / ``CollaborationMode`` / ``PermissionBrokerRequest`` /
# ``PermissionBrokerResult`` / ``PermissionRuleset`` / ``TraceContext`` / ``ToolExecutionSpanWriter``
# 未移植，保留为 ``Any``。``HookRunResult`` 来自 ``...hooks``（未移植）。``normalizeToolExecutionInput``
# 来自 ``..input_normalization``（已移植）。
"""

from __future__ import annotations

import time
from typing import Any, Optional, Union

from ...contracts.errors import CoreErrorType, create_core_error, is_core_error  # type: ignore
from ...contracts.tracing import trace_context_to_log_context  # type: ignore
from ...permission.ports import CollaborationMode  # type: ignore
from ..types import ExecutableToolCall, ToolEntry, ToolExecutionResult
from ..input_normalization import normalize_tool_execution_input
from .approval_gate import resolve_tool_approval
from .errors import create_error_result, create_permission_error_result, ErrorResultOptions
from .events import emit_permission_denied, emit_permission_requested, emit_permission_resolved
from .hook_flow import apply_pre_tool_permission_decision, run_permission_request_hooks
from .memory_file_permission import apply_memory_file_permission
from .permission_capability import resolve_runtime_permission_capability, resolve_runtime_permission_context
from .permission_input_recheck import recheck_permission_hook_modified_input
from .permission_responder_race import race_permission_responders
from .permission_rules_persistence import load_project_permission_ruleset, persist_project_permission_updates
from .permission_suggestions import build_default_permission_updates
from .types import ToolExecutorDeps
from .utils import summarize_input
from .validation import validate_input

__all__ = ["resolve_tool_permission"]

ToolPermissionFlowResult = Union[
    dict[str, Any],  # { allowed: True, executionInput, permissionWaitMs? }
    dict[str, Any],  # { allowed: False, result: ToolExecutionResult }
]


async def resolve_tool_permission(
    deps: ToolExecutorDeps,
    tool_call: ExecutableToolCall,
    entry: ToolEntry,
    execution_input: Any,
    pre_tool_hook_result: Any,
    mode: CollaborationMode,
    trace_context: Any,
    signal: Optional[Any] = None,
    telemetry: Optional[Any] = None,
) -> dict[str, Any]:
    permission_context: dict[str, Any] = {
        "toolName": tool_call.name,
        "input": execution_input,
        "riskLevel": entry.metadata.risk_level,
        "mode": mode,
        "prePlanMode": deps.session_mode_port.get_pre_plan_mode() if deps.session_mode_port is not None and getattr(deps.session_mode_port, "get_pre_plan_mode", None) is not None else None,
        "planEnabled": deps.session_mode_port.is_plan_enabled() if deps.session_mode_port is not None and getattr(deps.session_mode_port, "is_plan_enabled", None) is not None else None,
        # workflow 草稿免确认要按工作目录解析相对路径，见 PermissionService 的
        # isPreapprovedWorkflowDraftWrite。
        "workingDirectory": deps.get_working_directory(),
    }
    runtime_permission_context = resolve_runtime_permission_context(deps)
    resolve_permission_rule_policy = getattr(entry, "resolve_permission_rule_policy", None)
    rule_policy = (
        resolve_permission_rule_policy(execution_input, runtime_permission_context)
        if resolve_permission_rule_policy is not None
        else None
    )
    suggested_permission_updates = (
        rule_policy.suggested_permission_updates
        if rule_policy is not None and getattr(rule_policy, "suggested_permission_updates", None) is not None
        else build_default_permission_updates(tool_call.name, execution_input, getattr(entry, "permission_capability_group", None))
    )

    try:
        project_rules = await load_project_permission_ruleset(deps)
    except Exception as error:  # noqa: BLE001
        return {
            "allowed": False,
            "result": create_error_result(
                tool_call,
                create_core_error(
                    CoreErrorType.STORAGE_ERROR,
                    "Failed to load project permission rules",
                    {
                        "cause": error if isinstance(error, Exception) else None,
                        "context": {"sessionId": deps.session_id, "toolCallId": tool_call.id, "toolName": tool_call.name},
                        "recoverable": True,
                    },
                ),
            ),
        }

    permission_decision = deps.permission_service.check_permission(
        permission_context,
        resolve_runtime_permission_capability(entry, execution_input, runtime_permission_context),
        project_rules,
        rule_policy,
    )
    permission_decision = apply_pre_tool_permission_decision(permission_decision, pre_tool_hook_result, mode)
    permission_decision = apply_memory_file_permission(
        {
            "decision": permission_decision,
            "executionInput": execution_input,
            "memoryRoot": deps.get_memory_root() if deps.get_memory_root is not None else None,
            "toolName": tool_call.name,
            "workingDirectory": deps.get_working_directory(),
            "workspaceRoot": deps.get_workspace_root(),
        }
    )

    if deps.logger is not None:
        deps.logger.debug(
            "Tool permission evaluated",
            {
                **trace_context_to_log_context(trace_context),
                "decision": permission_decision.decision,
                "event": "tool.permission.evaluated",
                "inputSummary": summarize_input(tool_call.input),
                "mode": mode,
                "module": "core.tool.executor",
                "reason": permission_decision.reason,
                "riskLevel": permission_decision.risk_level,
                "ruleId": permission_decision.rule_id,
                "sideEffectScope": permission_decision.side_effect_scope,
                "status": "completed" if permission_decision.decision == "allow" else "waiting" if permission_decision.decision == "ask" else "failed",
                "toolCallId": tool_call.id,
                "toolName": tool_call.name,
            },
        )

    if permission_decision.allowed:
        if telemetry is not None:
            telemetry.set_permission_decision("not_required")
        return {"allowed": True, "executionInput": execution_input}

    if permission_decision.decision == "deny":
        if telemetry is not None:
            telemetry.set_permission_decision("denied")
        await emit_permission_denied(deps, tool_call, permission_decision.reason, trace_context)
        if deps.logger is not None:
            deps.logger.warn(
                "Tool permission denied",
                {
                    **trace_context_to_log_context(trace_context),
                    "decision": permission_decision.decision,
                    "event": "tool.permission.denied",
                    "mode": mode,
                    "module": "core.tool.executor",
                    "reason": permission_decision.reason,
                    "ruleId": permission_decision.rule_id,
                    "status": "failed",
                    "toolCallId": tool_call.id,
                    "toolName": tool_call.name,
                },
            )
        return {
            "allowed": False,
            "result": create_permission_error_result(
                tool_call,
                permission_decision.reason,
                {"decision": permission_decision.decision, "mode": mode, "ruleId": permission_decision.rule_id},
            ),
        }

    approval = resolve_tool_approval(deps, tool_call, entry, execution_input, trace_context)
    if approval.gate == "proceed":
        if telemetry is not None:
            telemetry.set_permission_decision("not_required")
        return {"allowed": True, "executionInput": execution_input}

    request_id = f"perm_{_uuid()}"
    if telemetry is not None:
        telemetry.mark_permission_requested()
    await emit_permission_requested(
        deps,
        tool_call,
        execution_input,
        request_id,
        permission_decision.risk_level,
        permission_decision.reason,
        suggested_permission_updates,
        trace_context,
        {"display": approval.display, "optionsPolicy": approval.options_policy},
    )

    normalized_hook_modified_input: Any = None
    use_normalized_hook_modified_input = False
    permission_wait_started_at = int(time.time() * 1000)
    try:
        # 这里曾经串行 ``await runPermissionRequestHooks(...)``，broker 要等 hook 链返回才启动。
        # 同步 PermissionRequest hook（外部审批桥接）阻塞期间，确认窗已经渲染（上面的 emitPermissionRequested），
        # 但应答 deferred 尚未注册，用户的每一次点击都被 resolveInteraction 按幂等语义静默丢弃——确认窗永久死亡。
        # 修法：hook 链与 broker 并发竞速，先到的决定生效，败者被 abort 且不被等待。
        race_outcome = await race_permission_responders(
            {
                "onHookFailure": lambda error: _log_hook_race_forfeited(deps, error, request_id, trace_context, tool_call),
                "requestBroker": lambda broker_signal, claim_response: deps.permission_broker.request_permission(
                    {
                        "input": execution_input,
                        "mode": mode,
                        "reason": permission_decision.reason or f"Tool {tool_call.name} requires approval",
                        "requestId": request_id,
                        "requestedAt": _utc_now(),
                        "riskLevel": permission_decision.risk_level,
                        "ruleId": permission_decision.rule_id,
                        "sessionId": deps.session_id,
                        "sideEffectScope": permission_decision.side_effect_scope,
                        "suggestedPermissionUpdates": suggested_permission_updates,
                        **({"optionsPolicy": approval.options_policy} if approval.options_policy is not None else {}),
                        "toolCallId": tool_call.id,
                        "toolName": tool_call.name,
                        "traceId": trace_context.trace_id,
                        "turnId": trace_context.turn_id if trace_context.turn_id is not None else deps.turn_id,
                    },
                    {
                        "signal": broker_signal,
                        "claimResponse": claim_response,
                        "timeoutMs": deps.permission_timeout_ms,
                    },
                ),
                "runHooks": lambda hook_signal: run_permission_request_hooks(
                    deps,
                    tool_call,
                    execution_input,
                    request_id,
                    permission_decision,
                    mode,
                    trace_context,
                    hook_signal,
                ),
                **({} if signal is None else {"signal": signal}),
            }
        )
        broker_result = race_outcome["result"]
        permission_hook_result = race_outcome["result"] if race_outcome["source"] == "hook" else None

        if permission_hook_result is not None and permission_hook_result.decision == "modify":
            normalized_hook_modified_input = normalize_tool_execution_input(
                {
                    "entry": entry,
                    "input": permission_hook_result.modified_input if permission_hook_result.modified_input is not None else execution_input,
                    "logger": deps.logger,
                    "source": "permission",
                }
            )
            use_normalized_hook_modified_input = True
            if not validate_input(normalized_hook_modified_input, entry):
                # PermissionRequest hook 可以改写目标路径，修改后的输入不能沿用修改前的权限结果。
                recheck = await recheck_permission_hook_modified_input(
                    {
                        "deps": deps,
                        "entry": entry,
                        "mode": mode,
                        "modifiedInput": normalized_hook_modified_input,
                        "projectRules": project_rules,
                        "requestId": request_id,
                        "signal": signal,
                        "toolCall": tool_call,
                        "traceContext": trace_context,
                    }
                )
                if recheck.get("permissionDecision") is not None:
                    permission_decision = recheck["permissionDecision"]
                if recheck.get("brokerResult") is not None:
                    broker_result = recheck["brokerResult"]
                    use_normalized_hook_modified_input = broker_result.decision == "allow"
    except Exception as error:  # noqa: BLE001
        if telemetry is not None:
            telemetry.set_permission_decision("denied")
        core_error = (
            error
            if is_core_error(error)
            else create_core_error(
                CoreErrorType.PERMISSION_DENIED,
                "Permission request failed",
                {
                    "cause": error if isinstance(error, Exception) else None,
                    "context": {"requestId": request_id, "toolCallId": tool_call.id, "toolName": tool_call.name},
                    "recoverable": True,
                },
            )
        )
        await emit_permission_resolved(
            deps,
            tool_call,
            request_id,
            {
                "decision": "deny",
                "reason": core_error.message,
                "resolvedAt": _utc_now(),
            },
            trace_context,
        )
        return {"allowed": False, "result": create_error_result(tool_call, core_error)}

    resolved_permission = {
        **broker_result,
        "resolvedAt": broker_result.resolved_at if broker_result.resolved_at is not None else _utc_now(),
    }
    permission_wait_ms = max(0, int(round(int(time.time() * 1000) - permission_wait_started_at)))
    await emit_permission_resolved(deps, tool_call, request_id, resolved_permission, trace_context)

    if deps.logger is not None:
        deps.logger.info(
            "Tool permission resolved",
            {
                **trace_context_to_log_context(trace_context),
                "decision": resolved_permission.decision,
                "event": "tool.permission.resolved",
                "mode": mode,
                "module": "core.tool.executor",
                "reason": resolved_permission.reason,
                "requestId": request_id,
                "status": "completed" if resolved_permission.decision in ("allow", "modify") else "failed",
                "toolCallId": tool_call.id,
                "toolName": tool_call.name,
            },
        )

    if resolved_permission.decision == "deny":
        if telemetry is not None:
            telemetry.set_permission_decision("denied")
        return {
            "allowed": False,
            "result": create_permission_error_result(
                tool_call,
                resolved_permission.reason,
                {
                    "decision": resolved_permission.decision,
                    "mode": mode,
                    "reasonSource": resolved_permission.reason_source,
                    "ruleId": permission_decision.rule_id,
                },
                ErrorResultOptions(preserve_reason_formatting=True) if getattr(resolved_permission, "preserve_reason_formatting", False) else None,
            ),
        }

    if resolved_permission.decision == "escalate":
        if telemetry is not None:
            telemetry.set_permission_decision("denied")
        return {
            "allowed": False,
            "result": create_error_result(
                tool_call,
                create_core_error(
                    CoreErrorType.PERMISSION_ESCALATION,
                    resolved_permission.reason or f"Permission escalation requested for {tool_call.name}",
                    {
                        "context": {
                            "decision": resolved_permission.decision,
                            "mode": mode,
                            "requestId": request_id,
                            "ruleId": permission_decision.rule_id,
                            "toolName": tool_call.name,
                        },
                        "recoverable": True,
                    },
                ),
            ),
        }

    if resolved_permission.permission_updates is not None and len(resolved_permission.permission_updates) > 0:
        try:
            await persist_project_permission_updates(deps, list(resolved_permission.permission_updates), trace_context)
        except Exception as error:  # noqa: BLE001
            return {
                "allowed": False,
                "result": create_error_result(
                    tool_call,
                    create_core_error(
                        CoreErrorType.STORAGE_ERROR,
                        "Failed to persist project permission update",
                        {
                            "cause": error if isinstance(error, Exception) else None,
                            "context": {
                                "requestId": request_id,
                                "sessionId": deps.session_id,
                                "toolCallId": tool_call.id,
                                "toolName": tool_call.name,
                            },
                            "recoverable": True,
                        },
                    ),
                ),
            }

    if resolved_permission.session_permission_updates is not None and len(resolved_permission.session_permission_updates) > 0:
        # 会话免确认：只进内存里的会话 ruleset，与上面的项目级持久化互不可见。
        deps.permission_service.grant_session_permission(list(resolved_permission.session_permission_updates))
        if deps.logger is not None:
            deps.logger.info(
                "Session permission granted",
                {
                    **trace_context_to_log_context(trace_context),
                    "event": "tool.permission.session_grant.applied",
                    "module": "core.tool.executor",
                    "requestId": request_id,
                    "status": "completed",
                    "toolCallId": tool_call.id,
                    "toolName": tool_call.name,
                    "updateCount": len(resolved_permission.session_permission_updates),
                },
            )

    if telemetry is not None:
        telemetry.set_permission_decision("granted")
    if resolved_permission.decision != "modify":
        return {
            "allowed": True,
            "executionInput": normalized_hook_modified_input if use_normalized_hook_modified_input else execution_input,
            "permissionWaitMs": permission_wait_ms,
        }

    modified_input = (
        normalized_hook_modified_input
        if use_normalized_hook_modified_input
        else normalize_tool_execution_input(
            {
                "entry": entry,
                "input": resolved_permission.modified_input if resolved_permission.modified_input is not None else execution_input,
                "logger": deps.logger,
                "source": "permission",
            }
        )
    )
    modified_input_validation = validate_input(modified_input, entry)
    if modified_input_validation is not None:
        return {
            "allowed": False,
            "result": create_error_result(tool_call, modified_input_validation),
        }
    return {"allowed": True, "executionInput": modified_input, "permissionWaitMs": permission_wait_ms}


def _log_hook_race_forfeited(
    deps: ToolExecutorDeps,
    error: Any,
    request_id: str,
    trace_context: Any,
    tool_call: ExecutableToolCall,
) -> None:
    # hook 链故障只令其退赛：辅助应答方的基础设施故障不应替用户做拒绝决定，
    # 确认窗继续等待 broker 应答。
    if deps.logger is not None:
        deps.logger.warn(
            "PermissionRequest hook chain failed; waiting for client decision",
            {
                **trace_context_to_log_context(trace_context),
                "errorMessage": error.message if isinstance(error, Exception) and hasattr(error, "message") else str(error),
                "event": "tool.permission.hook_race_forfeited",
                "module": "core.tool.executor",
                "requestId": request_id,
                "status": "waiting",
                "toolCallId": tool_call.id,
                "toolName": tool_call.name,
            },
        )


def _uuid() -> str:
    import uuid

    return uuid.uuid4().hex


def _utc_now() -> Any:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
