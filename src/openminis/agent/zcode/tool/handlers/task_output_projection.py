"""Task output projection — project a runtime task snapshot for model display.

Ported from: packages/core/src/tool/handlers/task-output-projection.ts
Original package: @zcode/core (tool/handlers)

# PORT: task-output.ts 顶到 oxlint max-lines 上限（400 行），把任务投影（projectTask 及
# dwf / agent 分支）、输出文件尾部快照读取和紧凑文件大小格式化拆到本文件；公开面仍从
# task-output.ts 导出。为了保持与上游一致（projection 依赖 bash 分支、bash 分支不反向依赖
# projection），``throw_if_aborted`` 在两个文件各自保留一份。
"""

from __future__ import annotations

import os
from typing import Any, Optional

from ...core.abort import AbortSignal
from .task_output_bash import project_bash_task

__all__ = [
    "format_compact_file_size",
    "project_task",
]

TASK_OUTPUT_FILE_TAIL_BYTES = 8 * 1024 * 1024
TASK_OUTPUT_RUNNING_BASH_PREFIX_BYTES = 30_000


def format_compact_file_size(size: int) -> str:
    """Mirrors ``formatCompactFileSize(size)``."""
    kilobytes = size / 1024
    if kilobytes < 1:
        return f"{size} bytes"
    if kilobytes < 1024:
        return f"{_trim_single_decimal(kilobytes)}KB"
    megabytes = kilobytes / 1024
    if megabytes < 1024:
        return f"{_trim_single_decimal(megabytes)}MB"
    return f"{_trim_single_decimal(megabytes / 1024)}GB"


def _trim_single_decimal(value: float) -> str:
    return f"{value:.1f}".replace(".0", "")


async def project_task(task: dict[str, Any], context: Any) -> dict[str, Any]:
    """Mirrors ``projectTask(task, context)``."""
    task_type = task.get("type")
    if task_type == "local_bash":
        return await project_bash_task(
            task,
            context,
            {
                "read_output_file": (lambda output_file: read_task_output_file_snapshot(output_file, context)),
                "running_output_prefix_bytes": TASK_OUTPUT_RUNNING_BASH_PREFIX_BYTES,
            },
        )
    if task_type == "local_agent":
        return await _project_agent_task(task, context)
    if task_type == "local_dynamic_workflow":
        return await _project_dynamic_workflow_task(task, context)

    output = await _read_task_output_file(task.get("output_file"), context)
    return {
        "task_id": task.get("task_id"),
        "task_type": task_type,
        "status": task.get("status"),
        "description": task.get("description"),
        "output": output,
        **({"output_file": task["output_file"]} if task.get("output_file") else {}),
    }


async def _project_dynamic_workflow_task(task: dict[str, Any], context: Any) -> dict[str, Any]:
    """Mirrors ``projectDynamicWorkflowTask(task, context)``."""
    output_file = task.get("output_file")
    output = task.get("result_text") or await _read_task_output_file(output_file, context)

    return {
        "task_id": task.get("task_id"),
        "task_type": task.get("type"),
        "status": task.get("status"),
        "description": task.get("description"),
        "output": output,
        **({"output_file": output_file} if output_file else {}),
        **({"result": task["result_text"]} if task.get("result_text") is not None else {}),
        **({"error": task["error"]} if task.get("error") else {}),
    }


async def _project_agent_task(task: dict[str, Any], context: Any) -> dict[str, Any]:
    """Mirrors ``projectAgentTask(task, context)``."""
    output_file = task.get("output_file")
    disk_output = await _read_task_output_file(output_file, context)
    output_obj = task.get("output")
    completed_output = output_obj if isinstance(output_obj, dict) and output_obj.get("status") == "completed" else None
    clean_result = "\n".join(block.get("text", "") for block in (completed_output or {}).get("content", []))
    output = clean_result or disk_output

    return {
        "task_id": task.get("task_id"),
        "task_type": task.get("type"),
        "status": task.get("status"),
        "description": task.get("description"),
        "output": output,
        **({"output_file": output_file} if output_file else {}),
        **({"prompt": task["prompt"]} if task.get("prompt") else {}),
        **({"result": output} if output else {}),
        **({"error": task["error"]} if task.get("error") else {}),
    }


async def _read_task_output_file(output_file: Optional[str], context: Any) -> str:
    """Mirrors ``readTaskOutputFile(outputFile, context)``."""
    snapshot = await read_task_output_file_snapshot(output_file, context)
    return snapshot["content"]


async def read_task_output_file_snapshot(
    output_file: Optional[str],
    context: Any,
) -> dict[str, Any]:
    """Mirrors ``readTaskOutputFileSnapshot(outputFile, context)``.

    # PORT: 原 ``node:fs/promises`` ``open`` + 尾部 seek 读取，这里用 Python ``open`` 读尾部。
    """
    if not output_file:
        return {"available": False, "content": "", "truncated": False}

    try:
        context.abort_signal.throw_if_aborted()
        file_size = os.path.getsize(output_file)
        bytes_to_read = min(file_size, TASK_OUTPUT_FILE_TAIL_BYTES)
        if bytes_to_read == 0:
            return {"available": True, "content": "", "truncated": False}

        with open(output_file, "rb") as handle:
            if file_size > bytes_to_read:
                handle.seek(file_size - bytes_to_read)
            data = handle.read(bytes_to_read)
        context.abort_signal.throw_if_aborted()

        omitted = file_size - len(data)
        content = data.decode("utf-8")
        return {
            "available": True,
            "content": (
                f"[{round(omitted / 1024)}KB of earlier output omitted]\n{content}"
                if omitted > 0
                else content
            ),
            "truncated": omitted > 0,
        }
    except Exception as error:
        if context.abort_signal.aborted:
            context.abort_signal.throw_if_aborted()
        return {"available": False, "content": "", "truncated": False}


def throw_if_aborted(signal: AbortSignal) -> None:
    """Mirrors the local ``throwIfAborted(signal)`` (also duplicated in task-output-bash.ts)."""
    signal.throw_if_aborted()
