"""Todo tool handlers — todo read/write.

Ported from: packages/core/src/tool/handlers/todo.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

from typing import Any, Optional

from ...contracts.errors import CoreErrorType, create_core_error
from ..types import ToolEntry, ToolHandler
from .ports import (
    TODO_READ_INPUT_JSON_SCHEMA,
    TODO_READ_INPUT_SCHEMA,
    TODO_READ_OUTPUT_JSON_SCHEMA,
    TODO_READ_OUTPUT_SCHEMA,
    TODO_WRITE_INPUT_JSON_SCHEMA,
    TODO_WRITE_INPUT_SCHEMA,
    TODO_WRITE_OUTPUT_JSON_SCHEMA,
    TODO_WRITE_OUTPUT_SCHEMA,
    TodoItem,
)

__all__ = [
    "todo_read_handler",
    "todo_write_handler",
    "todo_read_tool_entry",
    "todo_write_tool_entry",
    "summarize_todos",
]

# ---------------------------------------------------------------------------
# Constants (mirrors todo.ts module-level declarations)
# ---------------------------------------------------------------------------

MAX_TODO_MODEL_BYTES = 100_000

TODO_READ_TOOL_NAME = "TodoRead"
TODO_WRITE_TOOL_NAME = "TodoWrite"

TODO_READ_TOOL_DESCRIPTION = "Read the current session todo list"

TODO_WRITE_TOOL_DESCRIPTION = (
    "Create and update a task list for the current session. The list is rendered to the user as your working plan.\n"  # noqa: E501
    "\n"
    "- Each todo has `content`, `status` (\"pending\" | \"in_progress\" | \"completed\"), and `priority` (\"high\" | \"medium\" | \"low\").\n"  # noqa: E501
    "- Send the full list each call; it replaces the previous one.\n"
    "- Keep one item `in_progress` at a time and mark it `completed` when done."
)


async def todo_read_handler(input: Any, context: Any) -> Any:
    """Mirrors ``todoReadHandler: ToolHandler``."""
    # PORT: ``TodoReadInputSchema.parse(input)`` —— executor 已归一化，透传。
    if not context.session_store:
        raise create_core_error(
            CoreErrorType.CONFIGURATION_ERROR,
            "SessionStorePort is not configured for TodoRead",
            {
                "context": {
                    "tool_call_id": context.tool_call_id,
                    "tool_name": "TodoRead",
                },
                "recoverable": False,
            },
        )

    return {
        "todos": await context.session_store.read_todos({"session_id": context.session_id}),
    }


async def todo_write_handler(input: Any, context: Any) -> Any:
    """Mirrors ``todoWriteHandler: ToolHandler``."""
    # PORT: ``TodoWriteInputSchema.parse(input)`` —— executor 已归一化，透传。
    parsed: dict[str, Any] = input
    todos = parsed.get("todos") or []

    if not context.session_store:
        raise create_core_error(
            CoreErrorType.CONFIGURATION_ERROR,
            "SessionStorePort is not configured for TodoWrite",
            {
                "context": {
                    "tool_call_id": context.tool_call_id,
                    "tool_name": "TodoWrite",
                },
                "recoverable": False,
            },
        )

    old_todos = await context.session_store.read_todos({"session_id": context.session_id})
    await context.session_store.update_todos({"session_id": context.session_id, "todos": todos})

    return {
        "old_todos": old_todos,
        "todos": todos,
        "summary": summarize_todos(todos),
    }


def summarize_todos(todos: list[TodoItem]) -> dict[str, int]:
    """Mirrors ``summarizeTodos(todos)``."""
    return {
        "total": len(todos),
        "pending": sum(1 for todo in todos if todo.get("status") == "pending"),
        "in_progress": sum(1 for todo in todos if todo.get("status") == "in_progress"),
        "completed": sum(1 for todo in todos if todo.get("status") == "completed"),
    }


# ---------------------------------------------------------------------------
# ToolEntry 构造
# ---------------------------------------------------------------------------

def _todo_read_permission() -> Any:
    from ...contracts.tools import ToolPermissionSpec

    return ToolPermissionSpec(
        permission="todo.read",
        reason="TodoRead only reads session-local task state",
        risk_level="low",
        side_effect_scope="none",
        needs_approval=False,
        pattern_sources=["tool_name"],
        always_allow_pattern_sources=["tool_name"],
        deny_priority="beforeAsk",
    )


def _todo_write_permission() -> Any:
    from ...contracts.tools import ToolPermissionSpec

    return ToolPermissionSpec(
        permission="todo.write",
        reason="TodoWrite only updates session-local task state for progress tracking",
        risk_level="low",
        side_effect_scope="session",
        needs_approval=False,
        pattern_sources=["tool_name", "input"],
        always_allow_pattern_sources=["tool_name"],
        deny_priority="beforeAsk",
    )


def _todo_read_result_budget() -> Any:
    from ...contracts.tools import ToolResultBudget, ToolResultBudgetPreview

    return ToolResultBudget(
        max_inline_bytes=MAX_TODO_MODEL_BYTES,
        max_model_bytes=MAX_TODO_MODEL_BYTES,
        strategy="truncate",
        preview=ToolResultBudgetPreview(max_bytes=MAX_TODO_MODEL_BYTES, direction="head"),
    )


def _todo_write_result_budget() -> Any:
    from ...contracts.tools import ToolResultBudget, ToolResultBudgetPreview

    return ToolResultBudget(
        max_inline_bytes=MAX_TODO_MODEL_BYTES,
        max_model_bytes=MAX_TODO_MODEL_BYTES,
        strategy="truncate",
        preview=ToolResultBudgetPreview(max_bytes=MAX_TODO_MODEL_BYTES, direction="head"),
    )


def _todo_timeout() -> Any:
    from ...contracts.tools import TimedToolTimeoutPolicy

    return TimedToolTimeoutPolicy(default_ms=30000, max_ms=30000, allow_call_override=False)


def _todo_trace() -> Any:
    from ...contracts.tools import ToolTracePolicy

    return ToolTracePolicy(
        propagate_to_adapters=False,
        record_input="summary",
        record_output="summary",
    )


def _build_todo_read_tool_entry() -> ToolEntry:
    from ...contracts.tools import ToolCancellationPolicy

    return ToolEntry(
        capability="Read the current session todo list without modifying external state",
        metadata={
            "name": TODO_READ_TOOL_NAME,
            "description": TODO_READ_TOOL_DESCRIPTION,
            "read_only": True,
            "destructive": False,
            "concurrent_safe": True,
            "timeout_ms": 30000,
            "max_output_bytes": MAX_TODO_MODEL_BYTES,
            "side_effect_scope": "none",
            "risk_level": "low",
            "needs_approval": False,
        },
        handler=todo_read_handler,
        input_schema=TODO_READ_INPUT_JSON_SCHEMA,
        output_schema=TODO_READ_OUTPUT_JSON_SCHEMA,
        runtime_input_schema=TODO_READ_INPUT_SCHEMA,
        runtime_output_schema=TODO_READ_OUTPUT_SCHEMA,
        permission=_todo_read_permission(),
        result_budget=_todo_read_result_budget(),
        timeout=_todo_timeout(),
        cancellation=ToolCancellationPolicy(
            supported=True,
            cleanup="none",
            user_visible_message="TodoRead was cancelled before todo state was returned",
        ),
        trace=_todo_trace(),
    )


def _build_todo_write_tool_entry() -> ToolEntry:
    from ...contracts.tools import ToolCancellationPolicy

    return ToolEntry(
        capability="Replace the current session todo list to track multi-step task progress and resume state",
        metadata={
            "name": TODO_WRITE_TOOL_NAME,
            "description": TODO_WRITE_TOOL_DESCRIPTION,
            "read_only": True,
            "destructive": False,
            "concurrent_safe": False,
            "timeout_ms": 30000,
            "max_output_bytes": MAX_TODO_MODEL_BYTES,
            "side_effect_scope": "session",
            "risk_level": "low",
            "needs_approval": False,
        },
        handler=todo_write_handler,
        input_schema=TODO_WRITE_INPUT_JSON_SCHEMA,
        output_schema=TODO_WRITE_OUTPUT_JSON_SCHEMA,
        runtime_input_schema=TODO_WRITE_INPUT_SCHEMA,
        runtime_output_schema=TODO_WRITE_OUTPUT_SCHEMA,
        permission=_todo_write_permission(),
        result_budget=_todo_write_result_budget(),
        timeout=_todo_timeout(),
        cancellation=ToolCancellationPolicy(
            supported=True,
            cleanup="none",
            user_visible_message="TodoWrite was cancelled before todo state was updated",
        ),
        trace=_todo_trace(),
    )


todo_read_tool_entry: ToolEntry = _build_todo_read_tool_entry()
todo_write_tool_entry: ToolEntry = _build_todo_write_tool_entry()
