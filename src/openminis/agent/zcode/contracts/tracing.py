"""Tracing — trace context propagation.

Ported from: packages/contracts/src/tracing/tracer.ts
Original package: @zcode/contracts (tracing)
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from .ids import QueryId, SessionId, TraceId, TurnId, create_trace_id

__all__ = [
    "TraceContext",
    "ExecutionContext",
    "Span",
    "Tracer",
    "create_tracer",
    "get_current_trace_context",
    "run_with_context",
    "run_with_context_async",
    "create_span",
    "create_root_trace_context",
    "create_child_trace_context",
    "trace_context_to_log_context",
]


# -----------------------------------------------
# Trace Context
# -----------------------------------------------

@dataclass(slots=True)
class TraceContext:
    """Mirrors ``interface TraceContext``."""

    trace_id: TraceId
    query_id: Optional[QueryId] = None
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None
    parent_id: Optional[str] = None
    session_id: Optional[SessionId] = None
    turn_id: Optional[TurnId] = None
    attributes: Optional[dict[str, Any]] = None


@dataclass(slots=True)
class ExecutionContext:
    """Mirrors ``interface ExecutionContext``.

    # PORT: ``Logger`` / ``LogContext`` live in contracts/logging which is not
    # ported yet; the logger slot stays ``Any`` until that lands.
    """

    trace: TraceContext
    logger: Any = None
    abort_signal: Any = None  # AbortSignal


# -----------------------------------------------
# Span (single unit of work within a trace)
# -----------------------------------------------

@dataclass
class Span:
    """Mirrors ``interface Span`` + the upstream ``SpanImpl`` behavior.

    # PORT: upstream declares ``Span`` (interface) and ``SpanImpl`` (class)
    # separately; Python keeps one class holding both the fields and the
    # methods, and ``_on_end`` mirrors the private constructor hook of
    # ``SpanImpl``.
    """

    trace_id: TraceId
    span_id: str
    name: str
    start_time: datetime
    parent_id: Optional[str] = None
    end_time: Optional[datetime] = None
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "running"  # "running" | "completed" | "error"
    error: Optional[BaseException] = None
    _on_end: Optional[Callable[["Span"], None]] = field(default=None, repr=False)

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        self.attributes.update(attributes)

    def end(self, error: Optional[BaseException] = None) -> None:
        if self.status != "running":
            return
        self.end_time = datetime.now(timezone.utc)
        if error:
            self.status = "error"
            self.error = error
        else:
            self.status = "completed"
        if self._on_end is not None:
            self._on_end(self)

    def add_event(
        self, name: str, attributes: Optional[dict[str, Any]] = None
    ) -> None:
        # PORT: upstream also leaves this as a no-op ("Could emit events for
        # external tracing systems") — kept verbatim.
        del name, attributes


def _generate_span_id() -> str:
    # PORT: TS ``crypto.randomUUID().slice(0, 16)``.
    return uuid.uuid4().hex[:16]


def _new_span(
    name: str,
    trace_id: TraceId,
    parent_id: Optional[str],
    on_end: Optional[Callable[[Span], None]],
) -> Span:
    return Span(
        trace_id=trace_id,
        span_id=_generate_span_id(),
        parent_id=parent_id,
        name=name,
        start_time=datetime.now(timezone.utc),
        _on_end=on_end,
    )


# -----------------------------------------------
# Tracer
# -----------------------------------------------

class Tracer:
    """Mirrors the ``Tracer`` interface + the factory-built object literal.

    # PORT: upstream ``createTracer`` returns a closure object; Python is a
    # plain class with the same three methods.
    """

    def __init__(
        self,
        name: str,
        on_span_end: Optional[Callable[[Span], None]] = None,
    ) -> None:
        self.name = name
        self._on_span_end = on_span_end

    def start_span(
        self, name: str, parent_context: Optional[TraceContext] = None
    ) -> Span:
        trace_id = (
            parent_context.trace_id
            if parent_context is not None
            else create_trace_id()
        )
        parent_span = None
        if parent_context is not None:
            parent_span = parent_context.span_id or parent_context.parent_span_id
        return _new_span(name, trace_id, parent_span, self._on_span_end)

    def with_span(
        self,
        name: str,
        fn: Callable[[Span], Any],
        parent_context: Optional[TraceContext] = None,
    ) -> Any:
        span = self.start_span(name, parent_context)
        try:
            result = fn(span)
            span.end()
            return result
        except BaseException as error:
            # PORT: TS wraps non-Error throwables with ``new Error(String(error))``;
            # in Python every throw is already an Exception — pass through as-is.
            span.end(error)
            raise

    async def with_span_async(
        self,
        name: str,
        fn: Callable[[Span], Awaitable[Any]],
        parent_context: Optional[TraceContext] = None,
    ) -> Any:
        span = self.start_span(name, parent_context)
        try:
            result = await fn(span)
            span.end()
            return result
        except BaseException as error:
            span.end(error)
            raise


def create_tracer(
    name: str, on_span_end: Optional[Callable[[Span], None]] = None
) -> Tracer:
    return Tracer(name, on_span_end)


# -----------------------------------------------
# Async Local Storage for Context Propagation
# -----------------------------------------------

# PORT: Node ``AsyncLocalStorage<TraceContext>`` → ``contextvars``.
_storage: ContextVar[Optional[TraceContext]] = ContextVar(
    "_zcode_trace_context", default=None
)


def get_current_trace_context() -> Optional[TraceContext]:
    return _storage.get()


def run_with_context(context: TraceContext, fn: Callable[[], Any]) -> Any:
    token = _storage.set(context)
    try:
        return fn()
    finally:
        _storage.reset(token)


async def run_with_context_async(
    context: TraceContext, fn: Callable[[], Awaitable[Any]]
) -> Any:
    # PORT: upstream calls the same synchronous storage run; in asyncio the
    # token must survive awaits, so wrap it in try/finally (same semantics).
    token = _storage.set(context)
    try:
        return await fn()
    finally:
        _storage.reset(token)


# -----------------------------------------------
# Convenience: create span from current context
# -----------------------------------------------

def create_span(name: str) -> Span:
    current_context = get_current_trace_context()
    trace_id = (
        current_context.trace_id
        if current_context is not None
        else create_trace_id()
    )
    parent_span = (
        (current_context.span_id or current_context.parent_span_id)
        if current_context is not None
        else None
    )
    return _new_span(name, trace_id, parent_span, None)


def _opt(options: dict[str, Any], *keys: str) -> Any:
    """First non-None value among ``keys``.

    # PORT: upstream call sites pass camelCase option keys (``sessionId``,
    # ``turnId``); some Python ports call with snake_case — accept both so
    # either spelling keeps working.
    """
    for key in keys:
        value = options.get(key)
        if value is not None:
            return value
    return None


def create_root_trace_context(
    options: Optional[dict[str, Any]] = None,
) -> TraceContext:
    options = options or {}
    return TraceContext(
        trace_id=_opt(options, "traceId", "trace_id") or create_trace_id(),
        query_id=_opt(options, "queryId", "query_id"),
        span_id=_generate_span_id(),
        session_id=_opt(options, "sessionId", "session_id"),
        turn_id=_opt(options, "turnId", "turn_id"),
        attributes=_opt(options, "attributes"),
    )


def create_child_trace_context(
    parent: TraceContext, options: Optional[dict[str, Any]] = None
) -> TraceContext:
    options = options or {}
    merged_attributes = dict(parent.attributes or {})
    merged_attributes.update(_opt(options, "attributes") or {})
    return TraceContext(
        trace_id=parent.trace_id,
        query_id=_opt(options, "queryId", "query_id") or parent.query_id,
        span_id=_generate_span_id(),
        parent_span_id=parent.span_id,
        parent_id=parent.span_id,
        session_id=_opt(options, "sessionId", "session_id") or parent.session_id,
        turn_id=_opt(options, "turnId", "turn_id") or parent.turn_id,
        attributes=merged_attributes,
    )


def trace_context_to_log_context(context: TraceContext) -> dict[str, Any]:
    # PORT: upstream returns a ``LogContext`` (contracts/logging, not ported
    # yet); the shape is identical, so return the plain dict as-is.
    log_context: dict[str, Any] = {
        "traceId": context.trace_id,
        "queryId": context.query_id,
        "spanId": context.span_id,
        "parentSpanId": context.parent_span_id,
        "sessionId": context.session_id,
        "turnId": context.turn_id,
    }
    log_context.update(context.attributes or {})
    return log_context
