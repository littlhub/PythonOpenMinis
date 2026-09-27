"""Tool visibility rules — allow/deny name sets for the built-in tool surface.

Ported from: packages/core/src/tool/tool-visibility.ts
Original package: @zcode/core (tool)
"""

from __future__ import annotations

from typing import Optional, Sequence

__all__ = [
    "normalize_tool_name_alias",
    "create_tool_rule_name_set",
    "is_tool_name_disallowed",
    "filter_disallowed_tool_names",
]


def normalize_tool_name_alias(tool_name: str) -> str:
    """Mirrors ``normalizeToolNameAlias(toolName)``."""
    return "WebSearch" if tool_name == "web_search" else tool_name


def _get_tool_rule_name(rule: str) -> str:
    """Mirrors the non-exported ``getToolRuleName(rule)``."""
    trimmed = rule.strip()
    paren_index = trimmed.find("(")
    # PORT: TS ``parenIndex > 0``（必须是**正**索引）——``"("`` 开头的规则不取前缀。
    raw_name = trimmed[:paren_index] if paren_index > 0 else trimmed
    return normalize_tool_name_alias(raw_name)


def create_tool_rule_name_set(
    rules: Optional[Sequence[str]],
) -> Optional[set[str]]:
    """Mirrors ``createToolRuleNameSet(rules)``."""
    # PORT: TS ``!rules || rules.length === 0`` —— 空数组与缺席等价。
    if not rules:
        return None
    names: set[str] = set()
    for rule in rules:
        name = _get_tool_rule_name(rule)
        if name:
            names.add(name)
    return names if len(names) > 0 else None


def is_tool_name_disallowed(
    tool_name: str,
    disallowed_tools: Optional[Sequence[str]],
) -> bool:
    """Mirrors ``isToolNameDisallowed(toolName, disallowedTools)``."""
    disallowed = create_tool_rule_name_set(disallowed_tools)
    if disallowed is None:
        return False
    return normalize_tool_name_alias(tool_name) in disallowed


def filter_disallowed_tool_names(
    tool_names: Sequence[str],
    disallowed_tools: Optional[Sequence[str]],
) -> list[str]:
    """Mirrors ``filterDisallowedToolNames(toolNames, disallowedTools)``."""
    disallowed = create_tool_rule_name_set(disallowed_tools)
    if disallowed is None:
        return list(tool_names)
    return [
        tool_name
        for tool_name in tool_names
        if normalize_tool_name_alias(tool_name) not in disallowed
    ]
