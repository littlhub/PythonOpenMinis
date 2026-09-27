"""Glob tool — find files by pattern.

Ported from: packages/core/src/tool/handlers/glob.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

from typing import Any, Optional

from ...contracts.errors import CoreErrorType, create_core_error
from ...contracts.tracing import TraceContext
from ..types import ToolEntry, ToolHandler
from .ports import (
    GLOB_INPUT_JSON_SCHEMA,
    GLOB_INPUT_SCHEMA,
    GLOB_OUTPUT_JSON_SCHEMA,
    GLOB_OUTPUT_SCHEMA,
    GLOB_TOOL_NAME,
    FileSystemSearchFilesRequest,
)

__all__ = [
    "glob_handler",
    "glob_tool_entry",
]

MAX_GLOB_RESULTS = 100
MAX_GLOB_MODEL_BYTES = 100_000
GLOB_TOOL_DESCRIPTION = (
    'Fast file pattern matching. Supports glob patterns like "**/*.js" or "src/**/*.ts". '
    "Returns matching file paths sorted by modification time."
)


async def glob_handler(input: Any, context: Any) -> Any:
    """Mirrors ``globHandler: ToolHandler``."""
    parsed = input  # executor 已经归一化
    pattern = parsed.get("pattern")
    path = parsed.get("path")

    file_system_port = context.file_system_port
    if not file_system_port:
        raise create_core_error(
            CoreErrorType.CONFIGURATION_ERROR,
            "FileSystemPort is not configured for Glob tool",
            {
                "context": {
                    "tool_call_id": context.tool_call_id,
                    "tool_name": "Glob",
                },
                "recoverable": False,
            },
        )

    # 路径解析
    from ..path_policy import (
        ToolWorkspaceDirectoryOptions,
        ToolWorkspacePathOptions,
        resolve_tool_working_directory,
        resolve_workspace_path,
    )

    if path:
        search_path = resolve_workspace_path(
            ToolWorkspacePathOptions(
                input_path=path,
                operation="read",
                working_directory=context.working_directory,
                workspace_root=context.workspace_root,
            )
        )
    else:
        search_path = resolve_tool_working_directory(
            None,
            ToolWorkspaceDirectoryOptions(
                working_directory=context.working_directory,
                workspace_root=context.workspace_root,
                operation="read",
            ),
        )

    # 调用 FileSystemPort.search_files
    # PORT: TS 里 trace 由调用点构造对象字面量并 cast 成 ``TraceContext``；Python 显式构造
    # ``TraceContext``，请求体用 ``FileSystemSearchFilesRequest``（与 TS 对象字面量一一对应）。
    result = await file_system_port.search_files(
        FileSystemSearchFilesRequest(
            path=search_path,
            pattern=pattern,
            max_results=MAX_GLOB_RESULTS,
            trace=TraceContext(
                trace_id=context.trace_id,
                span_id=context.span_id,
                parent_span_id=context.parent_span_id,
                session_id=context.session_id,
                turn_id=context.turn_id,
            ),
        ),
        {"signal": context.abort_signal},
    )

    # 转换文件路径为显示路径
    filenames = [
        _to_display_path(fp, context.working_directory) for fp in result.files
    ]

    return {
        "durationMs": result.duration_ms,
        "numFiles": len(filenames),
        "filenames": filenames,
        "truncated": result.truncated,
    }


def _to_display_path(file_path: str, working_directory: str) -> str:
    """将文件路径转换为相对于工作目录的显示路径（mirrors ``toDisplayPath``）。"""
    import os

    # PORT: 原实现无 try/except（``node:path.relative`` 对字符串输入不抛）。Python 的
    # ``os.path.relpath`` 在 Windows 跨盘符时会抛 ``ValueError``，故保留兜底并回落原路径。
    try:
        rel_path = os.path.relpath(file_path, working_directory)
        # PORT: JS 真值判断 ``relativePath &&``——``node:path.relative(a, a) === ""`` 为假值，
        # 走回落分支。Python 的等价物是 ``"."``，故一并排除。
        if rel_path not in ("", ".") and not rel_path.startswith("..") and not os.path.isabs(rel_path):
            return rel_path.replace(os.sep, "/")
    except (ValueError, OSError):
        pass
    return file_path



def format_glob_model_content(output: Any) -> str:
    """格式化 glob 结果用于模型输出。"""
    result = output if isinstance(output, dict) else {}
    filenames = result.get("filenames", [])
    if not filenames:
        return "No files found"

    lines = list(filenames)
    if result.get("truncated"):
        lines.append("(Results are truncated. Consider using a more specific path or pattern.)")
    return "\n".join(lines)


def _glob_permission() -> Any:
    from ...contracts.tools import ToolPermissionSpec

    return ToolPermissionSpec(
        permission="read",
        reason="Glob only lists file paths and has no external side effects",
        risk_level="low",
        side_effect_scope="none",
        needs_approval=False,
        pattern_sources=["path", "input"],
        always_allow_pattern_sources=["path", "input"],
        deny_priority="beforeAsk",
    )


def _glob_result_budget() -> Any:
    from ...contracts.tools import ToolResultBudget, ToolResultBudgetArtifact, ToolResultBudgetPreview

    return ToolResultBudget(
        max_inline_bytes=MAX_GLOB_MODEL_BYTES,
        max_model_bytes=MAX_GLOB_MODEL_BYTES,
        strategy="artifact",
        preview=ToolResultBudgetPreview(max_bytes=MAX_GLOB_MODEL_BYTES, direction="head"),
        artifact=ToolResultBudgetArtifact(enabled=True, retention="session"),
    )


def _build_glob_tool_entry() -> ToolEntry:
    from ...contracts.tools import NoToolTimeoutPolicy, ToolCancellationPolicy, ToolTracePolicy

    return ToolEntry(
        capability="Find files by glob pattern through the file-system adapter without reading file contents",
        metadata={
            "name": GLOB_TOOL_NAME,
            "description": GLOB_TOOL_DESCRIPTION,
            "read_only": True,
            "destructive": False,
            "concurrent_safe": True,
            "timeout_ms": 30000,
            "max_output_bytes": MAX_GLOB_MODEL_BYTES,
            "side_effect_scope": "none",
            "risk_level": "low",
            "needs_approval": False,
        },
        handler=glob_handler,
        input_schema=GLOB_INPUT_JSON_SCHEMA,
        output_schema=GLOB_OUTPUT_JSON_SCHEMA,
        runtime_input_schema=GLOB_INPUT_SCHEMA,
        runtime_output_schema=GLOB_OUTPUT_SCHEMA,
        format_model_content=format_glob_model_content,
        permission=_glob_permission(),
        result_budget=_glob_result_budget(),
        timeout=NoToolTimeoutPolicy(),
        cancellation=ToolCancellationPolicy(
            supported=True,
            cleanup="none",
            user_visible_message="Glob was cancelled before file matches were returned",
        ),
        trace=ToolTracePolicy(
            propagate_to_adapters=True,
            record_input="summary",
            record_output="summary",
        ),
    )


glob_tool_entry: ToolEntry = _build_glob_tool_entry()