"""Memory File Path — resolves memory paths contained within the memory root.

Ported from: packages/core/src/memory/memory-file-path.ts
Original package: @zcode/core (memory)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

from ..tool.path_policy import ToolWorkspacePathOptions, resolve_workspace_path


SENSITIVE_MEMORY_PATH_SEGMENTS = frozenset(
    [
        ".git",
        "hooks",
        ".husky",
        ".githooks",
        "node_modules",
        ".vscode",
        ".idea",
        "head",
        "config",
        "objects",
        "refs",
        ".zcode",
        "skills",
        "commands",
        "agents",
        ".cargo",
        ".devcontainer",
        ".yarn",
        ".mvn",
    ]
)


@dataclass(slots=True)
class ResolveContainedMemoryFilePathInput:
    """Mirrors the inline argument object of ``resolveContainedMemoryFilePath``."""

    file_path: str
    root_dir: str
    working_directory: str
    workspace_root: str


@dataclass(slots=True)
class ResolveSafeMemoryFilePathInput:
    """Mirrors the inline argument object of ``resolveSafeMemoryFilePath``."""

    file_path: str
    root_dir: str
    working_directory: str
    workspace_root: str


def resolve_contained_memory_file_path(input: ResolveContainedMemoryFilePathInput) -> Optional[str]:
    """Mirrors ``resolveContainedMemoryFilePath(input)``.

    # PORT: 原 ``node:path`` 的 ``relative`` → ``os.path.relpath``；``isAbsolute`` → ``os.path.isabs``。
    """
    resolved_path = resolve_workspace_path(
        ToolWorkspacePathOptions(
            input_path=input.file_path,
            operation="write",
            working_directory=input.working_directory,
            workspace_root=input.workspace_root,
        )
    )
    relative_path = os.path.relpath(resolved_path, input.root_dir)
    return relative_path if is_contained_relative_path(relative_path) else None


def resolve_safe_memory_file_path(input: ResolveSafeMemoryFilePathInput) -> Optional[str]:
    """Mirrors ``resolveSafeMemoryFilePath(input)``."""
    resolved_path = resolve_contained_memory_file_path(input)
    if resolved_path is None:
        return None
    relative_path = memory_file_relative_path(input.root_dir, resolved_path)
    return (
        relative_path
        if relative_path is not None and not contains_sensitive_memory_path_segment(relative_path)
        else None
    )


def memory_file_relative_path(root_dir: str, file_path: str) -> Optional[str]:
    """Mirrors ``memoryFileRelativePath(rootDir, filePath)``."""
    relative_path = os.path.relpath(file_path, root_dir)
    return relative_path if is_contained_relative_path(relative_path) else None


def contains_sensitive_memory_path_segment(relative_path: str) -> bool:
    """Mirrors ``containsSensitiveMemoryPathSegment(relativePath)``."""
    return any(
        SENSITIVE_MEMORY_PATH_SEGMENTS.__contains__(normalize_sensitive_segment(segment))
        for segment in re.split(r"[\\/]+", relative_path)
    )


def is_contained_relative_path(relative_path: str) -> bool:
    """Mirrors ``isContainedRelativePath(relativePath)``.

    # PORT: 原 ``node:path`` 的 ``isAbsolute`` → ``os.path.isabs``。
    """
    return (
        len(relative_path) > 0
        and relative_path != ".."
        and not relative_path.startswith("../")
        and not relative_path.startswith("..\\")
        and not os.path.isabs(relative_path)
    )


def normalize_sensitive_segment(segment: str) -> str:
    """Mirrors ``normalizeSensitiveSegment(segment)``."""
    # PORT: TS 正则 ``/[\u200c-\u200f\u202a-\u202e\u206a-\u206f\ufeff]/gu`` → ``re.sub``。
    without_controls = re.sub(r"[\u200c-\u200f\u202a-\u202e\u206a-\u206f\ufeff]", "", segment)
    without_controls = without_controls.lower()
    head = without_controls.split(":", 1)[0]
    return re.sub(r"[. ]+$", "", head)
