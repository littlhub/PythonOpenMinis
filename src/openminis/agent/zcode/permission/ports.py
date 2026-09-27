"""Permission broker port — async client permission boundary.

Ported from: packages/contracts/src/interfaces/permission.port.ts
Original package: @zcode/contracts (interfaces)

# PORT: 临时落点。PermissionService / PermissionBroker 需要的下列契约类型，在 ZCode 里来自
# @zcode/contracts（``permission.port.ts`` / ``session.port.ts`` / ``events/session.events.ts``）、
# ``@zcode/shared``，以及本包 core 的 ``tool/types.ts``；本包对应模块尚未移植，故先集中落在
# ``permission/ports.py``，供 ``permission/*`` 引用。逐项真实来源见各定义上方的 ``# PORT:`` 注释。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Literal, Optional, Protocol, Sequence, TypedDict

from ..contracts.ids import InteractionRequestOrigin, SessionId, ToolCallId, TraceId, TurnId
from ..contracts.model import ModelToolSideEffectScope
from ..contracts.tools import RiskLevel, ToolPermissionSpec
from ..core.abort import AbortSignal

__all__ = [
    "ENTER_PLAN_MODE_TOOL_NAME",
    "EXIT_PLAN_MODE_TOOL_NAME",
    "AMEND_WORKFLOW_TOOL_NAME",
    "WORKFLOW_DRAFTS_DIR",
    "OFFICIAL_CUA_PERMISSION_RULE_TOOL_NAME",
    "PermissionRuleBehavior",
    "PermissionCapabilityGroup",
    "PermissionRuleValue",
    "PermissionRuleset",
    "PermissionUpdate",
    "PermissionBrokerReasonSource",
    "PermissionOptionsPolicy",
    "PermissionBrokerRequest",
    "PermissionBrokerResult",
    "PermissionBrokerRequestOptions",
    "PermissionBrokerPort",
    "PermissionDecision",
    "PermissionBehavior",
    "CollaborationMode",
    "PrePlanMode",
    "PermissionContext",
    "PermissionToolCapability",
    "PermissionDecisionResult",
    "ToolPermissionRulePolicy",
]

# ---------------------------------------------------------------------------
# Contract constants (暂无对应的已移植 contracts 模块)
# ---------------------------------------------------------------------------

# PORT: 真实来源 packages/contracts/src/tools/plan-mode.ts
ENTER_PLAN_MODE_TOOL_NAME = "EnterPlanMode"
EXIT_PLAN_MODE_TOOL_NAME = "ExitPlanMode"

# PORT: 真实来源 packages/contracts/src/tools/amend-workflow.ts
AMEND_WORKFLOW_TOOL_NAME = "AmendWorkflow"

# PORT: 真实来源 packages/contracts/src/tools/saved-workflow.ts
WORKFLOW_DRAFTS_DIR = ".zcode/workflow-drafts"

# PORT: 真实来源 packages/shared/src/zcode-protocol-legacy-types.ts
# （service.ts 经 ``@zcode/shared`` 引用；官方 CUA 权限规则的保留 toolName。）
OFFICIAL_CUA_PERMISSION_RULE_TOOL_NAME = "zcode:permission-capability:official_cua"


# ---------------------------------------------------------------------------
# permission.port.ts — literals / enums / value types
# ---------------------------------------------------------------------------

PermissionRuleBehavior = Literal["allow", "deny", "ask"]

# PORT: TS ``as const`` 对象 ``PermissionCapabilityGroup`` → ``enum.Enum``（值保持原字符串）。
class PermissionCapabilityGroup(str, Enum):
    OFFICIAL_CUA = "official_cua"


@dataclass(slots=True)
class PermissionRuleValue:
    """Mirrors ``PermissionRuleValue``."""

    tool_name: str
    rule_content: Optional[str] = None


class PermissionRuleset(TypedDict, total=False):
    """Mirrors ``PermissionRuleset``.

    # PORT: TS 带索引签名 ``[key: string]: unknown``；TypedDict 无法表达，故只对已声明字段
    # 建模。运行时它就是普通 dict，多余键原样透传（``applyPermissionUpdates`` 依赖这一点）。
    """

    version: Literal[1]
    allow: list[PermissionRuleValue]
    deny: list[PermissionRuleValue]
    ask: list[PermissionRuleValue]
    mode: "CollaborationMode"


@dataclass(slots=True)
class PermissionUpdate:
    """Mirrors ``PermissionUpdate``.

    TS: ``{ type: "addRules"; behavior: PermissionRuleBehavior; rules: PermissionRuleValue[] }``.
    """

    behavior: PermissionRuleBehavior
    rules: list[PermissionRuleValue]
    type: str = field(default="addRules", init=False)


# 修改原因：workflow Refine 与 plan 反馈同构——
# deny + reason 只有携带专用 source 才允许被升级为真实 user message。
PermissionBrokerReasonSource = Literal["plan_approval_feedback", "workflow_refine_feedback"]

# Narrows the option set a permission ask may offer. `no-always-allow` drops the
# persistent project rule option: some tools carry different code on every call
# (CreateWorkflow scripts), so "always allow" would permanently disable the gate
# rather than remember a comparable decision. `session-always-allow` replaces the
# persistent rule with a session-scoped one that lives only in the runtime's memory.
PermissionOptionsPolicy = Literal["no-always-allow", "session-always-allow"]


@dataclass(slots=True)
class PermissionBrokerRequest:
    """Mirrors ``PermissionBrokerRequest``."""

    request_id: str
    session_id: SessionId
    trace_id: TraceId
    tool_call_id: ToolCallId
    tool_name: str
    input: Any
    mode: "CollaborationMode"
    rule_id: str
    reason: str
    risk_level: RiskLevel
    requested_at: datetime
    turn_id: Optional[TurnId] = None
    side_effect_scope: Optional[ModelToolSideEffectScope] = None
    suggested_permission_updates: Optional[list[PermissionUpdate]] = None
    options_policy: Optional[PermissionOptionsPolicy] = None
    origin: Optional[InteractionRequestOrigin] = None


@dataclass(slots=True)
class PermissionBrokerResult:
    """Mirrors ``PermissionBrokerResult``."""

    decision: "PermissionDecision"
    reason: Optional[str] = None
    # V4 permission feedback 需要保留 provider-visible 原文；只由 broker 结果携带。
    preserve_reason_formatting: Optional[bool] = None
    reason_source: Optional[PermissionBrokerReasonSource] = None
    modified_input: Any = None
    permission_updates: Optional[list[PermissionUpdate]] = None
    # Session-scoped grants ("Always allow in this session"). Applied to the runtime's
    # in-memory session ruleset only; never persisted with `permissionUpdates`. Synthesized
    # by the broker on the answer side because the wire option schema is strict.
    session_permission_updates: Optional[list[PermissionUpdate]] = None
    resolved_at: Optional[datetime] = None


@dataclass(slots=True)
class PermissionBrokerRequestOptions:
    """Mirrors ``PermissionBrokerRequestOptions``."""

    # 带提交副作用的应答先取得唯一胜者；失败保留用户审批，Hook 不再竞争。
    claim_response: Optional[Callable[[], bool]] = None
    signal: Optional[AbortSignal] = None
    timeout_ms: Optional[int] = None


class PermissionBrokerPort(Protocol):
    """Mirrors ``PermissionBrokerPort`` — ``async client permission boundary``."""

    async def request_permission(
        self,
        request: PermissionBrokerRequest,
        options: Optional[PermissionBrokerRequestOptions] = None,
    ) -> PermissionBrokerResult: ...


# ---------------------------------------------------------------------------
# session.events.ts / session.port.ts / service.ts — shared literals
# ---------------------------------------------------------------------------

# PORT: 真实来源 packages/contracts/src/events/session.events.ts
PermissionDecision = Literal["allow", "deny", "escalate", "modify"]

# PORT: 真实来源 packages/core/src/permission/service.ts
PermissionBehavior = Literal["allow", "ask", "deny"]

# PORT: 真实来源 packages/contracts/src/interfaces/session.port.ts
CollaborationMode = Literal["plan", "build", "edit", "yolo", "auto"]
# TS ``Exclude<CollaborationMode, "plan">``
PrePlanMode = Literal["build", "edit", "yolo", "auto"]


# ---------------------------------------------------------------------------
# service.ts — permission context / capability / decision
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PermissionContext:
    """Ported from: packages/core/src/permission/service.ts (``PermissionContext``)."""

    tool_name: str
    input: Any
    risk_level: RiskLevel
    mode: CollaborationMode
    plan_enabled: Optional[bool] = None
    pre_plan_mode: Optional[PrePlanMode] = None
    # 会话工作目录。判定相对路径的落点用（目前只有 workflow 草稿免确认这一条），
    # 可选：拿不到工作目录的调用方照常按其余规则判定，不会因此少一层确认。
    working_directory: Optional[str] = None


@dataclass(slots=True)
class PermissionToolCapability:
    """Ported from: packages/core/src/permission/service.ts (``PermissionToolCapability``)."""

    allowed_in_plan_mode: Optional[bool] = None
    always_ask: Optional[bool] = None
    read_only: Optional[bool] = None
    destructive: Optional[bool] = None
    requires_user_interaction: Optional[bool] = None
    side_effect_scope: Optional[ModelToolSideEffectScope] = None
    risk_level: Optional[RiskLevel] = None
    needs_approval: Optional[bool] = None
    permission_capability_group: Optional[PermissionCapabilityGroup] = None
    permission: Optional[ToolPermissionSpec] = None


@dataclass(slots=True)
class PermissionDecisionResult:
    """Ported from: packages/core/src/permission/service.ts (``PermissionDecisionResult``)."""

    decision: PermissionBehavior
    allowed: bool
    escalated: bool
    mode: CollaborationMode
    rule_id: str
    risk_level: RiskLevel
    reason: Optional[str] = None
    modified_input: Any = None
    side_effect_scope: Optional[ModelToolSideEffectScope] = None
    # 该 ask 来自工具的 alwaysAsk 声明，不是模式或规则推导出来的。下游（PreToolUse hook 的
    # allow 覆盖）靠这个结构化标记识别"不可抹掉的确认"，而不是去匹配 ruleId 字符串。
    always_ask: Optional[bool] = None


# ---------------------------------------------------------------------------
# tool/types.ts — rule policy (临时落点)
# ---------------------------------------------------------------------------

class ToolPermissionRulePolicy(Protocol):
    """Mirrors ``ToolPermissionRulePolicy``.

    # PORT: 真实来源 packages/core/src/tool/types.ts；正式归属 ``tool/types.py``，此处临时落点。
    """

    # 工具自报的评估器：命中即代表该 behavior 生效。
    def evaluate_rules(
        self, behavior: PermissionRuleBehavior, rules: Sequence[PermissionRuleValue]
    ) -> bool: ...

    suggested_permission_updates: list[PermissionUpdate]
