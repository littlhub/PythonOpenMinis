"""Memory Recall Types — manifest entry shape and recall type enum.

Ported from: packages/core/src/memory/recall/types.ts
Original package: @zcode/core (memory)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

# PORT: TS ``export const MEMORY_RECALL_TYPES = [...] as const`` → 模块级 tuple 常量。
MEMORY_RECALL_TYPES: tuple[str, ...] = ("user", "feedback", "project", "reference")

# PORT: TS ``type MemoryRecallType = (typeof MEMORY_RECALL_TYPES)[number]`` → ``Literal``。
MemoryRecallType = Literal["user", "feedback", "project", "reference"]


@dataclass(slots=True)
class MemoryManifestEntry:
    """Mirrors ``interface MemoryManifestEntry``.

    # PORT: 必填字段（filePath / filename / mtimeMs）置于可选字段之前（dataclass 顺序约束）。
    """

    file_path: str
    filename: str
    mtime_ms: int
    description: Optional[str] = None
    type: Optional[MemoryRecallType] = None
