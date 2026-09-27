"""ZCode agent entry point.

Ported from: packages/core/src/runtime/agent-runtime.ts
Original package: @zcode/core (runtime)

This module provides the ``ZCodeAgent`` class: a simplified Python entry point for
the ZCode programming-agent kernel, orchestrating the already-ported components
(turn machine, tool registry, scheduler, executor, message history).

For the full-featured runtime see ``openminis.agent.agent_runtime``.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, Sequence

from .agent.message_history import (
    MessageHistory,
    MessageHistoryImpl,
    create_runtime_user_entry,
    create_runtime_assistant_entry,
    create_runtime_tool_result_entry,
)
from .agent.turn_machine import TurnMachineImpl, ToolCompletionResult
from .agent.turn_state import TurnState
from .contracts.ids import SessionId, TurnId, TraceId, create_turn_id
from .contracts.model import ModelMessageContent, model_message_content_to_text
from .tool.executor.impl import ToolExecutorImpl
from .tool.executor.types import ToolExecuteOptions, ToolExecutorOptions
from .tool.registry import ToolRegistry, ToolRegistryImpl, create_tool_registry
from .tool.scheduler import ToolScheduler, ToolSchedulerOptions
from .tool.types import (
    ExecutableToolCall,
    ReadFileStateMap,
    ToolBatchEvent,
)

__all__ = [
    "ZCodeAgent",
    "AgentContext",
    "AgentResponse",
    "AgentPorts",
    "CollaborationMode",
]


# -----------------------------------------------
# Collaboration Mode
# -----------------------------------------------

CollaborationMode = str  # "solo" | "guide" | "build"


# -----------------------------------------------
# Agent Context — ask() input
# -----------------------------------------------

@dataclass(slots=True)
class AgentContext:
    """Input context for a single ``ask`` call.

    Mirrors the subset of ``AgentRuntime`` input state needed for one turn.
    """

    session_id: Optional[str] = None
    turn_number: int = 0
    working_directory: str = "."
    workspace_root: Optional[str] = None
    model: Optional[Any] = None  # PORT: ``Model`` — @zcode/contracts (model), not ported yet
    trace_context: Optional[Any] = None  # PORT: ``TraceContext`` — not ported yet
    attachments: Optional[list[Any]] = None  # PORT: ``TurnState["attachments"]``
    mode: CollaborationMode = "solo"
    plan_enabled: bool = False
    # Abort signal for the whole ask call.
    abort_signal: Optional[Any] = None  # PORT: ``AbortSignal`` — not ported yet


# -----------------------------------------------
# Agent Response — ask() output
# -----------------------------------------------

@dataclass(slots=True)
class AgentResponse:
    """Output of a single ``ask`` call.

    Mirrors the subset of ``TurnResult`` needed by callers.
    """

    # Final text response from the agent.
    content: str
    # Tool calls made during this turn, if any.
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    # Whether the turn completed successfully.
    success: bool = True
    # Error message if ``success`` is ``False``.
    error: Optional[str] = None
    # Internal turn state for debugging / tracing.
    turn_state: Optional[TurnState] = None
    # Milliseconds elapsed for the turn.
    duration_ms: Optional[int] = None
    # The session / turn IDs used.
    session_id: Optional[str] = None
    turn_id: Optional[str] = None


# -----------------------------------------------
# Agent Ports — external dependencies
# -----------------------------------------------

class AgentPorts(Protocol):
    """Ports that the caller must provide.

    These mirror the ``*Port`` interfaces from the TypeScript runtime that are
    not part of the core library and must be injected by the host application.
    """

    # PORT: ``FileSystemPort`` — @zcode/contracts, not ported yet.
    def file_system_port(self) -> Any:
        raise NotImplementedError

    # PORT: ``ExecutionPort`` — @zcode/contracts, not ported yet.
    def execution_port(self) -> Any:
        raise NotImplementedError

    # PORT: ``Logger`` — not ported yet.
    def logger(self) -> Any:
        raise NotImplementedError


# -----------------------------------------------
# ZCode Agent
# -----------------------------------------------

class ZCodeAgent:
    """ZCode programming agent entry point.

    This class is a simplified Python port of the TypeScript ``AgentRuntime``.
    It wires together the already-ported ZCode components:

    - ``ToolRegistry`` — tool registration and lookup
    - ``ToolScheduler`` — tool execution scheduling
    - ``ToolExecutorImpl`` — tool call execution
    - ``MessageHistoryImpl`` — conversation context across turns
    - ``TurnMachineImpl`` — turn lifecycle state machine

    The full TypeScript runtime includes many more subsystems (event store,
    permission service, MCP support, subagents, workflows, memory, etc.).  This
    entry point exposes the core ask loop and the tool-registration surface.
    External ports (``FileSystemPort``, ``ExecutionPort``, ``Logger``, etc.)
    are supplied by the caller through the ``ports`` argument.

    Usage::

        from openminis.agent.zcode import ZCodeAgent, AgentContext

        agent = ZCodeAgent(working_directory="/path/to/project")
        agent.register_tool(my_tool)

        ctx = AgentContext(session_id="s1", turn_number=1, working_directory="/path/to/project")
        response = await agent.ask("Hello, world!", ctx)
        print(response.content)
    """

    def __init__(
        self,
        working_directory: str = ".",
        workspace_root: Optional[str] = None,
        ports: Optional[AgentPorts] = None,
        session_id: Optional[str] = None,
        max_tool_concurrency: int = 10,
    ) -> None:
        self._session_id = SessionId(session_id or str(uuid.uuid4()))
        self._turn_number: int = 0
        self._working_directory = working_directory
        self._workspace_root = workspace_root or working_directory
        self._ports = ports
        self._mode: CollaborationMode = "solo"
        self._plan_enabled: bool = False
        self._now = lambda: datetime.now(timezone.utc)

        # Core components — all wired to their Python-port implementations.
        self._registry: ToolRegistry = create_tool_registry()
        self._scheduler = ToolScheduler(
            options=ToolSchedulerOptions(max_concurrency=max_tool_concurrency)
        )
        self._message_history: MessageHistory = MessageHistoryImpl()
        self._read_file_state: ReadFileStateMap = {}

        # Build the executor lazily to allow the caller to register tools first.
        self._executor: Optional[ToolExecutorImpl] = None

    # -----------------------------------------------
    # Public API
    # -----------------------------------------------

    async def ask(
        self,
        prompt: str,
        context: Optional[AgentContext] = None,
    ) -> AgentResponse:
        """Process a single user prompt through the agent turn lifecycle.

        This is the main entry point for one agent turn.  It:

        1. Builds a turn ID and starts the turn machine.
        2. Adds the user message to message history.
        3. Simulates a model round-trip (placeholder — requires model integration).
        4. Schedules and executes any tool calls.
        5. Returns the final response.

        Args:
            prompt: The user input string.
            context: Optional turn context (session ID, working directory, etc.).

        Returns:
            An ``AgentResponse`` with the agent's reply and any tool-call metadata.
        """
        ctx = context or AgentContext()
        self._turn_number = ctx.turn_number
        session_id = SessionId(ctx.session_id or str(self._session_id))

        turn_id = create_turn_id()
        trace_id = TraceId(str(uuid.uuid4()))

        # Start the turn machine.
        machine = TurnMachineImpl.create(
            session_id=session_id,
            turn_number=self._turn_number,
            input=prompt,
            trace_id=trace_id,
            turn_id=turn_id,
        )
        machine.start()

        # Record the user prompt in message history.
        self._message_history.add_user(
            content=prompt,
        )

        started_at = self._now()

        try:
            # --- Placeholder model interaction ---
            # In the full TypeScript runtime this calls the model, receives
            # tool calls or a text response, and updates the turn machine
            # accordingly.  Here we produce a stub response so the loop
            # always completes.
            response_text, tool_calls = await self._model_round_trip(
                machine, prompt, ctx, session_id, turn_id
            )

            # Aggregate results and complete the turn.
            machine.aggregate_results()
            machine.complete(response_text)

            completed_at = self._now()
            duration_ms = int(
                (completed_at - started_at).total_seconds() * 1000
            )

            return AgentResponse(
                content=response_text,
                tool_calls=[tc for tc in tool_calls],
                success=True,
                turn_state=machine.state,
                duration_ms=duration_ms,
                session_id=str(session_id),
                turn_id=str(turn_id),
            )

        except Exception as exc:  # noqa: BLE001
            machine.fail(
                {
                    "type": "internal_error",
                    "message": str(exc),
                    "recoverable": True,
                }
            )
            completed_at = self._now()
            duration_ms = int(
                (completed_at - started_at).total_seconds() * 1000
            )
            return AgentResponse(
                content="",
                success=False,
                error=str(exc),
                turn_state=machine.state,
                duration_ms=duration_ms,
                session_id=str(session_id),
                turn_id=str(turn_id),
            )

    def register_tool(self, tool_entry: Any) -> None:
        """Register a tool with the agent's tool registry.

        Args:
            tool_entry: A ``ToolEntry`` (from ``openminis.agent.zcode.tool.types``)
                or any object with a ``metadata`` attribute.
        """
        self._registry.register(tool_entry)

    def unregister_tool(self, name: str) -> None:
        """Remove a tool from the registry by name."""
        self._registry.unregister(name)

    def get_tool_registry(self) -> ToolRegistry:
        """Return the tool registry for inspection."""
        return self._registry

    def get_message_history(self) -> MessageHistory:
        """Return the message history instance."""
        return self._message_history

    def set_mode(self, mode: CollaborationMode) -> None:
        """Set the collaboration mode (``solo``, ``guide``, ``build``)."""
        self._mode = mode

    def set_plan_enabled(self, enabled: bool) -> None:
        """Enable or disable plan mode."""
        self._plan_enabled = enabled

    # -----------------------------------------------
    # Internal helpers
    # -----------------------------------------------

    async def _model_round_trip(
        self,
        machine: TurnMachineImpl,
        prompt: str,
        ctx: AgentContext,
        session_id: SessionId,
        turn_id: TurnId,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Placeholder model interaction.

        The full runtime sends the prompt + message history to the model,
        receives ``ModelResponse`` objects (text and/or tool calls), and updates
        the turn machine accordingly.

        This stub returns an acknowledgement and no tool calls, matching the
        behaviour of a model that chose not to invoke any tools.

        Replace this with real model integration when available.
        """
        # Record the assistant response in message history.
        self._message_history.add_assistant(content=f"[ZCodeAgent stub response to: {prompt}]")

        return f"[ZCodeAgent]: I received your message: {prompt}", []

    def _build_executor(self) -> ToolExecutorImpl:
        """Lazily build the tool executor after tools have been registered."""
        # PORT: ToolExecutorOptions.emit_event (Callable) and permission_service
        # (PermissionService) are required in the TS interface but the corresponding
        # contracts are not ported yet.  None is used as a placeholder.
        options = ToolExecutorOptions(
            registry=self._registry,
            permission_service=None,  # type: ignore[arg-type]
            emit_event=None,  # type: ignore[arg-type]
            session_id=self._session_id,
            default_timeout_ms=300000,
            read_file_state=self._read_file_state,
            get_working_directory=lambda: self._working_directory,
            get_workspace_root=lambda: self._workspace_root,
            runtime_scope="main",
            get_mode=lambda: self._mode,
            max_concurrency=10,
            logger=self._ports.logger() if self._ports else None,
            # Optional ports from caller
            file_system_port=self._ports.file_system_port() if self._ports else None,
            execution_port=self._ports.execution_port() if self._ports else None,
        )
        return ToolExecutorImpl(options=options)