"""``ls`` tool — list a directory under the workspace.

Ported semantics from ``agent.tools.ls`` (the first-agent toolset copied into
``tools/firstagenttools``): relative paths resolve against the workspace root,
directory entries carry a ``/`` suffix, output is capped. Self-contained port:
no dependency on the external agent framework, one ``tool_title`` + ``path`` +
``limit`` schema in the OpenMinis style.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ..core.logging import get_logger
from ..data.model.agent_tool_definition import AgentToolDefinition, AgentToolParam
from .path_utils import resolve_workspace_path, workspace_root
from .tool_execution_result import ToolExecutionResult

logger = get_logger(__name__)

__all__ = ["LsTool"]

DEFAULT_LIMIT = 500
MAX_LIMIT = 2000


def _fmt_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    for unit in ("KB", "MB", "GB"):
        size /= 1024.0
        if size < 1024:
            return f"{size:.1f} {unit}"
    return f"{size:.1f} TB"


class LsTool:
    """List directory contents (workspace-relative)."""

    NAME = "ls"

    @staticmethod
    def definition() -> AgentToolDefinition:
        return AgentToolDefinition(
            name=LsTool.NAME,
            description=(
                "List a directory under the workspace. Returns entries sorted "
                "alphabetically; directories carry a '/' suffix, files show "
                "their size. Prefer this over running ls/find in shell_execute "
                "when you only need to see what is in a folder."
            ),
            parameters={
                "tool_title": AgentToolParam(
                    "string",
                    "A concise 5-10 word summary of what this tool call does, "
                    "shown to the user (e.g. 'List workspace files'). Use the "
                    "same language as the user.",
                ),
                "path": AgentToolParam(
                    "string",
                    "Directory to list. Relative paths are based on the workspace "
                    "root (default: the workspace root itself). Prefixes like "
                    "/var/minis/workspace/... are accepted.",
                ),
                "limit": AgentToolParam(
                    "integer",
                    f"Maximum number of entries to return (default: {DEFAULT_LIMIT}).",
                ),
            },
            required=["tool_title"],
            property_ordering=["tool_title", "path", "limit"],
        )

    @staticmethod
    async def execute(args_json: str, session_id: str) -> ToolExecutionResult:
        try:
            args = json.loads(args_json)
            tool_title = str(args.get("tool_title", LsTool.NAME))
            limit = max(
                1, min(int(args.get("limit", DEFAULT_LIMIT)), MAX_LIMIT)
            )
        except (ValueError, TypeError):
            return ToolExecutionResult(
                "Error: invalid JSON args", False, tool_title=LsTool.NAME
            )
        target = await asyncio.to_thread(resolve_workspace_path, args.get("path"))
        if target is None:
            return ToolExecutionResult(
                "Error: path escapes the workspace root", False, tool_title=tool_title
            )
        if target.is_file():
            size = target.stat().st_size
            return ToolExecutionResult(
                f"{target.name} ({_fmt_size(size)})\n[1 file]",
                True,
                tool_title=tool_title,
            )
        if not target.is_dir():
            return ToolExecutionResult(
                f"Error: no such directory: {args.get('path') or '.'}",
                False,
                tool_title=tool_title,
            )
        try:
            entries = sorted(target.iterdir(), key=lambda p: p.name.lower())
        except OSError as exc:
            return ToolExecutionResult(f"Error: cannot read directory: {exc}", False,
                                       tool_title=tool_title)

        show = entries[:limit]
        lines: list[str] = []
        for p in show:
            if p.is_dir():
                lines.append(f"{p.name}/")
            else:
                try:
                    size = p.stat().st_size
                except OSError:
                    size = 0
                lines.append(f"{p.name}  ({_fmt_size(size)})")
        abs_root = workspace_root().resolve()
        try:
            rel = target.relative_to(abs_root).as_posix()
        except ValueError:
            rel = str(target)
        if rel in {"", "."}:
            rel = "."
        header = f"[workspace/{rel} · {len(entries)} entries"
        header += f", showing {len(show)}]" if len(show) < len(entries) else "]"
        if len(entries) > len(show):
            lines.append(f"... ({len(entries) - len(show)} more entries; raise limit to see)")
        return ToolExecutionResult("\n".join([header, *lines]), True, tool_title=tool_title)
