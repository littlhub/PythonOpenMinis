"""Tool name compatibility — subagent dispatch aliases.

Ported from: packages/core/src/tool/compat.ts
Original package: @zcode/core (tool)
"""

from __future__ import annotations

__all__ = [
    "AGENT_TOOL_NAME",
    "TASK_TOOL_NAME",
    "is_subagent_dispatch_tool_name",
    "hook_matcher_tool_names_for_tool",
]

AGENT_TOOL_NAME = "Agent"
TASK_TOOL_NAME = "Task"

subagent_dispatch_tool_names = frozenset([AGENT_TOOL_NAME, TASK_TOOL_NAME])

#: PORT: upstream ``Map<string, readonly string[]>`` → ``dict[str, list[str]]``.
hook_matcher_aliases_by_tool_name: dict[str, list[str]] = {
    AGENT_TOOL_NAME: [TASK_TOOL_NAME],
    TASK_TOOL_NAME: [AGENT_TOOL_NAME],
    "ApplyPatch": ["Write", "Edit"],
}


def is_subagent_dispatch_tool_name(tool_name: str | None) -> bool:
    return tool_name in subagent_dispatch_tool_names if tool_name else False


def hook_matcher_tool_names_for_tool(tool_name: str) -> list[str]:
    aliases = hook_matcher_aliases_by_tool_name.get(tool_name) or []
    if len(aliases) == 0:
        return [tool_name]

    values: set[str] = {tool_name}
    for alias in aliases:
        values.add(alias)
    return list(values)
