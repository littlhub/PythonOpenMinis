"""Tool contract — shared tool declaration surface.

Ported from: packages/contracts/src/tools/contract.ts
Original package: @zcode/contracts (tools)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .ids import ToolCallId

__all__ = [
    "ToolSideEffectScope",
    "WORKSPACE_MUTATING_SIDE_EFFECT_SCOPES",
    "PROTOCOL_ONLY_SIDE_EFFECT_SCOPES",
    "is_workspace_mutating_tool_call",
    "is_world_touching_tool_call",
    "ToolPermissionPatternSource",
    "ToolResultBudgetStrategy",
    "ToolExecutionMode",
    "RiskLevel",
    "ProviderNativeToolSpec",
    "ToolPermissionSpec",
    "ToolPermissionAskOptions",
    "ToolResultBudget",
    "ToolResultBudgetPreview",
    "ToolResultBudgetArtifact",
    "NoToolTimeoutPolicy",
    "TimedToolTimeoutPolicy",
    "ToolTimeoutPolicy",
    "ToolCancellationPolicy",
    "ToolTracePolicy",
    "ToolContractDeclaration",
    "ToolCall",
    # Tool name constants (coding-agent subagent / task tools)
    "RESPOND_TO_COORDINATOR_TOOL_NAME",
    "SEND_MESSAGE_TOOL_NAME",
    "TASK_OUTPUT_TOOL_NAME",
    "TASK_STOP_TOOL_NAME",
    "AMEND_WORKFLOW_TOOL_NAME",
    "CREATE_WORKFLOW_TOOL_NAME",
    "EXIT_PLAN_MODE_TOOL_NAME",
    # MCP / CUA display metadata keys & limits
    "CUA_TARGET_APP_DISPLAY_META_KEY",
    "ZCODE_MCP_NODE_REPL_CUA_APP_META_KEY",
    "MCP_TOOL_DISPLAY_MAX_DESCRIPTION_CHARS",
    "MCP_TOOL_DISPLAY_MAX_NAME_CHARS",
    "TASK_OUTPUT_DISPLAY_MAX_OUTPUT_CHARS",
    "TASK_OUTPUT_DISPLAY_MAX_STATUS_CHARS",
    # Output / display JSON schemas
    "RespondToCoordinatorOutputSchema",
    "SendMessageOutputSchema",
    "TaskOutputResultSchema",
    "TaskStopOutputSchema",
    "cuaTargetAppDisplaySchema",
    "nodeReplCuaAppDisplaySchema",
]

# PORT: TS is a string union; Python keeps it as ``str`` so values stay verbatim.
ToolSideEffectScope = str  # "none" | "workspace" | "git" | "network" | "system" | "session" | "userInteraction"


# 会改写工作区的副作用范围：文件系统、git、以及无法证明只读的 shell / 宿主执行。
# `network`、`session`、`userInteraction`、`none` 不碰工作区。
WORKSPACE_MUTATING_SIDE_EFFECT_SCOPES: frozenset[ToolSideEffectScope] = frozenset(
    {"workspace", "git", "system"}
)


def is_workspace_mutating_tool_call(capability: dict[str, Any]) -> bool:
    """一次工具调用（按解析后的能力）是否会改写工作区。

    缺席的范围按会改写处理（保守：未声明即不可信）。

    Mirrors ``isWorkspaceMutatingToolCall(capability)``.
    """
    if capability.get("readOnly") is True:
        return False
    scope = capability.get("sideEffectScope")
    if scope is None:
        return True
    return scope in WORKSPACE_MUTATING_SIDE_EFFECT_SCOPES


# 只服务于协议本身、不看也不动外部世界的副作用范围：`session` 是把结果 / 问题交回调用方
# （dynamic-workflow 的 `submit_result`、`escalate` 就在这一档），`userInteraction` 是问用户。
PROTOCOL_ONLY_SIDE_EFFECT_SCOPES: frozenset[ToolSideEffectScope] = frozenset(
    {"session", "userInteraction"}
)


def is_world_touching_tool_call(capability: dict[str, Any]) -> bool:
    """一次工具调用是否**看或动了外部世界**（读文件、跑命令、访问网络……）。

    判定是排除法——只把协议档（`session` / `userInteraction`）排除，其余一律算碰；
    范围缺席同样算碰。

    Mirrors ``isWorldTouchingToolCall(capability)``.
    """
    scope = capability.get("sideEffectScope")
    if scope is None:
        return True
    return scope not in PROTOCOL_ONLY_SIDE_EFFECT_SCOPES


ToolPermissionPatternSource = str  # "none" | "toolName" | "input" | "path" | "command" | "network" | "custom"

ToolResultBudgetStrategy = str  # "inline" | "truncate" | "artifact"

ToolExecutionMode = str  # "client" | "providerNative"

RiskLevel = str  # "low" | "medium" | "high" | "critical"


@dataclass(slots=True)
class ProviderNativeToolSpec:
    """Mirrors ``ProviderNativeToolSpec``."""

    logical_name: str
    provider_tool_name: str
    fallback: str = "disabled"
    provider_ids: Optional[list[str]] = None
    args: Optional[dict[str, Any]] = None
    kind: str = field(default="provider_native", init=False)


@dataclass(slots=True)
class ToolPermissionAskOptions:
    """Narrows the options offered when this tool asks.

    ``allow_always=False`` suppresses the persistent project rule;
    ``allow_always="session"`` swaps it for an in-memory, session-scoped rule.
    """

    allow_always: Any = None  # False | "session"


@dataclass(slots=True)
class ToolPermissionSpec:
    """Mirrors ``ToolPermissionSpec``."""

    permission: str
    reason: str
    risk_level: RiskLevel
    side_effect_scope: ToolSideEffectScope
    needs_approval: bool
    pattern_sources: list[ToolPermissionPatternSource]
    deny_priority: str  # "beforeAsk" | "afterStaticAllow"
    always_allow_pattern_sources: Optional[list[ToolPermissionPatternSource]] = None
    # Requires explicit user approval in every permission mode. Unlike
    # ``needs_approval``, permissive modes may not short-circuit it: it survives
    # yolo's pass-through and plan mode's read-only pass-through. It overrides
    # allow-granting branches only, never deny-granting ones.
    always_ask: Optional[bool] = None
    ask_options: Optional[ToolPermissionAskOptions] = None


@dataclass(slots=True)
class ToolResultBudgetPreview:
    max_bytes: Optional[int] = None
    max_lines: Optional[int] = None
    direction: Optional[str] = None  # "head" | "tail"


@dataclass(slots=True)
class ToolResultBudgetArtifact:
    enabled: bool = False
    retention: Optional[str] = None  # "session" | "project" | "temporary"


@dataclass(slots=True)
class ToolResultBudget:
    """Mirrors ``ToolResultBudget``."""

    max_inline_bytes: int
    max_model_bytes: int
    strategy: ToolResultBudgetStrategy
    preview: Optional[ToolResultBudgetPreview] = None
    artifact: Optional[ToolResultBudgetArtifact] = None


@dataclass(slots=True)
class NoToolTimeoutPolicy:
    """Mirrors ``NoToolTimeoutPolicy``."""

    kind: str = field(default="none", init=False)


@dataclass(slots=True)
class TimedToolTimeoutPolicy:
    """Mirrors ``TimedToolTimeoutPolicy``.

    ``cleanup_grace_ms``: extra wall-clock budget reserved for cancellation and
    adapter cleanup after the tool's own timeout. The user-facing timeout still
    comes from ``default_ms`` / input.
    """

    default_ms: int
    allow_call_override: bool
    max_ms: Optional[int] = None
    cleanup_grace_ms: Optional[int] = None
    kind: str = field(default="timed", init=False)


ToolTimeoutPolicy = Any  # Union[NoToolTimeoutPolicy, TimedToolTimeoutPolicy]


@dataclass(slots=True)
class ToolCancellationPolicy:
    """Mirrors ``ToolCancellationPolicy``."""

    supported: bool
    cleanup: str  # "none" | "bestEffort" | "required"
    user_visible_message: str


@dataclass(slots=True)
class ToolTracePolicy:
    """Mirrors ``ToolTracePolicy``."""

    propagate_to_adapters: bool
    record_input: str  # "summary" | "full" | "none"
    record_output: str  # "summary" | "full" | "none"
    required: bool = field(default=True, init=False)


@dataclass(slots=True)
class ToolContractDeclaration:
    """Mirrors ``ToolContractDeclaration``."""

    capability: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    permission: ToolPermissionSpec
    result_budget: ToolResultBudget
    timeout: ToolTimeoutPolicy
    cancellation: ToolCancellationPolicy
    trace: ToolTracePolicy
    execution_mode: Optional[ToolExecutionMode] = None
    provider_native: Optional[ProviderNativeToolSpec] = None
    # 声明 `input_schema` **有资格**走 provider 的严格模式。
    # 只是资格，不是命令：adapter 按 provider / model 决定是否真的下发。
    strict: Optional[bool] = None
    requires_user_interaction: Optional[bool] = None


# ---------------------------------------------------------------------------
# Tool name constants (coding-agent subagent / task tools)
# ---------------------------------------------------------------------------
# PORT: mirrors the tool-name constants in ``packages/contracts/src/tools``.

RESPOND_TO_COORDINATOR_TOOL_NAME = "respond_to_coordinator"
SEND_MESSAGE_TOOL_NAME = "send_message"
TASK_OUTPUT_TOOL_NAME = "task_output"
TASK_STOP_TOOL_NAME = "task_stop"

# ---------------------------------------------------------------------------
# MCP / CUA display metadata keys & limits
# ---------------------------------------------------------------------------

CUA_TARGET_APP_DISPLAY_META_KEY = "cuaTargetAppDisplay"
ZCODE_MCP_NODE_REPL_CUA_APP_META_KEY = "zcodeMcpNodeReplCuaApp"

MCP_TOOL_DISPLAY_MAX_DESCRIPTION_CHARS = 200
MCP_TOOL_DISPLAY_MAX_NAME_CHARS = 64
TASK_OUTPUT_DISPLAY_MAX_OUTPUT_CHARS = 2000
TASK_OUTPUT_DISPLAY_MAX_STATUS_CHARS = 200

# ---------------------------------------------------------------------------
# Output / display JSON schemas
# ---------------------------------------------------------------------------
# PORT: minimal JSON-schema shapes mirroring the original TS ``*Schema`` objects.
# Kept as loose dicts so downstream display code imports resolve.

RespondToCoordinatorOutputSchema: dict[str, Any] = {
    "type": "object",
    "properties": {"result": {"type": "string"}},
    "required": ["result"],
}

SendMessageOutputSchema: dict[str, Any] = {
    "type": "object",
    "properties": {
        "taskId": {"type": "string"},
        "block": {"type": "boolean"},
        "result": {"type": "string"},
    },
    "required": ["taskId"],
}

TaskOutputResultSchema: dict[str, Any] = {
    "type": "object",
    "properties": {
        "taskId": {"type": "string"},
        "block": {"type": "boolean"},
        "timeout": {"type": "number"},
    },
    "required": ["taskId"],
}

TaskStopOutputSchema: dict[str, Any] = {
    "type": "object",
    "properties": {"taskId": {"type": "string"}},
    "required": ["taskId"],
}

cuaTargetAppDisplaySchema: dict[str, Any] = {
    "type": "object",
    "properties": {
        "appName": {"type": "string"},
        "windowTitle": {"type": "string"},
    },
    "required": ["appName"],
}

nodeReplCuaAppDisplaySchema: dict[str, Any] = {
    "type": "object",
    "properties": {
        "appName": {"type": "string"},
        "windowTitle": {"type": "string"},
    },
    "required": ["appName"],
}


# Workflow tool-name constants (office workflow — kept for turn_control import).
AMEND_WORKFLOW_TOOL_NAME = "amend_workflow"
CREATE_WORKFLOW_TOOL_NAME = "create_workflow"
# 上游 ``AMEND_WORKFLOW_TOOL_NAME`` / ``CREATE_WORKFLOW_TOOL_NAME`` 确实是 snake_case
# （见 ``contracts/src/tools/amend-workflow.ts``），但 ``EXIT_PLAN_MODE_TOOL_NAME`` 是
# **PascalCase 的 ``ExitPlanMode``**（``contracts/src/tools/plan-mode.ts``）——本行原先
# 照抄了邻居的命名风格写成 ``exit_plan_mode``。后果是
# ``with_plan_exit_denied_turn_stop`` 里 ``input.toolName != EXIT_PLAN_MODE_TOOL_NAME``
# 恒为真：用户拒绝计划后那道「本轮就此收手」的闸门**从来没关上过**。
EXIT_PLAN_MODE_TOOL_NAME = "ExitPlanMode"


@dataclass(slots=True)
class ToolCall:
    """A tool call issued by the model (runtime representation).

    PORT: mirrors ``ToolCall`` — distinct from ``ModelToolCall`` (the provider
    wire format); this is the kernel's in-flight call object.
    """

    id: ToolCallId
    name: str
    input: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # "pending" | "running" | "done" | "error"
