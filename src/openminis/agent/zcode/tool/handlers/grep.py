"""Grep tool — content search with ripgrep-compatible regex.

Ported from: packages/core/src/tool/handlers/grep.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

from typing import Any, Optional

from ...contracts.errors import CoreErrorType, create_core_error
from ...contracts.tracing import TraceContext
from ..types import ToolEntry, ToolHandler
from .ports import (
    GREP_INPUT_JSON_SCHEMA,
    GREP_INPUT_SCHEMA,
    GREP_OUTPUT_JSON_SCHEMA,
    GREP_OUTPUT_SCHEMA,
    GREP_TOOL_NAME,
    FileSystemSearchTextRequest,
    is_file_system_port_error,
)

__all__ = [
    "grep_handler",
    "grep_tool_entry",
]

MAX_GREP_MODEL_BYTES = 20_000
DEFAULT_GREP_TIMEOUT_MS = 30_000
GREP_TOOL_DESCRIPTION = """Content search built on ripgrep. Prefer this over `grep`/`rg` via Bash — results integrate with the permission UI and file links.

- Full regex syntax (e.g. "log.*Error", "function\\s+\\w+"). Ripgrep, not grep — escape literal braces (`interface\\{\\}`).
- Filter with `glob` (e.g. "**/*.tsx") or `type` (e.g. "js", "py", "rust").
- `output_mode`: "content" (matching lines), "files_with_matches" (paths only, default), or "count".
- `multiline: true` for patterns that span lines."""


async def grep_handler(input: Any, context: Any) -> Any:
    """Mirrors ``grepHandler: ToolHandler``."""
    parsed = input  # executor 已经归一化
    pattern = parsed.get("pattern")
    path = parsed.get("path")

    file_system_port = context.file_system_port
    if not file_system_port:
        raise create_core_error(
            CoreErrorType.CONFIGURATION_ERROR,
            "FileSystemPort is not configured for Grep tool",
            {
                "context": {
                    "tool_call_id": context.tool_call_id,
                    "tool_name": "Grep",
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

    # 构建搜索参数
    # PORT: 上游把请求对象字面量直接交给 ``fileSystemPort.searchText``，不做 None 过滤、
    # 也不在请求层补 ``output_mode`` / ``-n`` 的默认值（``-n`` 的默认 true 只作用于下面的
    # 格式化步骤）。这里按原样忠实搬运。
    context_lines = parsed.get("context") if parsed.get("context") is not None else parsed.get("-C")
    try:
        result = await file_system_port.search_text(
            FileSystemSearchTextRequest(
                path=search_path,
                pattern=parsed.get("pattern"),
                glob=parsed.get("glob"),
                output_mode=parsed.get("output_mode"),
                before_context=parsed.get("-B") if parsed.get("-B") is not None else context_lines,
                after_context=parsed.get("-A") if parsed.get("-A") is not None else context_lines,
                context=context_lines,
                show_line_numbers=parsed.get("-n"),
                only_matching=parsed.get("-o"),
                ignore_case=parsed.get("-i"),
                type=parsed.get("type"),
                head_limit=parsed.get("head_limit"),
                offset=parsed.get("offset"),
                multiline=parsed.get("multiline"),
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
    except Exception as error:
        if is_file_system_port_error(error) and getattr(error, "code", None) == "cancelled":
            raise create_core_error(
                CoreErrorType.TOOL_CANCELLED,
                "Grep was cancelled",
                {
                    "cause": error,
                    "context": {
                        "path": search_path,
                        "tool_call_id": context.tool_call_id,
                        "tool_name": "Grep",
                    },
                    "recoverable": True,
                },
            )
        raise error

    # 转换文件路径
    filenames = [
        _to_display_path(fp, context.working_directory) for fp in result.files
    ]
    show_line_numbers = parsed.get("-n", True)

    # 构建输出
    output: dict[str, Any] = {
        "mode": result.mode,
        "durationMs": result.duration_ms,
        "numFiles": len(filenames),
        "filenames": filenames if result.mode == "files_with_matches" else [],
        "truncated": result.truncated,
        "appliedLimit": result.applied_limit,
        "appliedOffset": result.applied_offset,
    }

    if result.mode == "content":
        output["content"] = "\n".join(
            _format_content_entry(entry, context.working_directory, show_line_numbers)
            for entry in result.entries
        )
        output["numLines"] = len(result.entries)
        output["numMatches"] = result.num_matches
    elif result.mode == "count":
        output["content"] = "\n".join(
            f"{_to_display_path(entry.path, context.working_directory)}:{entry.count or 0}"
            for entry in result.entries
        )
        output["numMatches"] = result.num_matches
    else:
        output["numMatches"] = result.num_matches

    return output


def _format_content_entry(entry: Any, working_directory: str, show_line_numbers: bool) -> str:
    """格式化单个匹配条目（mirrors ``formatContentEntry``）。

    # PORT: 上游 ``entry`` 是 ``FileSystemSearchTextEntry`` 对象，字段按属性访问；
    # Python 侧同款（``ports.FileSystemSearchTextEntry`` 是 dataclass）。
    """
    path = _to_display_path(entry.path, working_directory)
    if show_line_numbers and entry.line_number is not None:
        return f"{path}:{entry.line_number}:{entry.text or ''}"
    return f"{path}:{entry.text or ''}"


def _to_display_path(file_path: str, working_directory: str) -> str:
    """将文件路径转换为相对于工作目录的显示路径（mirrors ``toDisplayPath``）。"""
    import os

    # PORT: 同 glob.py 的 ``_to_display_path``——补上 JS 的真值判断
    # （``node:path.relative(a, a) === ""``，Python 等价物为 ``"."``）与 Windows 跨盘符的
    # ``ValueError`` 兜底。
    try:
        rel_path = os.path.relpath(file_path, working_directory)
        if rel_path not in ("", ".") and not rel_path.startswith("..") and not os.path.isabs(rel_path):
            return rel_path.replace(os.sep, "/")
    except (ValueError, OSError):
        pass
    return file_path


def format_grep_model_content(output: Any) -> str:
    """格式化 grep 结果用于模型输出。"""
    result = output if isinstance(output, dict) else {}
    mode = result.get("mode", "files_with_matches")
    limit_info = _format_limit_info(
        result.get("appliedLimit"),
        result.get("appliedOffset")
    )

    if mode == "content":
        content = result.get("content") or "No matches found"
        return f"{content}\n\n[Showing results with pagination = {limit_info}]" if limit_info else content

    if mode == "count":
        content = result.get("content") or "No matches found"
        matches = result.get("numMatches", 0)
        files = result.get("numFiles", 0)
        summary = f"Found {matches} total {'occurrence' if matches == 1 else 'occurrences'} across {files} {'file' if files == 1 else 'files'}."
        return f"{content}\n\n{summary}{' with pagination = ' + limit_info if limit_info else ''}"

    filenames = result.get("filenames", [])
    file_count = result.get("numFiles", len(filenames))
    if file_count == 0:
        return "No files found"

    return f"Found {file_count} {'file' if file_count == 1 else 'files'}{' ' + limit_info if limit_info else ''}\n{chr(10).join(filenames)}"


def _format_limit_info(applied_limit: Optional[int], applied_offset: Optional[int]) -> str:
    """格式化分页信息。"""
    parts = []
    if applied_limit is not None:
        parts.append(f"limit: {applied_limit}")
    if applied_offset is not None:
        parts.append(f"offset: {applied_offset}")
    return ", ".join(parts) if parts else ""


def _grep_permission() -> Any:
    from ...contracts.tools import ToolPermissionSpec

    return ToolPermissionSpec(
        permission="read",
        reason="Grep only searches file contents and has no external side effects",
        risk_level="low",
        side_effect_scope="none",
        needs_approval=False,
        pattern_sources=["path", "input"],
        always_allow_pattern_sources=["path", "input"],
        deny_priority="beforeAsk",
    )


def _grep_result_budget() -> Any:
    from ...contracts.tools import ToolResultBudget, ToolResultBudgetArtifact, ToolResultBudgetPreview

    return ToolResultBudget(
        max_inline_bytes=MAX_GREP_MODEL_BYTES,
        max_model_bytes=MAX_GREP_MODEL_BYTES,
        strategy="artifact",
        preview=ToolResultBudgetPreview(max_bytes=MAX_GREP_MODEL_BYTES, direction="head"),
        artifact=ToolResultBudgetArtifact(enabled=True, retention="session"),
    )


def _build_grep_tool_entry() -> ToolEntry:
    from ...contracts.tools import TimedToolTimeoutPolicy, ToolCancellationPolicy, ToolTracePolicy

    return ToolEntry(
        capability="Search file contents with ripgrep-compatible regular expressions",
        metadata={
            "name": GREP_TOOL_NAME,
            "description": GREP_TOOL_DESCRIPTION,
            "read_only": True,
            "destructive": False,
            "concurrent_safe": True,
            "timeout_ms": DEFAULT_GREP_TIMEOUT_MS,
            "max_output_bytes": MAX_GREP_MODEL_BYTES,
            "side_effect_scope": "none",
            "risk_level": "low",
            "needs_approval": False,
        },
        handler=grep_handler,
        input_schema=GREP_INPUT_JSON_SCHEMA,
        output_schema=GREP_OUTPUT_JSON_SCHEMA,
        runtime_input_schema=GREP_INPUT_SCHEMA,
        runtime_output_schema=GREP_OUTPUT_SCHEMA,
        format_model_content=format_grep_model_content,
        permission=_grep_permission(),
        result_budget=_grep_result_budget(),
        timeout=TimedToolTimeoutPolicy(
            default_ms=DEFAULT_GREP_TIMEOUT_MS,
            allow_call_override=False,
            max_ms=DEFAULT_GREP_TIMEOUT_MS,
        ),
        cancellation=ToolCancellationPolicy(
            supported=True,
            cleanup="none",
            user_visible_message="Grep was cancelled before search results were returned",
        ),
        trace=ToolTracePolicy(
            propagate_to_adapters=True,
            record_input="summary",
            record_output="summary",
        ),
    )


grep_tool_entry: ToolEntry = _build_grep_tool_entry()