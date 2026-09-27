"""Create-workflow display builder (office workflow — stub).

Ported from: packages/core/src/tool/executor/create-workflow-display.ts
Original package: @zcode/core (tool/executor/create-workflow-display)

# PORT-STATUS: partial — office workflow feature, excluded from the coding-only
# port. Provided as a no-op stub so ``result_display`` imports resolve.
"""

from __future__ import annotations

from typing import Any


def create_create_workflow_display(*args: Any, **kwargs: Any) -> Any:
    """Build a create-workflow tool-result display.

    PORT: no-op stub (workflow feature excluded). Returns ``None``.
    """
    return None


__all__ = ["create_create_workflow_display"]
