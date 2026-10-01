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

    # ``len(None)`` 会抛 ``TypeError: object of type 'NoneType' has no len()`` ——
    # 模型看不懂这种错，只会原地重试。路径非字符串（缺参数时调用方传 None）统一
    # 走「路径不能为空」这条可读失败：上游 JS 是 ``undefined.length`` 直接
    # TypeError，这里把它收成同一条语义化错误，行为等价但可诊断。
    if not isinstance(requested_path, str) or len(requested_path) == 0:
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
