"""Tool scheduler — tool execution scheduling.

Ported from: packages/core/src/tool/scheduler.ts
Original package: @zcode/core (tool)
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

from ..contracts.errors import CoreErrorType, create_core_error
from ..contracts.ids import ToolCallId
from ..contracts.model import ModelToolSideEffectScope

__all__ = [
    "ToolSchedule",
    "ToolScheduleItem",
    "ToolDependency",
    "ToolScheduler",
    "READ_ONLY_TOOLS",
    "defaultToolScheduler",
]


# -----------------------------------------------
# Types
# -----------------------------------------------

@dataclass(slots=True)
class ToolSchedule:
    """Mirrors ``interface ToolSchedule``."""

    items: list[ToolScheduleItem]
    parallel_groups: list[list[ToolCallId]]
    execution_order: list[ToolCallId]


@dataclass(slots=True)
class ToolScheduleItem:
    """Mirrors ``interface ToolScheduleItem``.

    # PORT: required fields hoisted above the optionals for dataclass ordering.
    """

    tool_call_id: ToolCallId
    dependencies: list[ToolCallId]
    can_run_parallel: bool
    tool_name: Optional[str] = None
    read_only: Optional[bool] = None
    destructive: Optional[bool] = None
    concurrent_safe: Optional[bool] = None
    side_effect_scope: Optional[ModelToolSideEffectScope] = None


@dataclass(slots=True)
class ToolDependency:
    """Mirrors ``interface ToolDependency``.

    # PORT: required fields hoisted above the optionals for dataclass ordering.
    """

    tool_call_id: ToolCallId
    depends_on: list[ToolCallId]
    tool_name: Optional[str] = None
    read_only: Optional[bool] = None
    destructive: Optional[bool] = None
    concurrent_safe: Optional[bool] = None
    side_effect_scope: Optional[ModelToolSideEffectScope] = None


# -----------------------------------------------
# Scheduler
# -----------------------------------------------

@dataclass(slots=True)
class ToolSchedulerOptions:
    """Mirrors non-exported ``interface ToolSchedulerOptions``."""

    max_concurrency: Optional[int] = None
    read_only_tools: Optional[set[str]] = None


# PORT: non-exported in the TS module; kept module-level (not in ``__all__``).
DEFAULT_MAX_CONCURRENCY = 10


class ToolScheduler:
    """Mirrors ``class ToolScheduler``."""

    __slots__ = ("_items_map", "_max_concurrency", "_read_only_tools")

    def __init__(self, options: Optional[ToolSchedulerOptions] = None) -> None:
        if options is None:
            options = ToolSchedulerOptions()
        self._max_concurrency = (
            options.max_concurrency
            if options.max_concurrency is not None
            else DEFAULT_MAX_CONCURRENCY
        )
        self._read_only_tools = (
            options.read_only_tools
            if options.read_only_tools is not None
            else READ_ONLY_TOOLS
        )
        self._items_map: dict[ToolCallId, ToolScheduleItem] = {}

    def schedule(self, tools: list[ToolDependency]) -> ToolSchedule:
        """Mirrors ``schedule(tools)``."""
        items: list[ToolScheduleItem] = []
        for tool in tools:
            # PORT: ``tool.readOnly ?? (tool.toolName ? this.readOnlyTools.has(tool.toolName) : undefined)``.
            read_only = (
                tool.read_only
                if tool.read_only is not None
                else (
                    (tool.tool_name in self._read_only_tools) if tool.tool_name else None
                )
            )
            items.append(
                ToolScheduleItem(
                    tool_call_id=tool.tool_call_id,
                    tool_name=tool.tool_name,
                    dependencies=tool.depends_on,
                    can_run_parallel=self._can_run_in_parallel(tool),
                    read_only=read_only,
                    destructive=tool.destructive,
                    concurrent_safe=tool.concurrent_safe,
                    side_effect_scope=tool.side_effect_scope,
                )
            )

        self._items_map = {item.tool_call_id: item for item in items}

        sorted_items = self._topological_sort(items)
        groups = self._group_by_parallel(sorted_items)
        self._validate_no_cycles(groups)

        return ToolSchedule(
            items=items,
            parallel_groups=groups,
            execution_order=[tool_id for group in groups for tool_id in group],
        )

    def _can_run_in_parallel(self, tool: ToolDependency) -> bool:
        """Mirrors ``canRunInParallel(tool)``."""
        has_tool_name = isinstance(tool.tool_name, str) and len(tool.tool_name) > 0
        has_safety_metadata = (
            tool.read_only is not None
            or tool.destructive is not None
            or tool.concurrent_safe is not None
            or tool.side_effect_scope is not None
        )

        if not has_tool_name and not has_safety_metadata:
            return True

        read_only = (
            tool.read_only
            if tool.read_only is not None
            else ((tool.tool_name in self._read_only_tools) if has_tool_name else False)
        )
        if tool.destructive:
            return False
        if tool.concurrent_safe is True:
            return True
        if tool.concurrent_safe is False:
            return False
        if read_only:
            return True
        return tool.side_effect_scope == "none"

    def _topological_sort(self, items: list[ToolScheduleItem]) -> list[ToolScheduleItem]:
        """Mirrors ``topologicalSort(items)``."""
        result: list[ToolScheduleItem] = []
        remaining: dict[ToolCallId, ToolScheduleItem] = {
            item.tool_call_id: item for item in items
        }
        in_degree: dict[str, int] = {}

        for item in items:
            in_degree[item.tool_call_id] = len(item.dependencies)

        # PORT: TS ``queue.shift()`` (FIFO pop from the front) → ``deque.popleft()``.
        queue: deque[ToolCallId] = deque(
            item.tool_call_id for item in items if len(item.dependencies) == 0
        )

        while len(queue) > 0:
            current = queue.popleft()
            item = remaining.get(current)

            if item is None:
                continue

            result.append(item)
            del remaining[current]

            for tool_id, dep_item in remaining.items():
                if current in dep_item.dependencies:
                    new_degree = in_degree.get(tool_id, 0) - 1
                    in_degree[tool_id] = new_degree
                    if new_degree == 0:
                        queue.append(tool_id)

        if len(remaining) > 0:
            raise create_core_error(
                CoreErrorType.INVALID_STATE_TRANSITION,
                "Circular dependency detected in tool scheduling",
                {
                    "context": {
                        "remaining": list(remaining.keys()),
                    },
                    "recoverable": False,
                },
            )

        return result

    def _group_by_parallel(self, items: list[ToolScheduleItem]) -> list[list[ToolCallId]]:
        """Mirrors ``groupByParallel(items)``."""
        if len(items) == 0:
            return []

        levels: dict[ToolCallId, int] = {}
        by_level: dict[int, list[ToolScheduleItem]] = {}

        for item in items:
            dependency_level = (
                -1
                if len(item.dependencies) == 0
                else max(
                    (levels.get(dep) if levels.get(dep) is not None else 0)
                    for dep in item.dependencies
                )
            )
            level = dependency_level + 1
            levels[item.tool_call_id] = level
            level_items = by_level.get(level, [])
            level_items.append(item)
            by_level[level] = level_items

        groups: list[list[ToolCallId]] = []
        sorted_levels = sorted(by_level.keys())

        for level in sorted_levels:
            parallel_group: list[ToolCallId] = []

            def flush_parallel_group() -> None:
                nonlocal parallel_group
                if len(parallel_group) == 0:
                    return
                groups.append(parallel_group)
                parallel_group = []

            for item in by_level.get(level, []):
                if item.can_run_parallel:
                    # Flush if current group is full
                    if len(parallel_group) >= self._max_concurrency:
                        flush_parallel_group()
                    parallel_group.append(item.tool_call_id)
                    continue

                flush_parallel_group()
                groups.append([item.tool_call_id])

            flush_parallel_group()

        return groups

    def _validate_no_cycles(self, groups: list[list[ToolCallId]]) -> None:
        """Mirrors ``validateNoCycles(groups)``."""
        for group in groups:
            group_set = set(group)

            for tool_id in group:
                item = self._items_map.get(tool_id)
                if item is None:
                    continue

                for dep in item.dependencies:
                    if dep in group_set:
                        raise create_core_error(
                            CoreErrorType.INVALID_STATE_TRANSITION,
                            f"Circular dependency detected: {tool_id} depends on {dep} in same group",
                            {
                                "context": {"toolId": tool_id, "dependency": dep, "group": group},
                                "recoverable": False,
                            },
                        )


# -----------------------------------------------
# Default instance
# -----------------------------------------------

READ_ONLY_TOOLS: set[str] = {
    "Read",
    "Glob",
    "Grep",
    "WebSearch",
    "WebFetch",
    "TodoRead",
    "TodoWrite",
    "AskUserQuestion",
    "Skill",
}

defaultToolScheduler = ToolScheduler()
