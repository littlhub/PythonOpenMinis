"""Permission capability — resolve per-call permission capability from a tool entry.

Ported from: packages/core/src/tool/executor/permission-capability.ts
Original package: @zcode/core (tool/executor)
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any, Optional, TypedDict

from ...contracts.model import ModelToolSideEffectScope
from ...contracts.tools import ToolPermissionSpec
from ...permission.ports import PermissionToolCapability
from ..types import ToolEntry, ToolRuntimePermissionCapabilityContext
from .types import ToolExecutorDeps

__all__ = [
    "ToolCallCapabilityFlags",
    "resolve_runtime_permission_capability",
    "resolve_tool_call_capability_flags",
    "resolve_runtime_permission_context",
]


class ToolCallCapabilityFlags(TypedDict, total=False):
    """Return shape of ``resolveToolCallCapabilityFlags``.

    # PORT: TS anonymous inline object ``{ readOnly?: boolean; sideEffectScope?: ... }``
    # → ``TypedDict``（键按既有 ``ToolModelContractProjection`` 先例转 snake_case）。
    """

    read_only: bool
    side_effect_scope: ModelToolSideEffectScope


def resolve_runtime_permission_capability(
    entry: ToolEntry,
    input: Any,
    context: ToolRuntimePermissionCapabilityContext,
) -> PermissionToolCapability:
    """Mirrors ``resolveRuntimePermissionCapability(entry, input, context)``."""
    resolver = entry.resolve_permission_capability
    runtime_capability = (
        resolver(input, context) if resolver is not None else None
    )
    # PORT: TS ``{ ...entry.metadata, ...runtimeCapability, permissionCapabilityGroup, permission }``。
    # JS 展开会把 runtimeCapability 上「显式 undefined」的键也写进结果；Python dataclass 无法
    # 区分「键缺席」与「值为 None」，故按 Partial 语义处理：runtime 值为 None 即刻回落 metadata。
    return PermissionToolCapability(
        allowed_in_plan_mode=_pick(
            runtime_capability.allowed_in_plan_mode if runtime_capability is not None else None,
            entry.metadata.allowed_in_plan_mode,
        ),
        # PORT: ToolRuntimePermissionCapability 无 ``alwaysAsk`` 字段，metadata 也没有，
        # 与 TS 展开结果一致（该键缺席 → None）。
        always_ask=None,
        read_only=_pick(
            runtime_capability.read_only if runtime_capability is not None else None,
            entry.metadata.read_only,
        ),
        destructive=_pick(
            runtime_capability.destructive if runtime_capability is not None else None,
            entry.metadata.destructive,
        ),
        requires_user_interaction=_pick(
            runtime_capability.requires_user_interaction
            if runtime_capability is not None
            else None,
            entry.metadata.requires_user_interaction,
        ),
        side_effect_scope=_pick(
            runtime_capability.side_effect_scope if runtime_capability is not None else None,
            entry.metadata.side_effect_scope,
        ),
        risk_level=_pick(
            runtime_capability.risk_level if runtime_capability is not None else None,
            entry.metadata.risk_level,
        ),
        needs_approval=_pick(
            runtime_capability.needs_approval if runtime_capability is not None else None,
            entry.metadata.needs_approval,
        ),
        # capability group is provenance, not a runtime/model-controlled override.
        permission_capability_group=entry.permission_capability_group,
        permission=_merge_permission_spec(
            entry.permission,
            runtime_capability.permission if runtime_capability is not None else None,
        ),
    )


def resolve_tool_call_capability_flags(
    deps: ToolExecutorDeps,
    entry: ToolEntry,
    input: Any,
) -> ToolCallCapabilityFlags:
    """一次调用**解析后**的副作用旗标，挂在 `ToolCallStarted` 上：与权限判定同一次解析，所以 Bash 的只读命令判定等运行时结论一并生效。
    订阅者（dynamic-workflow 的 driver）据它在 handler 动手之前判断这一笔是否会改写工作区。

    Mirrors ``resolveToolCallCapabilityFlags(deps, entry, input)``.
    """
    capability = resolve_runtime_permission_capability(
        entry,
        input,
        resolve_runtime_permission_context(deps),
    )
    return {
        "read_only": capability.read_only,
        "side_effect_scope": capability.side_effect_scope,
    }


def resolve_runtime_permission_context(
    deps: ToolExecutorDeps,
) -> ToolRuntimePermissionCapabilityContext:
    """Mirrors ``resolveRuntimePermissionContext(deps)``."""
    return ToolRuntimePermissionCapabilityContext(
        runtime_scope=deps.runtime_scope,
        working_directory=deps.get_working_directory(),
        workspace_root=deps.get_workspace_root(),
    )


def _pick(value: Any, fallback: Any) -> Any:
    """``value ?? fallback`` 的 Partial 语义替身（见上方 PORT 说明）。"""
    return value if value is not None else fallback


def _merge_permission_spec(base: ToolPermissionSpec, override: Any) -> ToolPermissionSpec:
    """Mirrors ``{ ...entry.permission, ...runtimeCapability?.permission }``."""
    if override is None:
        return base
    field_names = {f.name for f in dataclasses.fields(ToolPermissionSpec)}
    if isinstance(override, Mapping):
        changes = {key: value for key, value in override.items() if key in field_names}
    else:
        changes = {
            name: getattr(override, name)
            for name in field_names
            if getattr(override, name, None) is not None
        }
    return dataclasses.replace(base, **changes) if changes else base
