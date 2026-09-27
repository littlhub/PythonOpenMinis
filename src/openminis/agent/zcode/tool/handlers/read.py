"""Read tool — read a file from the local filesystem.

Ported from: packages/core/src/tool/handlers/read.ts
Original package: @zcode/core (tool/handlers)

# PORT-STATUS: partial
Image / video / PDF reading (``read-image.ts`` / ``read-video.ts`` /
``read-pdf.ts``) are out of scope for the coding-only port and are stubbed:
their mime/format probes report "not applicable" so the text path is used.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

from ...contracts.errors import CoreErrorType, create_core_error
from ..path_policy import ToolWorkspacePathOptions, resolve_workspace_path
from ..read_file_state import create_read_file_state_key, normalize_read_file_state_mtime_ms
from ..read_file_state_metadata import create_read_file_state_metadata
from ..types import ReadFileStateEntry, ReadFileStateMap, ToolEntry, ToolHandler
from .ports import (
    READ_DEFAULT_MAX_LINES,
    READ_INPUT_JSON_SCHEMA,
    READ_INPUT_SCHEMA,
    READ_MAX_FILE_SIZE_BYTES,
    READ_OUTPUT_JSON_SCHEMA,
    READ_OUTPUT_SCHEMA,
    READ_TOOL_NAME,
)
from .ports import is_file_system_port_error
from .read_text import add_read_line_numbers, format_read_text_output, read_text_file_for_model

__all__ = [
    "read_handler",
    "read_tool_entry",
    "add_read_line_numbers",
]

FILE_UNCHANGED_STUB = (
    "Wasted call — file unchanged since your last Read. Refer to that earlier tool_result instead."
)
READ_PROVIDER_DESCRIPTION = "\n".join(
    [
        "Reads a file from the local filesystem.",
        "",
        "- `file_path` must be an absolute path.",
        f"- Reads up to {READ_DEFAULT_MAX_LINES} lines by default.",
        "- You can optionally specify a line offset and limit (especially handy for long files), but it's recommended to read the whole file by not providing these parameters",
        "- Results are returned using cat -n format, with line numbers starting at 1",
        "- Reading a directory, a missing file, or an empty file returns an error or system reminder rather than content.",
        "- Do NOT re-read a file you just edited to verify — Edit/Write would have errored if the change failed, and the harness tracks file state for you.",
    ]
)

# PORT: ``fallbackReadFileStates`` WeakMap<ToolExecutionContext, ReadFileStateMap>.
# Python has no WeakMap keyed on arbitrary objects; an id()→map dict is used to
# preserve per-context fallback state without leaking (caller-owned lifecycle).
_fallback_read_file_states: dict[int, ReadFileStateMap] = {}


def format_read_model_content(output: Any) -> str:
    """Mirrors ``formatReadModelContent(output)``."""
    if not isinstance(output, dict):
        return _stringify_read_output_fallback(output)
    return _format_read_output(output)


def _format_read_output(output: dict[str, Any]) -> str:
    kind = output.get("type")
    if kind == "text":
        return format_read_text_output(output)
    if kind == "file_unchanged":
        return FILE_UNCHANGED_STUB
    # PORT: image/video/pdf/parts/notebook are excluded or handled outside the
    # coding-only scope — fall back to a JSON stringification.
    return _stringify_read_output_fallback(output)


def _stringify_read_output_fallback(output: Any) -> str:
    import json

    if isinstance(output, str):
        return output
    try:
        return json.dumps(output) or ""
    except Exception:  # noqa: BLE001
        return ""


async def read_handler(input: Any, context: Any) -> Any:
    """Mirrors ``readHandler: ToolHandler``."""
    parsed = _parse_read_input(input)
    file_path_input = parsed.get("file_path")
    offset = parsed.get("offset")
    limit = parsed.get("limit")

    file_system_port = context.file_system_port
    if not file_system_port:
        raise create_core_error(
            CoreErrorType.CONFIGURATION_ERROR,
            "FileSystemPort is not configured for Read tool",
            {
                "context": {"toolCallId": context.tool_call_id, "toolName": "Read"},
                "recoverable": False,
            },
        )

    file_path = resolve_workspace_path(
        ToolWorkspacePathOptions(
            input_path=file_path_input,
            operation="read",
            working_directory=context.working_directory,
            workspace_root=context.workspace_root,
        )
    )

    # PORT: image/video/pdf 探测（read-image / read-video / read-pdf）不在编程内核范围内。
    image_mime = _infer_image_mime_from_path(file_path)
    if image_mime:
        raise create_core_error(
            CoreErrorType.TOOL_EXECUTION_FAILED,
            "Reading images is not supported in the coding-only port.",
            {"context": {"code": "read_image_excluded"}, "recoverable": True},
        )
    video_mime = _infer_video_mime_from_path(file_path)
    if video_mime:
        raise create_core_error(
            CoreErrorType.TOOL_EXECUTION_FAILED,
            "Reading videos is not supported in the coding-only port.",
            {"context": {"code": "read_video_excluded"}, "recoverable": True},
        )
    if _is_pdf_path(file_path):
        raise create_core_error(
            CoreErrorType.TOOL_EXECUTION_FAILED,
            "Reading PDFs is not supported in the coding-only port.",
            {"context": {"code": "read_pdf_excluded"}, "recoverable": True},
        )

    try:
        trace = _create_read_trace(context)
        stat = await file_system_port.stat(
            {"path": file_path, "trace": trace},
            {"signal": context.abort_signal},
        )
        read_file_state = _get_read_file_state(context)
        cache_offset = _normalize_cache_offset(offset)
        cache_key = create_read_file_state_key(file_path, cache_offset, limit)
        cached = read_file_state.get(cache_key)
        if cached is not None and _is_cached_read_fresh(cached, stat):
            output = {"type": "file_unchanged", "filePath": file_path}
            _record_read_file_state_metadata(
                context,
                {"output": output, "read_file_state": read_file_state, "tool_input": input},
            )
            return output

        range_read_revision: dict[str, Any] = {}

        def _on_read(read: Any) -> None:
            range_read_revision["revision"] = getattr(read, "revision", None)

        output = await read_text_file_for_model(
            {
                "abort_signal": context.abort_signal,
                "file_path": file_path,
                "file_system_port": file_system_port,
                "limit": limit,
                "on_read": _on_read,
                "offset": offset,
                "trace": trace,
            }
        )
        _update_read_file_state(
            read_file_state,
            cache_key,
            {
                "output": output,
                "path": file_path,
                "stat": stat,
                "range_read_revision": range_read_revision.get("revision"),
                "offset": offset,
                "limit": limit,
            },
        )
        _record_read_file_state_metadata(
            context,
            {"output": output, "read_file_state": read_file_state, "tool_input": input},
        )
        return output
    except Exception as error:  # noqa: BLE001
        if is_file_system_port_error(error) and getattr(error, "code", None) == "not_found":
            message = await _create_missing_read_file_message(file_path, context)
            raise create_core_error(
                CoreErrorType.TOOL_EXECUTION_FAILED,
                message,
                {
                    "cause": error,
                    "context": {"code": "read_file_not_found", "filePath": file_path},
                    "recoverable": True,
                },
            )
        if is_file_system_port_error(error) and getattr(error, "code", None) == "too_large":
            raise create_core_error(
                CoreErrorType.TOOL_EXECUTION_FAILED,
                str(error),
                {
                    "cause": error,
                    "context": {
                        "code": "read_file_too_large",
                        "filePath": file_path,
                        "maxBytes": READ_MAX_FILE_SIZE_BYTES,
                    },
                    "recoverable": True,
                },
            )
        raise


def _parse_read_input(input: Any) -> dict[str, Any]:
    """Mirrors ``parseReadInput(input)``.

    PORT: executor 已完成归一化；这里只做 file_path 的类型哨兵 + <tool_use_error> 契约。
    """
    if isinstance(input, dict):
        return input
    return {}


def validate_read_input(input: Any) -> dict[str, Any]:
    """Mirrors ``validateReadInput(input)``."""
    if not isinstance(input, dict):
        return {"result": True}
    file_path = input.get("file_path")
    pages = input.get("pages")
    if not isinstance(file_path, str) or not isinstance(pages, str):
        return {"result": True}
    # PORT: PDF pages 语义约束属于 read-pdf（已排除），此处不再校验。
    return {"result": True}


def _create_read_trace(context: Any) -> dict[str, Any]:
    return {
        "traceId": context.trace_id,
        "spanId": context.span_id,
        "parentSpanId": context.parent_span_id,
        "sessionId": context.session_id,
        "turnId": context.turn_id,
    }


def _get_read_file_state(context: Any) -> ReadFileStateMap:
    if getattr(context, "read_file_state", None) is not None:
        return context.read_file_state
    key = id(context)
    existing = _fallback_read_file_states.get(key)
    if existing is not None:
        return existing
    state: ReadFileStateMap = {}
    _fallback_read_file_states[key] = state
    return state


def _normalize_cache_offset(offset: Optional[int]) -> int:
    return 1 if offset is None else offset


def _is_cached_read_fresh(entry: ReadFileStateEntry, stat: Any) -> bool:
    if entry.is_partial_view:
        return False

    revision = getattr(stat, "revision", None)
    mtime_ms = _get_field(revision, "mtime_ms") if revision is not None else None
    if mtime_ms is None:
        mtime_ms = _get_field(stat, "mtime_ms")
    if entry.mtime_ms is not None and mtime_ms is not None:
        # 和写前 freshness 校验保持同一套策略，mtime 只比较整数毫秒。
        return (
            normalize_read_file_state_mtime_ms(entry.mtime_ms) == normalize_read_file_state_mtime_ms(mtime_ms)
            and entry.size_bytes == _get_field(stat, "size_bytes")
        )

    revision_id = _get_field(revision, "id") if revision is not None else None
    if entry.revision_id and revision_id:
        return entry.revision_id == revision_id

    return entry.size_bytes is not None and entry.size_bytes == _get_field(stat, "size_bytes")


def _update_read_file_state(state: ReadFileStateMap, key: str, input: dict[str, Any]) -> None:
    stat = input["stat"]
    output = input["output"]
    revision = getattr(stat, "revision", None) or input.get("range_read_revision")
    from datetime import datetime, timezone

    state[key] = ReadFileStateEntry(
        path=input["path"],
        content=output.get("content", ""),
        offset=input.get("offset"),
        limit=input.get("limit"),
        # offset/limit 是 range view，不等价于 partial view。
        # partial view 只表示模型看到的内容被工具截断，Write/Edit 必须拒绝这种不完整视图。
        is_partial_view=output.get("truncatedByTokenCap") is True,
        read_at=datetime.now(timezone.utc),
        source_tool="Read",
        revision_id=_get_field(revision, "id") if revision is not None else None,
        mtime_ms=normalize_read_file_state_mtime_ms(
            (_get_field(revision, "mtime_ms") if revision is not None else None) or _get_field(stat, "mtime_ms")
        ),
        size_bytes=_get_field(stat, "size_bytes"),
    )


def _record_read_file_state_metadata(context: Any, input: dict[str, Any]) -> None:
    recorder = getattr(context, "record_read_file_state_metadata", None)
    if recorder is None:
        return
    from datetime import datetime, timezone

    metadata = create_read_file_state_metadata(
        {
            "completed_at": datetime.now(timezone.utc),
            "output": input["output"],
            "read_file_state": input["read_file_state"],
            "tool_input": input["tool_input"],
            "tool_name": "Read",
        }
    )
    if metadata is not None:
        recorder(metadata)


async def _create_missing_read_file_message(file_path: str, context: Any) -> str:
    suggestion = await _find_similar_filename(file_path, context)
    return "".join(
        [
            f"File does not exist. Note: your current working directory is {context.working_directory}.",
            f" Did you mean {suggestion}?" if suggestion else "",
        ]
    )


async def _find_similar_filename(file_path: str, context: Any) -> Optional[str]:
    file_system_port = context.file_system_port
    if not file_system_port:
        return None

    try:
        parent = os.path.dirname(file_path)
        target_name = os.path.basename(file_path)
        target_stem = os.path.splitext(target_name)[0]
        listed = await file_system_port.list_directory(
            {"path": parent, "trace": _create_read_trace(context)},
            {"signal": context.abort_signal},
        )
        entries = sorted(
            entry.name
            for entry in listed.entries
            if getattr(entry, "kind", None) in ("file", "symlink") and entry.name != target_name
        )

        same_stem = next((name for name in entries if os.path.splitext(name)[0] == target_stem), None)
        if same_stem:
            return same_stem

        return next((name for name in entries if _levenshtein_distance(name, target_name) <= 3), None)
    except Exception:  # noqa: BLE001
        return None


def _levenshtein_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    current = [0] * (len(right) + 1)

    for left_index in range(1, len(left) + 1):
        current[0] = left_index
        for right_index in range(1, len(right) + 1):
            cost = 0 if left[left_index - 1] == right[right_index - 1] else 1
            current[right_index] = min(
                current[right_index - 1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + cost,
            )
        previous = list(current)

    return previous[len(right)] if previous else 0


def _get_field(obj: Any, snake_name: str) -> Any:
    """Read a field that may be snake_case (Python) or camelCase (raw adapter)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        if snake_name in obj:
            return obj[snake_name]
        camel = _to_camel(snake_name)
        return obj.get(camel)
    if hasattr(obj, snake_name):
        return getattr(obj, snake_name)
    camel = _to_camel(snake_name)
    return getattr(obj, camel, None)


