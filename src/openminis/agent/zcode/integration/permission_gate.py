"""Permission gate for the host ZCode loop — 把内核的权限判定接进简化循环。

Ported from: packages/core/src/tool/executor/*（判定链） +
             packages/core/src/permission/service.ts（已移植，此处只是调用方）
Original package: @zcode/core

## 为什么需要这一层

上游的工具执行走 ``ToolExecutor``，它在真正调用 handler 之前先过
``PermissionService.checkPermission``。本移植集的 ZCode 循环
（``integration/agent.py::_run_turns``）是一个**精简循环**：它直接
``await handler(args, context)``，中间没有执行器，所以那一整条判定链是缺的。

缺了它，「plan 模式是只读的」这条**核心承诺**就只是提示词里的一句话 ——
模型完全可以一边说「我在规划」一边调 ``Write`` 把文件改了。这一层把闸门补回来：
判定复用**已移植**的 ``PermissionService``，不新写一套规则。

## 判定边界（刻意保守）

* 只有 ``plan_enabled`` 为真时才拦。其余模式（build/edit/yolo）一律放行 ——
  本移植集没有交互式审批通道，「ask」在这里无法变成真实的用户确认，把它当成
  「拒绝」会让正常工具全部失效。这属于**已知边界**，不是疏漏。
* **``ExitPlanMode`` 例外**：它 ``requires_user_interaction=True``，判定链会给出
  ``ask``（= 等用户点头）。宿主没有可以点头的通道，照字面处理会让**计划永远批不了、
  模型卡在只读阶段直到轮次耗尽** —— 上游 ``workflow-actor-tools.ts`` 的注释里写了
  同一个坑（「AskUserQuestion / EnterPlanMode 在 headless child 里没有人可问，
  turn 永远不结束」）。所以这里放行它：计划仍会**落盘**
  （``.zcode/plans/plan-<会话>.md``）并作为工具结果出现在前端工具卡里，用户可以用
  下一条消息否决或修正。**这是与上游的一处行为差异，刻意为之**，需要真正的
  「批准 / 拒绝」闸门时应由宿主提供交互端口，而不是让循环挂住。
* 拦下时返回**给模型看的错误文本**（而不是抛异常终止整轮）：模型看到
  「plan 模式只允许只读工具」后可以改用 ``Read``/``Glob``/``Grep``，或直接
  ``ExitPlanMode`` 去拿批准 —— 这正是上游的语义。
"""

from __future__ import annotations

from typing import Any, Optional

__all__ = [
    "PLAN_MODE_DENIED_TEMPLATE",
    "APPROVAL_REQUEST_TOOLS",
    "check_tool_gate",
    "build_permission_capability",
]

#: 判定为 ``ask`` 时，允许在「无交互通道」的宿主里继续执行的工具。
#:
#: 只有它一个 —— 这正是上游 ``interaction-broker.ts`` 里
#: ``EXIT_PLAN_MODE_APPROVAL_QUESTION = "Review this implementation plan."`` 那一问的
#: 落点。其余 ``requiresUserInteraction`` 工具（``AskUserQuestion`` 等）**不在**
#: 此列：它们本身就是为了拿到用户输入才存在的，放行等于凭空编一个答案。
APPROVAL_REQUEST_TOOLS: frozenset[str] = frozenset({"ExitPlanMode"})


#: 被 plan 模式拦下时给模型看的文本。刻意点明「下一步该做什么」——
#: 只丢一句「拒绝了」会让模型把失败当瞬时故障、原地重试到轮次耗尽（正是本项目
#: 「ZCode 又卡了」的成因之一）。
PLAN_MODE_DENIED_TEMPLATE = (
    "[tool error] {tool} 被 plan 模式拦下：{reason}\n"
    "当前处于**只读的规划阶段**，不要修改任何文件或系统状态。\n"
    "请改用 Read / Glob / Grep 继续调研；如果你已经想清楚方案，"
    "调用 ExitPlanMode 把计划交给用户批准，批准后再动手实现。"
)


def build_permission_capability(spec: Any) -> Any:
    """把内核 ``ToolEntry.metadata`` 转成 ``PermissionToolCapability``。

    ``metadata`` 是本包 ``ToolMetadata`` dataclass（``ToolEntry.__post_init__`` 已归一），
    所以这里读的是 snake_case 字段；缺字段时交给 ``PermissionService`` 自己的
    ``_is_read_only_tool`` / ``_is_destructive_tool`` 兜底。
    """
    from ..permission.ports import PermissionToolCapability

    metadata = getattr(spec.entry, "metadata", None) if spec is not None else None
    if metadata is None:
        return PermissionToolCapability()

    return PermissionToolCapability(
        allowed_in_plan_mode=_read(metadata, "allowed_in_plan_mode"),
        always_ask=_read(metadata, "always_ask"),
        read_only=_read(metadata, "read_only"),
        destructive=_read(metadata, "destructive"),
        requires_user_interaction=_read(metadata, "requires_user_interaction"),
        side_effect_scope=_read(metadata, "side_effect_scope"),
        risk_level=_read(metadata, "risk_level"),
        needs_approval=_read(metadata, "needs_approval"),
    )


def _read(metadata: Any, key: str) -> Any:
    if isinstance(metadata, dict):
        return metadata.get(key)
    return getattr(metadata, key, None)


def check_tool_gate(
    *,
    tool_name: str,
    tool_input: Any,
    capability: Any,
    mode: str,
    plan_enabled: bool,
) -> Optional[str]:
    """返回 ``None`` = 放行；返回字符串 = 拒绝理由（可直接给模型看）。

    只在 ``plan_enabled`` 为真时判定 —— 见模块 docstring 的「判定边界」。
    """
    if not plan_enabled:
        return None

    from ..permission.ports import PermissionContext
    from ..permission.service import PermissionService

    context = PermissionContext(
        tool_name=tool_name,
        input=tool_input,
        risk_level=_risk_of(capability),
        mode=mode or "build",
        plan_enabled=True,
    )
    try:
        decision = PermissionService().check_permission(context, capability)
    except Exception:  # pragma: no cover - 判定链故障时保守放行（不因闸门故障锁死工具）
        return None

    if getattr(decision, "allowed", True):
        return None

    # 「等用户点头」类工具的免挂起放行 —— 见 ``APPROVAL_REQUEST_TOOLS`` 的注释。
    if tool_name in APPROVAL_REQUEST_TOOLS and str(
        getattr(decision, "decision", "") or ""
    ) == "ask":
        return None

    reason = str(getattr(decision, "reason", "") or "permission denied")
    return PLAN_MODE_DENIED_TEMPLATE.format(tool=tool_name, reason=reason)


def _risk_of(capability: Any) -> str:
    risk = getattr(capability, "risk_level", None) if capability is not None else None
    return str(risk or "medium")
