"""Write tool — create or overwrite a file.

Ported from: packages/core/src/tool/handlers/write.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

import time
from typing import Any, Optional

from ...contracts.errors import CoreErrorType, create_core_error
from ...memory.origin_session import StampMemoryOriginSessionIdInput, stamp_memory_origin_session_id
from ..path_policy import ToolWorkspacePathOptions, resolve_workspace_path
from ..read_file_state import (
    create_read_file_state_key,
    find_latest_read_file_state,
    normalize_read_file_state_mtime_ms,
)
from ..read_file_state_metadata import create_read_file_state_metadata_from_entry
from ..structured_patch import create_structured_patch
from ..types import ReadFileStateEntry, ReadFileStateMap, ToolEntry
from .ports import (
    WRITE_INPUT_JSON_SCHEMA,
    WRITE_INPUT_SCHEMA,
    WRITE_OUTPUT_JSON_SCHEMA,
    WRITE_OUTPUT_SCHEMA,
    WRITE_TOOL_NAME,
    is_file_system_port_error,
)
from .tool_perf import (
    attach_tool_execution_telemetry,
    elapsed_ms_since,
    file_byte_count,
    workspace_kind,
)

WRITE_PROVIDER_DESCRIPTION = "\n".join(
    [
        "Writes a file to the local filesystem, overwriting if one exists.",
        "",
        "When to use: creating a new file, or fully replacing one you've already Read. Overwriting an existing file you haven't Read will fail. For partial changes, use Edit instead.",
    ]
)

WRITE_STORAGE_SUFFIX = " (file state is current in your context — no need to Read it back)"
WRITE_USER_MODIFIED_NOTE = " The user modified your proposed content before accepting it."
WRITE_NOT_READ_MESSAGE = "File has not been read yet. Read it first before writing to it."
WRITE_STALE_MESSAGE = (
    "File has been modified since read, either by the user or by a linter. Read it again before attempting to write it."
)


def format_write_model_content(output: Any) -> str:
    """Mirrors ``formatWriteModelContent(output)``."""
    if not _is_record(output):
        return "The file has been written successfully."

    file_path = output.get("filePath") if isinstance(output.get("filePath"), str) else "the file"
    user_modified = output.get("userModified") is True
    user_modified_note = WRITE_USER_MODIFIED_NOTE if user_modified else ""
    storage_suffix_note = "" if user_modified else WRITE_STORAGE_SUFFIX

    if output.get("type") == "create":
        return f"File created successfully at: {file_path}{user_modified_note}{storage_suffix_note}"

    return f"The file {file_path} has been updated successfully.{user_modified_note}{storage_suffix_note}"


async def write_handler(input: Any, context: Any) -> Any:
    """Mirrors ``writeHandler: ToolHandler``."""
    file_path_input = input.get("file_path")
    content = input.get("content")

    file_system_port = context.file_system_port
    if not file_system_port:
        raise create_core_error(
            CoreErrorType.CONFIGURATION_ERROR,
            "FileSystemPort is not configured for Write tool",
            {
                "context": {"toolCallId": context.tool_call_id, "toolName": "Write"},
                "recoverable": False,
            },
        )

    file_path = resolve_workspace_path(
        ToolWorkspacePathOptions(
            input_path=file_path_input,
            operation="write",
            working_directory=context.working_directory,
            workspace_root=context.workspace_root,
        )
    )

    original_file: Optional[str] = None
    original_encoding: Optional[str] = None
    original_line_endings: Optional[str] = None
    original_revision: Any = None
    read_started_at = int(time.time() * 1000)
    fs_read_ms = 0
    try:
        read = await file_system_port.read_text_file(
            {"path": file_path, "trace": _create_write_trace(context)},
            {"signal": context.abort_signal},
        )
        _assert_writable_existing_file_is_fresh(file_path, read, context.read_file_state)
        original_file = read.content
        original_encoding = _get_field(read, "encoding")
        original_line_endings = _get_field(read, "line_endings")
        original_revision = _get_field(read, "revision")
        fs_read_ms = elapsed_ms_since(read_started_at)
    except Exception as error:  # noqa: BLE001
        fs_read_ms = elapsed_ms_since(read_started_at)
        if not is_file_system_port_error(error) or getattr(error, "code", None) != "not_found":
            raise

    content_to_write = stamp_memory_origin_session_id(
        StampMemoryOriginSessionIdInput(
            content=content,
            file_path=file_path,
            memory_root=getattr(context, "memory_root", None),
            session_id=context.session_id,
        )
    )
    write_started_at = int(time.time() * 1000)
    write_result = await file_system_port.write_text_file(
        {
            "path": file_path,
            "content": content_to_write,
            "encoding": original_encoding,
            "lineEndings": original_line_endings,
            "createParents": True,
            "atomic": True,
            "expectedRevision": original_revision,
            "trace": _create_write_trace(context),
        },
        {"signal": context.abort_signal},
    )
    fs_write_ms = elapsed_ms_since(write_started_at)

    read_file_state_entry = _update_read_file_state_after_write(
        context.read_file_state, file_path, content_to_write, _get_field(write_result, "revision")
    )
    _record_read_file_state_metadata(context, read_file_state_entry)

    if original_file:
        return attach_tool_execution_telemetry(
            {
                "type": "update",
                "filePath": file_path_input,
                "content": content_to_write,
                "structuredPatch": create_structured_patch(
                    file_path=file_path_input, old_content=original_file, new_content=content_to_write
                ),
                "originalFile": original_file,
                "userModified": False,
            },
            _create_write_performance_telemetry(
                content=content_to_write, fs_read_ms=fs_read_ms, fs_write_ms=fs_write_ms, context=context
            ),
        )

    return attach_tool_execution_telemetry(
        {
            "type": "create",
            "filePath": file_path_input,
            "content": content_to_write,
            "structuredPatch": [],
            "originalFile": None,
            "userModified": False,
        },
        _create_write_performance_telemetry(
            content=content_to_write, fs_read_ms=fs_read_ms, fs_write_ms=fs_write_ms, context=context
        ),
    )


def _create_write_performance_telemetry(*, content: str, context: Any, fs_read_ms: int, fs_write_ms: int) -> dict[str, Any]:
    total_bytes = file_byte_count(content)
    return {
        "detail": {
            "kind": "filesystem",
            "filesystem": {
                "readMs": fs_read_ms,
                "writeMs": fs_write_ms,
                "fileCount": 1,
                "totalBytes": total_bytes,
                "maxFileBytes": total_bytes,
                "workspaceKind": workspace_kind(context),
            },
        }
    }


def _create_write_trace(context: Any) -> dict[str, Any]:
    return {
        "traceId": context.trace_id,
        "spanId": context.span_id,
        "parentSpanId": context.parent_span_id,
        "sessionId": context.session_id,
        "turnId": context.turn_id,
    }


def _assert_writable_existing_file_is_fresh(file_path: str, current_read: Any, read_file_state: Any) -> None:
    last_read = find_latest_read_file_state(read_file_state, file_path)
    if last_read is None or last_read.is_partial_view:
        raise create_core_error(
            CoreErrorType.TOOL_EXECUTION_FAILED,
            WRITE_NOT_READ_MESSAGE,
            {"context": {"code": "write_file_not_read", "filePath": file_path}, "recoverable": True},
        )

    if not _has_read_state_changed(last_read, current_read):
        return
    if _is_strict_full_read(last_read) and last_read.content == current_read.content:
        return

    raise create_core_error(
        CoreErrorType.TOOL_EXECUTION_FAILED,
        WRITE_STALE_MESSAGE,
        {"context": {"code": "write_file_stale", "filePath": file_path}, "recoverable": True},
    )


def _has_read_state_changed(last_read: ReadFileStateEntry, current_read: Any) -> bool:
    current_revision = _get_field(current_read, "revision")
    current_revision_id = _get_field(current_revision, "id") if current_revision is not None else None
    if last_read.revision_id and current_revision_id and last_read.revision_id != current_revision_id:
        return True

    current_mtime_ms = _get_field(current_revision, "mtime_ms") if current_revision is not None else None
    if last_read.mtime_ms is not None and current_mtime_ms is not None:
        # 让已确认内容未变的 Write 被误判为 stale。
        normalized_current = normalize_read_file_state_mtime_ms(current_mtime_ms)
        normalized_last = normalize_read_file_state_mtime_ms(last_read.mtime_ms)
        mtime_advanced = (
            normalized_current is not None
            and normalized_last is not None
            and normalized_current > normalized_last
        )
        return mtime_advanced or last_read.size_bytes != _get_field(current_read, "size_bytes")

    current_size = _get_field(current_read, "size_bytes")
    if last_read.size_bytes is not None and last_read.size_bytes != current_size:
        return True

    return _is_strict_full_read(last_read) and last_read.content != current_read.content


def _is_strict_full_read(entry: ReadFileStateEntry) -> bool:
    if entry.is_partial_view:
        return False
    return (entry.offset or 1) <= 1 and entry.limit is None


def _update_read_file_state_after_write(
    read_file_state: Optional[ReadFileStateMap],
    file_path: str,
    content: str,
    revision: Any,
) -> Optional[ReadFileStateEntry]:
    if read_file_state is None:
        return None
    from datetime import datetime, timezone

    revision_size = _get_field(revision, "size_bytes") if revision is not None else None
    entry = ReadFileStateEntry(
        path=file_path,
        content=content,
        offset=None,
        limit=None,
        is_partial_view=False,
        read_at=datetime.now(timezone.utc),
        source_tool="Write",
        revision_id=_get_field(revision, "id") if revision is not None else None,
        mtime_ms=normalize_read_file_state_mtime_ms(
            _get_field(revision, "mtime_ms") if revision is not None else None
        ),
        size_bytes=revision_size if revision_size is not None else file_byte_count(content),
    )
    read_file_state[create_read_file_state_key(file_path, 1, None)] = entry
    return entry


def _record_read_file_state_metadata(context: Any, entry: Optional[ReadFileStateEntry]) -> None:
    recorder = getattr(context, "record_read_file_state_metadata", None)
    if recorder is None:
        return
    from datetime import datetime, timezone

    metadata = create_read_file_state_metadata_from_entry(
        {
            "completed_at": entry.read_at if entry is not None else datetime.now(timezone.utc),
            "entry": entry,
            "tool_name": "Write",
        }
    )
    if metadata is not None:
        recorder(metadata)


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _get_field(obj: Any, snake_name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        if snake_name in obj:
            return obj[snake_name]
        return obj.get(_to_camel(snake_name))
    if hasattr(obj, snake_name):
        return getattr(obj, snake_name)
    return getattr(obj, _to_camel(snake_name), None)


def _to_camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


def _build_write_tool_entry() -> ToolEntry:
    return ToolEntry(
        capability="Create or overwrite a file through the file-system adapter",
        metadata={
            "name": WRITE_TOOL_NAME,
            "description": WRITE_PROVIDER_DESCRIPTION,
            "read_only": False,
            "destructive": False,
            "concurrent_safe": False,
            "timeout_ms": 30000,
            "max_output_bytes": 1_000_000,
            "side_effect_scope": "workspace",
            "risk_level": "medium",
            "needs_approval": True,
        },
        handler=write_handler,
        format_model_content=format_write_model_content,
        input_schema=WRITE_INPUT_JSON_SCHEMA,
        output_schema=WRITE_OUTPUT_JSON_SCHEMA,
        runtime_input_schema=WRITE_INPUT_SCHEMA,
        runtime_output_schema=WRITE_OUTPUT_SCHEMA,
        permission={
            "permission": "edit",
            "reason": "Write creates or overwrites files through the file-system adapter",
            "risk_level": "medium",
            "side_effect_scope": "workspace",
            "needs_approval": True,
            "pattern_sources": ["path"],
            "always_allow_pattern_sources": ["path"],
            "deny_priority": "beforeAsk",
        },
        result_budget={
            "max_inline_bytes": 1_000_000,
            "max_model_bytes": 100_000,
            "strategy": "truncate",
            "preview": {"max_bytes": 100_000, "direction": "head"},
        },
        timeout={"default_ms": 30000, "max_ms": 30000, "allow_call_override": False},
        cancellation={
            "supported": True,
            "cleanup": "bestEffort",
            "user_visible_message": "Write was cancelled before the file operation completed",
        },
        trace={
            "propagate_to_adapters": True,
            "record_input": "summary",
            "record_output": "summary",
        },
    )


write_tool_entry: ToolEntry = _build_write_tool_entry()
