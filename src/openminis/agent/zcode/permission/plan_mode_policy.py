"""Plan mode policy — plan-mode transition permission.

Ported from: packages/core/src/permission/plan-mode-policy.ts
Original package: @zcode/core (permission)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from .ports import (
    ENTER_PLAN_MODE_TOOL_NAME,
    EXIT_PLAN_MODE_TOOL_NAME,
    CollaborationMode,
    PrePlanMode,
)

__all__ = [
    "PlanModeTransitionContext",
    "PlanModeTransitionPermission",
    "resolve_plan_mode_transition_permission",
]


@dataclass(slots=True)
class PlanModeTransitionContext:
    """Mirrors ``PlanModeTransitionContext``."""

    tool_name: str
    mode: CollaborationMode
    plan_enabled: Optional[bool] = None
    pre_plan_mode: Optional[PrePlanMode] = None


@dataclass(slots=True)
class PlanModeTransitionPermission:
    """Mirrors ``PlanModeTransitionPermission``."""

    behavior: Literal["allow", "deny"]
    reason: str
    rule_id: str


def resolve_plan_mode_transition_permission(
    context: PlanModeTransitionContext,
) -> Optional[PlanModeTransitionPermission]:
    """Mirrors ``resolvePlanModeTransitionPermission(context)``."""
    if context.tool_name == ENTER_PLAN_MODE_TOOL_NAME:
        return PlanModeTransitionPermission(
            behavior="allow",
            reason="EnterPlanMode switches to plan mode without a permission prompt",
            rule_id="tool.plan.enter",
        )

    if context.tool_name == EXIT_PLAN_MODE_TOOL_NAME and not (
        context.plan_enabled if context.plan_enabled is not None else context.mode == "plan"
    ):
        return PlanModeTransitionPermission(
            behavior="deny",
            reason="ExitPlanMode can only be used while plan mode is active",
            rule_id="mode.plan.exitOnly",
        )

    return None
