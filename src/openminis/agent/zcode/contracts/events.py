"""Session events — all event types for the agent loop.

Ported from: packages/contracts/src/events/session.events.ts
Original package: @zcode/contracts (events)

# PORT-STATUS: partial — only the base ``SessionEvent`` / ``SessionEventType``
# surface is ported (the subset the tool executor consumes). Event payload
# interfaces live in the same upstream file; add them here as they are needed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from .ids import EventId, QueryId, SessionId, TraceId, TurnId

__all__ = [
    "SessionEvent",
    "SessionEventType",
    "create_session_event",
]


@dataclass(slots=True)
class SessionEvent:
    """Mirrors ``interface SessionEvent``."""

    id: EventId
    session_id: SessionId
    type: "SessionEventType"
    timestamp: datetime
    trace_id: TraceId
    sequence_number: int
    payload: Any = None
    turn_id: Optional[TurnId] = None


class SessionEventType(str, Enum):
    """Mirrors the ``SessionEventType`` const object.

    # PORT: values kept verbatim (snake_case) so event types cross the wire
    # unchanged. Upstream ``as const`` + union-of-literal-types → str-Enum.
    """

    # Session
    SESSION_CREATED = "session_created"
    SESSION_RESUMED = "session_resumed"
    SESSION_FORKED = "session_forked"
    SESSION_COMPACTED = "session_compacted"
    SESSION_TITLE_UPDATED = "session_title_updated"
    SESSION_MODE_CHANGED = "session_mode_changed"
    SESSION_ENDED = "session_ended"

    # Turn steering / queue
    TURN_STARTED = "turn_started"
    TURN_INPUT_RECEIVED = "turn_input_received"
    TURN_STEER_QUEUED = "turn_steer_queued"
    # guide 未遇到可用 tool batch 即收口时，原 intent 原地改投普通 queue。
    TURN_STEER_DELIVERY_CHANGED = "turn_steer_delivery_changed"
    # sendQueuedNow 原子提升：reservation/promoting/rollback 均进入事件流，投影不本地猜测。
    TURN_STEER_DISPATCH_CHANGED = "turn_steer_dispatch_changed"
    TURN_STEER_DRAINED = "turn_steer_drained"
    TURN_STEER_REJECTED = "turn_steer_rejected"
    TURN_STEER_DISCARDED = "turn_steer_discarded"
    # session_input 与 user message/parts 已在同一事务 promotion；CommandInbox 可安全解除 pin。
    SESSION_INPUT_PROMOTED = "session_input_promoted"
    # v4 queue 重排：queue 项顺序变化（reducer 按 orderedPendingInputIds 重排 queue rows）。
    TURN_STEER_REORDERED = "turn_steer_reordered"
    # v4 输入控制：queue autoDrain 开关（held 派生 heldQueueInputRequiresChoice 的授权位）。
    QUEUE_AUTO_DRAIN_CHANGED = "queue_auto_drain_changed"
    # v4 输入控制：followup 路由模式（running 时 enqueue vs guide）。
    FOLLOWUP_MODE_CHANGED = "followup_mode_changed"

    # Turn / messages / model
    TURN_COMPLETE = "turn_complete"
    TURN_ERROR = "turn_error"
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    ASSISTANT_FEEDBACK_UPDATED = "assistant_feedback_updated"
    SYSTEM_MESSAGE = "system_message"
    MODEL_REQUEST = "model_request"
    MODEL_SELECTED = "model_selected"
    MODEL_STREAMING = "model_streaming"
    STREAMING_TOOL_LEDGER_UPDATED = "streaming_tool_ledger_updated"
    STREAM_RECOVERY_ANCHOR_CREATED = "stream_recovery_anchor_created"
    STREAM_RECOVERY_STARTED = "stream_recovery_started"
    STREAM_RECOVERY_ANCHOR_SELECTED = "stream_recovery_anchor_selected"
    STREAM_RECOVERY_TAIL_DISCARDED = "stream_recovery_tail_discarded"
    STREAM_RECOVERY_RETRY_STARTED = "stream_recovery_retry_started"
    STREAM_RECOVERY_BLOCKED = "stream_recovery_blocked"
    MODEL_NETWORK_STATUS = "model_network_status"
    MODEL_ANOMALY_WARNING = "model_anomaly_warning"
    NETWORK_REQUEST_STATUS = "network_request_status"
    MODEL_COMPLETE = "model_complete"
    MODEL_ERROR = "model_error"

    # Tools
    TOOL_CALL_SCHEDULED = "tool_call_scheduled"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_PROGRESS = "tool_call_progress"
    TOOL_CALL_RESULT = "tool_call_result"
    TOOL_CALL_ERROR = "tool_call_error"
    TOOL_BATCH_COMPLETE = "tool_batch_complete"

    # Background tasks
    BACKGROUND_TASK_STARTED = "background_task_started"
    BACKGROUND_TASK_UPDATED = "background_task_updated"
    BACKGROUND_TASK_COMPLETED = "background_task_completed"
    # workflow run 的实时进度：一条引擎 RunEvent 一条事件，追加到**父会话**（run 自己没有会话）。
    # v4 侧归约成 workflowRuns 状态键；v3 侧在 shouldExposeSessionEventToProtocol 剥离。
    # 命名刻意带 dynamic_：legacy `Workflow` 工具的 script run 事件（workflow_started /
    # workflow_completed，script-workflow-runtime.ts）是另一套日志，同名会真的混淆。
    DYNAMIC_WORKFLOW_RUN_PROGRESS = "dynamic_workflow_run_progress"

    # Permission
    PERMISSION_REQUESTED = "permission_requested"
    PERMISSION_RESOLVED = "permission_resolved"
    PERMISSION_DENIED = "permission_denied"
    USER_INPUT_AUTO_RESOLUTION_UPDATED = "user_input_auto_resolution_updated"

    # Workspace hooks
    WORKSPACE_HOOK_REVIEW_REQUESTED = "workspace_hook_review_requested"
    WORKSPACE_HOOK_REVIEW_SETTLED = "workspace_hook_review_settled"
    WORKSPACE_HOOK_REVIEW_SUPERSEDED = "workspace_hook_review_superseded"
    # 软门禁:准入状态变化时发射,pendingCount=0 时投影层清空 snapshot 字段。
    WORKSPACE_HOOK_ADMISSION_UPDATED = "workspace_hook_admission_updated"

    # Hook runs
    HOOK_RUN_STARTED = "hook_run_started"
    HOOK_RUN_PROGRESS = "hook_run_progress"
    HOOK_RUN_COMPLETED = "hook_run_completed"
    HOOK_RUN_FAILED = "hook_run_failed"
    HOOK_RUN_BLOCKED = "hook_run_blocked"

    # Compact / rewind / checkpoints / targets
    COMPACT_STARTED = "compact_started"
    COMPACT_COMPLETED = "compact_completed"
    COMPACT_FAILED = "compact_failed"
    COMPACT_BOUNDARY = "compact_boundary"
    MICROCOMPACT_BOUNDARY = "microcompact_boundary"
    REWIND_TRIGGERED = "rewind_triggered"
    CHECKPOINT_CREATED = "checkpoint_created"
    TARGET_CHANGED = "target_changed"
    TARGET_COMPLETION_VERIFICATION = "target_completion_verification"

    # Subagents
    SUBAGENT_SPAWNED = "subagent_spawned"
    SUBAGENT_MESSAGE = "subagent_message"
    SUBAGENT_STOPPED = "subagent_stopped"

    # Control
    INTERRUPT = "interrupt"
    CANCEL = "cancel"
    RESUME = "resume"
    ERROR = "error"


def create_session_event(
    *,
    session_id: SessionId,
    event_type: SessionEventType,
    trace_id: TraceId,
    sequence_number: int,
    payload: Any = None,
    turn_id: Optional[TurnId] = None,
    event_id: Optional[EventId] = None,
) -> SessionEvent:
    """Convenience factory (upstream callers build the literal inline)."""
    return SessionEvent(
        id=EventId(event_id) if event_id is not None else EventId(f"evt_{uuid.uuid4()}"),
        session_id=session_id,
        type=event_type,
        timestamp=datetime.now(timezone.utc),
        trace_id=trace_id,
        sequence_number=sequence_number,
        payload=payload,
        turn_id=turn_id,
    )
