"""Read File State Metadata — persisted read-state for resume/stale guards.

Ported from: packages/core/src/tool/read-file-state-metadata.ts
Original package: @zcode/core (tool)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Union

from .read_file_state import create_read_file_state_key
from .types import ReadFileStateEntry, ReadFileStateMap

READ_FILE_STATE_METADATA_SCHEMA_VERSION = 1
PersistedReadFileStateTool = str  # "Read" | "Write" | "Edit"


@dataclass(slots=True)
class PersistedReadFileStateMetadata:
    """Mirrors ``interface PersistedReadFileStateMetadata``."""

    schema_version: int
    tool: PersistedReadFileStateTool
    path: str
    content: str
    is_partial_view: bool
    read_at_ms: int
    revision_id: str
    mtime_ms: int
    size_bytes: int
    offset: Optional[int] = None
    limit: Optional[int] = None


def create_read_file_state_metadata(input: dict[str, Any]) -> Optional[PersistedReadFileStateMetadata]:
    """Mirrors ``createReadFileStateMetadata(input)``.

    PORT: TS ``ReadOutputSchema.safeParse`` 的 zod 校验在此按下游消费的字段子集
    做等价判定（仅 ``text`` / ``file_unchanged`` 参与持久化）。
    """
    if input.get("tool_name") != "Read":
        return None

    output = _as_record(input.get("output"))
    if output is None:
        return None
    output_type = output.get("type")
    if output_type not in ("text", "file_unchanged"):
        return None
    file_path = output.get("filePath")
    if not isinstance(file_path, str):
        return None

    tool_input = _as_record(input.get("tool_input"))
    offset = _number_field(tool_input, "offset") if tool_input else None
    limit = _number_field(tool_input, "limit") if tool_input else None
    read_file_state: Optional[ReadFileStateMap] = input.get("read_file_state")
    entry = (
        read_file_state.get(create_read_file_state_key(file_path, 1 if offset is None else offset, limit))
        if read_file_state is not None
        else None
    )
    return create_read_file_state_metadata_from_entry(
        {
            "completed_at": input.get("completed_at"),
            "entry": entry,
            "tool_name": "Read",
        }
    )


def create_read_file_state_metadata_from_entry(
    input: dict[str, Any],
) -> Optional[PersistedReadFileStateMetadata]:
    """Mirrors ``createReadFileStateMetadataFromEntry(input)``."""
    entry: Optional[ReadFileStateEntry] = input.get("entry")
    if entry is None:
        return None
    if entry.mtime_ms is None or entry.size_bytes is None or not entry.revision_id:
        # resume stale guard 必须恢复文件时间戳和 adapter revision；
        # 缺 freshness metadata 的历史文件状态不能冒充已读，否则会放过 stale 写入。
        return None

    return PersistedReadFileStateMetadata(
        schema_version=READ_FILE_STATE_METADATA_SCHEMA_VERSION,
        tool=input["tool_name"],
        path=entry.path,
        content=entry.content,
        is_partial_view=entry.is_partial_view,
        read_at_ms=_to_ms(input.get("completed_at")),
        revision_id=entry.revision_id,
        mtime_ms=entry.mtime_ms,
        size_bytes=entry.size_bytes,
        offset=entry.offset,
        limit=entry.limit,
    )


def parse_read_file_state_metadata(metadata: Any) -> Optional[PersistedReadFileStateMetadata]:
    """Mirrors ``parseReadFileStateMetadata(metadata)``."""
    record = _as_record(metadata)
    if record is None:
        return None
    read_state = _as_record(record.get("readFileState"))
    if read_state is None:
        return None

    if read_state.get("schemaVersion") != READ_FILE_STATE_METADATA_SCHEMA_VERSION:
        return None
    tool = _string_field(read_state, "tool")
    if not _is_persisted_read_file_state_tool(tool):
        return None

    path = _string_field(read_state, "path")
    content = _string_field(read_state, "content")
    read_at_ms = _number_field(read_state, "readAtMs")
    is_partial_view = _boolean_field(read_state, "isPartialView")
    revision_id = _string_field(read_state, "revisionId")
    mtime_ms = _number_field(read_state, "mtimeMs")
    size_bytes = _number_field(read_state, "sizeBytes")
    if (
        not path
        or content is None
        or read_at_ms is None
        or is_partial_view is None
        or not revision_id
        or mtime_ms is None
        or size_bytes is None
    ):
        return None
    offset = _number_field(read_state, "offset")
    limit = _number_field(read_state, "limit")

    return PersistedReadFileStateMetadata(
        schema_version=READ_FILE_STATE_METADATA_SCHEMA_VERSION,
        tool=tool,
        path=path,
        content=content,
        is_partial_view=is_partial_view,
        read_at_ms=int(read_at_ms),
        revision_id=revision_id,
        mtime_ms=int(mtime_ms),
        size_bytes=int(size_bytes),
        offset=offset,
        limit=limit,
    )


def _is_persisted_read_file_state_tool(value: Any) -> bool:
    return value in ("Read", "Write", "Edit")


def _to_ms(value: Any) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def _as_record(value: Any) -> Optional[dict[str, Any]]:
    if isinstance(value, dict):
        return value
    return None


def _string_field(record: dict[str, Any], key: str) -> Optional[str]:
    value = record.get(key)
    return value if isinstance(value, str) else None


def _number_field(record: dict[str, Any], key: str) -> Optional[int]:
    value = record.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value == value and value not in (float("inf"), float("-inf")):
        return int(value)
    return None


def _boolean_field(record: dict[str, Any], key: str) -> Optional[bool]:
    value = record.get(key)
    return value if isinstance(value, bool) else None


__all__ = [
    "READ_FILE_STATE_METADATA_SCHEMA_VERSION",
    "PersistedReadFileStateMetadata",
    "create_read_file_state_metadata",
    "create_read_file_state_metadata_from_entry",
    "parse_read_file_state_metadata",
]
