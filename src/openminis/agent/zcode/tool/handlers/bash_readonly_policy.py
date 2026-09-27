"""Bash read-only policy barrel.

Ported from: packages/core/src/tool/handlers/bash-readonly-policy.ts
Original package: @zcode/core (tool/handlers)

# PORT: 上游只有 5 行，全是 ``export ... from``。Python 侧同名再导出即可；不放任何逻辑。
"""

from __future__ import annotations

from .bash_readonly_policy_argv import (
    evaluate_bash_readonly_policy,
    has_known_bash_write_option,
)
from .bash_readonly_policy_callbacks import is_sed_in_place_option

__all__ = [
    "has_known_bash_write_option",
    "is_sed_in_place_option",
    "evaluate_bash_readonly_policy",
]
