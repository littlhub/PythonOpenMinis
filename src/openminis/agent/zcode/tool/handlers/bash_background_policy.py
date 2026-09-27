"""Bash auto-background policy.

Ported from: packages/core/src/tool/handlers/bash-background-policy.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

import re

from .ports import BashInput

__all__ = [
    "is_bash_auto_background_eligible",
]

_WHITESPACE_RE = re.compile(r"\s+", re.UNICODE)


def is_bash_auto_background_eligible(input: BashInput) -> bool:
    """Mirrors ``isBashAutoBackgroundEligible(input)``.

    # PORT: TS ``command.split(/\\s+/u)[0]`` → ``re.split``；两者对前导空白的切片行为一致
    # （``" a".split(/\\s+/) === ["", "a"]``）。
    """
    if input.run_in_background is True:
        return False
    command = (input.command or "").strip()
    if len(command) == 0:
        return False
    first_token = _WHITESPACE_RE.split(command)[0]
    return first_token != "sleep"
