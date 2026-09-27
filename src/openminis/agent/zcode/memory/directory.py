"""Memory Directory — ensures the memory root directory exists.

Ported from: packages/core/src/memory/directory.ts
Original package: @zcode/core (memory)

# PORT: ``FileSystemPort`` / ``Logger`` / ``TraceContext`` 来自 @zcode/contracts，尚未移植；
# 此处保留端口（Protocol）占位，注入实现由 OpenMinis 适配层提供（见 ZCORE_PORTING.md §5）。
"""

from __future__ import annotations

from typing import Any, Awaitable, Optional, Protocol


# ---------------------------------------------------------------------------
# PORT: @zcode/contracts 端口占位（未移植）
# ---------------------------------------------------------------------------
class TraceContext:  # PORT: TS ``TraceContext`` 是 telemetry 上下文类型；此处用空壳占位。
    pass


class Logger(Protocol):
    """PORT: TS ``Logger.debug(message, fields)`` → Python ``debug(message, **fields)``."""

    def debug(self, message: str, **fields: Any) -> None: ...


class FileSystemPort(Protocol):
    """PORT: TS ``FileSystemPort.createDirectory({ path, trace })`` → ``create_directory``。

    # PORT: 原 Node API：``fileSystemPort.createDirectory({ path, trace })``。
    """

    async def create_directory(
        self, *, path: str, trace: Optional["TraceContext"] = None
    ) -> Awaitable[None]: ...


def trace_context_to_log_context(trace: Optional[TraceContext]) -> dict[str, Any]:
    """PORT: TS ``traceContextToLogContext`` 把 TraceContext 折成日志字段。

    # PORT: 实际实现依赖未移植的 telemetry 上下文，这里返回空 dict 兜底。
    """
    return {}


async def ensure_memory_directory_exists(
    file_system_port: FileSystemPort,
    root_dir: str,
    trace_context: Optional[TraceContext] = None,
    logger: Optional[Logger] = None,
) -> None:
    try:
        await file_system_port.create_directory(path=root_dir, trace=trace_context)
    except Exception as error:  # noqa: BLE001
        # 目录预创建失败不阻断执行；实际 Write/Edit 仍返回原始文件错误。
        if logger is not None:
            logger.debug(
                "Memory directory creation failed",
                **{
                    **(
                        trace_context_to_log_context(trace_context)
                        if trace_context is not None
                        else {}
                    ),
                    "error": str(error),
                    "event": "memory.directory.create_failed",
                    "module": "core.memory",
                    "status": "failed",
                },
            )
