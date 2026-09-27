"""Memory Recall Manifest — scans memory files and formats a manifest.

Ported from: packages/core/src/memory/recall/manifest.ts
Original package: @zcode/core (memory)
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, Sequence

from .types import MEMORY_RECALL_TYPES, MemoryManifestEntry, MemoryRecallType

# PORT: 原 TS 用 npm ``yaml`` 的 ``parse`` 解析 frontmatter；此处用 PyYAML。运行环境需安装 PyYAML。
try:  # pragma: no cover - 由运行环境决定
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


MANIFEST_FILE_LIMIT = 200
MANIFEST_PREVIEW_LINE_LIMIT = 30


# ---------------------------------------------------------------------------
# PORT: @zcode/contracts ``FileSystemPort`` 端口占位（未移植）
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class DirEntry:
    """PORT: 目录列举项的本地数据形状（``{ kind, path }``）。"""

    kind: str
    path: str


@dataclass(slots=True)
class DirectoryListing:
    """PORT: ``listDirectory`` 返回 ``{ entries }``。"""

    entries: list[DirEntry]


@dataclass(slots=True)
class FileStat:
    """PORT: ``stat`` 返回 ``{ kind, mtimeMs? }``。"""

    kind: str
    mtime_ms: Optional[int] = None


@dataclass(slots=True)
class TextFileRange:
    """PORT: ``readTextFileRange`` 返回 ``{ content }``。"""

    content: str


class FileSystemPort(Protocol):
    """PORT: TS ``FileSystemPort`` — 方法名就近按 Snake 命名并 ``# PORT:`` 标注原 Node API。

    # PORT: 原 ``listDirectory({ path }, { signal })`` → ``list_directory``。
    # PORT: 原 ``stat({ path }, { signal })`` → ``stat``。
    # PORT: 原 ``readTextFileRange({ path, offsetLine, limitLines }, { signal })`` → ``read_text_file_range``。
    """

    async def list_directory(
        self, *, path: str, signal: Optional[Any] = None
    ) -> DirectoryListing: ...

    async def stat(self, *, path: str, signal: Optional[Any] = None) -> FileStat: ...

    async def read_text_file_range(
        self,
        *,
        path: str,
        offset_line: int,
        limit_lines: int,
        signal: Optional[Any] = None,
    ) -> TextFileRange: ...


@dataclass(slots=True)
class ScanMemoryManifestInput:
    """Mirrors the inline argument object of ``scanMemoryManifest``."""

    file_system: FileSystemPort
    root_dir: str
    signal: Optional[Any] = None


async def scan_memory_manifest(input: ScanMemoryManifestInput) -> list[MemoryManifestEntry]:
    """Mirrors ``scanMemoryManifest(input)``."""
    try:
        paths = await collect_memory_paths(input.file_system, input.root_dir, input.signal)
        settled = await asyncio.gather(
            *[
                read_manifest_entry(input.file_system, input.root_dir, path, input.signal)
                for path in paths
            ],
            return_exceptions=True,
        )
        entries = [
            result
            for result in settled
            if not isinstance(result, Exception)
        ]
        # PORT: 原 ``sort((l, r) => r.mtimeMs - l.mtimeMs)`` → 按 mtimeMs 降序。
        entries.sort(key=lambda entry: entry.mtime_ms, reverse=True)
        return entries[:MANIFEST_FILE_LIMIT]
    except Exception:  # noqa: BLE001
        return []


def format_memory_manifest(manifest: Sequence[MemoryManifestEntry]) -> str:
    """Mirrors ``formatMemoryManifest(manifest)``."""
    lines: list[str] = []
    for entry in manifest:
        type_prefix = f"[{entry.type}] " if entry.type else ""
        # PORT: 原 ``new Date(entry.mtimeMs).toISOString()`` → UTC aware datetime 的 ISO 格式。
        timestamp = datetime.fromtimestamp(entry.mtime_ms / 1000, tz=timezone.utc).isoformat()
        base = f"- {type_prefix}{entry.filename} ({timestamp})"
        lines.append(f"{base}: {entry.description}" if entry.description else base)
    return "\n".join(lines)


async def collect_memory_paths(
    file_system: FileSystemPort, directory: str, signal: Optional[Any] = None
) -> list[str]:
    """Mirrors ``collectMemoryPaths(fileSystem, directory, signal)``."""
    listed = await file_system.list_directory(path=directory, signal=signal)
    paths: list[str] = []

    for entry in listed.entries:
        if entry.kind == "directory":
            paths.extend(await collect_memory_paths(file_system, entry.path, signal))
            continue
        if entry.kind == "file":
            if is_memory_candidate(entry.path):
                paths.append(entry.path)
            continue
        # symlink：仅在解析后确为文件时才纳入。
        if entry.kind != "symlink" or not is_memory_candidate(entry.path):
            continue
        try:
            target = await file_system.stat(path=entry.path, signal=signal)
            if target.kind == "file":
                paths.append(entry.path)
        except Exception:  # noqa: BLE001
            # 单个失效的文件 symlink 与单个无法读取的事实文件一样，不影响其他 manifest 项。
            pass

    return paths


def is_memory_candidate(file_path: str) -> bool:
    """Mirrors ``isMemoryCandidate(filePath)``."""
    return file_path.endswith(".md") and os.path.basename(file_path) != "MEMORY.md"


async def read_manifest_entry(
    file_system: FileSystemPort, root_dir: str, file_path: str, signal: Optional[Any] = None
) -> MemoryManifestEntry:
    """Mirrors ``readManifestEntry(fileSystem, rootDir, filePath, signal)``."""
    # PORT: 原 ``Promise.all([stat, readTextFileRange])`` → ``asyncio.gather``。
    stat, preview = await asyncio.gather(
        file_system.stat(path=file_path, signal=signal),
        file_system.read_text_file_range(
            path=file_path,
            offset_line=0,
            limit_lines=MANIFEST_PREVIEW_LINE_LIMIT,
            signal=signal,
        ),
    )
    frontmatter = parse_memory_frontmatter(preview.content)
    entry = MemoryManifestEntry(
        file_path=file_path,
        # PORT: 原 ``relative(rootDir, filePath).split(sep).join("/")`` → 跨平台用 "/" 拼接。
        filename=os.path.relpath(file_path, root_dir).replace(os.sep, "/"),
        mtime_ms=stat.mtime_ms if stat.mtime_ms is not None else 0,
    )
    if frontmatter.get("description") is not None:
        entry.description = frontmatter["description"]
    if frontmatter.get("type") is not None:
        entry.type = frontmatter["type"]
    return entry


def parse_memory_frontmatter(content: str) -> dict[str, Any]:
    """Mirrors ``parseMemoryFrontmatter(content)``。

    # PORT: 返回 ``{ description?, type? }`` 的字典，缺失字段不出现。
    """
    normalized = content.replace("\ufeff", "").replace("\r\n", "\n")
    lines = normalized.split("\n")
    if lines[0] != "---":
        return {}
    end = lines.index("---", 1) if len(lines) > 1 else -1
    if end < 0:
        return {}
    try:
        parsed = yaml.safe_load("\n".join(lines[1:end])) if yaml is not None else None
    except Exception:  # noqa: BLE001
        return {}
    if not is_record(parsed):
        return {}

    description = parsed.get("description") if isinstance(parsed.get("description"), str) else None
    metadata = parsed.get("metadata") if is_record(parsed.get("metadata")) else None
    type_candidate = (metadata or {}).get("type") if metadata is not None else parsed.get("type")
    type_value = type_candidate if is_memory_recall_type(type_candidate) else None
    result: dict[str, Any] = {}
    if description is not None:
        result["description"] = description
    if type_value is not None:
        result["type"] = type_value
    return result


def is_record(value: Any) -> bool:
    """Mirrors ``isRecord(value)``."""
    return isinstance(value, dict) and value is not None and not isinstance(value, list)


def is_memory_recall_type(value: Any) -> bool:
    """Mirrors ``isMemoryRecallType(value)``."""
    return isinstance(value, str) and value in MEMORY_RECALL_TYPES
