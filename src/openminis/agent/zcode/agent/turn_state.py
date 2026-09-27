"""Turn State — turn state machine types.

Ported from: packages/core/src/agent/turn-state.ts
Original package: @zcode/core (agent)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from ..contracts.ids import SessionId, ToolCallId, TraceId, TurnId
from ..contracts.model import ModelMessageContent
from ..contracts.model import ModelToolCall as ToolCall

__all__ = [
    "TurnPhase",
    "TurnState",
    "ToolCall",
    "ModelRequestState",
    "ModelMessage",
    "TokenUsageState",
    "ToolCallState",
    "ToolCallStateStatus",
    "ToolResultState",
    "ToolScheduleState",
    "ToolScheduleItem",
    "PermissionRequestState",
    "PermissionResultState",
    "PermissionDecision",
    "TurnResultType",
    "TurnErrorState",
    "TurnAttachment",
    "create_turn_state",
    "is_terminal_phase",
    "is_waiting_phase",
    "can_transition_to",
]

# PORT: ``PendingTurnInput`` is declared in
# ``packages/contracts/src/interfaces/session.port.ts``. That contract is not part of
# the Python ``contracts`` package yet (``..contracts`` only ships ids/errors/model/
# tools), so the field is typed as an opaque mapping until the real contract lands.
PendingTurnInput = dict[str, Any]

# Note: ModelMessage is defined locally to avoid conflicts with contracts' ModelMessage
# which uses ToolCallPayload[] instead of ModelToolCall[]

# -----------------------------------------------
# Turn Phase
# -----------------------------------------------


class TurnPhase(str, Enum):
    """Mirrors the ``TurnPhase`` const object (``as const`` → ``enum.Enum``).

    # PORT: values keep the original snake_case strings so the transition table and
    # any wire payload stay byte-identical. The ``str`` mixin keeps
    # ``TurnPhase.STREAMING == "streaming"`` true for call sites that still compare
    # against the raw literal.
    """

    IDLE = "idle"
    PROCESSING_INPUT = "processing_input"
    AWAITING_MODEL_RESPONSE = "awaiting_model_response"
    STREAMING = "streaming"
    SCHEDULING_TOOLS = "scheduling_tools"
    EXECUTING_TOOLS = "executing_tools"
    AGGREGATING_RESULTS = "aggregating_results"
    AWAITING_PERMISSION = "awaiting_permission"
    COMPLETING = "completing"
    ERROR = "error"


# -----------------------------------------------
# Turn State
# -----------------------------------------------


@dataclass(slots=True)
class TurnState:
    """Mirrors ``interface TurnState``.

    # PORT: dataclasses require non-default fields first, so the optional fields the
    # TS interface interleaves (``attachments`` / ``modelRequest`` / ``finalResponse``
    # / ``error`` / ``completedAt``) move to the tail. Field names and defaults are
    # otherwise unchanged.
    """

    id: TurnId
    session_id: SessionId
    turn_number: int
    phase: TurnPhase
    trace_id: TraceId
    input: str
    streaming_content: str
    tool_calls: list[ToolCallState]
    tool_results: list[ToolResultState]
    scheduled_tools: ToolScheduleState
    pending_inputs: list[PendingTurnInput]
    accepts_pending_input: bool
    pending_permissions: list[PermissionRequestState]
    resolved_permissions: list[PermissionResultState]
    result_type: TurnResultType
    started_at: datetime
    attachments: Optional[list[TurnAttachment]] = None
    model_request: Optional[ModelRequestState] = None
    final_response: Optional[str] = None
    error: Optional[TurnErrorState] = None
    completed_at: Optional[datetime] = None


# -----------------------------------------------
# Sub-states
# -----------------------------------------------


@dataclass(slots=True)
class ModelRequestState:
    """Mirrors ``interface ModelRequestState``."""

    model: str
    messages: list[ModelMessage]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    stop_reason: Optional[str] = None
    usage: Optional[TokenUsageState] = None


# ModelMessage for turn state - uses ToolCall from contracts
@dataclass(slots=True)
class ModelMessage:
    """Mirrors ``interface ModelMessage``."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: ModelMessageContent
    tool_calls: Optional[list[ToolCall]] = None
    tool_call_id: Optional[str] = None


@dataclass(slots=True)
class TokenUsageState:
    """Mirrors ``interface TokenUsageState``."""

    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass(slots=True)
class ToolCallState:
    """Mirrors ``interface ToolCallState``."""

    id: ToolCallId
    name: str
    input: Any
    status: ToolCallStateStatus
    scheduled_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[ToolResultState] = None


# PORT: TS string union — kept as ``str`` so the literal values stay verbatim.
ToolCallStateStatus = str  # "scheduled" | "waiting_permission" | "permission_denied" | "running" | "completed" | "failed"


@dataclass(slots=True)
class ToolResultState:
    """Mirrors ``interface ToolResultState``."""

    success: bool
    content: ModelMessageContent
    error: Optional[TurnErrorState] = None


@dataclass(slots=True)
class ToolScheduleState:
    """Mirrors ``interface ToolScheduleState``."""

    items: list[ToolScheduleItem]
    parallel_groups: list[list[ToolCallId]]
    execution_order: list[ToolCallId]


@dataclass(slots=True)
class ToolScheduleItem:
    """Mirrors ``interface ToolScheduleItem``."""

    tool_call_id: ToolCallId
    dependencies: list[ToolCallId]
    can_run_parallel: bool


@dataclass(slots=True)
class PermissionRequestState:
    """Mirrors ``interface PermissionRequestState``."""

    tool_call_id: ToolCallId
    tool_name: str
    risk_level: str
    requested_at: datetime


