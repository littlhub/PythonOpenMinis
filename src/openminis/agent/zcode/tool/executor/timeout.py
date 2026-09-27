"""Timeout — pausable tool deadline and timeout/cancellation racing.

Ported from: packages/core/src/tool/executor/timeout.ts
Original package: @zcode/core (tool/executor)

# PORT: 依赖尚未移植的契约模块 ``contracts/src/events``（``SessionEvent`` /
# ``SessionEventType``）与 ``tool/executor/utils.ts``（``isRecord``）。前者按 §6 映射直连
# ``...contracts.events``，后者由并行分工负责，直连 ``.utils``。
# PORT: ``setTimeout`` / ``clearTimeout`` → ``loop.call_later`` / ``TimerHandle.cancel``。
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any, Awaitable, Callable, Optional, TypeVar

from ...contracts.errors import CoreErrorType, create_core_error
from ...contracts.events import SessionEvent, SessionEventType
from ...core.abort import AbortController, AbortSignal
from ..types import ToolEntry, ToolExecutionContext, ToolExecutionModelContext
from .utils import is_record

__all__ = [
    "ToolDeadline",
    "observe_tool_admission_clock",
    "execute_with_timeout",
    "resolve_timeout_ms",
    "link_abort_signal",
]

TInput = TypeVar("TInput")
TOutput = TypeVar("TOutput")


def _now_ms() -> int:
    """Mirrors ``Date.now()``."""
    return int(time.time() * 1000)


class ToolDeadline:
    """可暂停的工具 deadline。

    工具内部的模型请求在进程级准入闸门前排队时暂停计时，拿到票再续，剩余时长守恒。超时守的是
    「provider 挂了」，不是「我们自己的队列长」：否则闸门把 cap 压低时会把 WebSearch / WebFetch 逐个
    逼成 60 s 超时，模型再补搜，越限流越吵（deep-research 实例里 7 次这样的 cancel）。
    多个请求并存取并集（计数器）；退避 sleep 不暂停（那是 provider 慢）；`timeoutMs` 缺席时只累计
    排队时长、不计时。
    """

    __slots__ = (
        "timeout_ms",
        "_remaining_ms",
        "_timer",
        "_armed_at",
        "_paused_at",
        "_pause_depth",
        "_queued_total_ms",
        "_on_expire",
    )

    def __init__(self, timeout_ms: Optional[int]) -> None:
        self.timeout_ms = timeout_ms
        self._remaining_ms: Optional[int] = timeout_ms
        self._timer: Optional[asyncio.TimerHandle] = None
        self._armed_at = 0
        self._paused_at = 0
        self._pause_depth = 0
        self._queued_total_ms = 0
        self._on_expire: Optional[Callable[[], None]] = None

    def start(self, on_expire: Callable[[], None]) -> None:
        """Mirrors ``start(onExpire)``."""
        self._on_expire = on_expire
        self._arm()

    def pause(self) -> None:
        """Mirrors ``pause()``."""
        self._pause_depth += 1
        if self._pause_depth != 1:
            return
        self._paused_at = _now_ms()
        if self._timer is None:
            return
        self._timer.cancel()
        self._timer = None
        self._remaining_ms = max(
            0,
            (self._remaining_ms if self._remaining_ms is not None else 0)
            - (self._paused_at - self._armed_at),
        )

    def resume(self) -> None:
        """Mirrors ``resume()``."""
        if self._pause_depth == 0:
            return
        self._pause_depth -= 1
        if self._pause_depth != 0:
            return
        self._queued_total_ms += _now_ms() - self._paused_at
        self._arm()

    def clear(self) -> None:
        """Mirrors ``clear()``."""
        if self._timer is not None:
            self._timer.cancel()
        self._timer = None
        self._on_expire = None

    @property
    def queued_ms(self) -> int:
        """累计的排队时长（含仍在暂停中的这一段）；超时错误的 context 带它，便于区分「慢」与「等」。

        Mirrors the ``queuedMs`` getter.
        """
        return self._queued_total_ms + (
            _now_ms() - self._paused_at if self._pause_depth > 0 else 0
        )

    def _arm(self) -> None:
        """Mirrors ``arm()``."""
        if (
            self._on_expire is None
            or self._remaining_ms is None
            or self._pause_depth > 0
        ):
            return
        self._armed_at = _now_ms()
        # PORT: TS ``setTimeout`` 与事件循环无关；Python 定时器必须挂在运行中的 loop 上，
        # 调用点（executeWithTimeout / 准入事件回调）都在 async 上下文内。
        self._timer = _current_loop().call_later(
            self._remaining_ms / 1000, self._fire
        )

    def _fire(self) -> None:
        """Mirrors the ``setTimeout`` callback body."""
        self._timer = None
        callback = self._on_expire
        if callback is not None:
            callback()


def _current_loop() -> asyncio.AbstractEventLoop:
    """# PORT: 无 TS 对应物 —— Python 侧取定时器要挂的 loop。"""
    return asyncio.get_running_loop()


def observe_tool_admission_clock(
    event: SessionEvent,
    tool_call_id: str,
    deadline: ToolDeadline,
) -> None:
    """从本次工具调用自己的模型状态事件里读准入等待的两端：`queued` 暂停、`admitted` 续。
    只认带本 toolCallId 的事件——同一 emitEvent 也会流过别的工具调用的状态。
    """
    if event.type != SessionEventType.MODEL_NETWORK_STATUS:
        return
    payload = getattr(event, "payload", None)
    if not isinstance(payload, dict):
        return
    if payload.get("toolCallId") != tool_call_id:
        return
    payload_type = payload.get("type")
    if payload_type == "model_request_queued":
        deadline.pause()
    elif payload_type == "model_request_admitted":
        deadline.resume()


