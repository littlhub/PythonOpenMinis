"""Tool model-contract resolution.

Ported from: packages/core/src/tool/model-contract.ts
Original package: @zcode/core (tool/model-contract)

# PORT-STATUS: partial
The original resolves the ``ModelToolContract`` (provider-native tool spec,
JSON schema, risk level) bound to a tool entry. In the coding-only port we
return the entry's own contract unchanged; host adapters can enrich this later.
"""

from __future__ import annotations

from typing import Any


def resolve_tool_entry_model_contract(entry: Any, *args: Any, **kwargs: Any) -> Any:
    """Resolve the model contract for a tool *entry*.

    PORT: mirrors ``resolveToolEntryModelContract``. Returns the entry's
    ``model_contract`` attribute when present, else ``None``.
    """
    if entry is None:
        return None
    return getattr(entry, "model_contract", None)


__all__ = ["resolve_tool_entry_model_contract"]
