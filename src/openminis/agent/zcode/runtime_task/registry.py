"""Runtime task registry — in-memory snapshot store + waiters.

Ported from: packages/core/src/runtime-task/registry.ts
Original package: @zcode/core (runtime-task)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from ..contracts.tracing import TraceContext

__all__ = [
    "RUNTIME_TASK_TYPES",
    "RuntimeTaskType",
    "RuntimeTaskUsageSnapshot",
    "RuntimeTaskPendingMessage",
    "RuntimeTaskMessageSink",
    "RuntimeTaskSnapshot",
    "RuntimeTaskRegistry",
    "InMemoryRuntimeTaskRegistry",
    "is_terminal_runtime_task",
    "has_running_background_runtime_task",
]

# local_dynamic_workflow 与 local_workflow 刻意分开：后者是 legacy `Workflow` 工具（不可取消），
# 前者是 workflow run（经 DynamicWorkflowRunPort.cancel 可取消）。合成一个类型，取消分派就无法区分。
RUNTIME_TASK_TYPES = (
    "local_agent",
    "local_bash",
    "local_workflow",
    "local_dynamic_workflow",
    "monitor_mcp",
)

#: PORT: upstream ``type RuntimeTaskType = ...`` union (registry.ts).
RuntimeTaskType = str


@dataclass(slots=True)
class RuntimeTaskUsageSnapshot:
    """Mirrors ``RuntimeTaskUsageSnapshot``.

    # PORT: ``ModelUsage`` is not ported; the slot stays ``Any``.
    """

    duration_ms: Optional[float] = None
    model_usage: Any = None
    tool_use_count: Optional[int] = None
    total_tokens: Optional[int] = None


@dataclass(slots=True)
class RuntimeTaskPendingMessage:
    """Mirrors ``RuntimeTaskPendingMessage``."""

    id: str
    message: str
    queued_at: datetime
    is_meta: bool = False
    origin: Optional[dict[str, Any]] = None  # {kind: "coordinator", toolCallId?}
    summary: Optional[str] = None
    trace_context: Optional[TraceContext] = None


class RuntimeTaskMessageSink:
    """Mirrors ``RuntimeTaskMessageSink``."""

    async def send(self, message: RuntimeTaskPendingMessage) -> str:
        raise NotImplementedError


#: PORT: upstream ``interface RuntimeTaskSnapshot extends SubagentTaskSnapshot``.
#: ``SubagentTaskSnapshot`` is not ported; its contributed fields live in
#: ``extra`` (a plain record) so callers that only read registered extras
#: keep working. The core fields the executor relies on are first-class.
@dataclass(slots=True)
class RuntimeTaskSnapshot:
    task_id: str
    status: str  # "running" | "completed" | "failed" | "cancelled" | "killed" | "stopped" | "lost"
    type: RuntimeTaskType
    branch_generation: Optional[int] = None
    exit_code: Optional[int] = None
    is_backgrounded: Optional[bool] = None
    message_sink: Optional[RuntimeTaskMessageSink] = None
    output: Any = None  # AgentOutput
    parent_session_id: Optional[str] = None
    pending_messages: Optional[list[RuntimeTaskPendingMessage]] = None
    prompt: Optional[str] = None
    result_text: Optional[str] = None
    # 是谁请求停止这个任务（"user" = GUI / 后台面板，"model" = TaskStop）。
    # dwf 停止分支在调端口 cancel 之前写下它；终态通知稍后由 waiter 结算时读它。
    stop_initiator: Optional[str] = None  # "user" | "model"
    task_type: Optional[RuntimeTaskType] = None
    trace_context: Optional[TraceContext] = None
    turn_id: Optional[str] = None
    usage: Optional[RuntimeTaskUsageSnapshot] = None
    #: PORT: ``SubagentTaskSnapshot`` fields (agentId / description / ...) live
    #: here as a free-form record until that contract is ported.
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 便捷转发：注册方常把 SubagentTaskSnapshot 的字段直接放进 snapshot。
        # 保留字段名原样（camelCase 来源），读写都走 extra。
        for key in ("agentId", "description", "subagentType", "startedAt", "completedAt"):
            if key in self.extra:
                setattr(self, key, self.extra[key])  # type: ignore[misc]


class RuntimeTaskRegistry:
    """Mirrors ``interface RuntimeTaskRegistry`` (behavioral contract)."""

    def all(self) -> dict[str, "RuntimeTaskSnapshot"]:
        raise NotImplementedError

    def get(self, task_id: str) -> Optional["RuntimeTaskSnapshot"]:
        raise NotImplementedError

    def drain_messages(self, task_id: str) -> list[RuntimeTaskPendingMessage]:
        raise NotImplementedError

    def queue_message(
        self, task_id: str, message: RuntimeTaskPendingMessage
    ) -> Optional["RuntimeTaskSnapshot"]:
        raise NotImplementedError

    def register(self, task: "RuntimeTaskSnapshot") -> None:
        raise NotImplementedError

    def remove(self, task_id: str) -> None:
        raise NotImplementedError

    def request_background(self, task_id: str) -> bool:
        raise NotImplementedError

    def set_active_branch_generation(self, generation: int) -> None:
        raise NotImplementedError

    def update(
        self,
        task_id: str,
        patcher: Callable[["RuntimeTaskSnapshot"], "RuntimeTaskSnapshot"],
    ) -> Optional["RuntimeTaskSnapshot"]:
        raise NotImplementedError

    async def wait_for_background_request(
        self, task_id: str, options: Optional[dict[str, Any]] = None
    ) -> Optional["RuntimeTaskSnapshot"]:
        raise NotImplementedError

    async def wait_for_terminal(
        self, task_id: str, options: Optional[dict[str, Any]] = None
    ) -> Optional["RuntimeTaskSnapshot"]:
        raise NotImplementedError


@dataclass(slots=True)
class _RuntimeTaskWaiter:
    resolve: Any
    reject: Any
    signal: Any = None
    on_abort: Optional[Callable[[], None]] = None


TERMINAL_STATUSES = frozenset(
    ["completed", "failed", "cancelled", "killed", "stopped", "lost"]
)


def _abort_reason(signal: Any) -> BaseException:
    reason = getattr(signal, "reason", None)
    if reason is not None:
        return reason
    return RuntimeError("Runtime task wait aborted")


def _subscribe_abort(signal: Any, on_abort: Callable[[], None]) -> None:
    """PORT: Node ``signal.addEventListener('abort', cb, {once: true})``.

    内核的 AbortSignal（``zcode.core.abort``）没有 addEventListener；若注入了
    带监听器的端口实现则直接挂上，否则退化为同步检查。
    """
    add_listener = getattr(signal, "add_listener", None)
    if callable(add_listener):
        add_listener(on_abort, once=True)
        return
    add_event_listener = getattr(signal, "addEventListener", None)
    if callable(add_event_listener):
        add_event_listener("abort", on_abort)


def _unsubscribe_abort(signal: Any, on_abort: Callable[[], None]) -> None:
    remove_listener = getattr(signal, "remove_listener", None)
    if callable(remove_listener):
        remove_listener(on_abort)
        return
    remove_event_listener = getattr(signal, "removeEventListener", None)
    if callable(remove_event_listener):
        remove_event_listener("abort", on_abort)


class InMemoryRuntimeTaskRegistry(RuntimeTaskRegistry):
    def __init__(self) -> None:
        self._active_branch_generation = 0
        self._background_waiters: dict[str, set[_RuntimeTaskWaiter]] = {}
        self._tasks: dict[str, RuntimeTaskSnapshot] = {}
        self._terminal_waiters: dict[str, set[_RuntimeTaskWaiter]] = {}

    def register(self, task: RuntimeTaskSnapshot) -> None:
        if task.branch_generation is None:
            task.branch_generation = self._active_branch_generation
        self._tasks[task.task_id] = task
        self._resolve_if_terminal(task.task_id, task)
        self._resolve_if_backgrounded(task.task_id, task)

    def set_active_branch_generation(self, generation: int) -> None:
        self._active_branch_generation = generation

    def update(
        self,
        task_id: str,
        patcher: Callable[[RuntimeTaskSnapshot], RuntimeTaskSnapshot],
    ) -> Optional[RuntimeTaskSnapshot]:
        current = self._tasks.get(task_id)
        if current is None:
            return None
        next_task = patcher(current)
        self._tasks[task_id] = next_task
        self._resolve_if_terminal(task_id, next_task)
        self._resolve_if_backgrounded(task_id, next_task)
        return next_task

    def request_background(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None or is_terminal_runtime_task(task):
            return False
        next_task = RuntimeTaskSnapshot(
            **{k: v for k, v in vars(task).items()},
        )
        next_task.is_backgrounded = True
        self._tasks[task_id] = next_task
        self._resolve_background_waiters(task_id, next_task)
        return True

    def remove(self, task_id: str) -> None:
        self._tasks.pop(task_id, None)
        self._resolve_terminal_waiters(task_id, None)
        self._resolve_background_waiters(task_id, None)

    def get(self, task_id: str) -> Optional[RuntimeTaskSnapshot]:
        return self._tasks.get(task_id)

    def all(self) -> dict[str, RuntimeTaskSnapshot]:
        return dict(self._tasks)

    def queue_message(
        self, task_id: str, message: RuntimeTaskPendingMessage
    ) -> Optional[RuntimeTaskSnapshot]:
        return self.update(
            task_id,
            lambda task: RuntimeTaskSnapshot(
                **{k: v for k, v in vars(task).items()},
                pending_messages=[*(task.pending_messages or []), message],
            ),
        )

    def drain_messages(self, task_id: str) -> list[RuntimeTaskPendingMessage]:
        task = self._tasks.get(task_id)
        if task is None or not task.pending_messages:
            return []
        messages = task.pending_messages
        updated = RuntimeTaskSnapshot(
            **{k: v for k, v in vars(task).items()}, pending_messages=[]
        )
        self._tasks[task_id] = updated
        return messages

    def wait_for_background_request(
        self, task_id: str, options: Optional[dict[str, Any]] = None
    ) -> Awaitable[Optional[RuntimeTaskSnapshot]]:
        return self._wait(self._background_waiters, task_id, options)

    def wait_for_terminal(
        self, task_id: str, options: Optional[dict[str, Any]] = None
    ) -> Awaitable[Optional[RuntimeTaskSnapshot]]:
        return self._wait(self._terminal_waiters, task_id, options)

    def _wait(
        self,
        waiters_by_task: dict[str, set[_RuntimeTaskWaiter]],
        task_id: str,
        options: Optional[dict[str, Any]],
    ) -> Awaitable[Optional[RuntimeTaskSnapshot]]:
        # PORT: 上游是同步返回 Promise；这里统一返回可 await 的协程/值，
        # 调用侧照旧 ``await registry.wait_for_terminal(id)``。
        async def _waiter() -> Optional[RuntimeTaskSnapshot]:
            current = self._tasks.get(task_id)
            if task_id not in waiters_by_task:
                if waiters_by_task is self._background_waiters:
                    if (
                        current is None
                        or current.is_backgrounded
                        or is_terminal_runtime_task(current)
                    ):
                        return current if current and current.is_backgrounded else None
                elif current is None or is_terminal_runtime_task(current):
                    return current
            else:
                pass  # fallthrough: below

            signal = options.get("signal") if options else None
            if signal is not None and getattr(signal, "aborted", False):
                raise _abort_reason(signal)

            loop = asyncio.get_running_loop()
            future: asyncio.Future = loop.create_future()
            waiter = _RuntimeTaskWaiter(resolve=future.set_result, reject=future.set_exception,
                                        signal=signal)

            if signal is not None:
                def _on_abort() -> None:
                    self._remove_waiter(waiters_by_task, task_id, waiter)
                    if not future.done():
                        future.set_exception(_abort_reason(signal))

                waiter.on_abort = _on_abort
                _subscribe_abort(signal, _on_abort)

            waiters = waiters_by_task.get(task_id)
            if waiters is None:
                waiters = set()
                waiters_by_task[task_id] = waiters
            waiters.add(waiter)
            try:
                return await future
            finally:
                waiters.discard(waiter)

        return _waiter()

    def _resolve_if_backgrounded(
        self, task_id: str, task: RuntimeTaskSnapshot
    ) -> None:
        if task.is_backgrounded:
            self._resolve_background_waiters(task_id, task)

    def _resolve_if_terminal(self, task_id: str, task: RuntimeTaskSnapshot) -> None:
        if is_terminal_runtime_task(task):
            self._resolve_terminal_waiters(task_id, task)
            self._resolve_background_waiters(task_id, None)

    def _resolve_background_waiters(
        self, task_id: str, task: Optional[RuntimeTaskSnapshot]
    ) -> None:
        self._resolve_waiters(self._background_waiters, task_id, task)

    def _resolve_terminal_waiters(
        self, task_id: str, task: Optional[RuntimeTaskSnapshot]
    ) -> None:
        self._resolve_waiters(self._terminal_waiters, task_id, task)

    def _resolve_waiters(
        self,
        waiters_by_task: dict[str, set[_RuntimeTaskWaiter]],
        task_id: str,
        task: Optional[RuntimeTaskSnapshot],
    ) -> None:
        waiters = waiters_by_task.get(task_id)
        if not waiters:
            return
        del waiters_by_task[task_id]
        for waiter in waiters:
            if waiter.signal is not None and waiter.on_abort is not None:
                _unsubscribe_abort(waiter.signal, waiter.on_abort)
            if not waiter.resolve.done():
                waiter.resolve(task)

    def _remove_waiter(
        self,
        waiters_by_task: dict[str, set[_RuntimeTaskWaiter]],
        task_id: str,
        waiter: _RuntimeTaskWaiter,
    ) -> None:
        waiters = waiters_by_task.get(task_id)
        if not waiters:
            return
        waiters.discard(waiter)
        if not waiters:
            waiters_by_task.pop(task_id, None)


def is_terminal_runtime_task(task: Any) -> bool:
    return getattr(task, "status", None) in TERMINAL_STATUSES


def has_running_background_runtime_task(
    registry: RuntimeTaskRegistry,
) -> bool:
    return any(
        task.is_backgrounded is True and task.status == "running"
        for task in registry.all().values()
    )
