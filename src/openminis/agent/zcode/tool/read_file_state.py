"""Read File State Helpers.

Ported from: packages/core/src/tool/read-file-state.ts
Original package: @zcode/core (tool)
"""

from __future__ import annotations

import math
import sys
from typing import Callable, Optional

from .path_normalization import normalize_tool_path_for_comparison
from .types import ReadFileStateEntry, ReadFileStateMap

ReadFileStatePlatform = str


def _create_read_file_state_path_key(
    file_path: str, platform: ReadFileStatePlatform = sys.platform
) -> str:
    """Mirrors ``createReadFileStatePathKey``."""
    return normalize_tool_path_for_comparison(file_path, platform)


def create_read_file_state_key(
    file_path: str,
    offset: Optional[int],
    limit: Optional[int],
    platform: ReadFileStatePlatform = sys.platform,
) -> str:
    """Mirrors ``createReadFileStateKey(filePath, offset, limit, platform?)``."""
    return "\0".join(
        [
            _create_read_file_state_path_key(file_path, platform),
            str(1 if offset is None else offset),
            "" if limit is None else str(limit),
        ]
    )


def find_editable_read_file_state(
    read_file_state: Optional[ReadFileStateMap],
    file_path: str,
    platform: ReadFileStatePlatform = sys.platform,
) -> Optional[ReadFileStateEntry]:
    """Mirrors ``findEditableReadFileState``."""
    return find_latest_read_file_state(read_file_state, file_path, platform)


def find_latest_read_file_state(
    read_file_state: Optional[ReadFileStateMap],
    file_path: str,
    platform: ReadFileStatePlatform = sys.platform,
) -> Optional[ReadFileStateEntry]:
    """Mirrors ``findLatestReadFileState``."""
    if not read_file_state:
        return None

    # 优先返回 full Read 会让 Bash/formatter 改完文件后即使模型按提示重新
    # range Read，Edit/Write 仍拿旧 full Read 做 mtime 校验并持续误报 stale。这里按
    # 单文件最新 read-state 语义选择基准；真正的 partial view 由消费者单独拒绝。
    return _find_latest_read_file_state_by_path(
        read_file_state, file_path, platform, lambda entry: True
    )


def normalize_read_file_state_mtime_ms(mtime_ms: Optional[float]) -> Optional[int]:
    """Mirrors ``normalizeReadFileStateMtimeMs``."""
    if mtime_ms is None:
        return None
    return math.floor(mtime_ms)


def _find_latest_read_file_state_by_path(
    read_file_state: ReadFileStateMap,
    file_path: str,
    platform: ReadFileStatePlatform,
    accepts: Callable[[ReadFileStateEntry], bool],
) -> Optional[ReadFileStateEntry]:
    path_key = _create_read_file_state_path_key(file_path, platform)
    latest: Optional[ReadFileStateEntry] = None
    latest_read_at = float("-inf")
    for entry in read_file_state.values():
        if _create_read_file_state_path_key(entry.path, platform) != path_key:
            continue
        if not accepts(entry):
            continue
        # PORT: TS ``entry.readAt.getTime()``（毫秒 epoch）→ ``timestamp() * 1000``。
        read_at = entry.read_at.timestamp() * 1000
        if read_at < latest_read_at:
            continue
        latest = entry
        latest_read_at = read_at
    return latest


__all__ = [
    "create_read_file_state_key",
    "find_editable_read_file_state",
    "find_latest_read_file_state",
    "normalize_read_file_state_mtime_ms",
]
