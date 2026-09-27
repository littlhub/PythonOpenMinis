"""Bash command registry — generated command catalog.

Ported from: packages/core/src/tool/handlers/generated/bash-command-registry.ts
Original package: @zcode/core (tool/handlers/generated/bash-command-registry)

# PORT-STATUS: partial — see ``__init__.py`` for rationale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class BashCommandRegistryNode:
    """A single registered command entry.

    PORT: mirrors ``BashCommandRegistryNode``.
    """

    name: str
    description: str = ""
    risk_level: str = "medium"
    side_effect_scope: str = "workspace"
    metadata: dict[str, Any] = field(default_factory=dict)


# PORT: TS ``BASH_COMMAND_REGISTRY`` is a ``Record<string, Node>`` keyed by
# command name (``Object.hasOwn`` / ``.get``). Kept as a dict accordingly.
# Empty until the command catalog is supplied by codegen / host.
BASH_COMMAND_REGISTRY: dict[str, BashCommandRegistryNode] = {}

__all__ = ["BashCommandRegistryNode", "BASH_COMMAND_REGISTRY"]
