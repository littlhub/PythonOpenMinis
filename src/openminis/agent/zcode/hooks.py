"""Hook runner contract — user-defined hook execution.

Ported from: packages/core/src/hooks/index.ts
Original package: @zcode/core (hooks)

# PORT-STATUS: partial
The original ``HookRunner`` bridges to the user's hook scripts (PreToolUse /
PostToolUse / permission-request). In the coding-only port the runner is injected
by the host (OpenMinis adapter); this module provides the contract types so the
executor kernel imports resolve. A concrete runner is supplied at runtime via
``ToolExecutorDeps.hook_runner``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Optional, Protocol, runtime_checkable


@dataclass(slots=True)
class HookRunResult:
    """Result of running a hook.

    PORT: mirrors ``HookRunResult`` — ``additional_contexts`` are strings the
    hook wants injected into the model context.
    """

    additional_contexts: list[str] = field(default_factory=list)
    allowed: bool = True
    decision: Optional[str] = None  # "approve" | "deny" | "ask" | None
    reason: Optional[str] = None


@runtime_checkable
class HookRunner(Protocol):
    """Contract for running a hook event.

    PORT: mirrors ``HookRunner.run(payload) -> Promise<HookRunResult>``.
    """

    async def run(self, payload: dict[str, Any]) -> HookRunResult:  # noqa: ANN401
        """Run the hook described by *payload* and return its result."""
        ...


__all__ = ["HookRunResult", "HookRunner"]
