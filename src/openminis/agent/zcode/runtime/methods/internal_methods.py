"""Internal method interfaces — AgentRuntimeCoreMethods, AgentRuntimeTurnMethods,
and AgentRuntimeHookMethods.

Ported from: packages/core/src/runtime/internal-methods.ts
              packages/core/src/runtime/internal-turn-methods.ts
              packages/core/src/runtime/internal-hook-methods.ts
Original package: @zcode/core (runtime)

These three interfaces define all the methods that the runtime internal object
must implement. They are split into:
- AgentRuntimeCoreMethods: session config, tools, context builder
- AgentRuntimeTurnMethods: turn execution, compact, rewind, persistence
- AgentRuntimeHookMethods: session/start and user-prompt hooks
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional, Protocol

# ---------------------------------------------------------------------------
# Forward type imports
# ---------------------------------------------------------------------------

from ..deps import (
    MessageId,
    Model,
    ModelInputMessage,
    ModelSelection,
    ModelSelectionOrigin,
    ModelToolCall,
    ModelToolContract,
    SessionEvent,
    SessionEventSink,
    SessionGoal,
    SkillLoadOutcome,
    SubagentPort,
    TodoItem,
    TraceContext,
    TurnInputIntentMetadata,
    TurnId,
)

__all__ = [
    "AgentRuntimeCoreMethods",
    "AgentRuntimeTurnMethods",
    "AgentRuntimeHookMethods",
]

# ---------------------------------------------------------------------------
# Core methods
# ---------------------------------------------------------------------------


class AgentRuntimeCoreMethods(Protocol):
    """The core runtime methods — session config, tools, context building."""

    def update_config(
        self,
        patch: dict[str, Any],
    ) -> None:
        """Mirrors ``updateConfig``."""
        ...

    def initialize_session_shell_environment_if_needed(
        self,
        selection: Any,
    ) -> bool:
        """Mirrors ``initializeSessionShellEnvironmentIfNeeded``."""
        ...

    def get_session_shell_selection(self) -> Optional[Any]:
        """Mirrors ``getSessionShellSelection``."""
        ...

    def get_mode(self) -> str:
        """Mirrors ``getMode``."""
        ...

    def get_plan_enabled(self) -> bool:
        """Mirrors ``getPlanEnabled``."""
        ...

    def grant_permission_full_access(
        self,
        interaction_id: str,
        signal: Any = None,
    ) -> Awaitable[str]:
        """Mirrors ``grantPermissionFullAccess``."""
        ...

    def set_execution_state(
        self,
        input: dict[str, Any],
        trace_context: Optional[TraceContext] = None,
    ) -> Awaitable[None]:
        """Mirrors ``setExecutionState``."""
        ...

    def get_session_model_selection(self) -> Optional[Any]:
        """Mirrors ``getSessionModelSelection``."""
        ...

    def set_session_model_selection(
        self,
        selection: Optional[Any],
    ) -> None:
        """Mirrors ``setSessionModelSelection``."""
        ...

    def get_project_id(self) -> Any:
        """Mirrors ``getProjectId``."""
        ...

    def set_working_directory(self, cwd: str) -> None:
        """Mirrors ``setWorkingDirectory``."""
        ...

    def ensure_session_persisted_for_external_activity(
        self,
        input: str,
        options: Optional[dict[str, Any]] = None,
    ) -> Awaitable[None]:
        """Mirrors ``ensureSessionPersistedForExternalActivity``."""
        ...

    def get_active_turn_info(self) -> Optional[Any]:
        """Mirrors ``getActiveTurnInfo``."""
        ...

    def get_tools(self, model: Optional[Model] = None) -> list[ModelToolContract]:
        """Mirrors ``getTools``."""
        ...

    def invalidate_tool_cache(self) -> None:
        """Mirrors ``invalidateToolCache``."""
        ...

    def get_tool_registry(self) -> Any:
        """Mirrors ``getToolRegistry``."""
        ...

    def get_tool_executor(self) -> Any:
        """Mirrors ``getToolExecutor``."""
        ...

    def subscribe_events(
        self,
        sink: SessionEventSink,
    ) -> Callable[[], None]:
        """Mirrors ``subscribeEvents`` — returns unsubscribe function."""
        ...

    def get_context_builder(self) -> Any:
        """Mirrors ``getContextBuilder``."""
        ...

    def ensure_context_initialized(
        self,
        trace_context: TraceContext,
        model: Optional[Model] = None,
    ) -> Awaitable[None]:
        """Mirrors ``ensureContextInitialized``."""
        ...

    def get_skill_catalog(
        self,
        trace_context: TraceContext,
    ) -> Awaitable[SkillLoadOutcome]:
        """Mirrors ``getSkillCatalog``."""
        ...

    def create_context_builder_from_snapshot(
        self,
        snapshot: Any,
        memory_root: Optional[str] = None,
        options: Optional[dict[str, Any]] = None,
    ) -> Any:
        """Mirrors ``createContextBuilderFromSnapshot``."""
        ...

    def load_project_memory_root(
        self,
        trace_context: TraceContext,
    ) -> Awaitable[Optional[str]]:
        """Mirrors ``loadProjectMemoryRoot``."""
        ...

    def inject_plugin_reference_reminder_from_turn(
        self,
        user_input: str,
        trace_context: TraceContext,
        tool_disallowlist: Optional[list[str]] = None,
    ) -> Awaitable[None]:
        """Mirrors ``injectPluginReferenceReminderFromTurn``."""
        ...

    def create_config_only_context_snapshot(
        self,
        working_directory: str,
    ) -> Any:
        """Mirrors ``createConfigOnlyContextSnapshot``."""
        ...

    def initialize_message_history_from_context(
        self,
        context_builder: Any,
        trace_context: TraceContext,
    ) -> None:
        """Mirrors ``initializeMessageHistoryFromContext``."""
        ...

    def extract_tool_calls_from_result(
        self,
        result: Any,
    ) -> list[ModelToolCall]:
        """Mirrors ``extractToolCallsFromResult``."""
        ...

    def should_stream_model_text(self) -> bool:
        """Mirrors ``shouldStreamModelText``."""
        ...

    def run_model_text_request(
        self,
        options: Any,
    ) -> Awaitable[Any]:
        """Mirrors ``runModelTextRequest``."""
        ...

    def is_project_memory_enabled(self) -> bool:
        """Mirrors ``isProjectMemoryEnabled``."""
        ...

    def drain_memory_extractions(
        self,
        timeout_ms: Optional[int] = None,
    ) -> Awaitable[None]:
        """Mirrors ``drainMemoryExtractions``."""
        ...

    def resume_from_store(
        self,
        options: Optional[Any] = None,
    ) -> Awaitable[Any]:
        """Mirrors ``resumeFromStore``."""
        ...

    def read_session_todos_for_context(
        self,
        trace_context: TraceContext,
    ) -> Awaitable[list[TodoItem]]:
        """Mirrors ``readSessionTodosForContext``."""
        ...

    def read_session_target_for_context(
        self,
        trace_context: TraceContext,
    ) -> Awaitable[Optional[SessionGoal]]:
        """Mirrors ``readSessionTargetForContext``."""
        ...

    def get_session_id(self) -> Any:
        """Mirrors ``getSessionId``."""
        ...

    def create_default_subagent_port(
        self,
        deps: Any,
    ) -> Optional[SubagentPort]:
        """Mirrors ``createDefaultSubagentPort``."""
        ...


# ---------------------------------------------------------------------------
# Turn methods
# ---------------------------------------------------------------------------


class AgentRuntimeTurnMethods(Protocol):
    """Turn execution methods — admission, execution, compact, rewind, persistence."""

    def admit_prompt(
        self,
        input: str,
        attachments: Optional[Any] = None,
        options: Optional[Any] = None,
    ) -> Awaitable[Any]:
        """Mirrors ``admitPrompt``."""
        ...

    def execute_turn(
        self,
        input: str,
        attachments: Optional[Any] = None,
        options: Optional[Any] = None,
    ) -> Awaitable[Any]:
        """Mirrors ``executeTurn``."""
        ...

    def execute_turn_command(
        self,
        input: str,
        attachments: Optional[Any] = None,
        options: Optional[Any] = None,
        start_reservation: Optional[Any] = None,
    ) -> Awaitable[Any]:
        """Mirrors ``executeTurnCommand``."""
        ...

    def execute_manual_compact(
        self,
        input: str,
        custom_instructions: Optional[str],
        turn_id: TurnId,
        turn_trace_context: TraceContext,
        abort_signal: Optional[Any] = None,
        input_id: Optional[str] = None,
        model: Optional[Model] = None,
    ) -> Awaitable[Any]:
        """Mirrors ``executeManualCompact``."""
        ...

    def execute_rewind_command(
        self,
        input: str,
        command: Any,
        turn_id: TurnId,
        turn_trace_context: TraceContext,
        abort_signal: Optional[Any] = None,
        input_id: Optional[str] = None,
    ) -> Awaitable[Any]:
        """Mirrors ``executeRewindCommand``."""
        ...

    def rewind_workspace_to_checkpoint(
        self,
        options: dict[str, Any],
    ) -> Awaitable[Any]:
        """Mirrors ``rewindWorkspaceToCheckpoint``."""
        ...

    def rewind_to_message(
        self,
        options: dict[str, Any],
    ) -> Awaitable[Any]:
        """Mirrors ``rewindToMessage``."""
        ...

    def schedule_tools(
        self,
        tool_calls: list[Any],
    ) -> Awaitable[Any]:
        """Mirrors ``scheduleTools``."""
        ...

    def execute_tools(
        self,
        tool_calls: list[Any],
        schedule: Any,
        options: Optional[dict[str, Any]] = None,
    ) -> Awaitable[Any]:
        """Mirrors ``executeTools``."""
        ...

    def emit_tool_scheduled_events(
        self,
        tool_calls: list[Any],
        schedule: Any,
        assistant_message_id: MessageId,
        trace_context: TraceContext,
    ) -> Awaitable[list[SessionEvent]]:
        """Mirrors ``emitToolScheduledEvents``."""
        ...

    def emit_file_mutation_checkpoint(
        self,
        options: dict[str, Any],
    ) -> Awaitable[None]:
        """Mirrors ``emitFileMutationCheckpoint``."""
        ...

    def emit_permission_request(
        self,
        tool_call_id: Any,
        tool_name: str,
        risk_level: str,
    ) -> Awaitable[None]:
        """Mirrors ``emitPermissionRequest``."""
        ...

    def resolve_permission(
        self,
        tool_call_id: Any,
        decision: Any,
    ) -> Awaitable[None]:
        """Mirrors ``resolvePermission``."""
        ...

    def get_pending_permission_requests(self) -> list[Any]:
        """Mirrors ``getPendingPermissionRequests``."""
        ...

    def get_projection(self) -> Awaitable[Any]:
        """Mirrors ``getProjection``."""
        ...

    def create_event(
        self,
        type: str,
        payload: Any,
        trace_context: TraceContext,
    ) -> SessionEvent:
        """Mirrors ``createEvent``."""
        ...

    def append_event(
        self,
        event: SessionEvent,
        trace_context: TraceContext,
    ) -> Awaitable[None]:
        """Mirrors ``appendEvent``."""
        ...

    def notify_event_sinks(
        self,
        event: SessionEvent,
        trace_context: TraceContext,
    ) -> Awaitable[None]:
        """Mirrors ``notifyEventSinks``."""
        ...

    def ensure_session_persisted(
        self,
        input: str,
        trace_context: TraceContext,
    ) -> Awaitable[None]:
        """Mirrors ``ensureSessionPersisted``."""
        ...

    def persist_message(
        self,
        input: dict[str, Any],
        trace_context: TraceContext,
        copy_from: Any = None,
    ) -> Awaitable[None]:
        """Mirrors ``persistMessage``."""
        ...

    def persist_part(
        self,
        input: Any,
        trace_context: TraceContext,
        copy_from: Any = None,
    ) -> Awaitable[None]:
        """Mirrors ``persistPart``."""
        ...

    def rebuild_projection(self) -> Awaitable[Any]:
        """Mirrors ``rebuildProjection``."""
        ...

    def fork_workspace_from_checkpoint(
        self,
        options: Optional[dict[str, Any]] = None,
    ) -> Awaitable[Any]:
        """Mirrors ``forkWorkspaceFromCheckpoint``."""
        ...

    def list_workspace_checkpoints(
        self,
        options: Optional[dict[str, Any]] = None,
    ) -> Awaitable[list[Any]]:
        """Mirrors ``listWorkspaceCheckpoints``."""
        ...


# ---------------------------------------------------------------------------
# Hook methods
# ---------------------------------------------------------------------------


class AgentRuntimeHookMethods(Protocol):
    """Hook execution methods — session start, user prompt, stop hooks."""

    def run_session_start_hooks(
        self,
        source: str,  # "startup" | "resume" | "clear" | "compact"
        trace_context: TraceContext,
        signal: Optional[Any] = None,
        model: Optional[Any] = None,
    ) -> Awaitable[Any]:
        """Mirrors ``runSessionStartHooks``."""
        ...

    def run_user_prompt_submit_hooks(
        self,
        prompt: str,
        attachments: Optional[Any],
        trace_context: TraceContext,
        signal: Optional[Any] = None,
    ) -> Awaitable[Any]:
        """Mirrors ``runUserPromptSubmitHooks``."""
        ...

    def run_stop_hooks(
        self,
        response: str,
        tool_call_count: int,
        trace_context: TraceContext,
        signal: Optional[Any] = None,
        stop_hook_active: Optional[bool] = None,
    ) -> Awaitable[Any]:
        """Mirrors ``runStopHooks``."""
        ...

    def inject_hook_additional_context_into_message_history(
        self,
        event_name: str,
        additional_contexts: list[str],
    ) -> Optional[Any]:
        """Mirrors ``injectHookAdditionalContextIntoMessageHistory``."""
        ...

    def should_continue_after_stop_hooks(
        self,
        result: Any,
        continuation_count: int,
    ) -> bool:
        """Mirrors ``shouldContinueAfterStopHooks``."""
        ...