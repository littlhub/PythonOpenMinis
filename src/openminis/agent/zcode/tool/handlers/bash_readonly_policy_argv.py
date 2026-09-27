"""Bash readonly policy — the argv-level evaluator entry point.

Ported from: packages/core/src/tool/handlers/bash-readonly-policy-argv.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence

from .bash_command_parser import BashCommandInvocation
from .bash_readonly_policy_argv_direct import (
    evaluate_direct_readonly_argv,
    is_find_write_option,
)
from .bash_readonly_policy_argv_flags import is_argv_allowed_by_policy
from .bash_readonly_policy_argv_git import (
    has_dangerous_git_global_option,
    is_git_read_only_command,
)
from .bash_readonly_policy_argv_io import (
    are_env_assignments_allowed,
    are_redirects_allowed,
    is_unsafe_windows_unc_path,
    strip_safe_command_wrappers,
)
from .bash_readonly_policy_callbacks import is_sed_in_place_option
from .bash_readonly_policy_commands import (
    READONLY_ALLOW_ANY_ARG_COMMANDS,
    READONLY_COMMAND_POLICIES,
    READONLY_MULTIWORD_COMMAND_POLICIES,
)
from .bash_readonly_policy_types import BashReadonlyCommandPolicy

__all__ = [
    "evaluate_bash_readonly_policy",
    "has_known_bash_write_option",
]


def evaluate_bash_readonly_policy(command_part: BashCommandInvocation) -> Optional[bool]:
    """Mirrors ``evaluateBashReadonlyPolicy(commandPart): boolean | undefined``.

    # PORT: TS 返回 ``boolean | undefined``；``None`` 承载 ``undefined``（调用点据
    # ``is False`` / ``is None`` 分支，与上游 ``!== undefined`` 判定一致）。
    """
    if not are_env_assignments_allowed(command_part):
        return False
    if not are_redirects_allowed(command_part):
        return False

    argv = strip_safe_command_wrappers(command_part.argv)
    if len(argv) == 0:
        return False
    if any(is_unsafe_windows_unc_path(arg) for arg in argv):
        return False
    if argv[0] == "git":
        return is_git_read_only_command(argv)

    direct_argv_result = evaluate_direct_readonly_argv(argv)
    if direct_argv_result is not None:
        return direct_argv_result

    prefix_policy_result = _evaluate_readonly_prefix_policy(argv, command_part.command_text)
    if prefix_policy_result is not None:
        return prefix_policy_result

    if (argv[0] if argv else "") in READONLY_ALLOW_ANY_ARG_COMMANDS:
        return True
    # PORT: TS ``process.platform === "win32"`` → ``sys.platform == "win32"``。
    if sys.platform == "win32" and (argv[0] if argv else "") == "xargs":
        return None

    policy = READONLY_COMMAND_POLICIES.get(argv[0] if argv else "")
    if not policy:
        return None
    if argv[0] == "cd" and len(argv) > 2:
        return False
    if policy.additional_command_is_dangerous_callback is not None and (
        policy.additional_command_is_dangerous_callback(
            command_part.command_text, list(argv[1:])
        )
    ):
        return False
    if not is_argv_allowed_by_policy(argv, policy, argv[0] if argv else ""):
        return False
    if policy.regex and not policy.regex.search(command_part.command_text):
        return False
    return True


def has_known_bash_write_option(command_part: BashCommandInvocation) -> bool:
    """Mirrors ``hasKnownBashWriteOption(commandPart)``."""
    argv = strip_safe_command_wrappers(command_part.argv)
    command_name = argv[0] if argv else None
    if command_name == "sed":
        return any(is_sed_in_place_option(word) for word in argv)
    if command_name == "find":
        return any(is_find_write_option(word) for word in argv)
    if command_name == "tree":
        return _tree_argv_has_output_option(argv)
    if command_name == "git":
        return has_dangerous_git_global_option(argv)
    return False


def _evaluate_readonly_prefix_policy(
    argv: Sequence[str],
    command_text: str,
) -> Optional[bool]:
    """Mirrors the non-exported ``evaluateReadonlyPrefixPolicy(argv, commandText)``."""
    for command_prefix, policy in _sorted_readonly_multiword_policies():
        prefix_words = command_prefix.split(" ")
        if not all(
            index < len(argv) and argv[index] == word
            for index, word in enumerate(prefix_words)
        ):
            continue
        args = list(argv[len(prefix_words) :])
        if _args_contain_unsafe_safe_flag_text(args):
            return False
        if policy.additional_command_is_dangerous_callback is not None and (
            policy.additional_command_is_dangerous_callback(command_prefix, args)
        ):
            return False
        if not is_argv_allowed_by_policy(
            argv, policy, argv[0] if argv else "", len(prefix_words)
        ):
            return False
        if policy.regex and not policy.regex.search(command_text):
            return False
        return True
    return None


# PORT: TS 模块级惰性缓存 ``let readOnlyMultiwordPoliciesByLength``。
_read_only_multiword_policies_by_length: Optional[
    list[tuple[str, BashReadonlyCommandPolicy]]
] = None


def _sorted_readonly_multiword_policies() -> list[tuple[str, BashReadonlyCommandPolicy]]:
    """Mirrors the non-exported ``sortedReadOnlyMultiwordPolicies()``."""
    global _read_only_multiword_policies_by_length

    if _read_only_multiword_policies_by_length is None:
        _read_only_multiword_policies_by_length = sorted(
            READONLY_MULTIWORD_COMMAND_POLICIES.items(),
            key=lambda item: len(item[0].split(" ")),
            reverse=True,
        )
    return _read_only_multiword_policies_by_length


def _args_contain_unsafe_safe_flag_text(args: Sequence[str]) -> bool:
    """Mirrors the non-exported ``argsContainUnsafeSafeFlagText(args)``."""
    return any(
        "$" in arg or ("{" in arg and ("," in arg or ".." in arg)) for arg in args
    )


def _tree_argv_has_output_option(argv: Sequence[str]) -> bool:
    """Mirrors the non-exported ``treeArgvHasOutputOption(argv)``."""
    index = 1
    while index < len(argv):
        word = argv[index]
        if not word:
            index += 1
            continue
        if word == "--":
            return False
        if word == "-o" or word == "--output" or word.startswith("--output="):
            return True
        if word.startswith("-") and not word.startswith("--") and "o" in word[1:]:
            return True
        index += 1
    return False
