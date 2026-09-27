"""Shared helpers — cross-module utilities.

Ported from: packages/core/src/shared/index.ts
Original package: @zcode/core (shared)

# PORT-STATUS: partial
Only the symbols referenced by the coding-agent kernel are provided. The
original ``shared`` module is a grab-bag of utilities; here we keep just the
MCP tool-error parser needed by ``result_display``.
"""

from __future__ import annotations

from typing import Any, Optional


def parse_official_mcp_tool_error(error: Any) -> Optional[dict[str, Any]]:
    """Parse an official MCP tool error into a structured payload.

    PORT: mirrors ``parseOfficialMcpToolError``. Returns ``None`` when *error*
    is not a recognized MCP tool error.
    """
    if not isinstance(error, dict):
        return None
    if "mcpToolError" not in error and "isMcpError" not in error:
        return None
    return {
        "is_mcp_error": True,
        "server_name": error.get("serverName"),
        "tool_name": error.get("toolName"),
        "message": error.get("message"),
    }


__all__ = ["parse_official_mcp_tool_error"]
