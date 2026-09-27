"""Generated bash command registry (codegen output).

Ported from: packages/core/src/tool/handlers/generated/bash-command-registry.ts
Original package: @zcode/core (tool/handlers/generated)

# PORT-STATUS: partial
The original file is produced by a codegen step from a curated command catalog.
In the coding-only port we provide the contract types and an empty registry so
``bash_command_permission_policy`` imports resolve. Populate ``BASH_COMMAND_REGISTRY``
with real entries when the catalog is available.
"""

from __future__ import annotations

from .bash_command_registry import BASH_COMMAND_REGISTRY, BashCommandRegistryNode

__all__ = ["BASH_COMMAND_REGISTRY", "BashCommandRegistryNode"]
