"""Host implementation of ``SessionModePort`` — 会话级模式状态（plan/build/edit/yolo/auto）。

Ported from: packages/core/src/runtime/session-mode-port.ts
             packages/core/src/runtime/execution-state.ts
             packages/shared/src/execution-state.ts
Original package: @zcode/core (runtime) + @zcode/shared

上游把模式当成 **运行时实例状态**（``runtime.config.mode`` / ``runtime.config.planEnabled``），
持久化成一条 session entry 并广播 ``SessionModeChanged`` 事件。本移植集没有移植
runtime 的「会话条目 / 事件总线」那一层，因此这里保留**完全相同的读改写语义**，
只把落点换成一张按会话 id 索引的内存表：宿主（本项目 = OpenMinis 的 ZCode 模式）
可以随时读它来决定 UI 与护栏，内核 handler 通过 ``SessionModePort`` 写它。

关键语义（照抄 ``resolveExecutionState``，别按直觉改）：

* **``mode`` 与 ``planEnabled`` 是两个独立字段。** 进 plan 模式**不改 ``mode``**，
  只把 ``planEnabled`` 置真 —— 退出后回到进之前的那个 mode。把 ``mode`` 写成
  ``"plan"`` 会让 ``getMode()`` 返回一个 ``ExecutionState`` 明确不接受的取值。
* ``mode`` 的合法集是 ``build|edit|yolo|auto``；``"plan"`` 只是**旧格式读取边界**上
  的兼容值（``input.mode === "plan"`` → ``planEnabled = true``）。
* 无效的 ``mode`` 输入**保持原值**，不报错也不重置。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any, Callable, Optional

from ..contracts.session_mode import (
    EnterPlanModeTransitionResult,
    ExitPlanModeTransitionResult,
    SessionModeTransitionInput,
)

__all__ = [
    "EXECUTION_PERMISSION_MODES",
    "SessionModeState",
    "resolve_execution_state",
    "HostSessionModePort",
    "get_session_mode_state",
    "reset_session_mode_state",
    "set_goal_activity_probe",
]

#: 与上游 ``executionPermissionModeSchema`` 同集：``auto`` 保留为内部权限，``plan`` 不在此列。
EXECUTION_PERMISSION_MODES: tuple[str, ...] = ("build", "edit", "yolo", "auto")


@dataclass(slots=True)
class SessionModeState:
    """Mirrors ``ExecutionState`` —— ``{ mode, planEnabled }``。"""

    mode: str = "build"
    plan_enabled: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "plan_enabled": self.plan_enabled}


def resolve_execution_state(
    input: dict[str, Any],
    current: Optional[SessionModeState] = None,
) -> SessionModeState:
    """Mirrors ``resolveExecutionState(input, current)``.

    TS:

    .. code-block:: ts

        const mode = executionPermissionModeSchema.safeParse(input.mode);
        return {
          mode: mode.success ? mode.data : current.mode,
          planEnabled: input.planEnabled ??
            (input.mode === "plan" ? true : mode.success ? false : current.planEnabled),
        };
    """
    current = current or SessionModeState()
    raw_mode = input.get("mode")
    mode_ok = isinstance(raw_mode, str) and raw_mode in EXECUTION_PERMISSION_MODES

    raw_plan = input.get("plan_enabled")
    if raw_plan is not None:
        plan_enabled = bool(raw_plan)
    elif raw_mode == "plan":
        plan_enabled = True
    elif mode_ok:
        plan_enabled = False
    else:
        plan_enabled = current.plan_enabled

    return SessionModeState(
        mode=raw_mode if mode_ok else current.mode,
        plan_enabled=plan_enabled,
    )


# ---------------------------------------------------------------------------
# 会话状态表
# ---------------------------------------------------------------------------
#: ``session_id -> SessionModeState``。宿主进程内共享：内核 handler 写、宿主读。
_SESSION_MODES: dict[str, SessionModeState] = {}
_LOCK = asyncio.Lock()


def get_session_mode_state(session_id: str) -> SessionModeState:
    """读某个会话的当前模式状态（不存在则给默认值，**不落表**）。"""
    return _SESSION_MODES.get(str(session_id or "")) or SessionModeState()


def reset_session_mode_state(session_id: str) -> None:
    """清掉某个会话的模式状态（会话删除/重置时用）。"""
    _SESSION_MODES.pop(str(session_id or ""), None)


#: 判定「当前会话是否有 active 的 goal」。上游在 ``applyRuntimeExecutionState``
#: 里硬校验「Plan 与 Goal 不能同时 active」；那需要读 session target，本移植集让宿主
#: 注入一个探针（不注入则跳过该校验，与「拿不到 target 端口」的上游行为一致）。
_GOAL_PROBE: Optional[Callable[[str], bool]] = None


def set_goal_activity_probe(probe: Optional[Callable[[str], bool]]) -> None:
    """注入「该会话是否有 active goal」的探针（``None`` = 关闭互斥校验）。"""
    global _GOAL_PROBE
    _GOAL_PROBE = probe


class HostSessionModePort:
    """按会话持有的 ``SessionModePort`` 实现（内核 handler 的唯一写入口）。

    一个实例绑定一个会话；``ZcodeHostRuntime`` 每次构造工具 context 时都会拿到
    同一个会话对应的实例，所以模型跨轮调用 ``EnterPlanMode`` / ``ExitPlanMode`` 时
    看到的是同一份状态。
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = str(session_id or "")

    # -- 读 ----------------------------------------------------------------
    def get_mode(self) -> str:
        """Mirrors ``getMode()``：``runtime.config.mode ?? "build"``。"""
        return get_session_mode_state(self.session_id).mode

    def is_plan_enabled(self) -> bool:
        """Mirrors ``isPlanEnabled()``：``readRuntimeExecutionState(runtime).planEnabled``。"""
        return get_session_mode_state(self.session_id).plan_enabled

    def get_pre_plan_mode(self) -> None:
        """Mirrors ``getPrePlanMode()`` —— 上游 runtime 实现同样恒为 ``undefined``。"""
        return None

    def supports_permission_full_access(self) -> bool:
        """Mirrors ``supportsPermissionFullAccess()``。

        上游是 ``Boolean(runtime.sessionStore?.commitPermissionFullAccess)``；本移植集的
        会话存储端口没有该能力，恒为 ``False``。
        """
        return False

    # -- 写 ----------------------------------------------------------------
    async def enter_plan_mode(
        self, input: Optional[SessionModeTransitionInput] = None, **kwargs: Any
    ) -> EnterPlanModeTransitionResult:
        """Mirrors ``enterPlanMode(input)`` —— 只置 ``planEnabled``，**不动 mode**。"""
        # PORT: 调用方（``exit_plan_mode_handler`` 风格）会把 input 打包成 dict；两种
        # 形状都接受，避免为了形式统一在 handler 里加一层不必要的转换。
        payload = _as_payload(input, kwargs)
        async with _LOCK:
            previous = get_session_mode_state(self.session_id)
            _assert_no_active_goal(self.session_id)
            next_state = resolve_execution_state({"plan_enabled": True}, previous)
            _SESSION_MODES[self.session_id] = next_state
            return EnterPlanModeTransitionResult(
                mode=next_state.mode,
                previous_mode=previous.mode,
                plan_enabled=next_state.plan_enabled,
                previous_plan_enabled=previous.plan_enabled,
            )

    async def exit_plan_mode(
        self, input: Optional[SessionModeTransitionInput] = None, **kwargs: Any
    ) -> ExitPlanModeTransitionResult:
        """Mirrors ``exitPlanMode(input)`` —— 未处于 plan 模式时抛错（不静默）。"""
        _ = _as_payload(input, kwargs)
        async with _LOCK:
            previous = get_session_mode_state(self.session_id)
            if not previous.plan_enabled:
                # 上游 ``session-mode-port.ts`` 抛的就是这句裸 Error；handler 侧另有一道
                # 同样的前置校验（会先转成 ``InvalidStateTransition`` 的 CoreError）。
                raise RuntimeError(
                    "You are not in plan mode. This tool is only for exiting plan mode "
                    "after writing a plan. If your plan was already approved, continue "
                    "with implementation."
                )
            next_state = resolve_execution_state({"plan_enabled": False}, previous)
            _SESSION_MODES[self.session_id] = next_state
            return ExitPlanModeTransitionResult(
                mode=next_state.mode,
                previous_mode=previous.mode,
                plan_enabled=next_state.plan_enabled,
                # 上游此处硬编码 ``true``（进到这一行时 plan 必然曾经开着）。
                previous_plan_enabled=True,
            )


def _as_payload(input: Any, kwargs: dict[str, Any]) -> Any:
    """兼容 ``dict`` / dataclass / 关键字三种调用形状。"""
    if isinstance(input, dict):
        return input
    if input is not None:
        return input
    if kwargs:
        return kwargs
    return None


def _assert_no_active_goal(session_id: str) -> None:
    """Mirrors ``execution-state.ts`` 的「Plan 与 Goal 不能同时 active」。"""
    probe = _GOAL_PROBE
    if probe is None:
        return
    try:
        active = bool(probe(session_id))
    except Exception:  # pragma: no cover - 探针故障不该挡住模式切换
        return
    if active:
        raise RuntimeError("Plan and Goal cannot be active at the same time.")


# PORT: 保留导入以便类型检查器识别 ``replace`` 的复用点（状态对象不可变更新）。
_ = replace
