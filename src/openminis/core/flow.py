"""Reactive state holders mirroring Kotlin coroutine Flow.

Ported from: the Kotlin ``kotlinx.coroutines.flow`` primitives used throughout
``com.openminis.app`` (``StateFlow`` / ``MutableStateFlow`` / ``SharedFlow`` /
``MutableSharedFlow``).

Why this exists: the Android port leans on ``StateFlow<T>`` for every
ViewModel's UI state, and on ``SharedFlow`` for one-shot events (navigation,
toasts, dialogs). TUI screens and the FastAPI layer both observe the same
objects, so the semantics have to match — in particular:

* ``StateFlow`` **always** has a current ``.value`` (Kotlin requires an initial
  value; so do we).
* ``StateFlow`` **deduplicates**: assigning an equal value emits nothing.
  Kotlin uses ``Any.equals``; we use ``==``.
* Collectors see the current value immediately on subscribe (Kotlin replays
  the latest value to new collectors).
* ``SharedFlow`` does *not* replay by default and never deduplicates.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Generic, TypeVar

T = TypeVar("T")

__all__ = [
    "StateFlow",
    "MutableStateFlow",
    "SharedFlow",
    "MutableSharedFlow",
    "flow_of",
    "collect_latest",
]


class _BaseFlow(Generic[T]):
    """Shared subscriber bookkeeping."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[T]] = []
        # Guards mutation of _subscribers; also serialises emissions so that
        # two concurrent emitters can't interleave into a subscriber queue.
        self._lock = asyncio.Lock()

    def _subscribe(self) -> asyncio.Queue[T]:
        q: asyncio.Queue[T] = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def _unsubscribe(self, q: asyncio.Queue[T]) -> None:
        with contextlib.suppress(ValueError):
            self._subscribers.remove(q)

    async def _fan_out(self, value: T) -> None:
        async with self._lock:
            for q in list(self._subscribers):
                q.put_nowait(value)

    @property
    def subscriber_count(self) -> int:
        """Kotlin: ``subscriptionCount`` — handy in tests and diagnostics."""
        return len(self._subscribers)

    def __aiter__(self) -> AsyncIterator[T]:
        return self.collect()

    async def collect(self) -> AsyncIterator[T]:  # pragma: no cover - interface
        raise NotImplementedError
        yield  # pragma: no cover


class MutableStateFlow(_BaseFlow[T]):
    """Kotlin ``MutableStateFlow<T>(initial)``.

    ``.value`` is the single source of truth; reading it never suspends.
    Assigning an equal value is a no-op (Kotlin's ``equals`` dedup).
    """

    def __init__(self, initial: T) -> None:
        super().__init__()
        self._value: T = initial

    @property
    def value(self) -> T:
        return self._value

    @value.setter
    def value(self, new_value: T) -> None:
        self.set(new_value)

    def set(self, new_value: T) -> None:
        """Kotlin: ``value = x`` / ``update { }``.

        Synchronous on purpose: Kotlin's ``MutableStateFlow.value = x`` does not
        suspend. Downstream delivery is scheduled on the running loop so callers
        inside a sync context (e.g. a Textual message handler) stay safe.
        """
        if self._value == new_value:
            return  # StateFlow dedup — matches Kotlin semantics
        self._value = new_value
        self._schedule_emit(new_value)

    def update(self, transform: Callable[[T], T]) -> None:
        """Kotlin: ``update { current -> ... }`` (atomic compare-and-apply)."""
        self.set(transform(self._value))

    def _schedule_emit(self, value: T) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No loop (sync context, e.g. module import or a plain script).
            # Value is already stored; subscribers would have to poll.
            return
        loop.create_task(self._fan_out(value))

    async def emit(self, value: T) -> None:
        """Awaitable variant — use from async code that wants back-pressure."""
        self._value = value
        await self._fan_out(value)

    async def collect(self) -> AsyncIterator[T]:
        """Kotlin: ``stateFlow.collect { }`` — replays current value first."""
        q = self._subscribe()
        try:
            yield self._value
            while True:
                yield await q.get()
        finally:
            self._unsubscribe(q)

    def __repr__(self) -> str:
        return f"MutableStateFlow({self._value!r})"


class StateFlow(MutableStateFlow[T]):
    """Read-only view. Same object, narrower intent.

    Python has no compile-time read-only, so this is documentation-first:
    annotate public ViewModel state as ``StateFlow[T]`` to signal
    "observe, don't write".
    """

    def __repr__(self) -> str:
        return f"StateFlow({self._value!r})"


class MutableSharedFlow(_BaseFlow[T]):
    """Kotlin ``MutableSharedFlow<T>(replay, extraBufferCapacity)``.

    Default ``replay=0``: late subscribers miss earlier events — this is what
    makes SharedFlow right for one-shot events (navigate, show-toast).
    """

    def __init__(self, replay: int = 0, extra_buffer_capacity: int = 0) -> None:
        super().__init__()
        self._replay = replay
        self._buffer: list[T] = []
        self._extra_buffer_capacity = extra_buffer_capacity

    async def emit(self, value: T) -> None:
        self._buffer.append(value)
        if len(self._buffer) > self._replay:
            # Keep only the replay window.
            self._buffer = self._buffer[-self._replay:] if self._replay else []
        await self._fan_out(value)

    def try_emit(self, value: T) -> bool:
        """Kotlin ``tryEmit`` — non-suspending, best effort."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self._buffer.append(value)
        if self._replay == 0:
            self._buffer.clear()
        else:
            self._buffer = self._buffer[-self._replay:]
        loop.create_task(self._fan_out(value))
        return True

    async def collect(self) -> AsyncIterator[T]:
        q = self._subscribe()
        try:
            for buffered in list(self._buffer):
                yield buffered
            while True:
                yield await q.get()
        finally:
            self._unsubscribe(q)

    def __repr__(self) -> str:
        return f"MutableSharedFlow(replay={self._replay})"


class SharedFlow(MutableSharedFlow[T]):
    """Read-only view of :class:`MutableSharedFlow`."""

    def __repr__(self) -> str:
        return f"SharedFlow(replay={self._replay})"


async def flow_of(*values: T) -> AsyncIterator[T]:
    """Kotlin ``flowOf(a, b, c)``."""
    for v in values:
        yield v


async def collect_latest(
    flow: _BaseFlow[T],
    action: Callable[[T], Awaitable[Any]],
) -> None:
    """Kotlin ``flow.collectLatest { }``.

    Cancels the previous ``action`` when a new value arrives — used in the
    original for search-as-you-type and streaming render passes.
    """
    current: asyncio.Task[Any] | None = None
    async for value in flow.collect():
        if current is not None and not current.done():
            current.cancel()
        current = asyncio.create_task(action(value))
