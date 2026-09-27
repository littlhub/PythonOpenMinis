"""后台任务输出元数据投影。

Ported from: packages/core/src/tool/executor/background-task-output.ts
Original package: @zcode/core (tool/executor)
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict


class BackgroundTaskOutputMetadata(TypedDict, total=False):
    """Mirrors ``interface BackgroundTaskOutputMetadata``（camelCase 键保持协议兼容）。"""

    childSessionId: str
    outputBytes: int
    outputFile: str
    outputTail: str
    outputTruncated: bool
    stderrBytes: int
    stderrFile: str
    stderrTail: str
    stdoutBytes: int
    stdoutFile: str
    stdoutTail: str


def background_task_output_metadata(
    snapshot: Optional[dict[str, Any]],
    launch_output: Optional[dict[str, Any]] = None,
) -> BackgroundTaskOutputMetadata:
    """把 runtime snapshot / launch output 投影成后台任务输出元数据。"""
    result = _record_property(snapshot, "result")
    # 后台 Agent 的 child session 身份若只留在 Agent output / runtime
    # snapshot，BackgroundTask* 事件就不会带出，V4 Running 行因此无法打开右侧详情。
    child_session_id = _string_property(snapshot, "childSessionId") or _string_property(
        launch_output, "childSessionId"
    )
    stdout = _record_property(result, "stdout")
    stderr = _record_property(result, "stderr")
    stdout_file = (
        _string_property(snapshot, "stdoutPersistedOutputPath")
        or _string_property(stdout, "artifactPath")
        or _string_property(launch_output, "stdoutPersistedOutputPath")
    )
    stderr_file = (
        _string_property(snapshot, "stderrPersistedOutputPath")
        or _string_property(stderr, "artifactPath")
        or _string_property(launch_output, "stderrPersistedOutputPath")
    )
    output_file = (
        _string_property(snapshot, "outputPath")
        or _string_property(snapshot, "outputFile")
        or _string_property(snapshot, "persistedOutputPath")
        or _string_property(snapshot, "rawOutputPath")
        or _string_property(launch_output, "persistedOutputPath")
        or _string_property(launch_output, "rawOutputPath")
        or _string_property(launch_output, "outputFile")
        or stdout_file
        or stderr_file
    )
    stdout_bytes = _number_property(stdout, "bytes") or _number_property(snapshot, "stdoutBytes")
    stderr_bytes = _number_property(stderr, "bytes") or _number_property(snapshot, "stderrBytes")
    stdout_tail = _string_property(stdout, "text") or _string_property(snapshot, "stdoutTail")
    stderr_tail = _string_property(stderr, "text") or _string_property(snapshot, "stderrTail")
    workflow_output = _record_property(snapshot, "output")
    workflow_tail = _string_property(workflow_output, "response")
    output_bytes = (
        None
        if stdout_bytes is None and stderr_bytes is None
        else (stdout_bytes or 0) + (stderr_bytes or 0)
    )
    output_truncated = (
        None
        if result is None
        else (
            _boolean_property(stdout, "truncated") is True
            or _boolean_property(stderr, "truncated") is True
            or _boolean_property(stdout, "artifactTruncated") is True
            or _boolean_property(stderr, "artifactTruncated") is True
        )
    )

    return {
        "childSessionId": child_session_id,
        "outputBytes": output_bytes,
        "outputFile": output_file,
        "outputTail": stdout_tail or stderr_tail or workflow_tail,
        "outputTruncated": output_truncated,
        "stderrBytes": stderr_bytes,
        "stderrFile": stderr_file,
        "stderrTail": stderr_tail,
        "stdoutBytes": stdout_bytes,
        "stdoutFile": stdout_file,
        "stdoutTail": stdout_tail,
    }


def _record_property(record: Any, key: str) -> Optional[dict[str, Any]]:
    if not _is_record(record) or key not in record:
        return None
    value = record[key]
    return value if _is_record(value) else None


def _string_property(record: Any, key: str) -> Optional[str]:
    if not _is_record(record) or key not in record:
        return None
    value = record[key]
    return value if isinstance(value, str) else None


def _number_property(record: Any, key: str) -> Optional[int]:
    if not _is_record(record) or key not in record:
        return None
    value = record[key]
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _boolean_property(record: Any, key: str) -> Optional[bool]:
    if not _is_record(record) or key not in record:
        return None
    value = record[key]
    return value if isinstance(value, bool) else None


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)
