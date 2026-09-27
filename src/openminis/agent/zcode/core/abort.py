"""AbortSignal — Python equivalent of the WHATWG ``AbortSignal`` / ``AbortController``.

Ported from: (no direct TS counterpart — WHATWG platform API used across
              packages/core/src/runtime and packages/core/src/tool)

The ZCode kernel threads an ``AbortSignal`` through every long-running operation
(turn loops, tool execution, model streaming) and relies on three behaviours the
Python standard library does not provide out of the box:

1. ``aborted`` is a **sticky** flag, not just a one-shot event.
2. ``reason`` carries an arbitrary payload (an exception object in practice).
3. ``throw_if_aborted()`` is called at every yield point of the turn loop —
   ``throwIfTurnAborted(state.turnAbortSignal)`` appears ~30 times in the
   original, so the helper must be cheap and must not allocate on the happy path.

# PORT: TS ``AbortController`` is sync-callable from anywhere; Python callbacks
# registered with ``add_event_listener`` are *not* invoked inline when the abort
# happens on a different task. They are scheduled on the loop instead, which
# keeps the original ordering guarantee (callbacks observe ``aborted == True``)
# without re-entrancy surprises.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Iterable, Optional

__all__ = [
    "AbortSignal",
    "AbortController",
    "AbortError",
    "TimeoutAbortError",
    "create_abort_signal",
]


class AbortError(Exception):
    """Default abort reason — mirrors the DOM ``AbortError``."""

    def __init__(self, message: str = "The operation was aborted") -> None:
        super().__init__(message)
        self.name = "AbortError"


class TimeoutAbortError(AbortError):
    """Mirrors the DOM ``TimeoutError`` thrown by ``AbortSignal.timeout``."""

    def __init__(self, message: str = "The operation timed out") -> None:
        super().__init__(message)
        self.name = "TimeoutError"


class AbortSignal:
    """A sticky, reason-carrying cancellation signal.

    Mirrors ``AbortSignal``. Create one through an :class:`AbortController`.
    """

    __slots__ = ("_aborted", "_reason", "_listeners", "_loop", "_abort_event")

    def __init__(self, *, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        self._aborted = False
        self._reason: Any = None
        self._listeners: list[Callable[[], None]] = []
        self._loop = loop
        # PORT: awaiting cancellation is expressed with an Event so that
        # ``await signal.wait()`` is possible without polling.
        self._abort_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def aborted(self) -> bool:
        """Whether the signal has been aborted. Sticky once true."""
        return self._aborted

    @property
    def reason(self) -> Any:
        """The abort reason, or ``None`` if the signal has not been aborted."""
        return self._reason

    # ------------------------------------------------------------------
    # Listener registration
    # ------------------------------------------------------------------

    def add_event_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register ``listener``; returns an unsubscribe function.

        Mirrors ``signal.addEventListener("abort", listener)``. If the signal is
        already aborted the listener is scheduled immediately, matching the DOM
        behaviour that a listener added after the fact still runs.
        """
        if self._aborted:
            self._schedule(listener)
            return lambda: None

        self._listeners.append(listener)

        def _unsubscribe() -> None:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return _unsubscribe

    def remove_event_listener(self, listener: Callable[[], None]) -> None:
        """Mirrors ``signal.removeEventListener("abort", listener)``."""
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Abort checks
    # ------------------------------------------------------------------

    def throw_if_aborted(self) -> None:
        """Mirrors ``signal.throwIfAborted()``.

        Raises the abort reason when it is already an exception, and a fresh
        :class:`AbortError` otherwise.
        """
        if not self._aborted:
            return
        reason = self._reason
        if isinstance(reason, BaseException):
            raise reason
        raise AbortError(str(reason) if reason is not None else "The operation was aborted")

    async def wait(self) -> None:
        """Await until the signal aborts, then return.

        # PORT: no TS counterpart — TS callers register a listener. This exists
        # so Python code can ``await signal.wait()`` inside a race.
        """
        await self._abort_event.wait()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _abort(self, reason: Any) -> None:
        if self._aborted:
            return
        self._aborted = True
        self._reason = reason
        self._abort_event.set()
        listeners = list(self._listeners)
        self._listeners.clear()
        for listener in listeners:
            self._schedule(listener)

    def _schedule(self, listener: Callable[[], None]) -> None:
        try:
            loop = self._loop if self._loop is not None else asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (plain sync context) — run inline like Node does.
            listener()
            return
        loop.call_soon(listener)


class AbortController:
    """Owns an :class:`AbortSignal` and can abort it once."""

    __slots__ = ("signal",)

    def __init__(self, *, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        self.signal = AbortSignal(loop=loop)

    def abort(self, reason: Any = None) -> None:
        """Mirrors ``controller.abort(reason)``."""
        if reason is None:
            reason = AbortError()
        self.signal._abort(reason)


def create_abort_signal() -> AbortController:
    """Convenience factory mirroring ``new AbortController()``."""
    return AbortController()


def any_of_signals(signals: Iterable[AbortSignal]) -> AbortSignal:
    """Mirrors ``AbortSignal.any([...])`` — aborts as soon as any input aborts."""
    controller = AbortController()
    for signal in signals:
        if signal.aborted:
            controller.abort(signal.reason)
            break

        def _forward(sig: AbortSignal = signal) -> None:
            controller.abort(sig.reason)

        signal.add_event_listener(_forward)
    return controller.signal
