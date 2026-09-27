"""Memory file permission — allow memory Markdown writes.

Ported from: packages/core/src/tool/executor/memory-file-permission.ts
Original package: @zcode/core (tool/executor)

# PORT: 依赖 ``core/src/memory/memory-file-path.ts``（memory 模块，在范围内但尚未移植），
# 按模块映射直连其未来落点 ``...memory.memory_file_path``。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Optional

from ...memory.memory_file_path import (
    resolve_contained_memory_file_path,
    resolve_safe_memory_file_path,
)
from ...permission.ports import PermissionDecisionResult

__all__ = [
    "MemoryFileTargetInput",
    "MemoryFilePermissionInput",
    "apply_memory_file_permission",
    "targets_memory_file",
]


@dataclass(slots=True)
class MemoryFileTargetInput:
    """Mirrors ``interface MemoryFileTargetInput``."""

    execution_input: Any
    tool_name: str
    working_directory: str
    workspace_root: str
    memory_root: Optional[str] = None


@dataclass(slots=True)
class MemoryFilePermissionInput:
    """Mirrors ``interface MemoryFilePermissionInput extends MemoryFileTargetInput``."""

    decision: PermissionDecisionResult
    execution_input: Any
    tool_name: str
    working_directory: str
    workspace_root: str
    memory_root: Optional[str] = None


def apply_memory_file_permission(input: MemoryFilePermissionInput) -> PermissionDecisionResult:
    """Mirrors ``applyMemoryFilePermission(input)``."""
    target = _resolve_memory_file_target(input)
    if not target or not input.memory_root:
        return input.decision

    if (
        not target.endswith(".md")
        or resolve_safe_memory_file_path(
            file_path=target,
            root_dir=input.memory_root,
            working_directory=input.working_directory,
            workspace_root=input.workspace_root,
        )
        is None
    ):
        return input.decision
    if _preserves_existing_permission_decision(input.decision):
        return input.decision

    # PORT: TS ``{...input.decision, allowed, decision, escalated, reason, ruleId}``。
    return dataclasses.replace(
        input.decision,
        allowed=True,
        decision="allow",
        escalated=False,
        reason="Memory Markdown writes are allowed",
        rule_id="memory.file.markdown",
    )


def targets_memory_file(input: MemoryFileTargetInput) -> bool:
    """Mirrors ``targetsMemoryFile(input)``."""
    return _resolve_memory_file_target(input) is not None


def _resolve_memory_file_target(input: MemoryFileTargetInput) -> Optional[str]:
    """Mirrors ``resolveMemoryFileTarget(input)``."""
    if input.tool_name != "Write" and input.tool_name != "Edit":
        return None
    if not input.memory_root:
        return None

    requested_path = _file_path_from_input(input.execution_input)
    if not requested_path:
        return None
    return resolve_contained_memory_file_path(
        file_path=requested_path,
        root_dir=input.memory_root,
        working_directory=input.working_directory,
        workspace_root=input.workspace_root,
    )


def _file_path_from_input(input: Any) -> Optional[str]:
    """Mirrors ``filePathFromInput(input)``."""
    if not input or not isinstance(input, dict):
        return None
    file_path = input.get("file_path")
    return file_path if isinstance(file_path, str) and len(file_path) > 0 else None


def _preserves_existing_permission_decision(decision: PermissionDecisionResult) -> bool:
    """Mirrors ``preservesExistingPermissionDecision(decision)``."""
    if decision.decision == "deny":
        return decision.rule_id != "mode.plan.nonReadOnly"
    # alwaysAsk 是工具自报的"任何情况都要问"，这里不能把它放行掉。今天走不到这条分支
    # （只有 Write/Edit 会命中 memory 目标，二者都没声明 alwaysAsk），但一旦有人给它们加上，
    # 少了这个判断就会出现"memory 文件让模式无关的确认静默消失"——正是本旗标要防的事。
    if decision.always_ask:
        return True
    return decision.decision == "ask" and (
        decision.rule_id == "rule.project.ask" or decision.rule_id == "hook.PreToolUse.ask"
    )
