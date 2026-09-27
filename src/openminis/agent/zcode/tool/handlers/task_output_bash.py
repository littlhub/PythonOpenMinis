"""Task output projection — bash background task.

Ported from: packages/core/src/tool/handlers/task-output-bash.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

from ...core.abort import AbortSignal

__all__ = [
    "ProjectBashTaskOptions",
    "TaskOutputFileRead",
    "BashOutputPart",
    "project_bash_task",
]


class TaskOutputFileRead:
    """Mirrors ``interface TaskOutputFileRead`` (anonymous object → named class)."""

    available: bool
    content: str
    truncated: bool


class BashOutputPart:
    """Mirrors ``interface BashOutputPart``."""

    content: str
    path: Optional[str] = None


class ProjectBashTaskOptions:
    """Mirrors ``interface ProjectBashTaskOptions``."""

    read_output_file: Callable[[Optional[str]], Any]  # Awaitable[TaskOutputFileRead]
    running_output_prefix_bytes: int


async def project_bash_task(
    task: dict[str, Any],
    context: Any,
    options: ProjectBashTaskOptions,
) -> dict[str, Any]:
    """Mirrors ``projectBashTask(task, context, options)``."""
    # PORT: ``context.executionPort?.getBackgroundTask?.(task.taskId)`` —— 窄调用。
    snapshot = None
    execution_port = context.execution_port
    if execution_port is not None and getattr(execution_port, "get_background_task", None) is not None:
        snapshot = await execution_port.get_background_task(task["task_id"])

    projected = (
        await _project_bash_snapshot(snapshot, task, context, options)
        if snapshot
        else await _project_fallback_task_file(task.get("output_file"), options)
    )

    result_obj = snapshot.get("result") if isinstance(snapshot, dict) else None
    exit_code = (
        (result_obj.get("exit_code") if isinstance(result_obj, dict) else None)
        or task.get("exit_code")
        or None
    )

    return {
        "task_id": task["task_id"],
        "task_type": task["type"],
        "status": task["status"],
        "description": task.get("description"),
        "output": projected["output"],
        "exit_code": exit_code,
        **({"output_file": projected["output_file"]} if projected.get("output_file") else {}),
    }


async def _project_bash_snapshot(
    snapshot: dict[str, Any],
    task: dict[str, Any],
    context: Any,
    options: ProjectBashTaskOptions,
) -> dict[str, Any]:
    """Mirrors ``projectBashSnapshot(snapshot, task, context, options)``."""
    result_obj = snapshot.get("result") if isinstance(snapshot.get("result"), dict) else {}

    stdout_path = (
        snapshot.get("output_path")
        or task.get("output_file")
        or snapshot.get("stdout_persisted_output_path")
        or (result_obj.get("stdout") or {}).get("artifact_path")
    )
    stderr_path = (
        snapshot.get("stderr_persisted_output_path")
        or (result_obj.get("stderr") or {}).get("artifact_path")
    )

    read_output_file = (
        (lambda path: _read_running_bash_output_file(path, context, options.running_output_prefix_bytes))
        if task["status"] == "running"
        else options.read_output_file
    )

    stdout = await _read_bash_output_part(
        stdout_path,
        (result_obj.get("stdout") or {}).get("text") or snapshot.get("stdout_tail") or "",
        read_output_file,
    )
    stderr = (
        {"content": "", "path": stderr_path}
        if (stderr_path and stderr_path == stdout.get("path"))
        else await _read_bash_output_part(
            stderr_path,
            (result_obj.get("stderr") or {}).get("text") or snapshot.get("stderr_tail") or "",
            read_output_file,
        )
    )

    output = "\n".join(part for part in [stdout["content"], stderr["content"]] if part)

    output_file = (
        stdout.get("path")
        if (stdout.get("path") and (not stderr["content"] or stderr.get("path") == stdout.get("path")))
        else (stderr.get("path") if (not stdout["content"] and stderr.get("path")) else None)
    )

    return {
        "output": output,
        **({"output_file": output_file} if output_file else {}),
    }


async def _read_bash_output_part(
    path: Optional[str],
    fallback: str,
    read_output_file: Callable[[Optional[str]], Any],
) -> dict[str, Any]:
    """Mirrors ``readBashOutputPart(path, fallback, readOutputFile)``."""
    read = await read_output_file(path)
    if read.get("available"):
        return {
            "content": read["content"],
            **({"path": path} if path else {}),
        }
    return {"content": fallback}


async def _project_fallback_task_file(
    output_file: Optional[str],
    options: ProjectBashTaskOptions,
) -> dict[str, Any]:
    """Mirrors ``projectFallbackTaskFile(outputFile, options)``."""
    read = await options.read_output_file(output_file)
    return {
        "output": read["content"],
        **({"output_file": output_file} if (output_file and read.get("available")) else {}),
    }


async def _read_running_bash_output_file(
    output_file: Optional[str],
    context: Any,
    max_bytes: int,
) -> dict[str, Any]:
    """Mirrors ``readRunningBashOutputFile(outputFile, context, maxBytes)``.

    # PORT: 运行中 Bash 复用通用 8 MiB 尾读后，模型会看到输出末尾；这里固定读取文件头部
    # maxBytes，再交给 TaskOutput 应用最终字符预算。
    """
    if not output_file:
        return {"available": False, "content": "", "truncated": False}

    try:
        context.abort_signal.throw_if_aborted()
        file_size = os.path.getsize(output_file)
        bytes_to_read = min(file_size, max_bytes)
        if bytes_to_read == 0:
            return {"available": True, "content": "", "truncated": False}

        with open(output_file, "rb") as handle:
            data = handle.read(bytes_to_read)
        context.abort_signal.throw_if_aborted()

        return {
            "available": True,
            "content": data.decode("utf-8"),
            "truncated": file_size > len(data),
        }
    except Exception as error:
        # PORT: 等价于 TS ``if (abortSignal.aborted) throwIfAborted`` 再 ``return available:false``。
        if context.abort_signal.aborted:
            context.abort_signal.throw_if_aborted()
        return {"available": False, "content": "", "truncated": False}


def throw_if_aborted(signal: AbortSignal) -> None:
    """Mirrors the local ``throwIfAborted(signal)`` (also duplicated in task-output-projection.ts)."""
    # PORT: TS ``throwIfAborted`` raises a DOMException("AbortError"); Python 侧用
    # AbortSignal.throw_if_aborted()，语义等价（aborted 时抛 AbortError 或原 reason）。
    signal.throw_if_aborted()
