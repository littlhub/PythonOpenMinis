"""MCP (Model Context Protocol) integration stubs.

Ported from: packages/core/src/mcp/**
Original package: @zcode/core (mcp)

# PORT-STATUS: partial
Only the symbol referenced by the coding-agent kernel
(``has_official_cua_frame_authority``) is provided. Full MCP server discovery,
transport, and tool bridging are out of scope for the coding-only port and are
left as ``NotImplementedError`` placeholders so imports resolve.
"""

from __future__ import annotations

from .image_normalization import has_official_cua_frame_authority

__all__ = ["has_official_cua_frame_authority"]
