"""Session mode port — plan/build/edit/yolo/auto 的模式切换边界。

Ported from: packages/contracts/src/tools/plan-mode.ts
Original package: @zcode/contracts (tools)

``SessionModePort`` 是**宿主注入**的端口：内核的 ``EnterPlanMode`` / ``ExitPlanMode``
handler 只负责「校验 + 调用 + 格式化结果」，真正的模式状态由宿主持有（TUI 会话、
web 会话、或是本包的宿主运行时）。这与 ``FileSystemPort`` / ``ExecutionPort`` 是
同一套思路 —— 内核不假设自己跑在谁里面。

``CollaborationMode`` 已由 ``permission/ports.py`` 移植（那里是 ``PermissionContext``
的既有落点）。此处**不重复定义**，直接复用，避免出现两份可能漂移的 Literal。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol

from ..permission.ports import CollaborationMode, PrePlanMode

__all__ = [
    "PLAN_MODE_MAX_PLAN_CHARS",
    "SessionModeTransitionInput",
    "EnterPlanModeTransitionResult",
    "ExitPlanModeTransitionResult",
    "SessionModePort",
]

# PORT: 真实来源 packages/contracts/src/tools/plan-mode.ts
PLAN_MODE_MAX_PLAN_CHARS = 20_000


@dataclass(slots=True)
class SessionModeTransitionInput:
    """Mirrors ``SessionModeTransitionInput``."""

    tool_call_id: Optional[str] = None
    #: PORT: ``TraceContext`` —— 本包以 Mapping 透传（契约层不依赖 tracing 实现）。
    trace_context: Optional[Any] = None


@dataclass(slots=True)
class EnterPlanModeTransitionResult:
    """Mirrors ``EnterPlanModeTransitionResult``."""

    mode: CollaborationMode
    previous_mode: CollaborationMode
    plan_enabled: Optional[bool] = None
    previous_plan_enabled: Optional[bool] = None


@dataclass(slots=True)
class ExitPlanModeTransitionResult:
    """Mirrors ``ExitPlanModeTransitionResult``.

    TS: ``mode: Exclude<CollaborationMode, "plan">`` —— 退出 plan 模式后不可能仍是
    ``"plan"``；本包用注释保留该约束（``Literal`` 减法在运行时不设防）。
    """

    mode: CollaborationMode
    previous_mode: CollaborationMode
    plan_enabled: Optional[bool] = None
    previous_plan_enabled: Optional[bool] = None


class SessionModePort(Protocol):
    """Mirrors ``SessionModePort`` — 宿主持有的会话模式状态边界。"""

    def supports_permission_full_access(self) -> bool:
        """TS 上是可选方法 ``supportsPermissionFullAccess?()``。"""
        ...

    def is_plan_enabled(self) -> bool:
        """TS 上是可选方法 ``isPlanEnabled?()``。

        缺席时调用方退回 ``get_mode() == "plan"``（见 ``exit_plan_mode_handler``）。
        """
        ...

    def get_mode(self) -> CollaborationMode:
        ...

    def get_pre_plan_mode(self) -> Optional[PrePlanMode]:
        ...

    async def enter_plan_mode(
        self, input: Optional[SessionModeTransitionInput] = None
    ) -> EnterPlanModeTransitionResult:
        ...

    async def exit_plan_mode(
        self, input: Optional[SessionModeTransitionInput] = None
    ) -> ExitPlanModeTransitionResult:
        ...


# PORT: 保留导入以便类型检查器识别本模块对 ``PrePlanMode`` 的类型引用。
_ = PrePlanMode
