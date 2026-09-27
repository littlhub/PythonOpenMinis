"""Bash readonly policy — command policy aggregation barrel.

Ported from: packages/core/src/tool/handlers/bash-readonly-policy-commands.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

from .bash_readonly_policy_git_subcommands_core import (
    GIT_READONLY_SUBCOMMAND_POLICY_ENTRIES_CORE,
)
from .bash_readonly_policy_git_subcommands_history import (
    GIT_READONLY_SUBCOMMAND_POLICY_ENTRIES_HISTORY,
)
from .bash_readonly_policy_multiword_core import READONLY_MULTIWORD_POLICY_ENTRIES_CORE
from .bash_readonly_policy_multiword_gh import READONLY_MULTIWORD_POLICY_ENTRIES_GH

# PORT: TS ``export { ... } from "./bash-readonly-policy-simple-commands.js"`` ——
# 逐名再导出（不用 ``import *``，以便 IDE 与静态检查看到确切来源）。
from .bash_readonly_policy_simple_commands import (
    GIT_GLOBAL_DANGEROUS_FLAGS,
    GIT_GLOBAL_NO_VALUE_FLAGS,
    GIT_GLOBAL_VALUE_FLAGS,
    READONLY_ALLOW_ANY_ARG_COMMAND_PREFIXES,
    READONLY_ALLOW_ANY_ARG_COMMANDS,
    READONLY_COMMAND_POLICIES,
)
from .bash_readonly_policy_types import BashReadonlyCommandPolicy

# PORT: TS ``new Map([...a, ...b])`` → ``dict([*a, *b])``；重复键后者胜，与 Map 语义一致。
GIT_READONLY_SUBCOMMAND_POLICIES: dict[str, BashReadonlyCommandPolicy] = dict(
    [
        *GIT_READONLY_SUBCOMMAND_POLICY_ENTRIES_CORE,
        *GIT_READONLY_SUBCOMMAND_POLICY_ENTRIES_HISTORY,
    ]
)

READONLY_MULTIWORD_COMMAND_POLICIES: dict[str, BashReadonlyCommandPolicy] = dict(
    [
        *READONLY_MULTIWORD_POLICY_ENTRIES_CORE,
        *READONLY_MULTIWORD_POLICY_ENTRIES_GH,
    ]
)

__all__ = [
    "GIT_GLOBAL_DANGEROUS_FLAGS",
    "GIT_GLOBAL_NO_VALUE_FLAGS",
    "GIT_GLOBAL_VALUE_FLAGS",
    "READONLY_ALLOW_ANY_ARG_COMMAND_PREFIXES",
    "READONLY_ALLOW_ANY_ARG_COMMANDS",
    "READONLY_COMMAND_POLICIES",
    "GIT_READONLY_SUBCOMMAND_POLICIES",
    "READONLY_MULTIWORD_COMMAND_POLICIES",
]
