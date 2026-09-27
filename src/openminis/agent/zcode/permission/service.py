"""Permission Service — permission checking and decision making.

Ported from: packages/core/src/permission/service.ts
Original package: @zcode/core (permission)

# PORT-STATUS: partial
# PORT: 三处外部依赖尚未移植（不在本次范围）：
#   1. ``applyPermissionUpdates`` ← ``core/src/tool/executor/permission-rules.ts``（按模块映射
#      直连其未来落点 ``..tool.executor.permission_rules``）。
#   2. ``isWebFetchPreapprovedUrl`` ← ``core/src/tool/webfetch-preapproved.ts``（未来落点
#      ``..tool.webfetch_preapproved``）。
#   3. ``isPreapprovedWorkflowDraftWrite`` ← ``core/src/permission/workflow-draft-path.ts``，
#      属 workflow(办公/编排) 范围，明确不移植；此处保留调用位与判定分支，占位实现保守返回
#      False（即不给出草稿免确认），绝不误放行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..contracts.model import ModelToolSideEffectScope
from ..contracts.tools import RiskLevel, ToolPermissionSpec
from ..tool.executor.permission_rules import apply_permission_updates
from ..tool.webfetch_preapproved import is_web_fetch_preapproved_url
from .plan_mode_policy import resolve_plan_mode_transition_permission
from .ports import (
    AMEND_WORKFLOW_TOOL_NAME,
    OFFICIAL_CUA_PERMISSION_RULE_TOOL_NAME,
    CollaborationMode,
    PermissionBehavior,
    PermissionCapabilityGroup,
    PermissionContext,
    PermissionDecisionResult,
    PermissionRuleValue,
    PermissionRuleset,
    PermissionToolCapability,
    PermissionUpdate,
    ToolPermissionRulePolicy,
)
from .rule_matching import web_fetch_rule_subjects, wildcard_to_regexp

__all__ = [
    # service.ts 中导出的公开面（类型实际落点见 ports.py）
    "PermissionService",
    "PermissionConfig",
    "default_permission_config",
    "PermissionContext",
    "PermissionToolCapability",
    "PermissionBehavior",
    "PermissionDecisionResult",
]

# -----------------------------------------------
# Types
# -----------------------------------------------

# PORT: 草稿免确认的规则号（TS 未导出，故下划线私有）。
_WORKFLOW_DRAFT_PREAPPROVED_RULE_ID = "tool.workflowDraft.preapproved"


def _is_preapproved_workflow_draft_write(input: dict[str, Any]) -> bool:
    """占位实现，对应 ``workflow-draft-path.ts`` 的 ``isPreapprovedWorkflowDraftWrite``。

    原函数判定「这次写入目标是否落在 ``<workingDirectory>/.zcode/workflow-drafts/`` 内」。
    该文件属 workflow(办公/编排) 范围，明确不移植；此处仅保留 service 侧的调用位与分支，
    以保守返回值 False 表示"不给出免确认"——绝不因缺失实现而误放行任何写入。

    # PORT: 待 workflow 模块纳入范围后替换为真实实现。
    """
    return False


# -----------------------------------------------
# Permission Service
# -----------------------------------------------

class PermissionService:
    """Mirrors ``PermissionService``."""

    def __init__(self, config: Optional["PermissionConfig"] = None) -> None:
        # PORT: TS 默认参数 ``config: PermissionConfig = defaultPermissionConfig``。Python 默认值
        # 在函数定义时求值，故改用 None 哨兵，在调用时按 ``??`` 语义回落到 defaultPermissionConfig。
        self.config: PermissionConfig = config if config is not None else default_permission_config

        # 会话级 allow 规则（「Always allow in this session」）。
        # 一个实例 = 一个 app = 一个会话，所以"随会话消亡"不需要任何额外机制：重启 / 冷恢复 / `/new`
        # 都会造一个空的新实例。只服务 alwaysAsk gate（见 checkAlwaysAsk），普通工具的模式语义不认它。
        self.session_rules: PermissionRuleset = {"version": 1}

    def grant_session_permission(self, updates: list[PermissionUpdate]) -> None:
        """Mirrors ``grantSessionPermission(updates)``."""
        self.session_rules = apply_permission_updates(self.session_rules, updates)

    def check_permission(
        self,
        context: PermissionContext,
        tool_capability: Optional[PermissionToolCapability] = None,
        project_rules: Optional[PermissionRuleset] = None,
        rule_policy: Optional[ToolPermissionRulePolicy] = None,
    ) -> PermissionDecisionResult:
        """Mirrors ``checkPermission(context, toolCapability?, projectRules?, rulePolicy?)``."""
        capability = self._resolve_capability(context, tool_capability)
        plan_mode_transition = resolve_plan_mode_transition_permission(context)

        if plan_mode_transition is not None:
            return (
                self._allow(
                    context, capability, plan_mode_transition.rule_id, plan_mode_transition.reason
                )
                if plan_mode_transition.behavior == "allow"
                else self._deny(
                    context, capability, plan_mode_transition.rule_id, plan_mode_transition.reason
                )
            )

        if capability.requires_user_interaction:
            if context.tool_name in self.config.disallowed_tools:
                return self._deny(
                    context,
                    capability,
                    "rule.disallowedTools",
                    f"Tool {context.tool_name} is explicitly disallowed",
                )

            return self._ask(
                context,
                capability,
                "tool.userInteraction",
                f"Tool {context.tool_name} requires user interaction",
            )

        # 声明 alwaysAsk 的工具必须经过用户确认，不能被权限模式的放行分支绕过。
        if capability.always_ask:
            return self._check_always_ask(context, capability, project_rules, rule_policy)

        plan_enabled = (
            context.plan_enabled if context.plan_enabled is not None else context.mode == "plan"
        )
        if context.mode == "yolo" and not plan_enabled:
            return self._allow(
                context, capability, "mode.yolo", "Yolo mode bypasses permission prompts"
            )

        if context.mode == "auto":
            return self._deny(
                context,
                capability,
                "mode.auto.unimplemented",
                "Auto mode is reserved but not implemented yet",
            )

        if context.tool_name in self.config.disallowed_tools:
            return self._deny(
                context,
                capability,
                "rule.disallowedTools",
                f"Tool {context.tool_name} is explicitly disallowed",
            )

        if self._matches_project_rules(project_rules, "deny", context, capability, rule_policy):
            return self._deny(
                context,
                capability,
                "rule.project.deny",
                f"Tool {context.tool_name} is denied by project permission rules",
            )

        if self._matches_project_rules(project_rules, "ask", context, capability, rule_policy):
            return self._ask(
                context,
                capability,
                "rule.project.ask",
                f"Tool {context.tool_name} requires approval by project permission rules",
            )

        if plan_enabled:
            return self._check_plan_mode(context, capability)

        if self._matches_project_rules(project_rules, "allow", context, capability, rule_policy):
            return self._allow(
                context,
                capability,
                "rule.project.allow",
                f"Tool {context.tool_name} is allowed by project permission rules",
            )

        if self._is_preapproved_web_fetch_request(context):
            return self._allow(
                context,
                capability,
                "tool.webfetch.preapproved",
                "WebFetch URL is preapproved",
            )

        # workflow 草稿免确认：
        # 与 WebFetch 预批同一位次——排在 plan 分支之后，因为 plan 模式必须继续拦下一切写入，
        # 草稿也是写入；也排在项目 deny / ask 之后，项目规则照样压得过它。判定本身见
        # workflow-draft-path.ts（含"为什么这样放行是安全的"）。
        if _is_preapproved_workflow_draft_write(
            {
                "input": context.input,
                "toolName": context.tool_name,
                "workingDirectory": context.working_directory,
            }
        ):
            return self._allow(
                context,
                capability,
                _WORKFLOW_DRAFT_PREAPPROVED_RULE_ID,
                "Workflow draft file is preapproved",
            )

        if context.tool_name in self.config.allowed_tools:
            return self._allow(
                context,
                capability,
                "rule.allowedTools",
                f"Tool {context.tool_name} is explicitly allowed",
            )

        if context.mode == "edit":
            return self._check_edit_mode(context, capability)

        return self._check_build_mode(context, capability)

    def _matches_project_rules(
        self,
        ruleset: Optional[PermissionRuleset],
        behavior: PermissionBehavior,
        context: PermissionContext,
        capability: "ResolvedPermissionCapability",
        rule_policy: Optional[ToolPermissionRulePolicy] = None,
    ) -> bool:
        """Mirrors ``matchesProjectRules(ruleset, behavior, context, capability, rulePolicy?)``."""
        rules = ruleset.get(behavior) if ruleset is not None else None
        if not isinstance(rules, list):
            return False
        tool_rules = [
            rule for rule in rules if self._matches_rule_scope(rule, context.tool_name, capability)
        ]
        if len(tool_rules) == 0:
            return False
        if rule_policy is not None:
            return rule_policy.evaluate_rules(behavior, tool_rules)
        return any(self._matches_rule(rule, context, capability) for rule in tool_rules)

    def _matches_rule(
        self,
        rule: PermissionRuleValue,
        context: PermissionContext,
        capability: "ResolvedPermissionCapability",
    ) -> bool:
        """Mirrors ``matchesRule(rule, context, capability)``."""
        if not self._matches_rule_scope(rule, context.tool_name, capability):
            return False
        if not rule.rule_content:
            return True

        subjects = self._rule_subjects(context.input, context.tool_name)
        if len(subjects) == 0:
            return False

        rule_content = rule.rule_content
        return any(self._matches_rule_content(subject, rule_content) for subject in subjects)

    def _matches_rule_tool_name(self, rule_tool_name: str, context_tool_name: str) -> bool:
        """Mirrors ``matchesRuleToolName(ruleToolName, contextToolName)``."""
        if rule_tool_name == context_tool_name:
            return True
        return context_tool_name == "Write" and rule_tool_name == "Edit"

    def _matches_rule_scope(
        self,
        rule: PermissionRuleValue,
        context_tool_name: str,
        capability: "ResolvedPermissionCapability",
    ) -> bool:
        """Mirrors ``matchesRuleScope(rule, contextToolName, capability)``."""
        if rule.tool_name == OFFICIAL_CUA_PERMISSION_RULE_TOOL_NAME:
            # 保留 key 只有在当前 tool entry 另行携带宿主验证后的 official_cua
            # capability 时才匹配。同名第三方 MCP、authority 漂移以及旧普通 tool
            # 都不能把可解析的 wire/storage 字符串升级成可信能力。
            return capability.permission_capability_group == PermissionCapabilityGroup.OFFICIAL_CUA
        return self._matches_rule_tool_name(rule.tool_name, context_tool_name)

    def _rule_subjects(self, input: Any, tool_name: str) -> list[str]:
        """Mirrors ``ruleSubjects(input, toolName)``."""
        if isinstance(input, str):
            return [input]
        if not isinstance(input, dict):
            return []

        record = input
        if tool_name == "WebFetch" and isinstance(record.get("url"), str):
            return web_fetch_rule_subjects(record["url"])

        for key in ("command", "url", "file_path", "path", "pattern", "patch_text"):
            value = record.get(key)
            if isinstance(value, str):
                return [value]

        return []

    def _is_preapproved_web_fetch_request(self, context: PermissionContext) -> bool:
        """Mirrors ``isPreapprovedWebFetchRequest(context)``."""
        if context.tool_name != "WebFetch":
            return False
        if not isinstance(context.input, dict):
            return False
        url = context.input.get("url")
        return isinstance(url, str) and is_web_fetch_preapproved_url(url)

    def _matches_rule_content(self, subject: str, rule_content: str) -> bool:
        """Mirrors ``matchesRuleContent(subject, ruleContent)``."""
        if rule_content.endswith(":*"):
            prefix = rule_content[:-2]
            return (
                subject == prefix
                or subject.startswith(f"{prefix} ")
                or subject.startswith(f"{prefix}\t")
            )

        if "*" in rule_content:
            return wildcard_to_regexp(rule_content).search(subject) is not None

        return subject == rule_content

    def _check_always_ask(
        self,
        context: PermissionContext,
        capability: "ResolvedPermissionCapability",
        project_rules: Optional[PermissionRuleset] = None,
        rule_policy: Optional[ToolPermissionRulePolicy] = None,
    ) -> PermissionDecisionResult:
        """工具自报 alwaysAsk 时的判定：ask 压过所有"放行"分支（yolo 直通、plan 的 readOnly 直通），
        但**压不过"阻断"**——所以这里先自己走一遍硬阻断判定。

        为什么不直接返回 ask：disallowedTools 是用户配置的硬禁用，项目 deny 规则符合工具自报的
        denyPriority: "beforeAsk"，auto 模式是"该模式未实现"的保护。少了这一步，一个被硬禁用的
        工具会退化成"弹个窗、用户一点就能跑"。

        这些判定在 checkPermission 里按原有顺序还会各自出现一次；此处刻意只覆盖 alwaysAsk 工具，
        不改动其他工具的既有优先级（尤其 yolo 目前先于 disallowedTools 放行这一点）。
        """
        if context.mode == "auto":
            return self._deny(
                context,
                capability,
                "mode.auto.unimplemented",
                "Auto mode is reserved but not implemented yet",
            )
        if context.tool_name in self.config.disallowed_tools:
            return self._deny(
                context,
                capability,
                "rule.disallowedTools",
                f"Tool {context.tool_name} is explicitly disallowed",
            )
        if self._matches_project_rules(project_rules, "deny", context, capability, rule_policy):
            return self._deny(
                context,
                capability,
                "rule.project.deny",
                f"Tool {context.tool_name} is denied by project permission rules",
            )
        # 会话免确认：阻断分支之后、ask 之前。命中即放行，不发 permission 事件、不弹窗；
        # 与 gate 本身一样不看模式（yolo / plan / build 一致）。
        if self._matches_project_rules(
            self.session_rules, "allow", context, capability, rule_policy
        ):
            return self._allow(
                context,
                capability,
                "rule.session.allow",
                f"Tool {context.tool_name} was allowed for this session",
            )
        # 修订免确认：AmendWorkflow 的前驱是
        # **本会话发起**的 run、且不是用户亲手停下的，即放行。与会话规则同位——阻断分支之后、ask 之前，
        # 不看模式。事实来自 resolveInput 回填的 `predecessor`（journal 的 parent_session_id / stopReason），
        # 不是内存表：重启、冷恢复后依然成立，也没有可播种、可撤销的东西。别的会话的 run、用户停过的
        # run 照常 ask：钥匙是 run 的归属，不是字段的在场。
        if self._is_owned_workflow_amend(context):
            return self._allow(
                context,
                capability,
                "rule.session.workflowOwner",
                f"Tool {context.tool_name} amends a run this session started",
            )
        return self._ask(
            context,
            capability,
            "tool.alwaysAsk",
            f"Tool {context.tool_name} always requires explicit approval",
        )

    def _is_owned_workflow_amend(self, context: PermissionContext) -> bool:
        """AmendWorkflow 且回填的 ``predecessor`` 说「本会话的 run、非用户停下」。"""
        if context.tool_name != AMEND_WORKFLOW_TOOL_NAME:
            return False
        if not isinstance(context.input, dict):
            return False
        predecessor = context.input.get("predecessor")
        if not isinstance(predecessor, dict):
            return False
        return (
            predecessor.get("owned_by_this_session") is True
            and predecessor.get("stop_reason") != "user"
        )

    def _check_plan_mode(
        self,
        context: PermissionContext,
        capability: "ResolvedPermissionCapability",
    ) -> PermissionDecisionResult:
        """Mirrors ``checkPlanMode(context, capability)``."""
        if capability.read_only and not capability.destructive:
            return self._allow(
                context,
                capability,
                "mode.plan.readOnly",
                "Plan mode allows read-only tool execution",
            )

        if self._is_mcp_tool_capability(capability) and not capability.destructive:
            return self._allow(
                context,
                capability,
                "mode.plan.mcp",
                "Plan mode allows non-destructive MCP tool execution",
            )

        if (
            capability.allowed_in_plan_mode
            and capability.side_effect_scope == "session"
            and not capability.destructive
            and not capability.needs_approval
        ):
            return self._allow(
                context,
                capability,
                "mode.plan.explicitSessionCapability",
                "Plan mode allows this explicit non-destructive session control action",
            )

        return self._deny(
            context,
            capability,
            "mode.plan.nonReadOnly",
            "Plan mode only allows read-only, non-destructive tools",
        )

    def _is_mcp_tool_capability(self, capability: "ResolvedPermissionCapability") -> bool:
        """Mirrors ``isMcpToolCapability(capability)``."""
        return capability.permission_name == "mcp"

    def _check_build_mode(
        self,
        context: PermissionContext,
        capability: "ResolvedPermissionCapability",
    ) -> PermissionDecisionResult:
        """Mirrors ``checkBuildMode(context, capability)``."""
        if capability.read_only and not capability.destructive and not capability.needs_approval:
            return self._allow(
                context, capability, "mode.build.readOnly", "Build mode allows read-only tools"
            )

        if capability.risk_level == "critical":
            return self._ask(
                context,
                capability,
                "mode.build.criticalRisk",
                "Critical risk tools require explicit approval",
            )

        if capability.risk_level == "high" and not self.config.auto_approve_high_risk:
            return self._ask(
                context,
                capability,
                "mode.build.highRisk",
                "High risk tools require explicit approval",
            )

        if (
            capability.side_effect_scope == "session"
            and capability.risk_level == "low"
            and not capability.destructive
            and not capability.needs_approval
        ):
            return self._allow(
                context,
                capability,
                "mode.build.sessionState",
                "Build mode allows low-risk session-local state updates",
            )

        if (
            capability.needs_approval
            or capability.destructive
            or capability.side_effect_scope != "none"
        ):
            return self._ask(
                context,
                capability,
                "mode.build.sideEffect",
                "Tool has side effects and requires approval",
            )

        return self._allow(
            context,
            capability,
            "mode.build.lowRisk",
            "Build mode allows low-risk tool execution",
        )

    def _check_edit_mode(
        self,
        context: PermissionContext,
        capability: "ResolvedPermissionCapability",
    ) -> PermissionDecisionResult:
        """Mirrors ``checkEditMode(context, capability)``."""
        if capability.permission_name == "edit" and capability.side_effect_scope == "workspace":
            return self._allow(
                context, capability, "mode.edit.fileEdit", "Edit mode allows file edit tools"
            )

        return self._check_build_mode(context, capability)

    def requires_approval(
        self,
        context: PermissionContext,
        tool_capability: Optional[PermissionToolCapability] = None,
    ) -> bool:
        """Mirrors ``requiresApproval(context, toolCapability?)``."""
        decision = self.check_permission(context, tool_capability)
        return decision.decision == "ask"

    def get_risk_level(
        self, tool_name: str, tool_capability: Optional[PermissionToolCapability] = None
    ) -> RiskLevel:
        """Mirrors ``getRiskLevel(toolName, toolCapability?)``."""
        if tool_capability is not None and tool_capability.risk_level is not None:
            return tool_capability.risk_level

        if self._is_read_only_tool(tool_name):
            return "low"

        if self._is_write_tool(tool_name):
            return "medium"

        if self._is_destructive_tool(tool_name):
            return "high"

        return "medium"

    def _is_read_only_tool(self, name: str) -> bool:
        """Mirrors ``isReadOnlyTool(name)``.

        # PORT: TS 每次调用 ``new Set([...]).has(name)``；此处置为模块级 frozenset，
        # 语义等价且避免每调用分配。
        """
        return name in _READ_ONLY_TOOLS

    def _is_write_tool(self, name: str) -> bool:
        """Mirrors ``isWriteTool(name)``."""
        return name in _WRITE_TOOLS

    def _is_destructive_tool(self, name: str) -> bool:
        """Mirrors ``isDestructiveTool(name)``."""
        return name in _DESTRUCTIVE_TOOLS

    def _resolve_capability(
        self,
        context: PermissionContext,
        tool_capability: Optional[PermissionToolCapability] = None,
    ) -> "ResolvedPermissionCapability":
        """Mirrors ``resolveCapability(context, toolCapability?)``."""
        permission = tool_capability.permission if tool_capability is not None else None

        # ``toolCapability?.permission?.alwaysAsk ?? toolCapability?.alwaysAsk ?? false``
        permission_always_ask = permission.always_ask if permission is not None else None
        capability_always_ask = tool_capability.always_ask if tool_capability is not None else None
        if permission_always_ask is not None:
            always_ask = permission_always_ask
        elif capability_always_ask is not None:
            always_ask = capability_always_ask
        else:
            always_ask = False

        # ``toolCapability?.permission?.sideEffectScope ?? toolCapability?.sideEffectScope``
        permission_scope = permission.side_effect_scope if permission is not None else None
        capability_scope = tool_capability.side_effect_scope if tool_capability is not None else None
        inner_scope = permission_scope if permission_scope is not None else capability_scope

        capability_requires_interaction = (
            tool_capability.requires_user_interaction if tool_capability is not None else None
        )
        if capability_requires_interaction is not None:
            requires_user_interaction = capability_requires_interaction
        else:
            requires_user_interaction = inner_scope == "userInteraction"

        if permission_scope is not None:
            side_effect_scope: ModelToolSideEffectScope = permission_scope
        elif capability_scope is not None:
            side_effect_scope = capability_scope
        else:
            side_effect_scope = "none" if self._is_read_only_tool(context.tool_name) else "workspace"

        permission_risk = permission.risk_level if permission is not None else None
        risk_level = (
            permission_risk
            if permission_risk is not None
            else self.get_risk_level(context.tool_name, tool_capability)
        )

        permission_needs = permission.needs_approval if permission is not None else None
        capability_needs = tool_capability.needs_approval if tool_capability is not None else None
        if permission_needs is not None:
            needs_approval = permission_needs
        elif capability_needs is not None:
            needs_approval = capability_needs
        else:
            needs_approval = not self._is_read_only_tool(context.tool_name)

        return ResolvedPermissionCapability(
            allowed_in_plan_mode=(
                tool_capability.allowed_in_plan_mode
                if tool_capability is not None and tool_capability.allowed_in_plan_mode is not None
                else False
            ),
            always_ask=always_ask,
            read_only=(
                tool_capability.read_only
                if tool_capability is not None and tool_capability.read_only is not None
                else self._is_read_only_tool(context.tool_name)
            ),
            destructive=(
                tool_capability.destructive
                if tool_capability is not None and tool_capability.destructive is not None
                else self._is_destructive_tool(context.tool_name)
            ),
            requires_user_interaction=requires_user_interaction,
            side_effect_scope=side_effect_scope,
            risk_level=risk_level,
            needs_approval=needs_approval,
            permission_capability_group=(
                tool_capability.permission_capability_group
                if tool_capability is not None
                else None
            ),
            permission_name=permission.permission if permission is not None else None,
        )

    def _allow(
        self,
        context: PermissionContext,
        capability: "ResolvedPermissionCapability",
        rule_id: str,
        reason: Optional[str] = None,
    ) -> PermissionDecisionResult:
        """Mirrors ``allow(context, capability, ruleId, reason?)``."""
        return self._result("allow", context, capability, rule_id, reason)

    def _ask(
        self,
        context: PermissionContext,
        capability: "ResolvedPermissionCapability",
        rule_id: str,
        reason: str,
    ) -> PermissionDecisionResult:
        """Mirrors ``ask(context, capability, ruleId, reason)``."""
        return self._result("ask", context, capability, rule_id, reason)

    def _deny(
        self,
        context: PermissionContext,
        capability: "ResolvedPermissionCapability",
        rule_id: str,
        reason: str,
    ) -> PermissionDecisionResult:
        """Mirrors ``deny(context, capability, ruleId, reason)``."""
        return self._result("deny", context, capability, rule_id, reason)

    def _result(
        self,
        decision: PermissionBehavior,
        context: PermissionContext,
        capability: "ResolvedPermissionCapability",
        rule_id: str,
        reason: Optional[str] = None,
    ) -> PermissionDecisionResult:
        """Mirrors ``result(decision, context, capability, ruleId, reason?)``."""
        return PermissionDecisionResult(
            decision=decision,
            allowed=decision == "allow",
            escalated=decision == "ask",
            mode=context.mode,
            rule_id=rule_id,
            risk_level=capability.risk_level,
            reason=reason,
            side_effect_scope=capability.side_effect_scope,
            # TS ``...(capability.alwaysAsk ? { alwaysAsk: true } : {})``
            always_ask=True if capability.always_ask else None,
        )


@dataclass(slots=True)
class ResolvedPermissionCapability:
    """Mirrors ``ResolvedPermissionCapability``."""

    allowed_in_plan_mode: bool
    always_ask: bool
    read_only: bool
    destructive: bool
    requires_user_interaction: bool
    side_effect_scope: ModelToolSideEffectScope
    risk_level: RiskLevel
    needs_approval: bool
    permission_capability_group: Optional[PermissionCapabilityGroup] = None
    permission_name: Optional[str] = None


# -----------------------------------------------
# Configuration
# -----------------------------------------------

@dataclass(slots=True)
class PermissionConfig:
    """Mirrors ``PermissionConfig``."""

    allowed_tools: set[str] = field(default_factory=set)
    disallowed_tools: set[str] = field(default_factory=set)
    auto_approve_high_risk: bool = False
    allow_medium_risk_in_auto_mode: bool = False


default_permission_config: PermissionConfig = PermissionConfig(
    allowed_tools=set(),
    disallowed_tools=set(),
    auto_approve_high_risk=False,
    allow_medium_risk_in_auto_mode=False,
)


# PORT: TS ``new Set([...])`` 在 ``isReadOnlyTool`` / ``isWriteTool`` / ``isDestructiveTool``
# 内每次调用重建；此处置为模块级 frozenset（语义等价）。
_READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "Read",
        "Glob",
        "Grep",
        "WebSearch",
        "WebFetch",
        "TodoRead",
        "TodoWrite",
        "AskUserQuestion",
        "Agent",
        "Task",
        "Skill",
    }
)

_WRITE_TOOLS: frozenset[str] = frozenset({"Write", "Edit", "ApplyPatch", "Bash"})

_DESTRUCTIVE_TOOLS: frozenset[str] = frozenset({"Bash"})
