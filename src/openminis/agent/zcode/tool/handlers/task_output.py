"""Task output tool handler — read output/logs from a background task.

Ported from: packages/core/src/tool/handlers/task-output.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

import asyncio
import os
import time as _time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from ...contracts.errors import CoreErrorType, create_core_error
from ...contracts.events import SessionEventType
from ..result_persistence_format import (
    PersistedOutputEnvelopeInput,
    format_persisted_output_envelope,
)
from ..types import (
    ToolEntry,
    ToolHandler,
    ToolHandlerFailure,
    ToolInputValidationContext,
    ToolPersistedModelContentInput,
)
from .ports import (
    TASK_OUTPUT_ALIASES,
    TASK_OUTPUT_INPUT_JSON_SCHEMA,
    TASK_OUTPUT_INPUT_SCHEMA,
    TASK_OUTPUT_PROVIDER_DESCRIPTION,
    TASK_OUTPUT_RESULT_JSON_SCHEMA,
    TASK_OUTPUT_RESULT_SCHEMA,
    TASK_OUTPUT_TOOL_NAME,
    TaskOutputResult,
    TaskOutputTask,
)
from .task_output_projection import format_compact_file_size, project_task

__all__ = [
    "task_output_handler",
    "task_output_tool_entry",
    "task_output_failure",
    "validate_task_output_input",
    "format_task_output_model_content",
]

# ---------------------------------------------------------------------------
# Constants (mirrors task-output.ts module-level declarations)
# ---------------------------------------------------------------------------

TASK_OUTPUT_DEFAULT_LENGTH = 32_000
TASK_OUTPUT_MAX_LENGTH = 160_000
TASK_OUTPUT_PERSIST_THRESHOLD_CHARS = 100_000
TASK_OUTPUT_RESULT_BUDGET_BYTES = 400_000
TASK_OUTPUT_PERSIST_PREVIEW_CHARS = 2_000
TASK_OUTPUT_POLL_INTERVAL_MS = 100
TASK_OUTPUT_ERROR_CODE = {
    "TASK_ID_REQUIRED": 1,
    "TASK_NOT_FOUND": 2,
}


async def task_output_handler(input: Any, context: Any) -> Any:
    """Mirrors ``taskOutputHandler: ToolHandler``."""
    parsed: dict[str, Any] = input
    input_failure = _get_task_output_input_failure(parsed, context.runtime_task_registry)
    if input_failure:
        return input_failure

    registry = _require_runtime_task_registry(context)

    initial_task = registry.get(parsed["task_id"])
    if not initial_task:
        return task_output_failure(
            TASK_OUTPUT_ERROR_CODE["TASK_NOT_FOUND"],
            f"No task found with ID: {parsed['task_id']}",
        )

    if not parsed.get("block"):
        if _is_task_active(_snapshot_get(initial_task, "status")):
            return task_output_result("not_ready", await project_task(initial_task, context))
        projected_task = await project_task(initial_task, context)
        # notified 是完成结果已成功交付的 claim；投影前写入会在读取失败或 abort 时吞掉后续
        # completion notification。异步投影不会被外层取消竞态强制停止，因此 await 返回后必须
        # 再次检查 signal，再提交 claim。
        context.abort_signal.throw_if_aborted()
        _mark_task_notified(initial_task, context)
        return task_output_result("success", projected_task)

    await _emit_waiting_progress(context)
    task = await _wait_for_task(parsed["task_id"], parsed.get("timeout", 0), context)
    if not task:
        return task_output_result("timeout", None)
    if _is_task_active(_snapshot_get(task, "status")):
        return task_output_result("timeout", await project_task(task, context))
    projected_task = await project_task(task, context)
    context.abort_signal.throw_if_aborted()
    _mark_task_notified(task, context)
    return task_output_result("success", projected_task)




def task_output_failure(error_code: int, message: str) -> ToolHandlerFailure:
    """Mirrors ``taskOutputFailure(errorCode, message)``."""
    return ToolHandlerFailure(error_code=error_code, message=message)


def validate_task_output_input(
    input: Any,
    context: ToolInputValidationContext,
) -> Any:
    """Mirrors ``validateTaskOutputInput(input, context)``."""
    parsed: dict[str, Any] = input
    failure = _get_task_output_input_failure(parsed, context.runtime_task_registry)
    return failure if failure is not None else {"result": True}


def _get_task_output_input_failure(
    input: dict[str, Any],
    registry: Any,
) -> Optional[ToolHandlerFailure]:
    """Mirrors ``getTaskOutputInputFailure(input, registry)``."""
    if not input.get("task_id"):
        return task_output_failure(TASK_OUTPUT_ERROR_CODE["TASK_ID_REQUIRED"], "Task ID is required")
    if registry is not None and registry.get(input["task_id"]) is None:
        return task_output_failure(
            TASK_OUTPUT_ERROR_CODE["TASK_NOT_FOUND"],
            f"No task found with ID: {input['task_id']}",
        )
    return None


def format_task_output_model_content(output: Any) -> str:
    """Mirrors ``formatTaskOutputModelContent(output)``."""
    parsed: TaskOutputResult = output
    blocks = [f"<retrieval_status>{parsed.retrieval_status}</retrieval_status>"]
    task: Optional[TaskOutputTask] = parsed.task
    if task is not None:
        blocks.append(f"<task_id>{task.task_id}</task_id>")
        blocks.append(f"<task_type>{task.task_type}</task_type>")
        blocks.append(f"<status>{task.status}</status>")
        if task.exit_code is not None:
            blocks.append(f"<exit_code>{task.exit_code}</exit_code>")
        if task.output and task.output.strip():
            # 没有真实完整文件时，task_id 不是可读取路径，不能把它伪装成“Full output”；
            # 此时保留原文，由外层的大结果 artifact 机制继续处理。
            content = (
                truncate_task_output(task.output, task.output_file)
                if task.output_file
                else task.output
            ).rstrip()
            blocks.append(f"<output>\n{content}\n</output>")
        if task.error:
            blocks.append(f"<error>{task.error}</error>")
    return "\n\n".join(blocks)


def truncate_task_output(
    output: str,
    output_path: str,
    configured_value: Optional[str] = None,
) -> str:
    """Mirrors ``truncateTaskOutput(output, outputPath, configuredValue?)``."""
    max_length = resolve_task_output_length(configured_value)
    if len(output) <= max_length:
        return output

    prefix = f"[Truncated. Full output: {output_path}]\n\n"
    tail_length = max_length - len(prefix)
    return prefix + output[-tail_length:]


def resolve_task_output_length(configured_value: Optional[str] = None) -> int:
    """Mirrors ``resolveTaskOutputLength(configuredValue?)``."""
    if not configured_value:
        return TASK_OUTPUT_DEFAULT_LENGTH
    # PORT: ``Number.parseInt(configuredValue, 10)`` + ``Number.isNaN`` 判别。
    try:
        parsed = int(configured_value, 10)
    except (ValueError, TypeError):
        return TASK_OUTPUT_DEFAULT_LENGTH
    if parsed <= 0:
        return TASK_OUTPUT_DEFAULT_LENGTH
    return min(parsed, TASK_OUTPUT_MAX_LENGTH)


def format_persisted_task_output_model_content(input: ToolPersistedModelContentInput) -> str:
    """Mirrors ``formatPersistedTaskOutputModelContent(input)``.

    # PORT: TS 从 ``../result-persistence-format.js`` 导入共享的
    # ``formatPersistedOutputEnvelope``；本文件早期把该实现复制了一份私有副本，
    # 现已删除并改为复用共享实现（避免同一格式两处维护）。
    """
    return format_persisted_output_envelope(
        PersistedOutputEnvelopeInput(
            content=input.content,
            format_bytes=format_compact_file_size,
            original_bytes=len(input.content),
            persisted_path=input.persisted_path,
            preview_chars=TASK_OUTPUT_PERSIST_PREVIEW_CHARS,
        )
    )


async def _wait_for_task(task_id: str, timeout_ms: int, context: Any) -> Any:
    """Mirrors ``waitForTask(taskId, timeoutMs, context)``."""
    started_at = _time.monotonic() * 1000
    while (_time.monotonic() * 1000) - started_at < timeout_ms:
        context.abort_signal.throw_if_aborted()
        task = (
            context.runtime_task_registry.get(task_id)
            if context.runtime_task_registry is not None
            else None
        )
        if task is None:
            return None
        if not _is_task_active(_snapshot_get(task, "status")):
            return task
        await _delay(TASK_OUTPUT_POLL_INTERVAL_MS)
    return (
        context.runtime_task_registry.get(task_id)
        if context.runtime_task_registry is not None
        else None
    )


async def _delay(milliseconds: int) -> None:
    """Mirrors ``delay(milliseconds)``."""
    await asyncio.sleep(milliseconds / 1000)


def _is_task_active(status: Any) -> bool:
    return status == "running" or status == "pending"


def _mark_task_notified(task: Any, context: Any) -> None:
    """Mirrors ``markTaskNotified(task, context)``."""
    registry = context.runtime_task_registry
    if registry is None:
        return
    registry.update(
        _snapshot_get(task, "task_id"),
        lambda current: (
            current
            if _snapshot_get(current, "notified")
            else ({**current, "notified": True} if isinstance(current, dict) else current)
        ),
    )


async def _emit_waiting_progress(context: Any) -> None:
    """Mirrors ``emitWaitingProgress(context)``."""
    if context.emit_event is None:
        return

    await context.emit_event(
        {
            "id": uuid.uuid4().hex,  # PORT: crypto.randomUUID() as never
            "session_id": context.session_id,
            "turn_id": context.turn_id,
            "type": SessionEventType.TOOL_CALL_PROGRESS,
            "timestamp": datetime.now(timezone.utc),
            "trace_id": context.trace_id,
            "sequence_number": 0,
            "payload": {
                "tool_call_id": context.tool_call_id,
                "tool_name": TASK_OUTPUT_TOOL_NAME,
                "elapsed_ms": 0,
            },
        }
    )


def task_output_result(retrieval_status: str, task: Any) -> TaskOutputResult:
    """Mirrors ``taskOutputResult(retrievalStatus, task)``."""
    return TaskOutputResult(retrieval_status=retrieval_status, task=task)


def _require_runtime_task_registry(context: Any) -> Any:
    """Mirrors ``requireRuntimeTaskRegistry(context)``."""
    registry = context.runtime_task_registry
    if registry is not None:
        return registry

    raise create_core_error(
        CoreErrorType.CONFIGURATION_ERROR,
        "Runtime task registry is not configured for TaskOutput",
        {
            "context": {
                "tool_call_id": context.tool_call_id,
                "tool_name": TASK_OUTPUT_TOOL_NAME,
            },
            "recoverable": False,
        },
    )


def _snapshot_get(snapshot: Any, key: str, default: Any = None) -> Any:
    """Read a field from a runtime task snapshot that may be a dict or a dataclass.

    # PORT: ``RuntimeTaskSnapshot`` 的 Python 落点（core/src/runtime-task/registry.ts）尚未移植，
    # 这里对 dict / 对象两种形态都做兼容读取。
    """
    if isinstance(snapshot, dict):
        return snapshot.get(key, default)
    return getattr(snapshot, key, default)


task_output_tool_entry: ToolEntry = ToolEntry(
    capability="read output/logs from a background task",
    aliases=TASK_OUTPUT_ALIASES,
    max_model_chars=TASK_OUTPUT_PERSIST_THRESHOLD_CHARS,
    metadata={
        "name": TASK_OUTPUT_TOOL_NAME,
        "description": TASK_OUTPUT_PROVIDER_DESCRIPTION,
        "read_only": True,
        "destructive": False,
        "concurrent_safe": True,
        "max_output_bytes": TASK_OUTPUT_RESULT_BUDGET_BYTES,
        "side_effect_scope": "none",
        "risk_level": "low",
        "needs_approval": False,
    },
    handler=task_output_handler,
    validate_input=validate_task_output_input,
    format_model_content=format_task_output_model_content,
    format_persisted_model_content=format_persisted_task_output_model_content,
    input_schema=TASK_OUTPUT_INPUT_JSON_SCHEMA,
    output_schema=TASK_OUTPUT_RESULT_JSON_SCHEMA,
    runtime_input_schema=TASK_OUTPUT_INPUT_SCHEMA,
    runtime_output_schema=TASK_OUTPUT_RESULT_SCHEMA,
    permission={
        "permission": "taskOutput",
        "reason": "TaskOutput reads a background task from the current runtime",
        "risk_level": "low",
        "side_effect_scope": "none",
        "needs_approval": False,
        "pattern_sources": ["tool_name", "input"],
        "always_allow_pattern_sources": ["tool_name"],
        "deny_priority": "beforeAsk",
    },
    result_budget={
        "max_inline_bytes": TASK_OUTPUT_RESULT_BUDGET_BYTES,
        "max_model_bytes": TASK_OUTPUT_RESULT_BUDGET_BYTES,
        "strategy": "artifact",
        "preview": {
            "max_bytes": TASK_OUTPUT_RESULT_BUDGET_BYTES,
            "direction": "head",
        },
        "artifact": {
            "enabled": True,
            "retention": "session",
        },
    },
    result_artifact_content_type="text/plain",
    # PORT: ``timeout: { kind: "none" }`` —— 等价于 NoToolTimeoutPolicy。
    timeout={"kind": "none"},
    cancellation={
        "supported": True,
        "cleanup": "none",
        "user_visible_message": "TaskOutput was cancelled while waiting for the task",
    },
    trace={
        "required": True,
        "propagate_to_adapters": False,
        "record_input": "summary",
        "record_output": "summary",
    },
)
