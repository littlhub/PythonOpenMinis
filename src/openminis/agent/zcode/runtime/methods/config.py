"""Runtime configuration methods — get/set session config, tools, registry.

Ported from: packages/core/src/runtime/methods/config.ts
Original package: @zcode/core (runtime/methods)
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from ..deps import Model, ModelToolContract, SessionEventSink

__all__ = [
    "set_execution_state",
    "update_config",
    "initialize_session_shell_environment_if_needed",
    "get_session_shell_selection",
    "get_mode",
    "get_plan_enabled",
    "get_session_model_selection",
    "set_session_model_selection",
    "get_project_id",
    "set_working_directory",
    "ensure_session_persisted_for_external_activity",
    "get_active_turn_info",
    "get_tools",
    "invalidate_tool_cache",
    "get_tool_registry",
    "get_tool_executor",
    "subscribe_events",
    "get_context_builder",
    "get_pending_permission_requests",
    "get_projection",
    "get_session_id",
    "get_session_event_store",
    "notify_external_child_session_event",
    "create_child_client_ports",
]


async def set_execution_state(
    runtime: Any,
    input: dict[str, Any],
    trace_context: Optional[Any] = None,
) -> None:
    """Mirrors ``setExecutionState``."""
    # PORT: full implementation in execution-state module
    pass


def update_config(
    runtime: Any,
    patch: dict[str, Any],
) -> None:
    """Mirrors ``updateConfig`` — applies a config patch.

    Handles updates to mode, planEnabled, language, and outputStyle.
    Rebuilds context prefix when language or outputStyle changes.
    """
    mode = patch.get("mode")
    plan_enabled = patch.get("planEnabled")

    if mode is not None or plan_enabled is not None:
        # Would resolve execution state here
        previous_plan_enabled = getattr(runtime, "needs_plan_mode_exit_reminder", False)
        if mode is not None:
            runtime.config.mode = mode
        if plan_enabled is not None:
            runtime.config.plan_enabled = plan_enabled
            runtime.needs_plan_mode_exit_reminder = not plan_enabled

    language = patch.get("language")
    if language is not None:
        runtime.config.language = language
        if not runtime.active_turn:
            _rebuild_context_prefix(runtime)

    if "outputStyle" in patch:
        runtime.config.output_style = patch["outputStyle"]
        if not runtime.active_turn:
            _rebuild_context_prefix(runtime)


def _rebuild_context_prefix(runtime: Any) -> None:
    """Rebuild context prefix after config change."""
    # PORT: stub - full implementation in context-refresh module
    pass


def initialize_session_shell_environment_if_needed(
    runtime: Any,
    selection: Any,
) -> bool:
    """Mirrors ``initializeSessionShellEnvironmentIfNeeded``."""
    # PORT: stub
    return False


def get_session_shell_selection(runtime: Any) -> Optional[Any]:
    """Mirrors ``getSessionShellSelection``."""
    # PORT: stub
    return None


def get_mode(runtime: Any) -> str:
    """Mirrors ``getMode``."""
    return getattr(runtime.config, "mode", None) or "build"


def get_plan_enabled(runtime: Any) -> bool:
    """Mirrors ``getPlanEnabled``."""
    # PORT: would read from execution state
    return getattr(runtime.config, "plan_enabled", False)


def get_session_model_selection(runtime: Any) -> Optional[Any]:
    """Mirrors ``getSessionModelSelection``."""
    selection = getattr(runtime, "session_model_selection", None)
    if selection is None:
        return None
    # Clone the selection to prevent mutation
    return _clone_model_selection(selection)


def _clone_model_selection(selection: Any) -> Any:
    """Clone a model selection."""
    # PORT: stub - would use cloneModelSelection
    return selection


def set_session_model_selection(
    runtime: Any,
    selection: Optional[Any],
) -> None:
    """Mirrors ``setSessionModelSelection``."""
    runtime.session_model_selection = _clone_model_selection(selection) if selection else None


def get_project_id(runtime: Any) -> Any:
    """Mirrors ``getProjectId``."""
    # PORT: stub - would use projectIdFromDirectory
    return None


def set_working_directory(runtime: Any, cwd: str) -> None:
    """Mirrors ``setWorkingDirectory``."""
    runtime.working_directory = cwd


async def ensure_session_persisted_for_external_activity(
    runtime: Any,
    input: str,
    options: Optional[dict[str, Any]] = None,
) -> None:
    """Mirrors ``ensureSessionPersistedForExternalActivity``."""
    # PORT: stub
    pass


def get_active_turn_info(runtime: Any) -> Optional[dict[str, Any]]:
    """Mirrors ``getActiveTurnInfo``."""
    active_turn = getattr(runtime, "active_turn", None)
    if not active_turn:
        return None

    return {
        "kind": active_turn.kind,
        # PORT: TS `...(activeTurn.inputId === undefined ? {} : { inputId })`
        **({"inputId": active_turn.input_id} if active_turn.input_id is not None else {}),
        "queueLength": len(active_turn.pending_inputs) if hasattr(active_turn, "pending_inputs") else 0,
        "steerable": active_turn.steerable if hasattr(active_turn, "steerable") else True,
        "turnId": active_turn.turn_id if hasattr(active_turn, "turn_id") else None,
    }


def get_tools(runtime: Any, model: Optional[Model] = None) -> list[ModelToolContract]:
    """Mirrors ``getTools``."""
    if getattr(runtime, "cached_tools", None) is None:
        # Would filter runtime-visible tools and cache them
        runtime.cached_tools = []

    # PORT: filtering logic for web search, tool allowlist/disallowlist
    tools = list(runtime.cached_tools)

    # Filter WebSearch based on model capability
    if model is not None:
        tools = [t for t in tools if t.name != "WebSearch" or _should_expose_web_search(model)]

    return tools


def _should_expose_web_search(model: Model) -> bool:
    """Check if WebSearch should be exposed for this model."""
    # PORT: checks model.properties.supportsNativeWebSearch
    return True


def invalidate_tool_cache(runtime: Any) -> None:
    """Mirrors ``invalidateToolCache``."""
    runtime.cached_tools = None


def get_tool_registry(runtime: Any) -> Any:
    """Mirrors ``getToolRegistry``."""
    return getattr(runtime, "registry", None)


def get_tool_executor(runtime: Any) -> Any:
    """Mirrors ``getToolExecutor``."""
    return getattr(runtime, "executor", None)


def subscribe_events(
    runtime: Any,
    sink: SessionEventSink,
) -> Callable[[], None]:
    """Mirrors ``subscribeEvents`` — returns unsubscribe function."""
    runtime.event_sinks.add(sink)
    return lambda: runtime.event_sinks.discard(sink)


def get_context_builder(runtime: Any) -> Any:
    """Mirrors ``getContextBuilder``."""
    context_builder = getattr(runtime, "context_builder", None)
    if context_builder is None:
        # Would create from snapshot
        runtime.context_builder = runtime.create_context_builder_from_snapshot(
            runtime.create_config_only_context_snapshot(runtime.working_directory),
            None,
            {"persistEnvInfo": False},
        )
    return runtime.context_builder


def get_pending_permission_requests(runtime: Any) -> list[Any]:
    """Mirrors ``getPendingPermissionRequests``."""
    broker = getattr(runtime, "permission_broker", None)
    if broker is None or not hasattr(broker, "list_pending_requests"):
        return []
    return broker.list_pending_requests()


async def get_projection(runtime: Any) -> Any:
    """Mirrors ``getProjection``."""
    return await runtime.rebuild_projection()


def get_session_id(runtime: Any) -> Any:
    """Mirrors ``getSessionId``."""
    return runtime.session_id


def get_session_event_store(runtime: Any) -> Any:
    """Mirrors ``getSessionEventStore``."""
    return runtime.event_store


async def notify_external_child_session_event(
    runtime: Any,
    input: dict[str, Any],
) -> None:
    """Mirrors ``notifyExternalChildSessionEvent``."""
    # PORT: stub
    pass


def create_child_client_ports(
    runtime: Any,
    context: Any,
) -> Any:
    """Mirrors ``createChildClientPorts``."""
    # PORT: stub - would derive from parent runtime
    return {}