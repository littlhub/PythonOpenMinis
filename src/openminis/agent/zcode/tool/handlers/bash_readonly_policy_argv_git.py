"""Bash readonly policy — git-specific argv normalization and gating.

Ported from: packages/core/src/tool/handlers/bash-readonly-policy-argv-git.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

from typing import Optional, Sequence

from .bash_readonly_policy_argv_flags import is_argv_allowed_by_policy
from .bash_readonly_policy_commands import (
    GIT_GLOBAL_DANGEROUS_FLAGS,
    GIT_GLOBAL_NO_VALUE_FLAGS,
    GIT_GLOBAL_VALUE_FLAGS,
    GIT_READONLY_SUBCOMMAND_POLICIES,
)
from .bash_readonly_policy_types import BashReadonlyCommandPolicy

__all__ = [
    "is_git_read_only_command",
    "has_dangerous_git_global_option",
]

GIT_ATTACHED_DANGEROUS_SHORT_FLAGS = ["-c", "-C"]


def is_git_read_only_command(argv: Sequence[str]) -> bool:
    """Mirrors ``isGitReadOnlyCommand(argv)``."""
    normalized = _normalize_git_argv(argv)
    if not normalized:
        return False

    for command_prefix, policy in _sorted_git_policies():
        prefix_words = command_prefix.split(" ")
        if not all(
            index < len(normalized) and normalized[index] == word
            for index, word in enumerate(prefix_words)
        ):
            continue
        if policy.additional_command_is_dangerous_callback is not None and (
            policy.additional_command_is_dangerous_callback(
                command_prefix,
                list(normalized[len(prefix_words) :]),
            )
        ):
            return False
        return is_argv_allowed_by_policy(
            normalized, policy, "git", len(prefix_words)
        )

    return False


def _normalize_git_argv(argv: Sequence[str]) -> Optional[list[str]]:
    """Mirrors the non-exported ``normalizeGitArgv(argv)``."""
    normalized: list[str] = ["git"]
    index = 1
    while index < len(argv):
        word = argv[index]
        if not word:
            index += 1
            continue
        if word in GIT_GLOBAL_NO_VALUE_FLAGS:
            index += 1
            continue
        if _has_dangerous_git_global_option_word(word):
            return None
        if word in GIT_GLOBAL_VALUE_FLAGS:
            index += 1
            # PORT: TS ``if (!argv[index]) return undefined`` —— 越界即假值，Python 显式判长度。
            if index >= len(argv) or not argv[index]:
                return None
            index += 1
            continue
        if word.startswith("-"):
            return None
        normalized.extend(argv[index:])
        return normalized

    return None


def has_dangerous_git_global_option(argv: Sequence[str]) -> bool:
    """Mirrors ``hasDangerousGitGlobalOption(argv)``."""
    return any(_has_dangerous_git_global_option_word(word) for word in argv)


def _has_dangerous_git_global_option_word(word: str) -> bool:
    """Mirrors ``hasDangerousGitGlobalOptionWord(word)``."""
    if _has_dangerous_attached_git_short_option_word(word):
        return True
    if word in GIT_GLOBAL_DANGEROUS_FLAGS:
        return True
    return any(word.startswith(f"{flag}=") for flag in GIT_GLOBAL_DANGEROUS_FLAGS)


def _has_dangerous_attached_git_short_option_word(word: str) -> bool:
    """Mirrors ``hasDangerousAttachedGitShortOptionWord(word)``."""
    for flag in GIT_ATTACHED_DANGEROUS_SHORT_FLAGS:
        if (
            len(word) > len(flag)
            and word.startswith(flag)
            # PORT: TS ``flag === "-C" || word[flag.length] !== "-"`` —— 越界取字符得 undefined，
            # Python 的 ``word[len(flag)]`` 在 ``len(word) > len(flag)`` 前提下必然存在。
            and (flag == "-C" or word[len(flag)] != "-")
        ):
            return True
    return False


# PORT: TS 模块级惰性缓存 ``let gitPoliciesByLength``；Python 同名模块级变量 + None 判别。
_git_policies_by_length: Optional[list[tuple[str, BashReadonlyCommandPolicy]]] = None


def _sorted_git_policies() -> list[tuple[str, BashReadonlyCommandPolicy]]:
    """Mirrors the non-exported ``sortedGitPolicies()``.

    # PORT: TS ``??=`` 惰性初始化；Python 用 ``if is None`` 显式判断。
    """
    global _git_policies_by_length

    if _git_policies_by_length is None:
        _git_policies_by_length = sorted(
            GIT_READONLY_SUBCOMMAND_POLICIES.items(),
            key=lambda item: len(item[0].split(" ")),
            reverse=True,
        )
    return _git_policies_by_length
