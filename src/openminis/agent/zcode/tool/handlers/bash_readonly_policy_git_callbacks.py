"""Bash readonly policy — git dynamic-danger callbacks.

Ported from: packages/core/src/tool/handlers/bash-readonly-policy-git-callbacks.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

# PORT: JS ``/%[-+ ]?G|%\(\*?signature/`` 无 g 标志、仅 ``.test``；Python ``re.search``。
_GIT_REVISION_FORMAT_DANGEROUS_RE = re.compile(r"%[-+ ]?G|%\(\*?signature")

_GIT_REFLOG_ALLOWED_SUBCOMMANDS = frozenset(["show", "list"])
_GIT_REFLOG_DANGEROUS_SUBCOMMANDS = frozenset(["expire", "delete", "exists", "drop", "write"])

# PORT: JS ``/^[a-zA-Z0-9_][a-zA-Z0-9_-]*$/``。
_GIT_REMOTE_NAME_RE = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9_-]*$")


def git_revision_format_command_is_dangerous(
    _command_text: str,
    args: Sequence[str],
) -> bool:
    """Mirrors ``gitRevisionFormatCommandIsDangerous(commandText, args)``."""
    for index in range(len(args)):
        arg = args[index] if index < len(args) else ""
        value = (
            arg[arg.index("=") + 1 :]
            if "=" in arg
            else (args[index + 1] if index + 1 < len(args) else None)
        )
        if (
            arg == "--format"
            or arg == "--pretty"
            or arg.startswith("--format=")
            or arg.startswith("--pretty=")
        ) and value:
            if _GIT_REVISION_FORMAT_DANGEROUS_RE.search(value):
                return True
    return False


def git_reflog_command_is_dangerous(
    _command_text: str,
    args: Sequence[str],
) -> bool:
    """Mirrors ``gitReflogCommandIsDangerous(commandText, args)``."""
    first_positional = next((arg for arg in args if arg and not arg.startswith("-")), None)
    if first_positional and first_positional not in _GIT_REFLOG_ALLOWED_SUBCOMMANDS:
        return True
    return any(arg in _GIT_REFLOG_DANGEROUS_SUBCOMMANDS for arg in args)


def git_ls_remote_command_is_dangerous(
    _command_text: str,
    args: Sequence[str],
) -> bool:
    """Mirrors ``gitLsRemoteCommandIsDangerous(commandText, args)``."""
    after_double_dash = False
    index = 0
    while index < len(args):
        arg = args[index] if index < len(args) else ""
        if not after_double_dash and arg == "--":
            after_double_dash = True
            index += 1
            continue
        if not after_double_dash and (arg.startswith("-") or not arg):
            if arg == "--sort":
                index += 1
            index += 1
            continue
        return True
    return False


def git_remote_show_command_is_dangerous(
    _command_text: str,
    args: Sequence[str],
) -> bool:
    """Mirrors ``gitRemoteShowCommandIsDangerous(commandText, args)``."""
    double_dash_index = args.index("--") if "--" in args else -1
    option_args = list(args) if double_dash_index == -1 else list(args[:double_dash_index])
    positional_args = (
        [] if double_dash_index == -1 else list(args[double_dash_index + 1 :])
    ) + [arg for arg in option_args if arg != "-n"]
    if "-n" not in option_args:
        return True
    if len(positional_args) != 1:
        return True
    return _GIT_REMOTE_NAME_RE.search(positional_args[0] if positional_args[0] else "") is None


def git_tag_command_is_dangerous(_command_text: str, args: Sequence[str]) -> bool:
    """Mirrors ``gitTagCommandIsDangerous(commandText, args)``."""
    return git_list_like_command_is_dangerous(
        args,
        frozenset(
            [
                "--contains",
                "--no-contains",
                "--merged",
                "--no-merged",
                "--points-at",
                "--sort",
                "--format",
                "-n",
            ]
        ),
    )


def git_branch_command_is_dangerous(
    _command_text: str,
    args: Sequence[str],
) -> bool:
    """Mirrors ``gitBranchCommandIsDangerous(commandText, args)``."""
    return git_list_like_command_is_dangerous(
        args,
        frozenset(["--contains", "--no-contains", "--points-at", "--sort"]),
    )


def git_list_like_command_is_dangerous(
    args: Sequence[str],
    value_flags: frozenset[str],
) -> bool:
    """Mirrors ``gitListLikeCommandIsDangerous(args, valueFlags)``."""
    has_list = False
    after_double_dash = False
    previous_flag = ""
    index = 0
    while index < len(args):
        arg = args[index] if index < len(args) else ""
        if not arg:
            index += 1
            continue
        if arg == "--" and not after_double_dash:
            after_double_dash = True
            previous_flag = ""
            index += 1
            continue
        if not after_double_dash and arg.startswith("-"):
            if (
                arg == "--list"
                or arg == "-l"
                or (arg[:1] == "-" and arg[1:2] != "-" and "l" in arg[1:])
            ):
                has_list = True
            previous_flag = arg[: arg.index("=")] if "=" in arg else arg
            if "=" not in arg and previous_flag in value_flags:
                index += 1
            index += 1
            continue
        if not has_list and previous_flag != "--merged" and previous_flag != "--no-merged":
            return True
        index += 1
    return False


__all__ = [
    "git_revision_format_command_is_dangerous",
    "git_reflog_command_is_dangerous",
    "git_ls_remote_command_is_dangerous",
    "git_remote_show_command_is_dangerous",
    "git_tag_command_is_dangerous",
    "git_branch_command_is_dangerous",
    "git_list_like_command_is_dangerous",
]
