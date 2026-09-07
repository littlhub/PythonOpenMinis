"""memory_write + memory_get tools.

Ported from: src/android/app/src/main/java/com/openminis/app/tools/MemoryTools.kt
Original package: com.openminis.app.tools

PORT: the Kotlin implementation is paired with a ``MemoryRepository`` that
manages the on-disk ``memory/YYYY-MM-DD.md`` and ``memory/GLOBAL.md`` files.
This port recreates the file layout and the same two operations inline rather
than introducing a separate repository module — keeps the surface small
while preserving the read / write contract the agent uses.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..core.context import app_context
from ..core.logging import get_logger
from ..data.model.agent_tool_definition import AgentToolDefinition, AgentToolParam
from .tool_execution_result import ToolExecutionResult

logger = get_logger(__name__)

__all__ = [
    "MemoryTools",
    "MemoryWriteTool",
    "MemoryGetTool",
    "memory_write_definition",
    "memory_get_definition",
]


#: Maximum characters returned by ``memory_get`` to keep tool results bounded.
#: T-android mirror — 8 KB is enough to surface a daily log without flooding
#: the model.
_MAX_GET_CHARS = 8_000

#: Context lines returned around a keyword match.
_KEYWORD_CONTEXT = 2


# ---------------------------------------------------------------------------
# Tool definitions — keep identical to Kotlin Anthropic schema
# ---------------------------------------------------------------------------
def memory_write_definition() -> AgentToolDefinition:
    return AgentToolDefinition(
        name="memory_write",
        description=(
            "Write a memory entry to today's daily log (YYYY-MM-DD.md). "
            "Memories persist across all sessions. Each entry is prepended "
            "with a timestamp. Save: user preferences, recurring patterns, "
            "key facts, project conventions, reusable knowledge. Avoid saving "
            "passwords, API keys, tokens, or secrets unless the user "
            "explicitly confirms after being warned. Keep entries concise and "
            "general-purpose. GLOBAL.md is read-only (user-maintained via "
            "Settings)."
        ),
        parameters={
            "tool_title": AgentToolParam(
                "string",
                "A concise 5-10 word summary of what this tool call does, "
                "shown to the user (e.g. 'Save user preference for Python', "
                "'Note today's project context'). Use the same language as "
                "the user.",
            ),
            "content": AgentToolParam(
                "string",
                "The memory content to write. Use concise Markdown with a "
                "short heading (## Topic) and context about what was "
                "done/learned.",
            ),
        },
        required=["tool_title", "content"],
        property_ordering=["tool_title", "content"],
    )


def memory_get_definition() -> AgentToolDefinition:
    return AgentToolDefinition(
        name="memory_get",
        description=(
            "Retrieve memories from persistent storage. Supports "
            "keyword-based fuzzy search across memory files. Returns "
            "matching lines with surrounding context. Use this to recall "
            "previous knowledge, user preferences, or past notes."
        ),
        parameters={
            "tool_title": AgentToolParam(
                "string",
                "A concise 5-10 word summary of what this tool call does, "
                "shown to the user (e.g. 'Recall user preferences', "
                "'Search past notes'). Use the same language as the user.",
            ),
            "scope": AgentToolParam(
                "string",
                "Memory scope to search: 'daily' for daily logs only, 'all' "
                "for daily logs + GLOBAL.md.",
                enum_values=["daily", "all"],
            ),
            "keywords": AgentToolParam(
                "string",
                "Space-separated keywords for fuzzy matching (e.g. 'python "
                "preference' or 'API key setup'). All keywords must appear in "
                "a line or its surrounding context for a match. Leave empty "
                "to return full memory files.",
            ),
        },
        required=["tool_title"],
        property_ordering=["tool_title", "scope", "keywords"],
    )


# ---------------------------------------------------------------------------
# Compatibility wrapper so existing build_tool_registry glue still works.
# ---------------------------------------------------------------------------
class MemoryWriteTool:
    NAME = "memory_write"

    @staticmethod
    def definition() -> AgentToolDefinition:
        return memory_write_definition()

    @staticmethod
    def execute(args_json: str, session_id: str) -> ToolExecutionResult:
        return MemoryTools.execute_write(args_json)


class MemoryGetTool:
    NAME = "memory_get"

    @staticmethod
    def definition() -> AgentToolDefinition:
        return memory_get_definition()

    @staticmethod
    def execute(args_json: str, session_id: str) -> ToolExecutionResult:
        return MemoryTools.execute_get(args_json)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _MemoryResult:
    output: str
    success: bool
    tool_title: str = ""


def _memory_dir() -> Path:
    """``memory/`` lives next to the rest of the data dir. Mirrors the
    Android layout — settings.py keeps GLOBAL.md in the same place.
    """
    return app_context().data_dir / "memory"


def _daily_path() -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _memory_dir() / f"{today}.md"


def _global_path() -> Path:
    return _memory_dir() / "GLOBAL.md"


class MemoryTools:
    """Namespace mirroring the Kotlin ``object MemoryTools``."""

    @staticmethod
    def execute_write(args_json: str) -> ToolExecutionResult:
        try:
            args = json.loads(args_json)
            content = str(args.get("content", ""))
            tool_title = str(args.get("tool_title", "memory_write"))
            if not content.strip():
                return ToolExecutionResult(
                    "Error: Missing required 'content' parameter",
                    False,
                    tool_title=tool_title,
                )
            path = _daily_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
            entry = f"\n## {timestamp}\n\n{content.rstrip()}\n"
            # Prepend so the freshest entry is at the top of the file.
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            path.write_text(entry + existing, encoding="utf-8")
            return ToolExecutionResult(
                f"Memory saved to {path.name} ({len(content)} chars)",
                True,
                tool_title=tool_title,
            )
        except Exception as e:
            logger.exception("memory_write failed")
            return ToolExecutionResult(f"Error: {e}", False)

    @staticmethod
    def execute_get(args_json: str) -> ToolExecutionResult:
        try:
            args = json.loads(args_json)
            keywords = str(args.get("keywords", "")).strip()
            scope = str(args.get("scope", "all")).strip().lower()
            tool_title = str(args.get("tool_title", "memory_get"))

            files: list[Path] = [_daily_path()]
            if scope != "daily":
                files.append(_global_path())

            chunks: list[str] = []
            total = 0
            kw_re = _compile_keyword_re(keywords) if keywords else None

            for f in files:
                if not f.exists():
                    continue
                text = f.read_text(encoding="utf-8")
                if kw_re is None:
                    body = text
                else:
                    body = _keyword_filter(text, kw_re)
                if not body.strip():
                    continue
                chunk = f"--- {f.name} ---\n{body.rstrip()}\n"
                if total + len(chunk) > _MAX_GET_CHARS:
                    remaining = _MAX_GET_CHARS - total
                    if remaining <= 0:
                        break
                    chunk = chunk[:remaining] + "\n…(truncated)"
                    chunks.append(chunk)
                    total += len(chunk)
                    break
                chunks.append(chunk)
                total += len(chunk)

            if not chunks:
                if keywords:
                    return ToolExecutionResult(
                        f"No memories matched keywords: {keywords!r}",
                        True,
                        tool_title=tool_title,
                    )
                return ToolExecutionResult(
                    "No memories yet.", True, tool_title=tool_title
                )
            return ToolExecutionResult("\n".join(chunks), True, tool_title=tool_title)
        except Exception as e:
            logger.exception("memory_get failed")
            return ToolExecutionResult(f"Error: {e}", False)


# ---------------------------------------------------------------------------
# Keyword search helpers
# ---------------------------------------------------------------------------
def _compile_keyword_re(keywords: str) -> re.Pattern[str]:
    """All keywords must appear in the same line OR its surrounding context
    window (mirrors the Kotlin fuzzy matcher). Case-insensitive, word-boundary
    free so Chinese lines match too.
    """
    parts = [_escape_re(k) for k in keywords.split() if k.strip()]
    if not parts:
        return re.compile(r"$^")  # matches nothing
    return re.compile("|".join(f"(?=.*?{p})" for p in parts), re.IGNORECASE | re.DOTALL)


def _keyword_filter(text: str, pattern: re.Pattern[str]) -> str:
    """Return the lines that match (and ``_KEYWORD_CONTEXT`` neighbours) so
    short snippets stay readable when the file is large.
    """
    lines = text.splitlines()
    keep = set()
    for i, line in enumerate(lines):
        if pattern.search(line):
            for j in range(max(0, i - _KEYWORD_CONTEXT), min(len(lines), i + _KEYWORD_CONTEXT + 1)):
                keep.add(j)
    return "\n".join(lines[i] for i in sorted(keep))


def _escape_re(s: str) -> str:
    return re.escape(s)
