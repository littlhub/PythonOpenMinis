"""Bash cwd policy — decide whether a successful command may move the shell cwd.

Ported from: packages/core/src/tool/handlers/bash-cwd-policy.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

from ..path_normalization import (
    ToolPathApi,
    ToolPathPlatform,
    get_tool_path_api,
    normalize_tool_path_for_comparison,
)
from ..types import ToolRuntimeScope

__all__ = [
    "BashCwdPolicyInput",
    "BashCwdPolicyDecision",
    "NormalizedCwdBoundary",
    "decide_bash_cwd_policy",
    "append_bash_cwd_stderr_suffix",
]

# PORT: TS ``process.platform`` 默认值 → ``sys.platform``。
_CURRENT_PLATFORM: ToolPathPlatform = os.sys.platform

_PRIVATE_VAR_RE = re.compile(r"^/private/var/")
_PRIVATE_TMP_RE = re.compile(r"^/private/tmp(/|$)")
_TRAILING_NEWLINES_RE = re.compile(r"[\r\n]+$")


@dataclass(slots=True)
class BashCwdPolicyInput:
    """Mirrors ``interface BashCwdPolicyInput``."""

    # PORT: TS ``ExecutionResult["status"]`` → 状态字面量；Python 用 str。
    status: str
    workspace_root: str
    resolved_cwd: Optional[str] = None
    exit_code: Optional[int] = None
    platform: Optional[ToolPathPlatform] = None
    runtime_scope: Optional[ToolRuntimeScope] = None


@dataclass(slots=True)
class BashCwdPolicyDecision:
    """Mirrors ``interface BashCwdPolicyDecision``."""

    next_working_directory: Optional[str] = None
    stderr_suffix: Optional[str] = None


@dataclass(slots=True)
class _EquivalentPair:
    """Mirrors the inline ``{ resolvedCwd; workspaceRoot }`` shape."""

    resolved_cwd: str
    workspace_root: str


@dataclass(slots=True)
class NormalizedCwdBoundary:
    """Mirrors ``interface NormalizedCwdBoundary``."""

    path_api: ToolPathApi
    resolved_cwd: str
    workspace_root: str
    equivalent_pairs: list[_EquivalentPair] = field(default_factory=list)


def decide_bash_cwd_policy(input: BashCwdPolicyInput) -> BashCwdPolicyDecision:
    """Mirrors ``decideBashCwdPolicy(input)``."""
    if input.status != "completed":
        return BashCwdPolicyDecision()
    if input.exit_code != 0:
        return BashCwdPolicyDecision()
    if not input.resolved_cwd:
        return BashCwdPolicyDecision()
    if (input.runtime_scope if input.runtime_scope is not None else "main") != "main":
        return BashCwdPolicyDecision()

    boundary = _normalize_cwd_boundary_input(input)

    if _is_resolved_cwd_inside_workspace(boundary):
        return BashCwdPolicyDecision(next_working_directory=boundary.resolved_cwd)

    return BashCwdPolicyDecision(
        next_working_directory=boundary.workspace_root,
        stderr_suffix=f"Shell cwd was reset to {boundary.workspace_root}",
    )


def append_bash_cwd_stderr_suffix(stderr: str, suffix: Optional[str]) -> str:
    """Mirrors ``appendBashCwdStderrSuffix(stderr, suffix)``."""
    if not suffix:
        return stderr
    stderr_without_trailing_newlines = _TRAILING_NEWLINES_RE.sub("", stderr)
    if not stderr_without_trailing_newlines:
        return suffix
    return f"{stderr_without_trailing_newlines}\n{suffix}"


def _normalize_cwd_boundary_input(input: BashCwdPolicyInput) -> NormalizedCwdBoundary:
    """Mirrors the non-exported ``normalizeCwdBoundaryInput(input)``."""
    platform = input.platform if input.platform is not None else _CURRENT_PLATFORM
    path_api = get_tool_path_api(platform)
    is_windows = platform == "win32"
    resolved_cwd = _normalize_cwd_boundary(input.resolved_cwd or "", is_windows)
    workspace_root = _normalize_cwd_boundary(input.workspace_root, is_windows)
    equivalent_pairs = [
        _EquivalentPair(resolved_cwd=resolved_cwd, workspace_root=workspace_root)
    ]
    # cwd 捕获使用 pwd -P/realpath 得到物理路径；session root 可能是 symlink。
    # 统一收集等价边界后再判断，避免把项目内物理路径误判成离开项目。
    real_resolved_cwd = _realpath_cwd_boundary(input.resolved_cwd or "", is_windows)
    real_workspace_root = _realpath_cwd_boundary(input.workspace_root, is_windows)

    if real_resolved_cwd and real_workspace_root:
        equivalent_pairs.append(
            _EquivalentPair(
                resolved_cwd=real_resolved_cwd,
                workspace_root=real_workspace_root,
            )
        )

    return NormalizedCwdBoundary(
        path_api=path_api,
        resolved_cwd=resolved_cwd,
        workspace_root=workspace_root,
        equivalent_pairs=equivalent_pairs,
    )


def _is_resolved_cwd_inside_workspace(boundary: NormalizedCwdBoundary) -> bool:
    """Mirrors the non-exported ``isResolvedCwdInsideWorkspace(boundary)``."""
    return any(
        _is_inside_or_same_path(pair.resolved_cwd, pair.workspace_root, boundary.path_api)
        for pair in boundary.equivalent_pairs
    )


def _is_inside_or_same_path(child: str, parent: str, path_api: ToolPathApi) -> bool:
    """Mirrors the non-exported ``isInsideOrSamePath(child, parent, pathApi)``."""
    relative_path = path_api.relative(parent, child)
    return relative_path == "" or not _is_path_outside_parent(relative_path, path_api)


def _is_path_outside_parent(relative_path: str, path_api: ToolPathApi) -> bool:
    """Mirrors the non-exported ``isPathOutsideParent(relativePath, pathApi)``."""
    return (
        relative_path == ".."
        or relative_path.startswith(f"..{path_api.sep}")
        or path_api.is_absolute(relative_path)
    )


def _normalize_cwd_boundary(value: str, is_windows: bool) -> str:
    """Mirrors the non-exported ``normalizeCwdBoundary(value, isWindows)``."""
    normalized = normalize_tool_path_for_comparison(
        value, "win32" if is_windows else "linux"
    )
    if not is_windows:
        normalized = _PRIVATE_VAR_RE.sub("/var/", normalized)
        normalized = _PRIVATE_TMP_RE.sub(r"/tmp\1", normalized)
    return normalized


def _realpath_cwd_boundary(value: str, is_windows: bool) -> Optional[str]:
    """Mirrors the non-exported ``realpathCwdBoundary(value, isWindows)``.

    # PORT: TS ``realpathSync.native(value)``（`node:fs`）→ ``os.path.realpath``。
    # 本仓既有先例（``tool/path_normalization.py``）同样直接用 stdlib 处理路径，
    # 未额外抽象端口；此处保持一致。解析失败（路径不存在等）一律返回 ``None``。
    """
    try:
        return _normalize_cwd_boundary(os.path.realpath(value), is_windows)
    except Exception:  # noqa: BLE001 — TS catch {} 吞掉一切，保持同样语义
        return None
