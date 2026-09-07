"""file_edit tool.

Ported from: src/android/app/src/main/java/com/openminis/app/tools/FileEditTool.kt
Original package: com.openminis.app.tools
"""

from __future__ import annotations

import json
from pathlib import Path

from ..core.context import app_context
from ..core.logging import get_logger
from ..data.model.agent_tool_definition import AgentToolDefinition, AgentToolParam
from .file_write_tool import _resolve_session_host_path
from .tool_execution_result import ToolExecutionResult

logger = get_logger(__name__)

__all__ = ["FileEditTool"]


class FileEditTool:
    """Kotlin: ``object FileEditTool`` — stateless namespace."""

    NAME = "file_edit"

    @staticmethod
    def definition() -> AgentToolDefinition:
        return AgentToolDefinition(
            name=FileEditTool.NAME,
            description=(
                "Make targeted edits to an existing file using exact string "
                "replacement. ALWAYS use file_read first to see the current file "
                "contents before editing. Prefer file_edit over file_write when "
                "modifying existing files — only the changed part needs to be "
                "specified. The old_string must match exactly one location in "
                "the file (including whitespace/indentation), unless replace_all "
                "is true."
            ),
            parameters={
                "tool_title": AgentToolParam(
                    "string",
                    "A concise 5-10 word summary of what this tool call does, "
                    "shown to the user (e.g. 'Fix typo in Python script', "
                    "'Update config value'). Use the same language as the user.",
                ),
                "path": AgentToolParam(
                    "string",
                    "Absolute path to the file to edit (e.g. /tmp/script.py)",
                ),
                "old_string": AgentToolParam(
                    "string",
                    "The exact text to find in the file. Must match precisely "
                    "including whitespace and indentation. Must be unique in the "
                    "file unless replace_all is true.",
                ),
                "new_string": AgentToolParam(
                    "string",
                    "The replacement text. Use empty string to delete old_string.",
                ),
                "replace_all": AgentToolParam(
                    "boolean",
                    "If true, replace ALL occurrences of old_string (default: false)",
                ),
            },
            required=["tool_title", "path", "old_string", "new_string"],
            property_ordering=[
                "tool_title",
                "path",
                "old_string",
                "new_string",
                "replace_all",
            ],
        )

    @staticmethod
    def execute(args_json: str, session_id: str) -> ToolExecutionResult:
        """Kotlin: ``execute(argsJson, sessionId)``."""
        try:
            args = json.loads(args_json)
            path = str(args.get("path", ""))
            old_string = str(args.get("old_string", ""))
            new_string = str(args.get("new_string", ""))
            replace_all = bool(args.get("replace_all", False))
            tool_title = str(args.get("tool_title", FileEditTool.NAME))

            if not path.strip():
                return ToolExecutionResult(
                    "Error: 'path' is required", False, tool_title=tool_title
                )
            if not old_string:
                return ToolExecutionResult(
                    "Error: 'old_string' is required and cannot be empty",
                    False,
                    tool_title=tool_title,
                )

            host = _resolve_session_host_path(session_id, path)
            if host is None:
                return ToolExecutionResult(
                    f"Error: Cannot resolve path: {path}", False, tool_title=tool_title
                )
            if not host.exists():
                return ToolExecutionResult(
                    f"Error: File not found: {path}", False, tool_title=tool_title
                )

            try:
                content = host.read_text(encoding="utf-8")
            except UnicodeDecodeError as e:
                return ToolExecutionResult(
                    f"Error: File is not valid UTF-8: {e}",
                    False,
                    tool_title=tool_title,
                )

            # Count occurrences (Python 3.8+ has str.count but a manual loop
            # matches the Kotlin implementation exactly, and lets us bail with
            # the same boundary conditions).
            count = content.count(old_string)
            if count == 0:
                return ToolExecutionResult(
                    f"Error: old_string not found in {path}",
                    False,
                    tool_title=tool_title,
                )
            if count > 1 and not replace_all:
                return ToolExecutionResult(
                    f"Error: old_string found {count} times in {path}. "
                    "Use replace_all=true to replace all occurrences, or provide "
                    "a more specific old_string that matches exactly once.",
                    False,
                    tool_title=tool_title,
                )

            new_content = (
                content.replace(old_string, new_string)
                if replace_all
                else content.replace(old_string, new_string, 1)
            )
            host.write_text(new_content, encoding="utf-8")
            replacements = count if replace_all else 1
            return ToolExecutionResult(
                f"Edited {path} ({replacements} replacement(s), {len(new_content)} bytes)",
                True,
                tool_title=tool_title,
            )
        except Exception as e:
            logger.exception("file_edit failed")
            return ToolExecutionResult(f"Error editing file: {e}", False)
