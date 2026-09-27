"""Tool Path Policy.

Ported from: packages/core/src/tool/path-policy.ts
Original package: @zcode/core (tool)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from ..contracts.errors import CoreErrorType, create_core_error

__all__ = [
    "ToolWorkspacePathOptions",
    "ToolWorkspaceDirectoryOptions",
    "resolve_workspace_path",
    "resolve_tool_working_directory",
]


@dataclass(slots=True)
class ToolWorkspacePathOptions:
    """Mirrors ``interface ToolWorkspacePathOptions``."""

    input_path: str
    working_directory: str
    workspace_root: str
    # "read" | "write" | "execute"
    operation: str


@dataclass(slots=True)
class ToolWorkspaceDirectoryOptions:
    """Mirrors ``Omit<ToolWorkspacePathOptions, "inputPath">``."""

    working_directory: str
    workspace_root: str
    # "read" | "write" | "execute"
    operation: str


def resolve_workspace_path(options: ToolWorkspacePathOptions) -> str:
    """Mirrors ``resolveWorkspacePath(options)``."""
    working_directory = _normalize_absolute_directory(
        options.working_directory,
        "workingDirectory",
    )
    _normalize_absolute_directory(options.workspace_root, "workspaceRoot")
    requested_path = options.input_path

    if len(requested_path) == 0:
        raise create_core_error(
            CoreErrorType.TOOL_EXECUTION_FAILED,
            "Tool path must not be empty",
            {
                "context": {"operation": options.operation},
                "recoverable": True,
            },
        )

    resolved_path = (
        os.path.normpath(requested_path)
        if os.path.isabs(requested_path)
        # PORT: JS ``resolve(workingDirectory, requestedPath)`` = 绝对化 + 归一化；
        # Python 用 ``join`` + ``normpath`` 等价（``join`` 在第二个参数为绝对路径时直接返回它）。
        else os.path.normpath(os.path.join(working_directory, requested_path))
    )

    # Current release intentionally does not hard-block paths outside workspaceRoot.
    # Cause: subagents may need to inspect user-requested sibling repos or external files
    # before the filesystem permission adapter grows explicit ask/deny rules for them.
    return resolved_path


def resolve_tool_working_directory(
    input_cwd: Optional[str],
    options: ToolWorkspaceDirectoryOptions,
) -> str:
    """Mirrors ``resolveToolWorkingDirectory(inputCwd, options)``."""
    if not input_cwd:
        working_directory = _normalize_absolute_directory(
            options.working_directory,
            "workingDirectory",
        )
        _normalize_absolute_directory(options.workspace_root, "workspaceRoot")
        return working_directory

    return resolve_workspace_path(
        ToolWorkspacePathOptions(
            input_path=input_cwd,
            working_directory=options.working_directory,
            workspace_root=options.workspace_root,
            operation=options.operation,
        )
    )


def _normalize_absolute_directory(value: str, label: str) -> str:
    """Mirrors the non-exported ``normalizeAbsoluteDirectory(value, label)``."""
    normalized = os.path.normpath(value)
    if not os.path.isabs(normalized):
        raise create_core_error(
            CoreErrorType.CONFIGURATION_ERROR,
            f"{label} must be an absolute path for tool execution",
            {
                "context": {label: value},
                "recoverable": False,
            },
        )
    return normalized
