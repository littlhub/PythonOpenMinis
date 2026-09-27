"""Session event management — create, append, notify.

Ported from: packages/core/src/runtime/methods/events.ts
Original package: @zcode/core (runtime/methods)
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Optional

from ..deps import (
    MessageId,
    PartId,
    SessionEvent,
    SessionEventSink,
    SessionEventStorePort,
    SessionEventType,
    SessionId,
    TraceContext,
    TurnId,
    create_message_id,
    create_part_id,
)

__all__ = [
    "create_event",
    "append_event",
    "notify_event_sinks",
    "is_session_persisted",
    "ensure_session_persisted",
]

# ---------------------------------------------------------------------------
# Constants (from source)
# ---------------------------------------------------------------------------

SESSION_EVENT_APPEND_SUMMARY_FLUSH_COUNT = 100

# Types that get aggregated for debug summaries
_SUMMARY_SESSION_EVENT_TYPES: frozenset = frozenset([
    SessionEventType.MODEL_STREAMING,
    SessionEventType.MODEL_NETWORK_STATUS,
    "streaming_tool_ledger_updated",
    "tool_call_progress",
])

# Lifecycle events — low-frequency, logged at info level
_LIFECYCLE_SESSION_EVENT_TYPES: frozenset = frozenset([
    "session_title_updated",
    "turn:started",
    "model:request",
    "model:complete",
    "turn:complete",
    "turn:error",
])


# ---------------------------------------------------------------------------
# Session event aggregate (for debug logging)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class _SessionEventAppendAggregate:
    event_count: int
    event_type: str
    first_event_id: str
    first_session_event_sequence_number: int
    last_event_id: str
    last_session_event_sequence_number: int
    payload_bytes: int
    payload_kinds: dict[str, int]


# Weak map: runtime -> aggregate map
_session_event_append_aggregates: dict = {}


def _get_aggregate_map(runtime: Any) -> dict:
    """Get or create the aggregate map for a runtime instance."""
    if id(runtime) not in _session_event_append_aggregates:
        _session_event_append_aggregates[id(runtime)] = {}
    return _session_event_append_aggregates[id(runtime)]


def _get_payload_kind(payload: Any) -> str:
    """Get the 'kind' discriminator from a payload object."""
    if payload and isinstance(payload, dict):
        kind = payload.get("kind")
        if isinstance(kind, str) and len(kind) > 0:
            return kind
    return "<missing>"


def _measure_json_bytes(value: Any) -> int:
    """Measure JSON byte size of a value."""
    try:
        return len(json.dumps(value).encode("utf-8"))
    except Exception:
        return 0


def _trace_context_to_log_context(trace_context: TraceContext) -> dict[str, Any]:
    """Convert trace context to log context dict."""
    ctx = {}
    if hasattr(trace_context, "trace_id"):
        ctx["traceId"] = str(trace_context.trace_id)
    if hasattr(trace_context, "session_id"):
        ctx["sessionId"] = str(trace_context.session_id)
    if hasattr(trace_context, "turn_id"):
        ctx["turnId"] = str(trace_context.turn_id)
    if hasattr(trace_context, "query_id"):
        ctx["queryId"] = str(trace_context.query_id)
    return ctx


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_event(
    runtime: Any,
    type: str,
    payload: Any,
    trace_context: TraceContext,
) -> SessionEvent:
    """Mirrors ``createEvent`` — creates a session event.

    Args:
        runtime: The AgentRuntimeInternal instance (used for sessionId)
        type: The event type string
        payload: The event payload
        trace_context: The trace context for the operation

    Returns:
        A SessionEvent with the given type, payload, and trace-derived fields.
    """
    return SessionEvent(
        id=f"evt_{uuid.uuid4()}",
        type=type,
        session_id=runtime.session_id,
        timestamp=None,  # Will be set by event store
        sequence_number=0,  # Will be assigned by event store
        turn_id=trace_context.turn_id if hasattr(trace_context, "turn_id") else None,
        trace_id=trace_context.trace_id if hasattr(trace_context, "trace_id") else None,
        payload=payload,
    )


async def append_event(
    runtime: Any,
    event: SessionEvent,
    trace_context: TraceContext,
) -> None:
    """Mirrors ``appendEvent`` — appends an event to the store and notifies sinks.

    This is the main event persistence flow:
    1. Append to event store (assigns sequence number)
    2. Persist durable session event (checkpoint, rewind, etc.)
    3. Record tool usage from event
    4. Notify event sinks
    5. Flush aggregates if needed

    Args:
        runtime: The AgentRuntimeInternal instance
        event: The event to append
        trace_context: The trace context
    """
    # Determine if this is a lifecycle event (logged at info level)
    should_log_lifecycle = event.type in _LIFECYCLE_SESSION_EVENT_TYPES
    started_at = 0  # Will be set below

    if should_log_lifecycle:
        started_at = 0  # PORT: would use Date.now() in JS

    logger = getattr(runtime, "logger", None)

    if should_log_lifecycle and logger:
        log_ctx = _trace_context_to_log_context(trace_context)
        logger.info(
            "Session event persistence started",
            **{**log_ctx, "event": "session.event.persistence.started", "module": "core.runtime",
               "sessionEventType": event.type, "status": "started"}
        )

    phase = "event_store.append"
    try:
        # Append to event store (assigns sequence number)
        stored_event = await runtime.event_store.append(event)

        # Persist durable session event (checkpoints, rewinds, etc.)
        phase = "session_event.persist_durable"
        await _persist_durable_session_event(runtime, stored_event, trace_context)

        # Record tool usage from event
        phase = "session_event.record_usage"
        await _record_tool_usage_from_event(runtime, stored_event, trace_context)

        # Notify event sinks
        phase = "session_event.notify_sinks"
        await notify_event_sinks(runtime, stored_event, trace_context)

        if should_log_lifecycle and logger:
            logger.info(
                "Session event persistence completed",
                **_trace_context_to_log_context(trace_context),
                duration_ms=0,  # PORT: Date.now() - startedAt
                event="session.event.persistence.completed",
                module="core.runtime",
                session_event_sequence_number=stored_event.sequence_number,
                session_event_type=stored_event.type,
                status="completed",
            )

        # Check aggregates
        if _record_session_event_append_aggregate(runtime, stored_event, trace_context):
            return

        _flush_session_event_append_aggregates(runtime, trace_context, "low_frequency_event")

        if logger:
            logger.debug(
                "Session event appended",
                **_trace_context_to_log_context(trace_context),
                event="event_store.appended",
                module="core.runtime",
                session_event_sequence_number=stored_event.sequence_number,
                session_event_type=stored_event.type,
            )

    except Exception as error:
        if should_log_lifecycle and logger:
            logger.warn(
                "Session event persistence failed",
                **_trace_context_to_log_context(trace_context),
                duration_ms=0,
                error_message=str(error),
                event="session.event.persistence.failed",
                module="core.runtime",
                phase=phase,
                session_event_type=event.type,
                status="failed",
            )
        raise


async def notify_event_sinks(
    runtime: Any,
    event: SessionEvent,
    trace_context: TraceContext,
) -> None:
    """Mirrors ``notifyEventSinks`` — notifies all event sinks of an event.

    Args:
        runtime: The AgentRuntimeInternal instance
        event: The event to notify about
        trace_context: The trace context
    """
    logger = getattr(runtime, "logger", None)

    for sink in runtime.event_sinks:
        try:
            await sink.on_session_event(event)
        except Exception as error:
            if logger:
                logger.warn(
                    "Session event sink failed",
                    **_trace_context_to_log_context(trace_context),
                    error_message=str(error),
                    event="session_event_sink.failed",
                    module="core.runtime",
                    session_event_type=event.type,
                    status="failed",
                )


def is_session_persisted(runtime: Any) -> bool:
    """Mirrors ``isSessionPersisted`` — returns whether session has been persisted.

    Checks if the session has already been persisted to the store.
    """
    return getattr(runtime, "session_persisted", False)


async def ensure_session_persisted(
    runtime: Any,
    input: str,
    trace_context: TraceContext,
) -> None:
    """Mirrors ``ensureSessionPersisted`` — creates session record if not yet persisted.

    Called when the first user input arrives or when an external activity requires
    a session record. Sets up session title, model selection, shell snapshot, etc.

    Args:
        runtime: The AgentRuntimeInternal instance
        input: The first input string
        trace_context: The trace context
    """
    # PORT: this is a stub; full implementation will be added
    # when we port the session shell environment and model selection helpers
    pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _persist_durable_session_event(
    runtime: Any,
    event: SessionEvent,
    trace_context: TraceContext,
) -> None:
    """Handle special event types that need durable persistence."""
    # PORT: This handles CheckpointCreated, RewindTriggered,
    # UserInputAutoResolutionUpdated, TurnSteerQueued/DeliveryChanged/Discarded,
    # and TargetCompletionVerification events.
    # Full implementation in the session persistence module.
    pass


async def _record_tool_usage_from_event(
    runtime: Any,
    event: SessionEvent,
    trace_context: TraceContext,
) -> None:
    """Record tool usage metrics from tool-related events."""
    # PORT: stub - full implementation in usage-observability module
    pass


def _record_session_event_append_aggregate(
    runtime: Any,
    event: SessionEvent,
    trace_context: TraceContext,
) -> bool:
    """Record an event in the aggregate map. Returns True if aggregate was flushed."""
    if event.type not in _SUMMARY_SESSION_EVENT_TYPES:
        return False

    aggregate_key = f"{trace_context.turn_id}:{event.type}" if hasattr(trace_context, "turn_id") else f"session:{event.type}"
    aggregate_map = _get_aggregate_map(runtime)
    payload_kind = _get_payload_kind(event.payload)

    existing = aggregate_map.get(aggregate_key)
    if existing:
        existing.event_count += 1
        existing.last_event_id = str(event.id)
        existing.last_session_event_sequence_number = event.sequence_number
        existing.payload_bytes += _measure_json_bytes(event.payload)
        existing.payload_kinds[payload_kind] = existing.payload_kinds.get(payload_kind, 0) + 1
        if existing.event_count >= SESSION_EVENT_APPEND_SUMMARY_FLUSH_COUNT:
            _flush_session_event_append_aggregate(runtime, aggregate_key, existing, trace_context, "count_threshold")
        return True

    aggregate_map[aggregate_key] = _SessionEventAppendAggregate(
        event_count=1,
        event_type=event.type,
        first_event_id=str(event.id),
        first_session_event_sequence_number=event.sequence_number,
        last_event_id=str(event.id),
        last_session_event_sequence_number=event.sequence_number,
        payload_bytes=_measure_json_bytes(event.payload),
        payload_kinds={payload_kind: 1},
    )
    return True


def _flush_session_event_append_aggregates(
    runtime: Any,
    trace_context: TraceContext,
    reason: str,
) -> None:
    """Flush all session event aggregates."""
    aggregate_map = _get_aggregate_map(runtime)
    if not aggregate_map or len(aggregate_map) == 0:
        return

    for aggregate_key, aggregate in list(aggregate_map.items()):
        _flush_session_event_append_aggregate(runtime, aggregate_key, aggregate, trace_context, reason)


def _flush_session_event_append_aggregate(
    runtime: Any,
    aggregate_key: str,
    aggregate: _SessionEventAppendAggregate,
    trace_context: TraceContext,
    reason: str,
) -> None:
    """Flush a single session event aggregate."""
    aggregate_map = _get_aggregate_map(runtime)
    aggregate_map.pop(aggregate_key, None)

    logger = getattr(runtime, "logger", None)
    if logger:
        logger.debug(
            "Session event append summary",
            **_trace_context_to_log_context(trace_context),
            event="event_store.appended.summary",
            event_count=aggregate.event_count,
            first_event_id=aggregate.first_event_id,
            first_session_event_sequence_number=aggregate.first_session_event_sequence_number,
            flush_reason=reason,
            last_event_id=aggregate.last_event_id,
            last_session_event_sequence_number=aggregate.last_session_event_sequence_number,
            module="core.runtime",
            payload_bytes=aggregate.payload_bytes,
            payload_kinds=aggregate.payload_kinds,
            session_event_type=aggregate.event_type,
        )