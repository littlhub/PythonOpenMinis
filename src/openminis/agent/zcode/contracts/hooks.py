"""Hook event contract — event names dispatched to user hooks.

Ported from: packages/contracts/src/hooks/index.ts
Original package: @zcode/contracts (hooks)
"""

from __future__ import annotations

from enum import Enum


class HookEventName(str, Enum):
    """Hook event names — values kept as original protocol strings.

    PORT: mirrors ``HookEventName`` (``as const`` object → ``enum.Enum``).
    """

    PRE_TOOL_USE = "PreToolUse"
    PERMISSION_REQUEST = "PermissionRequest"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    SUBAGENT_STOP = "SubagentStop"
    STOP = "Stop"


__all__ = ["HookEventName"]
