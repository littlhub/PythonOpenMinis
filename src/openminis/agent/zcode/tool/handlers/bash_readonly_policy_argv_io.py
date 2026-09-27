"""Bash readonly policy — argv IO guards (env assignments / redirects / wrappers).

Ported from: packages/core/src/tool/handlers/bash-readonly-policy-argv-io.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

import re
from typing import Sequence

from .bash_command_parser import BashCommandInvocation

SAFE_ENV_ASSIGNMENTS = frozenset(
    [
        "ANTHROPIC_API_KEY",
        "BLOCK_SIZE",
        "BLOCKSIZE",
        "CGO_ENABLED",
        "CHARSET",
        "CI",
        "CLICOLOR",
        "CLICOLOR_FORCE",
        "COLORTERM",
        "COLUMNS",
        "DEBIAN_FRONTEND",
        "FORCE_COLOR",
        "GCC_COLORS",
        "GIT_TERMINAL_PROMPT",
        "GO111MODULE",
        "GOARCH",
        "GOEXPERIMENT",
        "GOOS",
        "GREP_COLOR",
        "GREP_COLORS",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
        "LC_TIME",
        "LINES",
        "LSCOLORS",
        "LS_COLORS",
        "NO_COLOR",
        "NODE_ENV",
        "PYTEST_DEBUG",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONUNBUFFERED",
        "RUST_BACKTRACE",
        "RUST_LOG",
        "TERM",
        "TIME_STYLE",
        "TZ",
    ]
)


def are_env_assignments_allowed(command_part: BashCommandInvocation) -> bool:
    """Mirrors ``areEnvAssignmentsAllowed(commandPart)``."""
    return all(
        assignment.name is not None and assignment.name in SAFE_ENV_ASSIGNMENTS
        for assignment in command_part.env_assignments
    )


SAFE_INPUT_REDIRECTS = frozenset(["<", "<<", "<&", "<<<"])


def are_redirects_allowed(command_part: BashCommandInvocation) -> bool:
    """Mirrors ``areRedirectsAllowed(commandPart)``."""
    for redirect in command_part.redirects:
        if _is_unsafe_device_redirect_target(redirect.target):
            return False
        if redirect.operator == ">&" and _DIGITS_RE.search(redirect.target) is not None:
            continue
        if redirect.target == "/dev/null":
            continue
        if redirect.operator in SAFE_INPUT_REDIRECTS:
            if is_unsafe_windows_unc_path(redirect.target):
                return False
            continue
        return False
    return True


# PORT: JS ``/^\/dev\/(?:tcp|udp)\//``。
_UNSAFE_DEVICE_REDIRECT_RE = re.compile(r"^/dev/(?:tcp|udp)/")
# PORT: JS ``/^\d+$/``。
_DIGITS_RE = re.compile(r"^\d+$")


def _is_unsafe_device_redirect_target(target: str) -> bool:
    return _UNSAFE_DEVICE_REDIRECT_RE.search(target) is not None


# PORT: JS ``/^(?:\/\/|\\\\)[^/\\]/``。
_UNSAFE_WINDOWS_UNC_RE = re.compile(r"^(?://|\\\\)[^/\\]")


def is_unsafe_windows_unc_path(value: str) -> bool:
    """Mirrors ``isUnsafeWindowsUncPath(value)``."""
    return _UNSAFE_WINDOWS_UNC_RE.search(value) is not None


# PORT: JS ``/^-p+$/``。
_DASH_P_FLAGS_RE = re.compile(r"^-p+$")


def strip_safe_command_wrappers(argv: Sequence[str]) -> list[str]:
    """Mirrors ``stripSafeCommandWrappers(argv)``."""
    stripped = list(argv)
    while True:
        if len(stripped) > 0 and stripped[0] == "command":
            index = 1
            while index < len(stripped) and _DASH_P_FLAGS_RE.search(
                stripped[index] if index < len(stripped) else ""
            ):
                index += 1
            if index < len(stripped) and stripped[index] == "--":
                index += 1
            if index >= len(stripped) or (
                index < len(stripped) and stripped[index].startswith("-")
            ):
                return stripped
            stripped = stripped[index:]
            continue
        if len(stripped) > 0 and stripped[0] == "builtin":
            index = 2 if (len(stripped) > 1 and stripped[1] == "--") else 1
            if index >= len(stripped):
                return stripped
            stripped = stripped[index:]
            continue
        if len(stripped) > 0 and stripped[0] == "noglob":
            if len(stripped) <= 1:
                return stripped
            stripped = stripped[1:]
            continue
        return stripped


__all__ = [
    "SAFE_ENV_ASSIGNMENTS",
    "are_env_assignments_allowed",
    "SAFE_INPUT_REDIRECTS",
    "are_redirects_allowed",
    "is_unsafe_windows_unc_path",
    "strip_safe_command_wrappers",
]
