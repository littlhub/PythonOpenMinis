"""Tool registry — tool registration and lookup.

Ported from: packages/core/src/tool/registry.ts
Original package: @zcode/core (tool)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Protocol

from ..contracts.model import ModelToolContract
from .types import ToolEntry, ToolMetadata

__all__ = [
    "ToolRegistry",
    "ToolRegistryRegisterOptions",
    "ToolRegistryImpl",
    "create_tool_registry",
]

logger = logging.getLogger(__name__)


# -----------------------------------------------
# Tool Registry Interface
# -----------------------------------------------

class ToolRegistry(Protocol):
    """Mirrors ``interface ToolRegistry``.

    # PORT: TS method contract → ``Protocol`` (ZCORE_PORTING.md §5).
    """

    def register(
        self, entry: ToolEntry, options: Optional["ToolRegistryRegisterOptions"] = None
    ) -> None:
        raise NotImplementedError

    def unregister(self, name: str) -> None:
        raise NotImplementedError

    def get(self, name: str) -> Optional[ToolEntry]:
        raise NotImplementedError

    def has(self, name: str) -> bool:
        raise NotImplementedError

    def list(self) -> list[str]:
        raise NotImplementedError

    def get_metadata(self, name: str) -> Optional[ToolMetadata]:
        raise NotImplementedError

    def to_contracts(self) -> list[ModelToolContract]:
        raise NotImplementedError


@dataclass(slots=True)
class ToolRegistryRegisterOptions:
    """Mirrors ``interface ToolRegistryRegisterOptions``."""

    silent_duplicate_warning: Optional[bool] = None


# -----------------------------------------------
# Tool Registry Implementation
# -----------------------------------------------

class ToolRegistryImpl:
    """Mirrors ``class ToolRegistryImpl implements ToolRegistry``."""

    __slots__ = ("_aliases", "_tools")

    def __init__(self) -> None:
        self._aliases: dict[str, str] = {}
        self._tools: dict[str, ToolEntry] = {}

    def register(
        self, entry: ToolEntry, options: Optional[ToolRegistryRegisterOptions] = None
    ) -> None:
        if options is None:
            options = ToolRegistryRegisterOptions()

        displaced_alias_target = self._aliases.get(entry.metadata.name)
        if displaced_alias_target:
            # 查找时 alias 若优先于 canonical，后注册的真实同名工具会继续被旧
            # alias 遮蔽。canonical 始终优先，并留下告警，避免兼容别名改变工具身份。
            del self._aliases[entry.metadata.name]
            if options.silent_duplicate_warning is not True:
                logger.warning(
                    "Tool %s replaces alias previously targeting %s",
                    entry.metadata.name,
                    displaced_alias_target,
                )
        if entry.metadata.name in self._tools and options.silent_duplicate_warning is not True:
            logger.warning("Tool %s already registered, overwriting", entry.metadata.name)
        # PORT: TS iterates a ``Map`` while deleting from it (safe in JS). Python
        # forbids mutating a dict during iteration, so the items are snapshotted.
        for alias, target in list(self._aliases.items()):
            if target == entry.metadata.name:
                del self._aliases[alias]
        self._tools[entry.metadata.name] = entry
        for alias in entry.aliases or []:
            existing_alias_target = self._aliases.get(alias)
            if (
                alias == entry.metadata.name
                or alias in self._tools
                or (
                    existing_alias_target is not None
                    and existing_alias_target != entry.metadata.name
                )
            ):
                # 兼容 alias 若静默覆盖 canonical/另一个 alias，会把一次工具调用路由到
                # 错误权限和 handler。冲突时拒绝本 alias，保留已注册身份。
                if options.silent_duplicate_warning is not True:
                    logger.warning(
                        "Tool alias %s conflicts with an existing tool or alias; skipping", alias
                    )
                continue
            self._aliases[alias] = entry.metadata.name

    def unregister(self, name: str) -> None:
        alias_target = self._aliases.get(name)
        if alias_target:
            del self._aliases[name]
            return

        # PORT: TS ``Map.delete`` is a no-op when the key is absent; ``dict.pop``
        # keeps that behaviour (``del`` would raise ``KeyError``).
        self._tools.pop(name, None)
        # PORT: same Map-mutation-during-iteration note as ``register``.
        for alias, target in list(self._aliases.items()):
            if target == name:
                del self._aliases[alias]

    def get(self, name: str) -> Optional[ToolEntry]:
        target = self._aliases.get(name)
        return self._tools.get(target if target is not None else name)

    def has(self, name: str) -> bool:
        return self.get(name) is not None

    def list(self) -> list[str]:
        return list(self._tools.keys())

    def get_metadata(self, name: str) -> Optional[ToolMetadata]:
        entry = self.get(name)
        return entry.metadata if entry is not None else None

    def to_contracts(self) -> list[ModelToolContract]:
        contracts: list[ModelToolContract] = []
        for entry in self._tools.values():
            if entry.metadata.provider_visible is False:
                continue
            # PORT: TS ``entry.requiresUserInteraction ?? entry.metadata.requiresUserInteraction``.
            requires_user_interaction = (
                entry.requires_user_interaction
                if entry.requires_user_interaction is not None
                else entry.metadata.requires_user_interaction
            )
            contracts.append(
                ModelToolContract(
                    name=entry.metadata.name,
                    description=_tool_description_for_provider(entry.metadata),
                    capability=entry.capability,
                    execution_mode=entry.execution_mode,
                    provider_native=entry.provider_native,
                    input_schema=entry.input_schema,
                    output_schema=entry.output_schema,
                    # PORT: TS spreads ``strict`` conditionally
                    # (``...(entry.strict === undefined ? {} : { strict })``); a
                    # dataclass field left at ``None`` is equivalent.
                    strict=entry.strict,
                    read_only=entry.metadata.read_only,
                    destructive=entry.metadata.destructive,
                    concurrent_safe=entry.metadata.concurrent_safe,
                    requires_user_interaction=requires_user_interaction,
                    max_output_bytes=entry.metadata.max_output_bytes,
                    timeout_ms=entry.metadata.timeout_ms,
                    needs_approval=entry.metadata.needs_approval,
                    side_effect_scope=entry.metadata.side_effect_scope,
                    permission=entry.permission,
                    result_budget=entry.result_budget,
                    execute=None,
                )
            )
        return contracts


def _tool_description_for_provider(metadata: ToolMetadata) -> Optional[str]:
    """Mirrors the non-exported ``toolDescriptionForProvider(metadata)``."""
    description = metadata.description
    instructions: list[str] = [
        trimmed
        for trimmed in (line.strip() for line in (metadata.model_instructions or []))
        if trimmed
    ]
    if len(instructions) == 0:
        return description

    usage = "\n".join(["Usage:", *(f"- {instruction}" for instruction in instructions)])
    return f"{description}\n\n{usage}" if description and len(description) > 0 else usage


# -----------------------------------------------
# Factory
# -----------------------------------------------

def create_tool_registry() -> ToolRegistry:
    """Mirrors ``createToolRegistry(): ToolRegistry``."""
    return ToolRegistryImpl()
