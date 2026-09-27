"""Permission input recheck — re-validate input rewritten by a PermissionRequest hook.

Ported from: packages/core/src/tool/executor/permission-input-recheck.ts
Original package: @zcode/core (tool/executor)

# PORT: ``@zcode/contracts`` 的 ``CollaborationMode`` / ``PermissionBrokerRequest`` / ``PermissionBrokerResult``
# / ``PermissionRuleset`` / ``TraceContext`` 及 ``permission/service`` 的 ``PermissionDecisionResult`` /
# ``PermissionContext`` 未移植，保留为 ``Any``。``PermissionBrokerRequest["toolCallId"]`` 仅用于类型收窄。
"""

from __future__ import annotations

from typing import Any

from ...contracts.tracing import TraceContext  # type: ignore
from ..types import ExecutableToolCall, ToolEntry
from .memory_file_permission import apply_memory_file_permission, targets_memory_file
from .permission_capability import resolve_runtime_permission_capability, resolve_runtime_permission_context
from .permission_suggestions import build_default_permission_updates
from .types import ToolExecutorDeps

__all__ = ["recheck_permission_hook_modified_input"]


async def recheck_permission_hook_modified_input(input: dict[str, Any]) -> dict[str, Any]:
    """Mirrors ``recheckPermissionHookModifiedInput``.

    返回 ``{ brokerResult?, permissionDecision? }``。
    """
    deps: ToolExecutorDeps = input["deps"]
    entry: ToolEntry = input["entry"]
    mode = input["mode"]
    modified_input = input["modifiedInput"]
    project_rules = input["projectRules"]
    request_id = input["requestId"]
    signal = input.get("signal")
    tool_call: ExecutableToolCall = input["toolCall"]
    trace_context: TraceContext = input["traceContext"]

    runtime_permission_context = resolve_runtime_permission_context(deps)
    permission_context: dict[str, Any] = {
        "input": modified_input,
        "mode": mode,
        "prePlanMode": deps.session_mode_port.get_pre_plan_mode() if deps.session_mode_port is not None else None,
        "planEnabled": deps.session_mode_port.is_plan_enabled() if deps.session_mode_port is not None and getattr(deps.session_mode_port, "is_plan_enabled", None) is not None else None,
        "riskLevel": entry.metadata.risk_level,
        "toolName": tool_call.name,
        # 与首次判定同源：hook 改过 input 之后，草稿免确认仍要按同一个工作目录复核。
        "workingDirectory": deps.get_working_directory(),
    }
    resolve_permission_rule_policy = getattr(entry, "resolve_permission_rule_policy", None)
    rule_policy = (
        resolve_permission_rule_policy(modified_input, runtime_permission_context)
        if resolve_permission_rule_policy is not None
        else None
    )
    decision = deps.permission_service.check_permission(
        permission_context,
        resolve_runtime_permission_capability(entry, modified_input, runtime_permission_context),
        project_rules,
        rule_policy,
    )
    decision = apply_memory_file_permission(
        {
            "decision": decision,
            "executionInput": modified_input,
            "memoryRoot": deps.get_memory_root() if deps.get_memory_root is not None else None,
            "toolName": tool_call.name,
            "workingDirectory": deps.get_working_directory(),
            "workspaceRoot": deps.get_workspace_root(),
        }
    )

    if decision.decision == "deny":
        return {
            "brokerResult": {"decision": "deny", "reason": decision.reason},
            "permissionDecision": decision,
        }
    if (
        decision.decision != "ask"
        or (
            decision.rule_id != "rule.project.ask"
            and not targets_memory_file(
                {
                    "executionInput": modified_input,
                    "memoryRoot": deps.get_memory_root() if deps.get_memory_root is not None else None,
                    "toolName": tool_call.name,
                    "workingDirectory": deps.get_working_directory(),
                    "workspaceRoot": deps.get_workspace_root(),
                }
            )
        )
    ):
        return {}

    suggested_permission_updates = (
        rule_policy.suggested_permission_updates
        if rule_policy is not None and getattr(rule_policy, "suggested_permission_updates", None) is not None
        else build_default_permission_updates(tool_call.name, modified_input)
    )
    broker_result = await deps.permission_broker.request_permission(
        {
            "input": modified_input,
            "mode": mode,
            "reason": decision.reason or f"Tool {tool_call.name} requires approval",
            "requestId": request_id,
            "requestedAt": _utc_now(),
            "riskLevel": decision.risk_level,
            "ruleId": decision.rule_id,
            "sessionId": deps.session_id,
            "sideEffectScope": decision.side_effect_scope,
            "suggestedPermissionUpdates": suggested_permission_updates,
            "toolCallId": tool_call.id,
            "toolName": tool_call.name,
            "traceId": trace_context.trace_id,
            "turnId": trace_context.turn_id if trace_context.turn_id is not None else deps.turn_id,
        },
        {
            "signal": signal,
            "timeoutMs": deps.permission_timeout_ms,
        },
    )
    return {"brokerResult": broker_result, "permissionDecision": decision}


def _utc_now() -> Any:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
