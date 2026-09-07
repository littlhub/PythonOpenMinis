"""shell_execute tool — run commands in the session's persistent sandbox shell.

Ported from: src/android/app/src/main/java/com/openminis/app/tools/AgentTools.kt
             (shellExecuteDefinition + its execution path through
              ExecutionCoordinator / PersistentShell)
Original package: com.openminis.app.tools

Schema kept identical to the Kotlin definition (name ``shell_execute``, params
``tool_title``/``command``/``timeout``/``delay``). Execution semantics match the
Android pipeline: a shared per-session ``PersistentShell`` via
``ExecutionCoordinator.execute``, with TerminalSanitizer cleanup applied there.

# PORT: unlike the stateless file tools, shell_execute touches shared shell
# state, so this tool is stateful by necessity. ``execute`` is therefore
# ``async`` — the agent loop awaits tools uniformly.
"""

from __future__ import annotations

import asyncio
import json
from typing import Awaitable, Callable, Optional

from ..core.logging import get_logger
from ..data.model.agent_tool_definition import AgentToolDefinition, AgentToolParam
from ..sandbox.execution_coordinator import ExecutionCoordinator
from .tool_execution_result import ToolExecutionResult

logger = get_logger(__name__)

__all__ = ["ShellExecuteTool"]


class ShellExecuteTool:
    """Kotlin ``shell_execute`` tool definition + execution."""

    NAME = "shell_execute"

    def __init__(
        self,
        coordinator: Optional[ExecutionCoordinator] = None,
        get_env: Optional[Callable[[], dict[str, str]]] = None,
    ) -> None:
        """``get_env`` supplies per-call user env vars (T124a snapshot)."""
        self.coordinator = coordinator or _default_coordinator()
        self.get_env = get_env

    @staticmethod
    def definition() -> AgentToolDefinition:
        return AgentToolDefinition(
            name=ShellExecuteTool.NAME,
            description=(
                "Execute a command in an isolated Linux process (Alpine Linux via PRoot). "
                "The command runs via /bin/sh -c with stdout and stderr merged. "
                "Environment variables persist between commands in the same session "
                "(cd and export persist too)."
            ),
            parameters={
                "tool_title": AgentToolParam(
                    "string",
                    "A concise 5-10 word summary of what this command does, shown to the "
                    "user (e.g. 'List project files', 'Install Python dependencies'). "
                    "Use the same language as the user.",
                ),
                "command": AgentToolParam(
                    "string",
                    "The shell command to execute. Supports multi-line commands directly — "
                    "no special escaping needed. Keep under 1000 chars; for longer scripts, "
                    "write to a file with file_write first, then run it.",
                ),
                "timeout": AgentToolParam(
                    "integer",
                    "Timeout in seconds (default: 900). Use a larger value for long-running "
                    "commands like package installs.",
                ),
                "delay": AgentToolParam(
                    "integer",
                    "Delay in seconds before execution begins. The tool blocks the agent "
                    "flow during this wait WITHOUT occupying the shell.",
                ),
            },
            required=["tool_title", "command"],
            property_ordering=["tool_title", "command", "timeout", "delay"],
        )

    async def execute(
        self,
        args_json: str,
        session_id: str,
        line_callback: Optional[Callable[[str], None]] = None,
    ) -> ToolExecutionResult:
        """Kotlin ``execute(argsJson, sessionId)`` — async shell dispatch."""
        try:
            args = json.loads(args_json)
        except ValueError as exc:
            return ToolExecutionResult(f"Error: invalid JSON args: {exc}", True,
                                       tool_title=ShellExecuteTool.NAME)

        command = str(args.get("command", ""))
        tool_title = str(args.get("tool_title", ShellExecuteTool.NAME))
        if not command.strip():
            return ToolExecutionResult("Error: 'command' is required", True,
                                       tool_title=tool_title)
        try:
            timeout = int(args.get("timeout", 900))
        except (TypeError, ValueError):
            timeout = 900
        try:
            delay = int(args.get("delay", 0))
        except (TypeError, ValueError):
            delay = 0
        if delay > 0:
            await asyncio.sleep(delay)

        env = self.get_env() if self.get_env is not None else None
        result = await self.coordinator.execute(
            session_id,
            command,
            timeout=max(float(timeout), 1.0),
            line_callback=line_callback,
            env_vars=env,
        )

        output = result.output or "(no output)"
        if result.exit_code == 124:
            output += "\n[command timed out]"
        return ToolExecutionResult(
            output, result.exit_code == 0, tool_title=tool_title,
        )


_shared: Optional[ExecutionCoordinator] = None


def _default_coordinator() -> ExecutionCoordinator:
    """Module-level shared coordinator — the shell cache is app-global."""
    global _shared
    if _shared is None:
        _shared = ExecutionCoordinator()
    return _shared


def get_coordinator() -> ExecutionCoordinator:
    """Access the shared coordinator (creating it if needed).

    Callers (the Web server) use this to register per-session sandbox roots
    before a chat turn starts, so filed sessions boot inside their workspace.
    """
    return _default_coordinator()


def install_coordinator(coordinator: ExecutionCoordinator) -> None:
    """Point the default tool at an externally-owned coordinator (server/CLI)."""
    global _shared
    _shared = coordinator