def _to_camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


# PORT: read-image.ts — excluded (media).
def _infer_image_mime_from_path(file_path: str) -> Optional[str]:
    return None


# PORT: runtime/helpers/attachment-video.ts — excluded (media).
def _infer_video_mime_from_path(file_path: str) -> Optional[str]:
    return None


# PORT: read-pdf.ts — excluded (office).
def _is_pdf_path(file_path: str) -> bool:
    return file_path.lower().endswith(".pdf")


def _build_read_tool_entry() -> ToolEntry:
    from ...contracts.tools import ToolCancellationPolicy, ToolResultBudget, ToolTracePolicy, TimedToolTimeoutPolicy

    return ToolEntry(
        capability="Read text files and supported images from the file-system adapter without modifying files",
        metadata={
            "name": READ_TOOL_NAME,
            "description": READ_PROVIDER_DESCRIPTION,
            "read_only": True,
            "destructive": False,
            "concurrent_safe": True,
            "timeout_ms": 30000,
            "max_output_bytes": READ_MAX_FILE_SIZE_BYTES,
            "side_effect_scope": "none",
            "risk_level": "low",
            "needs_approval": False,
        },
        handler=read_handler,
        format_model_content=format_read_model_content,
        validate_input=validate_read_input,
        input_schema=READ_INPUT_JSON_SCHEMA,
        output_schema=READ_OUTPUT_JSON_SCHEMA,
        runtime_input_schema=READ_INPUT_SCHEMA,
        runtime_output_schema=READ_OUTPUT_SCHEMA,
        permission={
            "permission": "read",
            "reason": "Read only inspects file content and has no external side effects",
            "risk_level": "low",
            "side_effect_scope": "none",
            "needs_approval": False,
            "pattern_sources": ["path"],
            "always_allow_pattern_sources": ["path"],
            "deny_priority": "beforeAsk",
        },
        result_budget={
            "max_inline_bytes": READ_MAX_FILE_SIZE_BYTES,
            "max_model_bytes": READ_MAX_FILE_SIZE_BYTES,
            "strategy": "truncate",
            "preview": {"max_bytes": READ_MAX_FILE_SIZE_BYTES, "direction": "head"},
        },
        timeout={"default_ms": 30000, "max_ms": 30000, "allow_call_override": False},
        cancellation={
            "supported": True,
            "cleanup": "required",
            "user_visible_message": "Read was cancelled before file content was returned",
        },
        trace={
            "propagate_to_adapters": True,
            "record_input": "summary",
            "record_output": "summary",
        },
    )


read_tool_entry: ToolEntry = _build_read_tool_entry()
