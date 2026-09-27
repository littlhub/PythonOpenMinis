"""TaskStop tool — stop a running background task by ID.

Ported from: packages/core/src/tool/handlers/task-stop.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

import json
from typing import Any

from ...contracts.errors import CoreErrorType, create_core_error
from ..types import ToolEntry
from .ports import (
    TASK_STOP_INPUT_JSON_SCHEMA,
    TASK_STOP_INPUT_SCHEMA,
    TASK_STOP_OUTPUT_JSON_SCHEMA,
    TASK_STOP_OUTPUT_SCHEMA,
    TASK_STOP_TOOL_NAME,
)

MAX_TASK_STOP_MODEL_BYTES = 100_000

# provider 请求直接使用 metadata.description，内部短说明会让模型看不到
# 参数、返回值和使用时机。capability 继续保留短说明。
TASK_STOP_PROVIDER_DESCRIPTION = "\n".join(
    [
        "",
        "- Stops a running background task by its ID",
        "- Takes a task_id parameter identifying the task to stop",
        "- Returns a success or failure status",
        "- Use this tool when you need to terminate a long-running task",
        "",
    ]
)


async def task_stop_handler(input: Any, context: Any) -> Any:
    """Mirrors ``taskStopHandler: ToolHandler``."""
    parsed = input if isinstance(input, dict) else {}
    task_id = parsed.get("task_id") or parsed.get("shell_id")
    if not task_id:
        raise _task_stop_error(
            "Missing required parameter: task_id", 1, {"toolCallId": context.tool_call_id}
        )

    if not context.background_task_control_port:
        raise create_core_error(
            CoreErrorType.CONFIGURATION_ERROR,
            "Background task control is not configured for TaskStop",
            {
                "context": {"toolCallId": context.tool_call_id, "toolName": TASK_STOP_TOOL_NAME},
                "recoverable": False,
            },
        )

    result = await context.background_task_control_port.stop_background_task(
        task_id,
        {
            # 模型自己停的：终态通知会说「stopped by you」，而不是把它写成用户的决定。
            "initiator": "model",
            "strict": True,
            "traceContext": context.trace_context,
        },
    )
    if not result.ok:
        if result.reason == "background_task_not_running":
            raise _task_stop_error(
                f"Task {task_id} is not running (status: {result.status or 'unknown'})",
                3,
                {
                    "status": result.status,
                    "taskId": task_id,
                    "taskType": result.type,
                    "toolCallId": context.tool_call_id,
                },
            )
        if result.reason == "background_task_cancel_not_supported":
            raise _task_stop_error(
                f"Task {task_id} cannot be stopped",
                1,
                {
                    "reason": result.reason,
                    "status": result.status,
                    "taskId": task_id,
                    "taskType": result.type,
                    "toolCallId": context.tool_call_id,
                },
            )
        raise _task_stop_error(
            f"No task found with ID: {task_id}",
            1,
            {
                "reason": result.reason,
                "taskId": task_id,
                "taskType": result.type,
                "toolCallId": context.tool_call_id,
            },
        )

    task_type = result.type or "background_task"
    output: dict[str, Any] = {
        "message": f"Successfully stopped task: {result.task_id} ({result.command or task_type})",
        "task_id": result.task_id,
        "task_type": task_type,
    }
    if result.command:
        output["command"] = result.command
    return output


def format_task_stop_model_content(output: Any) -> str:
    """Mirrors ``formatTaskStopModelContent(output)``."""
    return json.dumps(output)


def _task_stop_error(message: str, code: int, context: dict[str, Any]) -> Exception:
    return create_core_error(
        CoreErrorType.TOOL_EXECUTION_FAILED,
        message,
        {
            "context": {**context, "code": code, "toolName": TASK_STOP_TOOL_NAME},
            "recoverable": True,
        },
    )


def _build_task_stop_tool_entry() -> ToolEntry:
    return ToolEntry(
        aliases=["KillShell", "KillBash"],
        capability="Stop a running background task by ID",
        metadata={
            "name": TASK_STOP_TOOL_NAME,
            "description": TASK_STOP_PROVIDER_DESCRIPTION,
            "read_only": False,
            "destructive": False,
            "concurrent_safe": True,
            "timeout_ms": 10000,
            "max_output_bytes": MAX_TASK_STOP_MODEL_BYTES,
            "side_effect_scope": "session",
            "risk_level": "low",
            "needs_approval": False,
        },
        handler=task_stop_handler,
        format_model_content=format_task_stop_model_content,
        input_schema=TASK_STOP_INPUT_JSON_SCHEMA,
        output_schema=TASK_STOP_OUTPUT_JSON_SCHEMA,
        runtime_input_schema=TASK_STOP_INPUT_SCHEMA,
        runtime_output_schema=TASK_STOP_OUTPUT_SCHEMA,
        permission={
            "permission": "backgroundTask.stop",
            "reason": "TaskStop stops a running background task in the current runtime",
            "risk_level": "low",
            "side_effect_scope": "session",
            "needs_approval": False,
            "pattern_sources": ["toolName", "input"],
            "always_allow_pattern_sources": ["toolName"],
            "deny_priority": "beforeAsk",
        },
        result_budget={
            "max_inline_bytes": MAX_TASK_STOP_MODEL_BYTES,
            "max_model_bytes": MAX_TASK_STOP_MODEL_BYTES,
            "strategy": "truncate",
            "preview": {"max_bytes": MAX_TASK_STOP_MODEL_BYTES, "direction": "head"},
        },
        timeout={"default_ms": 10000, "max_ms": 10000, "allow_call_override": False},
        cancellation={
            "supported": True,
            "cleanup": "none",
            "user_visible_message": "TaskStop was cancelled before the stop request completed",
        },
        trace={
            "propagate_to_adapters": False,
            "record_input": "summary",
            "record_output": "summary",
        },
    )


task_stop_tool_entry: ToolEntry = _build_task_stop_tool_entry()
