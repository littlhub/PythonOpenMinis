"""Bash readonly policy — safe-flags aggregates (``export *`` barrel).

Ported from: packages/core/src/tool/handlers/bash-readonly-policy-flags.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

# PORT: TS ``export * from`` 四个文件 → Python ``from ... import *``（各源模块均定义了
# ``__all__``，逐名完整转出）。
from .bash_readonly_policy_flags_file import *  # noqa: F401,F403
from .bash_readonly_policy_flags_git import *  # noqa: F401,F403
from .bash_readonly_policy_flags_process import *  # noqa: F401,F403
from .bash_readonly_policy_flags_text import *  # noqa: F401,F403