@dataclass(slots=True)
class PermissionResultState:
    """Mirrors ``interface PermissionResultState``.

    # PORT: ``resolvedAt`` is required while ``reason`` / ``modifiedInput`` are
    # optional; dataclasses need required fields first, so ``resolved_at`` is moved
    # ahead of them. All three keep their original names.
    """

    tool_call_id: ToolCallId
    decision: PermissionDecision
    resolved_at: datetime
    reason: Optional[str] = None
    modified_input: Any = None


# PORT: TS string union — kept as ``str`` so the literal values stay verbatim.
PermissionDecision = str  # "allow" | "deny" | "escalate" | "modify"

# PORT: TS string union ``TurnResultType`` — kept as ``str``. Members, with the
# original inline comment preserved verbatim:
#   "success"
#   "cancelled": 用户主动中断（TurnCancelled）属于正常结束，复用 TurnComplete 上报而非 TurnError。
#   "error_max_turns"
#   "error_max_budget"
#   "error_during_execution"
#   "error_max_tool_calls"
TurnResultType = str


@dataclass(slots=True)
class TurnErrorState:
    """Mirrors ``interface TurnErrorState``."""

    type: str
    message: str
    recoverable: bool


@dataclass(slots=True)
class TurnAttachment:
    """Mirrors ``interface TurnAttachment``."""

    type: str  # "file" | "image" | "video" | "pdf" | "url"
    path: Optional[str] = None
    content: Optional[str] = None
    # clipboard-text 是 UI 长粘贴落盘生成的临时附件，模型请求中只保留路径引用。
    source_kind: Optional[str] = None  # "clipboard-text"
    # 展示元信息（协议边界保真透传，TurnStarted 事件/v4 投影展示用；
    # 缺省时由 basename/扩展名推断兜底）。不参与内容解析。
    filename: Optional[str] = None
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None


# -----------------------------------------------
# Turn State Factory
# -----------------------------------------------


def create_turn_state(
    id: TurnId,
    session_id: SessionId,
    turn_number: int,
    trace_id: TraceId,
    input: str,
    attachments: Optional[list[TurnAttachment]] = None,
) -> TurnState:
    """Mirrors ``createTurnState(...)``."""
    return TurnState(
        id=id,
        session_id=session_id,
        turn_number=turn_number,
        phase=TurnPhase.IDLE,
        trace_id=trace_id,
        input=input,
        attachments=attachments,
        streaming_content="",
        tool_calls=[],
        tool_results=[],
        scheduled_tools=ToolScheduleState(
            items=[],
            parallel_groups=[],
            execution_order=[],
        ),
        pending_inputs=[],
        accepts_pending_input=True,
        pending_permissions=[],
        resolved_permissions=[],
        result_type="success",
        started_at=datetime.now(timezone.utc),
    )


# -----------------------------------------------
# Phase Predicates
# -----------------------------------------------


def is_terminal_phase(phase: TurnPhase) -> bool:
    """Mirrors ``isTerminalPhase(phase)``."""
    return phase == TurnPhase.COMPLETING or phase == TurnPhase.ERROR


def is_waiting_phase(phase: TurnPhase) -> bool:
    """Mirrors ``isWaitingPhase(phase)``."""
    return (
        phase == TurnPhase.AWAITING_MODEL_RESPONSE
        or phase == TurnPhase.AWAITING_PERMISSION
        or phase == TurnPhase.EXECUTING_TOOLS
    )


def can_transition_to(current: TurnPhase, next: TurnPhase) -> bool:
    """Mirrors ``canTransitionTo(current, next)`` — the transition table verbatim."""
    valid_transitions: dict[TurnPhase, list[TurnPhase]] = {
        TurnPhase.IDLE: [TurnPhase.PROCESSING_INPUT],
        TurnPhase.PROCESSING_INPUT: [TurnPhase.AWAITING_MODEL_RESPONSE, TurnPhase.COMPLETING],
        TurnPhase.AWAITING_MODEL_RESPONSE: [
            TurnPhase.STREAMING,
            TurnPhase.COMPLETING,
            TurnPhase.ERROR,
        ],
        TurnPhase.STREAMING: [
            TurnPhase.SCHEDULING_TOOLS,
            TurnPhase.AGGREGATING_RESULTS,
            TurnPhase.COMPLETING,
            TurnPhase.ERROR,
        ],
        TurnPhase.SCHEDULING_TOOLS: [
            TurnPhase.EXECUTING_TOOLS,
            TurnPhase.AWAITING_PERMISSION,
            TurnPhase.ERROR,
        ],
        TurnPhase.EXECUTING_TOOLS: [
            TurnPhase.AGGREGATING_RESULTS,
            TurnPhase.AWAITING_PERMISSION,
            TurnPhase.ERROR,
        ],
        TurnPhase.AGGREGATING_RESULTS: [
            TurnPhase.AWAITING_MODEL_RESPONSE,
            TurnPhase.SCHEDULING_TOOLS,
            TurnPhase.COMPLETING,
            TurnPhase.ERROR,
        ],
        TurnPhase.AWAITING_PERMISSION: [TurnPhase.EXECUTING_TOOLS, TurnPhase.ERROR],
        TurnPhase.COMPLETING: [TurnPhase.IDLE],
        TurnPhase.ERROR: [TurnPhase.IDLE],
    }

    # PORT: TS ``validTransitions[current]?.includes(next) ?? false``.
    allowed = valid_transitions.get(current)
    return next in allowed if allowed is not None else False
