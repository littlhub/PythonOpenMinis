"""Bash readonly policy — shared types.

Ported from: packages/core/src/tool/handlers/bash-readonly-policy-types.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Literal, Optional, Sequence

SafeFlagValue = Literal["{}", "EOF", "char", "none", "number", "optionalString", "string"]


@dataclass(slots=True)
class BashReadonlyCommandPolicy:
    """Mirrors ``interface BashReadonlyCommandPolicy``."""

    additional_command_is_dangerous_callback: Optional[
        Callable[[str, Sequence[str]], bool]
    ] = None
    allow_any_args: bool = False
    allow_compact_numeric_count_flag: bool = False
    command_only: bool = False
    regex: Optional[re.Pattern[str]] = None
    respects_double_dash: Optional[bool] = None
    safe_flags: Optional[dict[str, SafeFlagValue]] = None


__all__ = ["SafeFlagValue", "BashReadonlyCommandPolicy"]
