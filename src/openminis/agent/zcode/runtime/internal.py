"""AgentRuntimeInternal interface — the internal runtime state contract.

Ported from: packages/core/src/runtime/internal.ts
Original package: @zcode/core (runtime)

The internal runtime extends the core, turn, and hook method interfaces and
adds all the runtime-specific state fields. In Python this is a Protocol
that concrete implementations must satisfy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Protocol

from .deps import (
    EventId,
    MessageId,
    ModelSelection,
    PartId,
    QueryId,
    SessionEvent,
    SessionEventSink,
    SessionId,
    ToolCallId,
    TraceContext,
    TurnId,
)

__all__ = [
    "AgentRuntimeInternal",
]

# ---------------------------------------------------------------------------
# Forward declarations (filled in by methods modules)
# ---------------------------------------------------------------------------

# These will be filled in as we port the methods
ActiveTurnSteeringState = Any
ActiveTurnStartReservation = Any
ActiveForegroundExecutionState = Any
ForegroundPromotionLeaseState = Any
MainTurnCacheHitAggregate = Any
PendingModelChangeTimeline = Any
ProviderRuntimeHeadersPort = Any
RuntimeTurnFileChangeMap = Any
BackgroundTaskNotificationSealReason = str

# Tool registry / executor (from tool module)
ToolExecutor = Any
ToolRegistry = Any

# Hook runner
HookRunner = Any

# Context builder
ContextBuilder = Any
ContextBuildResult = Any
ContextSourceSnapshot = Any

# Message history
MessageHistory = Any

# Read file state
ReadFileStateMap = Any

# MCP connection
McpConnectionSnapshot = Any

# Skill load outcome
SkillLoadOutcome = Any

# Runtime task registry
RuntimeTaskRegistry = Any

# Tool artifact store
ToolArtifactStorePort = Any

# Session store / mailbox
SessionStorePort = Any
SessionMailboxPort = Any
SessionEventStorePort = Any

# Execution ports
ExecutionPort = Any
FileSystemPort = Any
ImageProcessorPort = Any
PdfDocumentPort = Any
SkillPort = Any
McpPort = Any
SubagentPort = Any
DynamicWorkflowRunPort = Any
ModelCatalogPort = Any
AutomationPort = Any
OffPeakPort = Any
ContextSourcePort = Any
BrowserControlPort = Any

# Agent telemetry
RuntimeTelemetryFacade = Any

# Event reducer
EventReducer = Any

# Permission service / broker
PermissionService = Any
PermissionBrokerPort = Any

# Tool scheduler
ToolScheduler = Any

# Workspace hook admission
WorkspaceHookRuntimeAdmissionPort = Any
WorkspaceHookBundleSnapshot = Any

# Model factory
RuntimeModelFactory = Any

# Model request admission
ModelRequestAdmission = Any

# Artifact store
ToolArtifactStore = Any

# Project memory extraction scheduler
ProjectMemoryExtractionScheduler = Any

# Command queue
RuntimeCommandQueue = Any

# ---------------------------------------------------------------------------
# AgentRuntimeInternal Protocol
# ---------------------------------------------------------------------------


class AgentRuntimeInternal(Protocol):
    """The internal runtime interface that all runtime method implementations
    operate against. Mirrors the TypeScript ``AgentRuntimeInternal`` interface
    which extends ``AgentRuntimeCoreMethods``, ``AgentRuntimeTurnMethods``,
    and ``AgentRuntimeHookMethods``.
    """

    # ---- Identity ----
    session_id: SessionId
    turn_number: int

    # ---- Configuration ----
    config: Any  # AgentRuntimeConfig
    permission_service: PermissionService
    permission_broker: PermissionBrokerPort
    tool_scheduler: ToolScheduler
    event_reducer: EventReducer
    event_store: SessionEventStorePort

    # ---- Tracing ----
    root_trace_context: TraceContext

    # ---- App info ----
    app_version: str

    # ---- Logging ----
    logger: Optional[Any]  # Logger

    # ---- Event sinks ----
    event_sinks: set  # Set[SessionEventSink]

    # ---- Time ----
    now: Callable[[], Any]  # () => Date

    # ---- Workspace ----
    is_remote_workspace: Callable[[], bool]
    working_directory: str
    workspace_root: str

    # ---- Tools ----
    registry: ToolRegistry
    executor: ToolExecutor

    # ---- Hooks ----
    hook_runner: Optional[HookRunner]
    workspace_hook_admission: Optional[WorkspaceHookRuntimeAdmissionPort]
    workspace_hook_snapshot: Optional[WorkspaceHookBundleSnapshot]

    # ---- Model ----
    model_factory: RuntimeModelFactory
    model_io_dir: Optional[str]
    provider_runtime_headers_port: Optional[ProviderRuntimeHeadersPort]
    browser_control_port: Optional[BrowserControlPort]
    model_request_admission: Optional[ModelRequestAdmission]
    session_model_selection: Optional[ModelSelection]

    # ---- Message history ----
    message_history: MessageHistory
    read_file_state: ReadFileStateMap
    cached_tools: Optional[list[Any]]  # ModelToolContract[] | null
    context_builder: Optional[ContextBuilder]
    context_initialized: bool
    context_source_snapshot: Optional[ContextSourceSnapshot]
    latest_context_build_result: Optional[ContextBuildResult]

    # ---- Memory ----
    memory_root: Optional[str]
    memory_index_content: Optional[str]
    memory_extraction_scheduler: Optional[ProjectMemoryExtractionScheduler]

    # ---- Context source ----
    context_source_port: Optional[ContextSourcePort]

    # ---- Skills / MCP ----
    skill_port: Optional[SkillPort]
    mcp_port: Optional[McpPort]
    mcp_startup_promise: Optional[Awaitable[McpConnectionSnapshot]]
    mcp_initialized: bool
    mcp_tools_registered: bool

    # ---- Subagents / workflow ----
    subagent_port: Optional[SubagentPort]
    dynamic_workflow_run_port: Optional[DynamicWorkflowRunPort]
    model_catalog_port: Optional[ModelCatalogPort]
    runtime_task_registry: RuntimeTaskRegistry
    branch_generation: int

    # ---- Artifact store ----
    artifact_store: Optional[ToolArtifactStorePort]

    # ---- Execution ports ----
    execution_port: Optional[ExecutionPort]
    file_system_port: Optional[FileSystemPort]
    image_processor_port: Optional[ImageProcessorPort]
    pdf_document_port: Optional[PdfDocumentPort]
    skill_load_outcome: Optional[SkillLoadOutcome]

    # ---- Session store ----
    session_store: Optional[SessionStorePort]
    session_mailbox_port: Optional[SessionMailboxPort]
    session_persisted: bool

    # ---- Plan mode ----
    needs_plan_mode_exit_reminder: bool

    # ---- Latest message IDs ----
    latest_conversation_message_id: Optional[MessageId]
    latest_assistant_message_id: Optional[MessageId]
    latest_assistant_turn_id: Optional[TurnId]

    # ---- Cache stats ----
    main_turn_cache_hit_aggregate: MainTurnCacheHitAggregate

    # ---- File changes tracking ----
    current_turn_file_changes: RuntimeTurnFileChangeMap

    # ---- Timing ----
    last_assistant_completed_at_ms: Optional[int]
    last_emitted_local_date: Optional[str]

    # ---- Compact ----
    auto_compact_consecutive_failures: int

    # ---- Command queue ----
    runtime_command_queue: RuntimeCommandQueue
    runtime_command_drain_active: bool

    # ---- Foreground execution ----
    active_foreground_execution: Optional[ActiveForegroundExecutionState]
    foreground_promotion_lease: Optional[ForegroundPromotionLeaseState]

    # ---- Active turn ----
    active_turn: Optional[ActiveTurnSteeringState]
    active_turn_start_reservation: Optional[ActiveTurnStartReservation]

    # ---- Pending input ----
    pending_input_sequence: int
    pending_input_reservations: dict  # Map<string, string>
    permission_full_access_pending: Optional[bool]
    pending_input_drains: Optional[int]
    last_permission_grant_id: Optional[str]
    queue_auto_drain: bool
    queue_external_drain_active: bool

    # ---- Shutdown ----
    shutting_down: bool

    # ---- Background tasks ----
    background_task_notifications_sealed: bool
    background_task_notification_seal_reason: Optional[BackgroundTaskNotificationSealReason]

    # ---- Model change timeline ----
    pending_model_change_timeline: Optional[PendingModelChangeTimeline]

    # ---- Hooks tracking ----
    session_start_hook_ran: bool
    session_title_generation_attempted: bool

    # ---- Telemetry ----
    agent_telemetry: RuntimeTelemetryFacade

    # ---- Residency tracking ----
    residency_blocking_work_count: int


# ---------------------------------------------------------------------------
# Stub implementations for types not yet fully defined
# ---------------------------------------------------------------------------

# AgentRuntimeConfig stub
@dataclass(slots=True)
class AgentRuntimeConfig:
    """Mirrors ``AgentRuntimeConfig`` — stub with common fields."""
    workspace_path: Optional[str] = None
    workspace_identity: Optional[Any] = None
    parent_session_id: Optional[SessionId] = None
    task_type: Optional[str] = None
    mode: Optional[str] = None
    plan_enabled: Optional[bool] = None
    language: Optional[str] = None
    output_style: Optional[Any] = None
    memory: Optional[Any] = None
    model_selection: Optional[Any] = None
    hooks: Optional[Any] = None
    max_turns: Optional[int] = None
    compact: Optional[Any] = None
    subagents: Optional[dict[str, Any]] = None
    tool_allowlist: Optional[list[str]] = None
    tool_disallowlist: Optional[list[str]] = None
    toolset: Optional[str] = None
    tool_concurrency: Optional[dict[str, Any]] = None
    runtime_features: Optional[dict[str, Any]] = None
    model_anomaly_guard: Optional[Any] = None
    mcp: Optional[dict[str, Any]] = None
    plugin_reference_catalog: Optional[Any] = None
    embedded_search_backend: Optional[Any] = None
    native_search_enhancements_enabled: Optional[bool] = None
    bash_shell_selection: Optional[Any] = None
    title_generation: Optional[dict[str, Any]] = None
    dynamic_workflow_enabled: Optional[bool] = None
    system_prompt: Optional[str] = None
    workflow_actor: Optional[dict[str, Optional[str]]] = None
    subagent_context: Optional[dict[str, Any]] = None
    agent_name: Optional[str] = None
    working_directory: Optional[str] = None
    env_info: Optional[dict[str, Any]] = None
    current_date: Optional[str] = None
    user_instructions: Optional[Any] = None
    project_context: Optional[Any] = None
    skill_metadata_budget: Optional[int] = None
    client_mode: Optional[str] = None
    delivery_kind: Optional[str] = None
    remote_session_id: Optional[str] = None
    bash_timeout_policy: Optional[Any] = None
    presentation_surface: Optional[Any] = None
    model_streaming: Optional[str] = None
    streaming_tool_execution: Optional[str] = None
    model_context_budget_strategy: Optional[str] = None
    permission_timeout_ms: Optional[int] = None
    target_completion_verification: Optional[dict[str, Any]] = None
    mid_conversation_system: Optional[dict[str, str]] = None