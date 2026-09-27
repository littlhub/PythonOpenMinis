"""Bash readonly policy — direct argv evaluators (printf / find / history ...).

Ported from: packages/core/src/tool/handlers/bash-readonly-policy-argv-direct.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

from .bash_readonly_policy_commands import READONLY_ALLOW_ANY_ARG_COMMAND_PREFIXES
from .bash_readonly_policy_callbacks import has_dangerous_docker_option

FIND_WRITE_OPTIONS = frozenset(
    [
        "-delete",
        "-exec",
        "-execdir",
        "-files0-from",
        "-fls",
        "-fprint",
        "-fprint0",
        "-fprintf",
        "-ok",
        "-okdir",
    ]
)


def is_find_write_option(word: str) -> bool:
    """Mirrors ``isFindWriteOption(word)``."""
    return word in FIND_WRITE_OPTIONS


FIND_VALUE_OPTIONS = frozenset(
    [
        "-Bmin",
        "-Bnewer",
        "-Btime",
        "-D",
        "-amin",
        "-anewer",
        "-atime",
        "-cmin",
        "-cnewer",
        "-context",
        "-ctime",
        "-f",
        "-flags",
        "-fstype",
        "-gid",
        "-group",
        "-ilname",
        "-iname",
        "-inum",
        "-ipath",
        "-iregex",
        "-iwholename",
        "-lname",
        "-links",
        "-maxdepth",
        "-mindepth",
        "-mmin",
        "-mnewer",
        "-mtime",
        "-name",
        "-newer",
        "-path",
        "-perm",
        "-printf",
        "-regex",
        "-regextype",
        "-samefile",
        "-size",
        "-type",
        "-used",
        "-user",
        "-wholename",
        "-xattrname",
        "-xtype",
        "-uid",
    ]
)


def evaluate_direct_readonly_argv(argv: Sequence[str]) -> Optional[bool]:
    """Mirrors ``evaluateDirectReadonlyArgv(argv)``."""
    if _argv_matches_any(argv, READONLY_EXACT_ARGV_COMMANDS):
        return True
    if (argv[0] if len(argv) > 0 else None) == "docker" and _argv_matches_any_prefix(
        argv, READONLY_ALLOW_ANY_ARG_COMMAND_PREFIXES
    ):
        return not has_dangerous_docker_option(argv)
    if (argv[0] if len(argv) > 0 else None) == "printf":
        return _is_safe_printf_argv(argv)
    if (argv[0] if len(argv) > 0 else None) == "find":
        return _is_safe_find_argv(argv)
    if (argv[0] if len(argv) > 0 else None) == "history":
        return len(argv) == 1 or (
            len(argv) == 2 and _DIGITS_ONLY_RE.search(argv[1] if len(argv) > 1 else "") is not None
        )
    if (argv[0] if len(argv) > 0 else None) == "arch":
        return len(argv) == 1 or (
            len(argv) == 2
            and ((argv[1] if len(argv) > 1 else None) == "-h" or (argv[1] if len(argv) > 1 else None) == "--help")
        )
    if (argv[0] if len(argv) > 0 else None) == "ifconfig":
        return len(argv) == 1 or (
            len(argv) == 2
            and _LEADING_LETTER_RE.search(argv[1] if len(argv) > 1 else "") is not None
        )
    return None


READONLY_EXACT_ARGV_COMMANDS: list[list[str]] = [
    ["ip", "addr"],
    ["node", "-v"],
    ["node", "--version"],
    ["python", "--version"],
    ["python3", "--version"],
]


def _argv_matches_any(argv: Sequence[str], expected_commands: Sequence[Sequence[str]]) -> bool:
    return any(
        len(argv) == len(expected)
        and all(
            (argv[index] if index < len(argv) else None) == word
            for index, word in enumerate(expected)
        )
        for expected in expected_commands
    )


def _argv_matches_any_prefix(
    argv: Sequence[str], expected_commands: Sequence[Sequence[str]]
) -> bool:
    return any(
        len(argv) >= len(expected)
        and all(
            (argv[index] if index < len(argv) else None) == word
            for index, word in enumerate(expected)
        )
        for expected in expected_commands
    )


# PORT: JS ``/^\d+$/``。
_DIGITS_ONLY_RE = re.compile(r"^\d+$")
# PORT: JS ``/^[a-zA-Z]/``。
_LEADING_LETTER_RE = re.compile(r"^[a-zA-Z]")
# PORT: JS ``/%[^%a-zA-Z]*(?:hh|ll|[lLhqjzZt])?\\[0-7xX]/``。
_PRINTF_OCTAL_ESCAPE_RE = re.compile(r"%[^%a-zA-Z]*(?:hh|ll|[lLhqjzZt])?\\[0-7xX]")
# PORT: JS ``/\\[uU]/``。
_PRINTF_UNICODE_ESCAPE_RE = re.compile(r"\\[uU]")
# PORT: JS ``/%[-+ 0#']*[0-9.*]*(?:hh|ll|[lLhqjzZt])?[diouxXeEfFgGaAn]/``。
_PRINTF_NUMERIC_FORMAT_RE = re.compile(
    r"%[-+ 0#']*[0-9.*]*(?:hh|ll|[lLhqjzZt])?[diouxXeEfFgGaAn]"
)
# PORT: JS ``/%[^%a-zA-Z]*\*/``。
_PRINTF_STAR_WIDTH_RE = re.compile(r"%[^%a-zA-Z]*\*")
# PORT: JS ``/^[-+]?(0[xX][0-9a-fA-F]+|[0-9]+#[0-9a-zA-Z]+|[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?)$/``。
_PRINTF_SAFE_VALUE_RE = re.compile(
    r"^[-+]?(0[xX][0-9a-fA-F]+|[0-9]+#[0-9a-zA-Z]+|[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?)$"
)


def _is_safe_printf_argv(argv: Sequence[str]) -> bool:
    first = argv[1] if len(argv) > 1 else None
    if first is not None and first.startswith("-") and first != "--":
        return False
    format_index = 2 if (argv[1] if len(argv) > 1 else None) == "--" else 1
    format_text = argv[format_index] if format_index < len(argv) else ""
    if "$" in format_text:
        return False
    normalized_format = format_text.replace("%%", "")
    if _PRINTF_OCTAL_ESCAPE_RE.search(normalized_format):
        return False
    if _PRINTF_UNICODE_ESCAPE_RE.search(normalized_format):
        return False
    numeric_format = _PRINTF_NUMERIC_FORMAT_RE.search(normalized_format) is not None
    if numeric_format or _PRINTF_STAR_WIDTH_RE.search(normalized_format):
        index = format_index + 1
        while index < len(argv):
            value = argv[index] if index < len(argv) else ""
            if "[" in value or "`" in value or "$(" in value:
                return False
            if _PRINTF_SAFE_VALUE_RE.search(value) is None:
                return False
            index += 1
    return True


# PORT: JS ``/^-newer[aBcm][aBcmt]$/``。
_FIND_NEWER_RE = re.compile(r"^-newer[aBcm][aBcmt]$")


def _is_safe_find_argv(argv: Sequence[str]) -> bool:
    index = 1
    while index < len(argv):
        word = argv[index] if index < len(argv) else ""
        if is_find_write_option(word):
            return False
        if word in FIND_VALUE_OPTIONS or _FIND_NEWER_RE.search(word):
            index += 1
            index += 1
            continue
        index += 1
    return True


__all__ = [
    "FIND_WRITE_OPTIONS",
    "is_find_write_option",
    "FIND_VALUE_OPTIONS",
    "evaluate_direct_readonly_argv",
    "READONLY_EXACT_ARGV_COMMANDS",
]