async def execute_with_timeout(
    handler: Callable[[TInput, ToolExecutionContext], Awaitable[TOutput]],
    input: TInput,
    context: ToolExecutionContext,
    deadline: ToolDeadline,
    abort_controller: AbortController,
    entry: ToolEntry,
) -> TOutput:
    """Mirrors ``executeWithTimeout(handler, input, context, deadline, abortController, entry)``."""
    if context.abort_signal.aborted:
        raise create_core_error(
            CoreErrorType.TOOL_CANCELLED,
            _cancellation_message(entry),
        )

    settled = False
    timed_out = False
    loop = asyncio.get_running_loop()
    outcome: asyncio.Future[TOutput] = loop.create_future()

    def cleanup() -> None:
        deadline.clear()
        context.abort_signal.remove_event_listener(abort_handler)

    def reject_once(error: BaseException) -> None:
        nonlocal settled
        if settled:
            return
        settled = True
        cleanup()
        if not outcome.done():
            outcome.set_exception(error)

    def resolve_once(result: TOutput) -> None:
        nonlocal settled
        if settled:
            return
        settled = True
        cleanup()
        if not outcome.done():
            outcome.set_result(result)

    # 无 timeout 工具仍保留父级 abort 监听，但不创建墙钟定时器（deadline.start 对缺席的 timeoutMs 是空操作）。
    timeout_ms = deadline.timeout_ms

    def on_expire() -> None:
        nonlocal timed_out
        timed_out = True
        error = create_core_error(
            CoreErrorType.TOOL_TIMEOUT,
            f"Tool execution timed out after {timeout_ms}ms",
            {
                "context": {
                    "cancellation": _cancellation_cleanup(entry),
                    "queuedMs": deadline.queued_ms,
                    "timeoutMs": timeout_ms,
                    "toolName": entry.metadata.name,
                },
                "recoverable": True,
            },
        )
        abort_controller.abort(error)
        reject_once(error)

    deadline.start(on_expire)

    def abort_handler() -> None:
        if timed_out:
            return
        reject_once(
            create_core_error(
                CoreErrorType.TOOL_CANCELLED,
                _cancellation_message(entry),
                {
                    "context": {
                        "cancellation": _cancellation_cleanup(entry),
                        "toolName": entry.metadata.name,
                    },
                    "recoverable": True,
                },
            )
        )

    context.abort_signal.add_event_listener(abort_handler)

    handler_task = asyncio.ensure_future(handler(input, context))

    def _on_handler_done(task: "asyncio.Future[TOutput]") -> None:
        if task.cancelled():
            reject_once(asyncio.CancelledError())
            return
        error = task.exception()
        if error is not None:
            reject_once(error)
        else:
            resolve_once(task.result())

    handler_task.add_done_callback(_on_handler_done)

    return await outcome


def resolve_timeout_ms(
    entry: ToolEntry,
    input: Any,
    default_timeout_ms: int,
    context: Optional[ToolExecutionModelContext] = None,
) -> Optional[int]:
    """Mirrors ``resolveTimeoutMs(entry, input, defaultTimeoutMs, context?)``."""
    policy = entry.timeout
    if policy is not None and getattr(policy, "kind", None) == "none":
        return None

    # ``policy?.defaultMs ?? entry.metadata.timeoutMs ?? defaultTimeoutMs``
    default_ms = getattr(policy, "default_ms", None)
    if default_ms is None:
        default_ms = entry.metadata.timeout_ms
    if default_ms is None:
        default_ms = default_timeout_ms

    resolver = entry.resolve_timeout_budget_ms
    entry_resolved_ms = resolver(input, context) if resolver is not None else None
    allow_call_override = bool(getattr(policy, "allow_call_override", False))
    if entry_resolved_ms is not None:
        requested_ms = entry_resolved_ms
    elif (
        allow_call_override
        and is_record(input)
        and _is_number(input.get("timeout_ms"))
    ):
        requested_ms = input["timeout_ms"]
    elif allow_call_override and is_record(input) and _is_number(input.get("timeout")):
        requested_ms = input["timeout"]
    else:
        requested_ms = default_ms

    max_ms = getattr(policy, "max_ms", None)
    capped_ms = requested_ms if max_ms is None else min(requested_ms, max_ms)
    cleanup_grace_ms = max(0, math.trunc(getattr(policy, "cleanup_grace_ms", None) or 0))
    return max(1, math.trunc(capped_ms)) + cleanup_grace_ms


def link_abort_signal(
    parent_signal: Optional[AbortSignal],
    child_controller: AbortController,
) -> Callable[[], None]:
    """Mirrors ``linkAbortSignal(parentSignal, childController)``."""
    if parent_signal is None:
        return lambda: None

    def abort_child() -> None:
        if not child_controller.signal.aborted:
            child_controller.abort(parent_signal.reason)

    if parent_signal.aborted:
        abort_child()
        return lambda: None

    parent_signal.add_event_listener(abort_child)
    return lambda: parent_signal.remove_event_listener(abort_child)


def _is_number(value: Any) -> bool:
    """Mirrors ``typeof value === "number"``（``bool`` 是 ``int`` 的子类，必须排除）。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _cancellation_message(entry: ToolEntry) -> str:
    """``entry.cancellation?.userVisibleMessage ?? "Tool execution cancelled"``。"""
    if entry.cancellation is None or entry.cancellation.user_visible_message is None:
        return "Tool execution cancelled"
    return entry.cancellation.user_visible_message


def _cancellation_cleanup(entry: ToolEntry) -> str:
    """``entry.cancellation?.cleanup ?? "none"``。"""
    if entry.cancellation is None or entry.cancellation.cleanup is None:
        return "none"
    return entry.cancellation.cleanup
