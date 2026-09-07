"""file_write tool.

Ported from: src/android/app/src/main/java/com/openminis/app/tools/FileWriteTool.kt
Original package: com.openminis.app.tools
"""

from __future__ import annotations

import json
from pathlib import Path

from ..core.context import app_context
from ..core.logging import get_logger
from ..data.model.agent_tool_definition import AgentToolDefinition, AgentToolParam
from .tool_execution_result import ToolExecutionResult

logger = get_logger(__name__)

__all__ = ["FileWriteTool"]


class FileWriteTool:
    """Kotlin: ``object FileWriteTool`` — stateless namespace, so Python keeps
    it as a class with only static/class methods (no instantiation).
    """

    NAME = "file_write"

    @staticmethod
    def definition() -> AgentToolDefinition:
        return AgentToolDefinition(
            name=FileWriteTool.NAME,
            description=(
                "Write content to a file on the filesystem. Faster than "
                "shell_execute for writing files. Creates the file if it doesn't "
                "exist. Use append mode to add to existing files."
            ),
            parameters={
                "tool_title": AgentToolParam(
                    "string",
                    "A concise 5-10 word summary of what this tool call does, "
                    "shown to the user (e.g. 'Create Python statistics script', "
                    "'Write configuration file'). Use the same language as the user.",
                ),
                "path": AgentToolParam(
                    "string", "Absolute path to write (e.g. /tmp/test.txt)"
                ),
                "content": AgentToolParam(
                    "string", "The text content to write to the file"
                ),
                "append": AgentToolParam(
                    "boolean",
                    "If true, append to existing file instead of overwriting (default: false)",
                ),
                "create_dirs": AgentToolParam(
                    "boolean",
                    "If true, create parent directories if they don't exist (default: false)",
                ),
            },
            required=["tool_title", "path", "content"],
            property_ordering=["tool_title", "path", "content", "append", "create_dirs"],
        )

    @staticmethod
    def execute(args_json: str, session_id: str) -> ToolExecutionResult:
        """Kotlin: ``execute(argsJson, sessionId)``."""
        try:
            args = json.loads(args_json)
            path = str(args.get("path", ""))
            content = str(args.get("content", ""))
            append = bool(args.get("append", False))
            create_dirs = bool(args.get("create_dirs", False))
            tool_title = str(args.get("tool_title", FileWriteTool.NAME))

            if not path.strip():
                return ToolExecutionResult(
                    "Error: 'path' is required", False, tool_title=tool_title
                )

            # Reuse the file_read resolver to keep the per-session host
            # directory consistent across read/write/edit.
            host = _resolve_session_host_path(session_id, path)
            if host is None:
                return ToolExecutionResult(
                    f"Error: Cannot resolve path: {path}", False, tool_title=tool_title
                )

            # Mirror iOS — auto-create the parent dir whenever it doesn't exist.
            parent = host.parent
            if create_dirs or not parent.exists():
                try:
                    parent.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    return ToolExecutionResult(
                        f"Error: Cannot create parent directory: {e}",
                        False,
                        tool_title=tool_title,
                    )

            # Validate UTF-8 (cheap — str → bytes is always valid in Python 3,
            # but we keep the round-trip to fail loudly on surrogate pairs).
            try:
                content.encode("utf-8")
            except UnicodeEncodeError as e:
                return ToolExecutionResult(
                    f"Error: Content is not valid UTF-8: {e}",
                    False,
                    tool_title=tool_title,
                )

            mode = "ab" if append else "wb"
            with host.open(mode) as fh:
                fh.write(content.encode("utf-8"))

            bytes_written = host.stat().st_size
            return ToolExecutionResult(
                f"Wrote to {path} ({bytes_written} bytes)",
                True,
                tool_title=tool_title,
            )
        except Exception as e:
            logger.exception("file_write failed")
            return ToolExecutionResult(f"Error writing file: {e}", False)


def _resolve_session_host_path(session_id: str, path: str) -> Path | None:
    """T123: per-session resolver — see FileReadTool for rationale.

    Kept as a local helper so file_write + file_edit can be used independently
    of file_read_tool (and so tests can import just one symbol).
    """
    workspace = app_context().external_files_dir
    session_root = workspace / session_id if session_id else workspace

    candidate = path.strip()
    if not candidate:
        return None
    for prefix in ("/var/minis/workspace", "/workspace", "/var/minis"):
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix) :]
            break
    candidate = candidate.lstrip("/") or ""

    resolved = (
        (session_root / candidate).resolve() if candidate else session_root.resolve()
    )
    root = session_root.resolve()
    if resolved != root and root not in resolved.parents:
        logger.warning("file_write rejected path outside session root: %s", path)
        return None
    return resolved
