"""Bash semantics — read-only / silent-command / exit-code interpretation.

Ported from: packages/core/src/tool/handlers/bash-semantics.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol, Sequence

from .bash_command_parser import (
    analyze_bash_command,
    is_bash_command_permission_safe,
)
from .bash_git_runtime_safety import (
    BashReadonlyRuntimeContext,
    analysis_contains_git_and_directory_change,
    analysis_contains_git_command,
    is_git_runtime_context_unsafe,
)
from .bash_readonly_policy import (
    evaluate_bash_readonly_policy,
    has_known_bash_write_option,
    is_sed_in_place_option,
)

__all__ = [
    "BashReadonlyRuntimeContext",
    "is_runtime_read_only_bash_command",
    "is_sed_in_place_bash_command",
    "is_silent_bash_command",
    "interpret_bash_return_code",
    "is_bash_provider_error_status",
]


class _ExecutionErrorSlice(Protocol):
    """Mirrors the ``error`` slice of ``ExecutionResult`` read here."""

    type: str
    message: str


class BashReturnCodeResult(Protocol):
    """Mirrors ``Pick<ExecutionResult, "error" | "exitCode" | "signal" | "status">``."""

    status: str
    exit_code: Optional[int]
    signal: Optional[str]
    error: Optional[_ExecutionErrorSlice]


@dataclass(slots=True)
class BashProviderErrorStatusInput:
    """Mirrors the inline ``{ exitCode?; returnCodeInterpretation?; status? }`` shape.

    # PORT: TS 是匿名结构类型（调用点直接传对象字面量或 ``BashOutput``）；Python 用一个
    # 具名 dataclass 承载，调用点按字段构造。
    """

    status: Any = None
    exit_code: Any = None
    return_code_interpretation: Any = None


BASH_SEMANTIC_NEUTRAL_COMMANDS = frozenset(["", ":", "echo", "false", "printf", "true"])
BASH_SILENT_COMMANDS = frozenset(
    [
        "cd",
        "chgrp",
        "chmod",
        "chown",
        "cp",
        "export",
        "ln",
        "mkdir",
        "mv",
        "rm",
        "rmdir",
        "touch",
        "unset",
        "wait",
    ]
)
SEMANTIC_NON_ERROR_MESSAGES = frozenset(
    [
        "Condition is false",
        "Files differ",
        "No matches found",
        "Some directories were inaccessible",
    ]
)
SEMANTIC_NO_MATCH_COMMANDS = frozenset(["egrep", "fgrep", "grep", "rg"])


def is_runtime_read_only_bash_command(
    command: str,
    context: Optional[BashReadonlyRuntimeContext] = None,
) -> bool:
    """Mirrors ``isRuntimeReadOnlyBashCommand(command, context?)``."""
    analysis = analyze_bash_command(command)
    if not is_bash_command_permission_safe(analysis):
        return False
    if len(analysis.commands) == 0:
        return False
    if analysis_contains_git_and_directory_change(analysis.commands):
        return False
    if analysis_contains_git_command(analysis.commands) and is_git_runtime_context_unsafe(context):
        return False

    has_read_only_command = False

    for command_part in analysis.commands:
        if has_known_bash_write_option(command_part):
            return False

        policy_result = evaluate_bash_readonly_policy(command_part)
        if policy_result is False:
            return False
        if policy_result is True:
            has_read_only_command = True
            continue

        return False

    return has_read_only_command


def is_sed_in_place_bash_command(command: str) -> bool:
    """Mirrors ``isSedInPlaceBashCommand(command)``."""
    analysis = analyze_bash_command(command)
    if analysis.has_parse_errors:
        return False
    return any(
        command_part.name == "sed"
        and any(is_sed_in_place_option(arg) for arg in command_part.argv)
        for command_part in analysis.commands
    )


def is_silent_bash_command(command: str) -> bool:
    """Mirrors ``isSilentBashCommand(command)``."""
    analysis = analyze_bash_command(command)
    if analysis.has_parse_errors or analysis.has_unsupported_syntax or analysis.has_dynamic_words:
        return False
    if len(analysis.commands) == 0:
        return False

    has_non_fallback_command = False

    for command_part in analysis.commands:
        command_name = command_part.name
        if not command_name:
            continue
        if (
            command_part.operator_before == "||"
            and command_name in BASH_SEMANTIC_NEUTRAL_COMMANDS
        ):
            continue

        has_non_fallback_command = True
        if command_name not in BASH_SILENT_COMMANDS:
            return False

    return has_non_fallback_command


def interpret_bash_return_code(
    command: str,
    result: BashReturnCodeResult,
) -> Optional[str]:
    """Mirrors ``interpretBashReturnCode(command, result)``."""
    error = result.error
    if error is not None and error.type == "output_limit":
        return "Command stopped because output exceeded the configured limit"
    if result.status == "timed_out":
        return "Command timed out"
    if result.status == "cancelled":
        return "Command was cancelled"
    if result.status == "spawn_error":
        return "Command failed to start"
    if result.exit_code is not None and result.exit_code != 0:
        semantic = _semantic_non_error_exit(command, result.exit_code)
        return semantic if semantic is not None else f"Command exited with code {result.exit_code}"
    if result.signal:
        return f"Command exited due to signal {result.signal}"
    return None


def _is_semantic_non_error_interpretation(message: Optional[str]) -> bool:
    return message is not None and message in SEMANTIC_NON_ERROR_MESSAGES


def is_bash_provider_error_status(output: BashProviderErrorStatusInput) -> bool:
    """Mirrors ``isBashProviderErrorStatus(output)``."""
    if output.status != "failed":
        return False
    if not isinstance(output.exit_code, (int, float)) or isinstance(output.exit_code, bool):
        return False
    if output.exit_code == 0:
        return False
    return not _is_semantic_non_error_interpretation(
        output.return_code_interpretation
        if isinstance(output.return_code_interpretation, str)
        else None
    )


def _semantic_non_error_exit(command: str, exit_code: int) -> Optional[str]:
    if exit_code != 1:
        return None
    command_name = _status_command_name_for_exit_one(command)
    return None if command_name is None else _semantic_exit_one_interpretation(command_name)


def _status_command_name_for_exit_one(command: str) -> Optional[str]:
    analysis = analyze_bash_command(command)
    if analysis.has_parse_errors or analysis.has_unsupported_syntax:
        return None
    status_command = analysis.commands[-1] if len(analysis.commands) > 0 else None
    if status_command is None:
        return None

    # 不能因为命令行前面出现过 rg/grep，就把后续 test/exit 的 1 误判成 No matches found。
    if status_command.name == "git":
        git_subcommand = _git_semantic_subcommand_name(status_command.argv)
        if git_subcommand == "grep":
            return "grep"
        if git_subcommand == "diff":
            return "diff"
    return status_command.name


def _semantic_exit_one_interpretation(command_name: str) -> Optional[str]:
    if command_name in SEMANTIC_NO_MATCH_COMMANDS:
        return "No matches found"
    if command_name == "find":
        return "Some directories were inaccessible"
    if command_name == "diff":
        return "Files differ"
    if command_name == "test" or command_name == "[":
        return "Condition is false"
    return None


def _git_semantic_subcommand_name(argv: Sequence[str]) -> Optional[str]:
    index = 1
    while index < len(argv):
        arg = argv[index]
        if arg is None:
            index += 1
            continue
        if arg.startswith("-"):
            # PORT: TS 循环体里 ``index += 1`` 后仍会执行 for 的 ``index += 1``；
            # Python 用 while 时在此显式补一次，保持 ``-C``/``-c`` 跳一个值。
            if arg == "-C" or arg == "-c":
                index += 1
            index += 1
            continue
        return arg
    return None
