"""CLI Prefix section builder — short leading identity block.

Ported from: packages/core/src/context/sections/cli-prefix.ts
Original package: @zcode/core (context/sections)
"""

from __future__ import annotations

from ..types import ContextSection
from ..utils import estimate_tokens

CLI_PREFIX_PROMPT = "You are ZCode, an interactive coding agent"


def build_cli_prefix_section() -> ContextSection:
    content = CLI_PREFIX_PROMPT

    return ContextSection(
        name="CLI Prefix",
        source="cli_prefix",
        injection_target="system",
        cache_hint="stable",
        chars=len(content),
        tokens=estimate_tokens(content),
        content=content,
        preview=content[:100],
    )
