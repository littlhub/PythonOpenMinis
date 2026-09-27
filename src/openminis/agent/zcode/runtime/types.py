"""Runtime type definitions — the core data structures used across the runtime.

Ported from: packages/core/src/runtime/types.ts
Original package: @zcode/core (runtime)

This file is the primary surface for runtime-facing types consumed by the
programming-agent kernel. It mirrors the TypeScript ``types.ts`` source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Protocol, Union

# ---------------------------------------------------------------------------
# Contracts re-exports (brand types, enums)
# ---------------------------------------------------------------------------

# Brand types from contracts/ids.py
from ..contracts.ids import (
    EventId,
    MessageId,
    PartId,
    ProjectId,
    QueryId,
    SessionId,
    ToolCallId,
    TraceId,
    TurnId,
    WorkspaceId,
)

from ..contracts.tracing import TraceContext

# Model types from contracts/model.py
from ..contracts.model import (
    JsonSchema,
    Model,
    ModelInputMessage,
    ModelMessageContentBlock,
    ModelReasoningContentBlock,
    ModelStreamRecoveryStatus,
    ModelToolCall,
    ModelToolContract,
    ModelUsage,
    ModelUsageSummary,
)

# Error types from contracts/errors.py
from ..contracts.errors import CoreError, CoreErrorType

__all__ = [
    # Brand types
    "EventId",
    "MessageId",
    "PartId",
    "ProjectId",
    "QueryId",
    "SessionId",
    "ToolCallId",
    "TraceContext",
    "TraceId",
    "TurnId",
    "WorkspaceId",
    # Model types
    "JsonSchema",
    "Model",
    "ModelInputMessage",
    "ModelMessageContentBlock",
    "ModelReasoningContentBlock",
    "ModelStreamRecoveryStatus",
    "ModelToolCall",
    "ModelToolContract",
    "ModelUsage",
    "ModelUsageSummary",
    # Errors
    "CoreError",
    "CoreErrorType",
    # Session / context
    "CollaborationMode",
    "SessionProjection",
    "SessionEvent",
    "SessionEventSink",
    "SessionEventStorePort",
    "SessionStorePort",
    "SessionMailboxPort",
    "AgentRuntimeConfig",
    "AgentRuntimeDeps",
    "TurnResult",
    "ExecuteTurnOptions",
    "ExecuteTurnOptionsBase",
    "PromptAdmissionOptions",
    "PromptAdmissionReceipt",
    "ResumeSessionOptions",
    "ResumeSessionResult",
    # Active turn
    "ActiveTurnKind",
    "ActiveTurnSteeringState",
    "ActiveTurnStartReservation",
    "ActiveForegroundExecutionState",
    "StopActiveForegroundExecutionOptions",
    "StopActiveForegroundExecutionResult",
    "ForegroundPromotionLeaseState",
    "AcquireForegroundPromotionLeaseResult",
    "ActiveTurnInfo",
    # Tools
    "ExecuteToolsOptions",
    "ExecuteToolsResult",
    "PermissionDecisionResult",
    "ToolExecutionResult",
    # Context
    "ContextBuilder",
    "ContextBuildResult",
    "ContextSourcePort",
    "ContextSourceSnapshot",
    # Model
    "ModelExecutionContext",
    "RuntimeModelFactory",
    "RuntimeModelFactoryInput",
    "RunModelTextRequestOptions",
    "RuntimeModelStreamSnapshot",
    "StreamedToolExecutionResult",
    "RuntimeModelTextResult",
    "ProviderRuntimeHeadersPort",
    # Steering / queue
    "TurnSteerResult",
    "PendingTurnInput",
    "DrainedPendingInputDiagnostics",
    "ActiveTargetLoopTrigger",
    "ContinueActiveTargetLoopOptions",
    # Compact / rewind
    "CompactPhase",
    "CompactReason",
    "CompactTrigger",
    "CompactTimelineContext",
    "WorkspaceRewindResult",
    "WorkspaceForkResult",
    "ConversationRewindResult",
    "WorkspaceFileRewindPreview",
    "WorkspaceFileRewindApplyResult",
    "ParsedRewindCommand",
    # Checkpoint / workspace
    "WorkspaceCheckpointSummary",
    "CheckpointCreatedPayload",
    "RewindTargetEvaluation",
    "WorkspaceRewindRestoredFile",
    # Background tasks
    "BackgroundTaskNotificationSealReason",
    "SealBackgroundTaskNotificationsInput",
    "PendingModelChangeTimeline",
    # Memory
    "MemoryRuntimeConfig",
    # Fork / child session
    "StableConversationForkOptions",
    "StableConversationForkTarget",
    "StableConversationForkGoalBoundary",
    "StableConversationForkChildMetadata",
    "ConversationBeforeInputForkOptions",
    "SelectionSideChatCreateOptions",
    "WorkspaceForkResult",
    # File changes
    "RuntimeTurnFileChangeEntry",
    "RuntimeTurnFileChangeMap",
    # Context usage
    "ContextUsageTokenMethod",
    "ContextUsageConfidence",
    "ContextUsageMetric",
    "ContextUsageCategory",
    "ContextUsageToolDetail",
    "ContextUsageSkillDetail",
    "ContextUsageMessageRoleBreakdown",
    "ProviderContextUsageSnapshot",
    # Constants
    "INLINE_TEXT_ATTACHMENT_MAX_BYTES",
    "INLINE_MEDIA_ATTACHMENT_MAX_BYTES",
    "MAX_IMAGE_ATTACHMENT_DIMENSION",
    "ResolvedTurnAttachment",
    "PreparedImageData",
    # Enums
    "CollaborationMode",
]

# ---------------------------------------------------------------------------
# Constants (from original TS)
# ---------------------------------------------------------------------------

INLINE_TEXT_ATTACHMENT_MAX_BYTES = 64 * 1024
INLINE_MEDIA_ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024
MAX_IMAGE_ATTACHMENT_DIMENSION = 2000

# ---------------------------------------------------------------------------
# Collaboration / session modes
# ---------------------------------------------------------------------------

CollaborationMode = str  # "build" | "plan" | "auto" | "yolo"


# ---------------------------------------------------------------------------
# Session event types
# ---------------------------------------------------------------------------

class SessionEventType:
    """Ported from the TS const object — mirrors every SessionEventType value."""
    # Lifecycle
    SESSION_CREATED = "session:created"
    SESSION_UPDATED = "session:updated"
    SESSION_TITLE_UPDATED = "session:title_updated"
    SESSION_MODE_CHANGED = "session:mode_changed"
    # Turn lifecycle
    TURN_STARTED = "turn:started"
    TURN_COMPLETE = "turn:complete"
    TURN_ERROR = "turn:error"
    # Model events
    MODEL_REQUEST = "model:request"
    MODEL_STREAMING = "model:streaming"
    MODEL_NETWORK_STATUS = "model:network_status"
    MODEL_COMPLETE = "model:complete"
    # Tool events
    TOOL_CALL_SCHEDULED = "tool:call_scheduled"
    TOOL_CALL_PROGRESS = "tool:call_progress"
    TOOL_BATCH_COMPLETE = "tool:batch_complete"
    # Permission events
    PERMISSION_REQUESTED = "permission:requested"
    PERMISSION_RESOLVED = "permission:resolved"
    # Checkpoint / rewind
    CHECKPOINT_CREATED = "checkpoint:created"
    REWIND_TRIGGERED = "rewind:triggered"
    # Steering
    TURN_STEER_QUEUED = "turn:steer_queued"
    TURN_STEER_DELIVERY_CHANGED = "turn:steer_delivery_changed"
    TURN_STEER_DISCARDED = "turn:steer_discarded"
    # Target / goal
    TARGET_CHANGED = "target:changed"
    TARGET_COMPLETION_VERIFICATION = "target:completion_verification"
    # Compact
    COMPACT_TIMELINE = "compact:timeline"
    # Background tasks
    BACKGROUND_TASK_STARTED = "background:task_started"
    BACKGROUND_TASK_COMPLETED = "background:task_completed"
    BACKGROUND_TASK_CANCELLED = "background:task_cancelled"
    # User input
    USER_INPUT_AUTO_RESOLUTION_UPDATED = "user_input:auto_resolution_updated"


# ---------------------------------------------------------------------------
# Core interfaces (dataclass-based for Python idiom)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SessionEvent:
    """Mirrors ``SessionEvent``."""
    id: str
    type: str
    session_id: SessionId
    timestamp: Any  # Date
    sequence_number: int
    turn_id: Optional[TurnId] = None
    trace_id: Optional[TraceId] = None
    payload: Any = None


@dataclass(slots=True)
class SessionProjection:
    """Mirrors ``SessionProjection``."""
    session_id: SessionId
    turn_number: int
    latest_message_id: Optional[MessageId] = None
    pending_steer_inputs: list[Any] = field(default_factory=list)
    # PORT: remaining fields added as needed


@dataclass(slots=True)
class TurnResult:
    """Mirrors ``TurnResult``."""
    response: str
    turn_id: TurnId
    trace_id: TraceId
    usage: Optional[ModelUsageSummary] = None
    events: list[SessionEvent] = field(default_factory=list)
    projection: Optional[SessionProjection] = None


@dataclass(slots=True)
class ExecuteToolsOptions:
    """Mirrors ``ExecuteToolsOptions``."""
    automation_turn: bool = False
    off_peak_turn: bool = False
    signal: Any = None  # AbortSignal
    trace_context: Optional[TraceContext] = None
    subagent_model_override: Any = None
    model: Optional[Model] = None
    on_batch_start: Optional[Callable[[list[str]], Awaitable[None]]] = None


@dataclass(slots=True)
class ExecuteToolsResult:
    """Mirrors ``ExecuteToolsResult``."""
    results: list[Any]  # ToolExecutionResult[]
    events: list[SessionEvent] = field(default_factory=list)


@dataclass(slots=True)
class PermissionDecisionResult:
    """Mirrors ``PermissionDecisionResult``."""
    allowed: bool
    reason: Optional[str] = None
    modified_input: Any = None
    permission_updates: Any = None  # PermissionUpdate[]


# ---------------------------------------------------------------------------
# Active turn types
# ---------------------------------------------------------------------------

ActiveTurnKind = str  # "regular" | "compact" | "rewind"


@dataclass(slots=True)
class ActiveTurnSteeringState:
    """Mirrors ``ActiveTurnSteeringState``."""
    kind: ActiveTurnKind
    goal_state_change_reminder_deferral_open: bool
    pending_inputs: list[Any]  # PendingTurnInput[]
    steerable: bool
    trace_context: TraceContext
    turn_id: TurnId
    input_id: Optional[str] = None


@dataclass(slots=True)
class ActiveTurnStartReservation:
    """Mirrors ``ActiveTurnStartReservation``."""
    kind: ActiveTurnKind
    trace_context: TraceContext
    turn_id: TurnId


@dataclass(slots=True)
class ActiveForegroundExecutionState:
    """Mirrors ``ActiveForegroundExecutionState``."""
    controller: Any  # AbortController (not exposed in Python)
    dispose_parent_abort: Callable[[], None]
    foreground_execution_id: str
    preserve_queue_auto_drain_on_cancel: bool


@dataclass(slots=True)
class StopActiveForegroundExecutionOptions:
    """Mirrors ``StopActiveForegroundExecutionOptions``."""
    expected_foreground_execution_id: Optional[str] = None
    preserve_queue_auto_drain_on_cancel: bool = False
    reason: Optional[str] = None


@dataclass(slots=True)
class StopActiveForegroundExecutionResult:
    """Mirrors ``StopActiveForegroundExecutionResult`` (discriminated union)."""
    kind: str  # "stopped" | "idle" | "mismatch"
    foreground_execution_id: Optional[str] = None


@dataclass(slots=True)
class ForegroundPromotionLeaseState:
    """Mirrors ``ForegroundPromotionLeaseState``."""
    lease_id: str
    promoted_input_id: str


@dataclass(slots=True)
class AcquireForegroundPromotionLeaseResult:
    """Mirrors ``AcquireForegroundPromotionLeaseResult`` (discriminated union)."""
    kind: str  # "acquired" | "busy" | "conflict"
    lease_id: Optional[str] = None


@dataclass(slots=True)
class ActiveTurnInfo:
    """Mirrors ``ActiveTurnInfo``."""
    kind: ActiveTurnKind
    queue_length: int
    steerable: bool
    turn_id: TurnId
    input_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Context usage
# ---------------------------------------------------------------------------

ContextUsageTokenMethod = str  # "estimated" | "provider_count" | "proportional_estimate"
ContextUsageConfidence = str  # "high" | "medium" | "low"


@dataclass(slots=True)
class ContextUsageMetric:
    chars: int
    confidence: ContextUsageConfidence
    token_method: ContextUsageTokenMethod
    tokenizer: str
    tokens: int


@dataclass(slots=True)
class ContextUsageCategory(ContextUsageMetric):
    name: str
    percent_tokens: float
    source: str  # "system_prompt" | "meta_user_context" | "skills" | "tool_prompt" | ...


@dataclass(slots=True)
class ContextUsageToolDetail(ContextUsageMetric):
    name: str
    read_only: Optional[bool] = None
    server_name: Optional[str] = None
    side_effect_scope: Optional[str] = None
    source: str = "system_tool"  # "system_tool" | "mcp_tool"


@dataclass(slots=True)
class ContextUsageSkillDetail(ContextUsageMetric):
    name: str
    path: str
    scope: str
    source: str


@dataclass(slots=True)
class ContextUsageMessageRoleBreakdown(ContextUsageMetric):
    count: int
    role: str


@dataclass(slots=True)
class ProviderContextUsageSnapshot:
    cache_read_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    context_usage_tokens: Optional[int] = None
    input_tokens: int = 0
    message_count: int = 0
    model: Optional[dict[str, str]] = None  # {providerId, modelId}
    output_tokens: Optional[int] = None
    recorded_at: int = 0
    trace_id: TraceId = TraceId("")
    turn_id: Optional[TurnId] = None


# ---------------------------------------------------------------------------
# Model execution
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ModelExecutionContext:
    """Mirrors ``ModelExecutionContext``."""
    memory_extraction: Optional[str] = None  # "skip"
    selection_scope: str = "execution"
    request_dependencies: Any = None
    subagents: Optional[dict[str, str]] = None


@dataclass(slots=True)
class RuntimeModelFactoryInput:
    """Mirrors ``RuntimeModelFactoryInput``."""
    selection: Any  # ModelSelection
    request_dependencies: Any = None


RuntimeModelFactory = Callable[[RuntimeModelFactoryInput], Model]


@dataclass(slots=True)
class RunModelTextRequestOptions:
    """Mirrors ``RunModelTextRequestOptions``."""
    abort_signal: Any = None
    assistant_message_id: Optional[MessageId] = None
    events: list[SessionEvent] = field(default_factory=list)
    max_output_tokens: Optional[int] = None
    latest_real_user_message_index: Optional[int] = None
    messages: list[ModelInputMessage] = field(default_factory=list)
    source_entries: Optional[list[Any]] = None
    model: Optional[Model] = None
    on_stream_snapshot: Optional[Callable[[Any], None]] = None
    on_stream_reasoning_delta: Optional[Callable[[str], None]] = None
    on_stream_text_delta: Optional[Callable[[str], None]] = None
    on_stream_tool_call: Optional[Callable[[ModelToolCall], None]] = None
    on_model_network_status: Optional[Callable[[Any], None]] = None
    stream_recovery: Optional[ModelStreamRecoveryStatus] = None
    tools: list[ModelToolContract] = field(default_factory=list)
    trace_context: Optional[TraceContext] = None


@dataclass(slots=True)
class RuntimeModelStreamSnapshot:
    """Mirrors ``RuntimeModelStreamSnapshot``."""
    reasoning: list[ModelReasoningContentBlock] = field(default_factory=list)
    text: str = ""


@dataclass(slots=True)
class StreamedToolExecutionResult:
    """Mirrors ``StreamedToolExecutionResult``."""
    input: dict[str, Any]
    ledger_recorded: Optional[bool] = None
    part_id: Optional[PartId] = None
    result: Any = None  # ToolExecutionResult
    tool_call_id: Optional[ToolCallId] = None


@dataclass(slots=True)
class RuntimeModelTextResult:
    """Mirrors ``RuntimeModelTextResult``."""
    finish_reason: str
    text: str
    usage: ModelUsage
    context_usage_breakdown: Optional[list[Any]] = None
    provider_metadata: Optional[dict[str, Any]] = None
    reasoning: Optional[list[ModelReasoningContentBlock]] = None
    tool_calls: Optional[list[ModelToolCall]] = None


# ---------------------------------------------------------------------------
# Provider headers port
# ---------------------------------------------------------------------------

class ProviderRuntimeHeadersPort:
    """Mirrors ``ProviderRuntimeHeadersPort``."""

    def should_refresh_before_model_request(self, input: dict[str, Any]) -> bool:
        return False

    async def refresh_before_model_request(self, input: dict[str, Any]) -> dict[str, Any]:
        return {"headers_applied": False}


# ---------------------------------------------------------------------------
# Compact types
# ---------------------------------------------------------------------------

CompactPhase = str  # "attempt" | "retry_wait" | "user_confirmation" | "pending"
CompactReason = str  # "manual" | "auto" | "turn_limit" | "context_exceeded"
CompactTrigger = str  # "manual" | "auto" | "turn_count" | "token_count"


@dataclass(slots=True)
class CompactTimelineContext:
    """Mirrors ``CompactTimelineContext``."""
    operation_id: str
    message_id: MessageId
    part_id: PartId
    trigger: CompactTrigger
    phase: CompactPhase
    compact_reason: CompactReason
    source_command_id: Optional[str] = None
    started_at: int = 0
    pre_compact_token_count: Optional[int] = None


# ---------------------------------------------------------------------------
# Rewind types
# ---------------------------------------------------------------------------

RewindScope = str  # "workspace" | "conversation" | "full"
RewindStrategy = str  # "checkpoint" | "message" | "cascade"


@dataclass(slots=True)
class WorkspaceRewindRestoredFile:
    """Mirrors ``WorkspaceRewindRestoredFile``."""
    action: str  # "delete" | "restore"
    bytes_written: Optional[int] = None
    path: str = ""


@dataclass(slots=True)
class WorkspaceRewindResult:
    """Mirrors ``WorkspaceRewindResult``."""
    checkpoint: Any = None  # CheckpointCreatedPayload
    evaluation: Any = None  # RewindTargetEvaluation
    restored_files: list[WorkspaceRewindRestoredFile] = field(default_factory=list)
    response: str = ""
    rewind_id: str = ""
    strategy: RewindStrategy = ""


@dataclass(slots=True)
class CheckpointCreatedPayload:
    """Mirrors ``CheckpointCreatedPayload``."""
    checkpoint_id: str
    message_id: MessageId
    target_message_id: MessageId
    tool_message_id: Optional[MessageId] = None
    scope: RewindScope = "workspace"
    snapshot_ref: str = ""
    diff_ref: Optional[str] = None
    file_count: Optional[int] = None


@dataclass(slots=True)
class RewindTargetEvaluation:
    """Mirrors ``RewindTargetEvaluation``."""
    checkpoint_id: Optional[str] = None
    message_id: Optional[MessageId] = None
    target_message_id: Optional[MessageId] = None
    tool_names: list[str] = field(default_factory=list)


@dataclass(slots=True)
class WorkspaceFileRewindSafeFile:
    """Mirrors ``WorkspaceFileRewindSafeFile``."""
    action: str  # "restore" | "delete"
    operation_count: int
    path: str
    tool_names: list[str]


@dataclass(slots=True)
class WorkspaceFileRewindUnsafeFile:
    """Mirrors ``WorkspaceFileRewindUnsafeFile``."""
    current_hash: Optional[str] = None
    expected_hash: Optional[str] = None
    message: Optional[str] = None
    operation_count: Optional[int] = None
    path: Optional[str] = None
    reason: Optional[str] = None  # WorkspaceFileRewindUnsafeReason
    tool_names: Optional[list[str]] = None


@dataclass(slots=True)
class WorkspaceFileRewindIgnoredFile:
    """Mirrors ``WorkspaceFileRewindIgnoredFile``."""
    operation_count: int
    path: str
    reason: str = "bash_ignored"
    tool_names: Optional[list[str]] = None


@dataclass(slots=True)
class WorkspaceFileRewindPreview:
    """Mirrors ``WorkspaceFileRewindPreview``."""
    can_apply: bool
    ignored_files: list[WorkspaceFileRewindIgnoredFile] = field(default_factory=list)
    safe_files: list[WorkspaceFileRewindSafeFile] = field(default_factory=list)
    unsafe_files: list[WorkspaceFileRewindUnsafeFile] = field(default_factory=list)


@dataclass(slots=True)
class WorkspaceFileRewindApplyResult:
    """Mirrors ``WorkspaceFileRewindApplyResult``."""
    applied: bool
    preview: WorkspaceFileRewindPreview
    response: str = ""


@dataclass(slots=True)
class ConversationRewindResult:
    """Mirrors ``ConversationRewindResult``."""
    branch_generation: Optional[int] = None
    evaluation: Any = None
    kept_message_count: int = 0
    response: str = ""
    rewind_id: str = ""
    strategy: RewindStrategy = ""
    target_message_id: Optional[MessageId] = None


@dataclass(slots=True)
class WorkspaceForkResult:
    """Mirrors ``WorkspaceForkResult``."""
    checkpoint: Any = None
    copied_message_count: int = 0
    forked_session_id: Optional[SessionId] = None
    parent_session_id: Optional[SessionId] = None
    target_message_id: Optional[MessageId] = None
    target_checkpoint_id: Optional[str] = None
    restored_files: list[WorkspaceRewindRestoredFile] = field(default_factory=list)
    response: str = ""


@dataclass(slots=True)
class WorkspaceCheckpointSummary:
    """Mirrors ``WorkspaceCheckpointSummary``."""
    checkpoint_id: str
    compact_boundary_id: Optional[str] = None
    covered_by_compact: Optional[bool] = None
    created_at: Optional[Any] = None  # Date
    diff_ref: Optional[str] = None
    file_count: Optional[int] = None
    message_id: Optional[MessageId] = None
    target_message_id: Optional[MessageId] = None
    tool_message_id: Optional[MessageId] = None
    preview: Optional[str] = None
    scope: RewindScope = "workspace"
    snapshot_ref: str = ""


@dataclass(slots=True)
class RuntimeTurnFileChangeEntry:
    """Mirrors ``RuntimeTurnFileChangeEntry``."""
    after_content: Optional[str] = None
    before_content: Optional[str] = None  # null in TS
    fallback_additions: int = 0
    fallback_deletions: int = 0
    path: Optional[str] = None
    tool_names: set[str] = field(default_factory=set)
    write_count: int = 0


RuntimeTurnFileChangeMap = dict[str, RuntimeTurnFileChangeEntry]


# ---------------------------------------------------------------------------
# Parsed rewind commands
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ParsedRewindCommand:
    """Mirrors ``ParsedRewindCommand`` (discriminated union)."""
    action: str  # "apply" | "fork" | "cascade-message" | "message" | "status"
    target_checkpoint_id: Optional[str] = None
    scope: Optional[RewindScope] = None
    target_message_id: Optional[MessageId] = None


# ---------------------------------------------------------------------------
# Session store / event store ports (Protocols for Python DI)
# ---------------------------------------------------------------------------

class SessionEventStorePort(Protocol):
    """Mirrors ``SessionEventStorePort``."""

    async def append(self, event: SessionEvent) -> SessionEvent:
        ...

    async def events(self, query: dict[str, Any]) -> list[SessionEvent]:
        ...


@dataclass(slots=True)
class SessionGoal:
    """A session-level goal / objective.

    PORT: mirrors ``SessionGoal``. Carries the user's stated goal for the session
    and its completion status.
    """

    id: Optional[str] = None
    description: str = ""
    status: str = "open"  # "open" | "completed" | "abandoned"


@dataclass(slots=True)
class TurnInputIntentMetadata:
    """Metadata describing the intent of a turn input.

    PORT: mirrors ``TurnInputIntentMetadata``. Captures whether the input is a
    fresh submission, a steer, a plan-mode exit, etc.
    """

    intent: str = "submit"  # "submit" | "steer" | "planExit" | "resume"
    source: Optional[str] = None



class SessionStorePort(Protocol):
    """Mirrors ``SessionStorePort``."""

    async def create_session(self, input: dict[str, Any]) -> None:
        ...

    async def save_message(self, input: dict[str, Any], copy_from: Any = None) -> None:
        ...

    async def save_part(self, input: dict[str, Any], copy_from: Any = None) -> None:
        ...

    async def save_session_entry(self, input: dict[str, Any]) -> None:
        ...

    async def messages(self, query: dict[str, Any]) -> list[Any]:
        ...

    async def update_session_inputs(self, input: dict[str, Any]) -> None:
        ...

    async def settle_session_input(self, input: dict[str, Any]) -> None:
        ...

    async def save_session_input(self, input: dict[str, Any]) -> None:
        ...


class SessionEventSink(Protocol):
    """Mirrors ``SessionEventSink``."""

    async def on_session_event(self, event: SessionEvent) -> None:
        ...


# ---------------------------------------------------------------------------
# Pending turn input / steering
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PendingTurnInput:
    """Mirrors ``PendingTurnInput``."""
    input_id: str
    input: str
    added_at: int
    source: str  # "steer" | "queue"
    delivery: str = "queue"
    intent: Optional[Any] = None


@dataclass(slots=True)
class TurnSteerInput:
    """Mirrors ``TurnSteerInput``."""
    text: str
    input_id: Optional[str] = None
    intent: Optional[Any] = None


@dataclass(slots=True)
class TurnSteerResult:
    """Mirrors ``TurnSteerResult`` (discriminated union)."""
    kind: str  # "steer_queued" | "steer_started" | "steer_rejected"
    input_id: Optional[str] = None
    requeued: bool = False


@dataclass(slots=True)
class DrainedPendingInputDiagnostics:
    """Mirrors ``DrainedPendingInputDiagnostics``."""
    injected_message_ids: list[MessageId] = field(default_factory=list)
    intent: Optional[Any] = None
    latest_message_id: Optional[MessageId] = None
    pending_input_ids: list[str] = field(default_factory=list)
    query_ids: Optional[list[QueryId]] = None
    runtime_entries: list[Any] = field(default_factory=list)
    tool_disallowlist: Optional[list[str]] = None


ActiveTargetLoopTrigger = str  # "manual" | "user-prompt" | "task-notification"


@dataclass(slots=True)
class ContinueActiveTargetLoopOptions:
    """Mirrors ``ContinueActiveTargetLoopOptions``."""
    abort_signal: Any = None
    input_id: Optional[str] = None
    intent: Optional[Any] = None
    trace_context: Optional[TraceContext] = None
    trigger: Optional[ActiveTargetLoopTrigger] = None
    verify_before_first_continue: bool = False


# ---------------------------------------------------------------------------
# Background task types
# ---------------------------------------------------------------------------

BackgroundTaskNotificationSealReason = str  # "subagent_terminal" | "subagent_cancelled"


@dataclass(slots=True)
class SealBackgroundTaskNotificationsInput:
    """Mirrors ``SealBackgroundTaskNotificationsInput``."""
    reason: BackgroundTaskNotificationSealReason
    trace_context: Optional[TraceContext] = None


@dataclass(slots=True)
class PendingModelChangeTimeline:
    """Mirrors ``PendingModelChangeTimeline``."""
    created_at: int
    from_model: Optional[Any] = None
    from_model_label: Optional[str] = None
    request_id: Optional[str] = None
    to_model: Optional[Any] = None  # ModelSelection
    to_model_label: Optional[str] = None


# ---------------------------------------------------------------------------
# Fork / child session types
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class StableConversationForkOptions:
    """Mirrors ``StableConversationForkOptions``."""
    forked_session_id: Optional[SessionId] = None
    model_selection: Optional[Any] = None
    goal_boundary: Optional[Any] = None  # StableConversationForkGoalBoundary
    source_command_id: Optional[str] = None
    revision_at_decision: Optional[int] = None
    target: Optional[Any] = None  # StableConversationForkTarget
    trace_context: Optional[TraceContext] = None


@dataclass(slots=True)
class ConversationBeforeInputForkOptions:
    """Mirrors ``ConversationBeforeInputForkOptions``."""
    model_selection: Optional[Any] = None
    forked_session_id: Optional[SessionId] = None
    goal_boundary: Optional[Any] = None
    source_command_id: Optional[str] = None
    target_message_id: Optional[MessageId] = None
    target_product_turn_id: Optional[str] = None
    target_transcript_turn_id: Optional[str] = None
    initial_input: Optional[Any] = None
    command_fact: Optional[Any] = None
    trace_context: Optional[TraceContext] = None


@dataclass(slots=True)
class SelectionSideChatCreateOptions:
    """Mirrors ``SelectionSideChatCreateOptions``."""
    model_selection: Optional[Any] = None
    source_command_id: Optional[str] = None
    revision_at_decision: Optional[int] = None
    trace_context: Optional[TraceContext] = None


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ResolvedTurnAttachment:
    """Mirrors ``ResolvedTurnAttachment``."""
    content_block: ModelMessageContentBlock
    filename: Optional[str] = None
    metadata: Any = None  # AttachmentStorageMetadata
    mime: str = ""
    source: Optional[Any] = None
    url: str = ""


@dataclass(slots=True)
class PreparedImageData:
    """Mirrors ``PreparedImageData``."""
    data_url: str
    media_type: str
    metadata: Optional[Any] = None


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

class ContextBuilder(Protocol):
    """Mirrors ``ContextBuilder``."""

    def build(self, input: dict[str, Any]) -> Awaitable[Any]:
        ...


@dataclass(slots=True)
class ContextBuildResult:
    """Mirrors ``ContextBuildResult``."""
    content: str
    system_prompt: Optional[str] = None
    context_messages: list[Any] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Memory config
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class MemoryRuntimeConfig:
    """Mirrors ``MemoryRuntimeConfig``."""
    cli_storage_root: Optional[str] = None
    enabled: bool = True
    extraction_enabled: bool = True
    storage_root: Optional[str] = None
    use: bool = True
    workspace_identity: Optional[str] = None


# ---------------------------------------------------------------------------
# Main turn cache
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class MainTurnCacheHitAggregate:
    """Mirrors ``MainTurnCacheHitAggregate``."""
    request_count: int = 0
    total_input_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_write_tokens: int = 0


# ---------------------------------------------------------------------------
# Resume types
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ResumeSessionOptions:
    """Mirrors ``ResumeSessionOptions``."""
    abort_signal: Any = None
    trace_context: Optional[TraceContext] = None
    persisted_messages: Optional[list[Any]] = None
    mode_override: Optional[CollaborationMode] = None


@dataclass(slots=True)
class ResumeSessionResult:
    """Mirrors ``ResumeSessionResult``."""
    directory: str
    model_selection: Optional[Any] = None
    persisted_messages_reload_required: Optional[bool] = None
    read_file_state_restored_count: int = 0
    read_file_state_skipped_range_read_count: int = 0
    read_file_state_skipped_unreadable_edit_count: int = 0
    trace_id: Optional[TraceId] = None
    session_id: Optional[SessionId] = None
    workspace_id: Optional[WorkspaceId] = None


# ---------------------------------------------------------------------------
# Tool execution result (re-export from tool module for now)
# ---------------------------------------------------------------------------

ToolExecutionResult = Any  # Will be imported from tool module


# ---------------------------------------------------------------------------
# Context source
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ContextSourceSnapshot:
    """Mirrors ``ContextSourceSnapshot``."""
    env_info: Optional[dict[str, Any]] = None
    model_selection: Optional[Any] = None
    project_context: Optional[dict[str, Any]] = None
    session_id: Optional[SessionId] = None
    workspace_root: Optional[str] = None


class ContextSourcePort(Protocol):
    """Mirrors ``ContextSourcePort``."""

    async def resolve(self, input: dict[str, Any]) -> dict[str, Any]:
        ...