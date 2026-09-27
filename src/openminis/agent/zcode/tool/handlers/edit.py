"""Edit tool — exact string replacement in a file.

Ported from: packages/core/src/tool/handlers/edit.ts
Original package: @zcode/core (tool/handlers)

# PORT: 原文件头 ``/* eslint-disable max-lines */`` —— Edit 工具需要集中维护文本匹配、
# read-before-edit 与写回状态，避免 bugfix 期间拆分扩大行为面。
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

from ...contracts.errors import CoreErrorType, create_core_error
from ...memory.origin_session import StampMemoryOriginSessionIdInput, stamp_memory_origin_session_id
from ..edit_matchers import (
    find_edit_match,
    normalize_line_endings,
    normalize_replacement_for_match,
    preserve_quote_style,
)
from ..path_policy import ToolWorkspacePathOptions, resolve_workspace_path
from ..read_file_state import (
    create_read_file_state_key,
    find_editable_read_file_state,
    normalize_read_file_state_mtime_ms,
)
from ..read_file_state_metadata import create_read_file_state_metadata_from_entry
from ..structured_patch import create_structured_patch
from ..types import ReadFileStateEntry, ReadFileStateMap, ToolEntry
from .ports import (
    EDIT_ERROR_CODE,
    EDIT_INPUT_JSON_SCHEMA,
    EDIT_INPUT_SCHEMA,
    EDIT_OUTPUT_JSON_SCHEMA,
    EDIT_OUTPUT_SCHEMA,
    EDIT_TOOL_NAME,
    is_file_system_port_error,
)
from .tool_perf import (
    attach_tool_execution_telemetry,
    elapsed_ms_since,
    file_byte_count,
    workspace_kind,
)

EDIT_PROVIDER_DESCRIPTION = "\n".join(
    [
        "Performs exact string replacement in a file.",
        "",
        "- You must Read the file in this conversation before editing, or the call will fail.",
        "- `old_string` must match the file exactly, including indentation, and be unique — the edit fails otherwise. Strip the Read line prefix (line number + tab) before matching.",
        "- `replace_all: true` replaces every occurrence instead.",
    ]
)
NON_UNIQUE_OLD_STRING_MESSAGE = (
    "old_string is not unique in the file. Provide more surrounding context or set replace_all to true."
)
MAX_EDIT_FILE_SIZE_BYTES = 1024 * 1024 * 1024
EDIT_NOT_READ_MESSAGE = "File has not been read yet. Read it first before writing to it."
EDIT_STALE_MESSAGE = (
    "File has been modified since read, either by the user or by a linter. Read it again before attempting to write it."
)
EDIT_FRESHNESS_SUFFIX = " (file state is current in your context — no need to Read it back)"


def format_edit_model_content(output: Any) -> str:
    """Mirrors ``formatEditModelContent(output)``."""
    file_path = output.get("filePath") if _is_record(output) and isinstance(output.get("filePath"), str) else "the file"
    user_modified = _is_record(output) and output.get("userModified") is True
    replace_all = _is_record(output) and output.get("replaceAll") is True
    modified_note = (
        ".  The user modified your proposed changes before accepting them. " if user_modified else ""
    )
    freshness_suffix = "" if user_modified else EDIT_FRESHNESS_SUFFIX

    if replace_all:
        return f"The file {file_path} has been updated{modified_note}. All occurrences were successfully replaced.{freshness_suffix}"

    return f"The file {file_path} has been updated successfully{modified_note}.{freshness_suffix}"


async def edit_handler(input: Any, context: Any) -> Any:
    """Mirrors ``editHandler: ToolHandler``."""
    file_path_input = input.get("file_path")
    old_string_raw = input.get("old_string")
    new_string_raw = input.get("new_string")
    replace_all = bool(input.get("replace_all", False))

    file_system_port = context.file_system_port
    if not file_system_port:
        raise create_core_error(
            CoreErrorType.CONFIGURATION_ERROR,
            "FileSystemPort is not configured for Edit tool",
            {
                "context": {"toolCallId": context.tool_call_id, "toolName": "Edit"},
                "recoverable": False,
            },
        )

    if old_string_raw == new_string_raw:
        return _edit_failure(
            EDIT_ERROR_CODE["NO_CHANGE"],
            "No changes to make: old_string and new_string are exactly the same.",
        )

    if not file_path_input:
        # 空路径由共享 path-policy 抛出普通异常，绕过了 Edit 自己维护的
        # code + message 失败契约，导致 provider-visible 内容丢失 tool_use_error envelope。
        return _edit_failure(EDIT_ERROR_CODE["INVALID_PATH"], "Tool path must not be empty")

    file_path = resolve_workspace_path(
        ToolWorkspacePathOptions(
            input_path=file_path_input,
            operation="write",
            working_directory=context.working_directory,
            workspace_root=context.workspace_root,
        )
    )

    stat = await _stat_editable_file(file_path, context)
    if stat is None:
        if old_string_raw == "":
            return await _write_edit_result(
                context=context,
                file_path=file_path,
                input_file_path=file_path_input,
                original_file="",
                actual_old_string="",
                actual_new_string=new_string_raw,
                new_content=new_string_raw,
                read=None,
                replace_all=replace_all,
                fs_read_ms=0,
                patch_match_ms=0,
                match_attempts=0,
            )

        return _edit_failure(
            EDIT_ERROR_CODE["FILE_NOT_EXIST"],
            await _create_missing_edit_file_message(file_path, context),
        )

    if (_get_field(stat, "size_bytes") or 0) > MAX_EDIT_FILE_SIZE_BYTES:
        return _edit_failure(
            EDIT_ERROR_CODE["FILE_TOO_LARGE"],
            "File is too large to edit (1GB). Maximum editable file size is 1GB.",
        )

    read_started_at = int(time.time() * 1000)
    read = await file_system_port.read_text_file(
        {"path": file_path, "trace": _create_edit_trace(context)},
        {"signal": context.abort_signal},
    )
    fs_read_ms = elapsed_ms_since(read_started_at)
    content = normalize_line_endings(read.content)
    old_string = normalize_line_endings(old_string_raw)
    requested_new_string = normalize_line_endings(new_string_raw)

    if old_string == "":
        if content.strip() != "":
            return _edit_failure(
                EDIT_ERROR_CODE["FILE_EXISTS_NO_OLD_STRING"],
                "Cannot create new file - file already exists.",
            )
        read_state_failure = _get_editable_read_state_failure(file_path, read, context.read_file_state)
        if read_state_failure is not None:
            return read_state_failure
        return await _write_edit_result(
            context=context,
            file_path=file_path,
            input_file_path=file_path_input,
            original_file=content,
            actual_old_string="",
            actual_new_string=requested_new_string,
            new_content=requested_new_string,
            read=read,
            replace_all=replace_all,
            fs_read_ms=fs_read_ms,
            patch_match_ms=0,
            match_attempts=0,
        )

    if file_path.endswith(".ipynb"):
        return _edit_failure(
            EDIT_ERROR_CODE["NOTEBOOK_FILE"],
            "File is a Jupyter Notebook. Use the NotebookEdit to edit this file.",
        )

    read_state_failure = _get_editable_read_state_failure(file_path, read, context.read_file_state)
    if read_state_failure is not None:
        return read_state_failure

    patch_match_started_at = int(time.time() * 1000)
    match = find_edit_match(content=content, search=old_string, replace_all=replace_all)
    patch_match_ms = elapsed_ms_since(patch_match_started_at)
    if match.status == "not_found":
        return _edit_failure(
            EDIT_ERROR_CODE["OLD_STRING_NOT_FOUND"],
            f"String to replace not found in file.\nString: {old_string}",
        )
    if match.status == "ambiguous":
        return _edit_failure(
            EDIT_ERROR_CODE["AMBIGUOUS_REPLACE"],
            _create_ambiguous_edit_message(match.candidate_count, old_string),
        )

    actual_old_string = match.actual_string
    match_count = _count_occurrences(content, actual_old_string)
    if not replace_all and match_count > 1:
        return _edit_failure(
            EDIT_ERROR_CODE["AMBIGUOUS_REPLACE"],
            _create_ambiguous_edit_message(match_count, old_string),
        )

    normalized_new_string = normalize_replacement_for_match(match.strategy, requested_new_string)
    actual_new_string = preserve_quote_style(old_string, actual_old_string, normalized_new_string)
    new_content = _apply_edit_to_content(content, actual_old_string, actual_new_string, replace_all)

    return await _write_edit_result(
        context=context,
        file_path=file_path,
        input_file_path=file_path_input,
        original_file=content,
        actual_old_string=actual_old_string,
        actual_new_string=actual_new_string,
        new_content=new_content,
        read=read,
        replace_all=replace_all,
        match_strategy=match.strategy,
        match_candidate_count=match.candidate_count,
        fs_read_ms=fs_read_ms,
        patch_match_ms=patch_match_ms,
        match_attempts=1,
    )


def _edit_failure(error_code: int, message: str) -> dict[str, Any]:
    return {"result": False, "errorCode": error_code, "message": message}


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _count_occurrences(content: str, needle: str) -> int:
    count = 0
    position = 0
    while position < len(content):
        index = content.find(needle, position)
        if index == -1:
            break
        count += 1
        position = index + len(needle)
    return count


def _create_edit_trace(context: Any) -> dict[str, Any]:
    return {
        "traceId": context.trace_id,
        "spanId": context.span_id,
        "parentSpanId": context.parent_span_id,
        "sessionId": context.session_id,
        "turnId": context.turn_id,
    }


async def _stat_editable_file(file_path: str, context: Any) -> Any:
    file_system_port = context.file_system_port
    if not file_system_port:
        return None
    try:
        return await file_system_port.stat(
            {"path": file_path, "trace": _create_edit_trace(context)},
            {"signal": context.abort_signal},
        )
    except Exception as error:  # noqa: BLE001
        if is_file_system_port_error(error) and getattr(error, "code", None) == "not_found":
            return None
        raise


async def _create_missing_edit_file_message(file_path: str, context: Any) -> str:
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
            {"path": parent, "trace": _create_edit_trace(context)},
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


def _get_editable_read_state_failure(
    file_path: str, current_read: Any, read_file_state: Optional[ReadFileStateMap]
) -> Optional[dict[str, Any]]:
    if read_file_state is None:
        return None

    last_read = find_editable_read_file_state(read_file_state, file_path)
    if last_read is None or last_read.is_partial_view:
        return _edit_failure(EDIT_ERROR_CODE["FILE_NOT_READ"], EDIT_NOT_READ_MESSAGE)

    if not _has_read_state_changed(last_read, current_read):
        return None
    if _is_strict_full_read(last_read) and last_read.content == current_read.content:
        return None

    return _edit_failure(EDIT_ERROR_CODE["STALE_FILE"], EDIT_STALE_MESSAGE)


def _is_strict_full_read(entry: ReadFileStateEntry) -> bool:
    if entry.is_partial_view:
        return False
    return (entry.offset or 1) <= 1 and entry.limit is None


def _has_read_state_changed(last_read: ReadFileStateEntry, current_read: Any) -> bool:
    current_revision = _get_field(current_read, "revision")
    current_mtime_ms = _get_field(current_revision, "mtime_ms") if current_revision is not None else None
    if last_read.mtime_ms is not None and current_mtime_ms is not None:
        # 亚毫秒级精度，只在当前文件的整数毫秒晚于 Read 记录或大小变化时判 stale，减少误报。
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

    current_revision_id = _get_field(current_revision, "id") if current_revision is not None else None
    return bool(last_read.revision_id and current_revision_id and last_read.revision_id != current_revision_id)


async def _write_edit_result(
    *,
    context: Any,
    file_path: str,
    input_file_path: str,
    original_file: str,
    actual_old_string: str,
    actual_new_string: str,
    new_content: str,
    read: Any,
    replace_all: bool,
    fs_read_ms: int,
    patch_match_ms: int,
    match_attempts: int,
    match_strategy: Optional[str] = None,
    match_candidate_count: Optional[int] = None,
) -> Any:
    file_system_port = context.file_system_port
    if not file_system_port:
        raise create_core_error(
            CoreErrorType.CONFIGURATION_ERROR,
            "FileSystemPort is not configured for Edit tool",
            {
                "context": {"toolCallId": context.tool_call_id, "toolName": "Edit"},
                "recoverable": False,
            },
        )

    content_to_write = stamp_memory_origin_session_id(
        StampMemoryOriginSessionIdInput(
            content=new_content,
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
            "encoding": _get_field(read, "encoding") if read is not None else None,
            "lineEndings": (_get_field(read, "line_endings") if read is not None else None)
            or _detect_line_endings(original_file),
            "createParents": True,
            "atomic": True,
            "expectedRevision": _get_field(read, "revision") if read is not None else None,
            "trace": _create_edit_trace(context),
        },
        {"signal": context.abort_signal},
    )
    fs_write_ms = elapsed_ms_since(write_started_at)

    read_file_state_entry = _update_read_file_state_after_edit(
        context.read_file_state, file_path, content_to_write, _get_field(write_result, "revision")
    )
    _record_read_file_state_metadata(context, read_file_state_entry)

    structured_patch = create_structured_patch(
        file_path=input_file_path, old_content=original_file, new_content=content_to_write
    )
    # perf 里 totalBytes/maxFileBytes 语义相同，缓存结果避免大文件编辑时重复扫描新内容。
    new_content_bytes = file_byte_count(content_to_write)

    return attach_tool_execution_telemetry(
        {
            "filePath": input_file_path,
            "oldString": actual_old_string,
            "newString": actual_new_string,
            "originalFile": original_file,
            "structuredPatch": structured_patch,
            "userModified": False,
            "replaceAll": replace_all,
            "matchStrategy": match_strategy,
            "matchCandidateCount": match_candidate_count,
        },
        {
            "detail": {
                "kind": "patch",
                "filesystem": {
                    "readMs": fs_read_ms,
                    "writeMs": fs_write_ms,
                    "fileCount": 1,
                    "totalBytes": new_content_bytes,
                    "maxFileBytes": new_content_bytes,
                    "workspaceKind": workspace_kind(context),
                },
                "patch": {
                    "matchMs": patch_match_ms,
                    "hunkCount": len(structured_patch),
                    "matchAttempts": match_attempts,
                },
            }
        },
    )


def _update_read_file_state_after_edit(
    read_file_state: Optional[ReadFileStateMap], file_path: str, content: str, revision: Any
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
        source_tool="Edit",
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
            "tool_name": "Edit",
        }
    )
    if metadata is not None:
        recorder(metadata)


def _apply_edit_to_content(content: str, old_string: str, new_string: str, replace_all: bool) -> str:
    replacement = new_string
    if replacement != "":
        return _replace_literal(content, old_string, replacement, replace_all)

    search = f"{old_string}\n" if (not old_string.endswith("\n") and f"{old_string}\n" in content) else old_string
    return _replace_literal(content, search, replacement, replace_all)


def _replace_literal(content: str, search: str, replacement: str, replace_all: bool) -> str:
    # String.replace 的字符串 replacement 会把 $$/$& 当特殊 token；
    # PORT: Python str.replace 无此语义，直接替换即可（等价于 TS 的 () => replacement）。
    if replace_all:
        return content.replace(search, replacement)
    return content.replace(search, replacement, 1)


def _create_ambiguous_edit_message(match_count: int, old_string: str) -> str:
    if match_count > 0:
        return (
            f"Found {match_count} matches of the string to replace, but replace_all is false. "
            "To replace all occurrences, set replace_all to true. To replace only one occurrence, "
            f"please provide more context to uniquely identify the instance.\nString: {old_string}"
        )
    return NON_UNIQUE_OLD_STRING_MESSAGE


def _detect_line_endings(content: str) -> str:
    crlf_count = 0
    lf_count = 0
    for index, char in enumerate(content):
        if char != "\n":
            continue
        if index > 0 and content[index - 1] == "\r":
            crlf_count += 1
        else:
            lf_count += 1
    return "CRLF" if crlf_count > lf_count else "LF"


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


def _build_edit_tool_entry() -> ToolEntry:
    return ToolEntry(
        capability="Replace exact text in a file through the file-system adapter",
        metadata={
            "name": EDIT_TOOL_NAME,
            "description": EDIT_PROVIDER_DESCRIPTION,
            "read_only": False,
            "destructive": False,
            "concurrent_safe": False,
            "timeout_ms": 30000,
            "max_output_bytes": 1_000_000,
            "side_effect_scope": "workspace",
            "risk_level": "medium",
            "needs_approval": True,
        },
        handler=edit_handler,
        format_model_content=format_edit_model_content,
        input_schema=EDIT_INPUT_JSON_SCHEMA,
        output_schema=EDIT_OUTPUT_JSON_SCHEMA,
        runtime_input_schema=EDIT_INPUT_SCHEMA,
        runtime_output_schema=EDIT_OUTPUT_SCHEMA,
        permission={
            "permission": "edit",
            "reason": "Edit modifies file contents through the file-system adapter",
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
            "user_visible_message": "Edit was cancelled before the file operation completed",
        },
        trace={
            "propagate_to_adapters": True,
            "record_input": "summary",
            "record_output": "summary",
        },
    )


edit_tool_entry: ToolEntry = _build_edit_tool_entry()
