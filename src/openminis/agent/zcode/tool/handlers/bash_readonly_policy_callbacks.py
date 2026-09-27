"""Bash readonly policy — per-command dynamic-danger callbacks.

Ported from: packages/core/src/tool/handlers/bash-readonly-policy-callbacks.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

import re
from typing import Optional, Sequence


def is_sed_in_place_option(word: str) -> bool:
    """Mirrors ``isSedInPlaceOption(word)``."""
    return word.startswith("-i") or word == "--in-place" or word.startswith("--in-place=")


def jq_command_is_dangerous(_command_text: str, args: Sequence[str]) -> bool:
    """Mirrors ``jqCommandIsDangerous(commandText, args)``."""
    index = 0
    while index < len(args):
        arg = args[index] if index < len(args) else ""
        if not arg:
            index += 1
            continue
        if _is_jq_dangerous_option(arg):
            return True
        if arg == "--":
            return _jq_filter_is_dangerous(args[index + 1] if index + 1 < len(args) else "")
        if arg == "--indent":
            index += 1
            index += 1
            continue
        if arg.startswith("-"):
            index += 1
            continue
        return _jq_filter_is_dangerous(arg)
    return False


def _is_jq_dangerous_option(word: str) -> bool:
    return (
        word == "-f"
        or word.startswith("-f")
        or word == "-L"
        or word.startswith("-L")
        or word == "--argfile"
        or word.startswith("--argfile=")
        or word == "--from-file"
        or word.startswith("--from-file=")
        or word == "--library-path"
        or word.startswith("--library-path=")
        or word == "--rawfile"
        or word.startswith("--rawfile=")
        or word == "--run-tests"
        or word.startswith("--run-tests=")
        or word == "--slurpfile"
        or word.startswith("--slurpfile=")
    )


# PORT: JS ``/\$ENV\b/``、``/(^|[^A-Za-z0-9_$.])env(?=$|[^A-Za-z0-9_])/``、
# ``/(^|[^A-Za-z0-9_])(?:include|import)(?=$|[^A-Za-z0-9_])/`` —— 无 g 标志，用 re.search。
_JQ_ENV_RE = re.compile(r"\$ENV\b")
_JQ_ENV_FN_RE = re.compile(r"(^|[^A-Za-z0-9_$.])env(?=$|[^A-Za-z0-9_])")
_JQ_INCLUDE_RE = re.compile(r"(^|[^A-Za-z0-9_])(?:include|import)(?=$|[^A-Za-z0-9_])")


def _jq_filter_is_dangerous(filter_text: str) -> bool:
    return (
        _JQ_ENV_RE.search(filter_text) is not None
        or _JQ_ENV_FN_RE.search(filter_text) is not None
        or _JQ_INCLUDE_RE.search(filter_text) is not None
    )


def sed_command_is_dangerous(_command_text: str, args: Sequence[str]) -> bool:
    """Mirrors ``sedCommandIsDangerous(commandText, args)``."""
    first_script_seen = False
    index = 0
    while index < len(args):
        arg = args[index] if index < len(args) else ""
        if not arg:
            index += 1
            continue
        if is_sed_in_place_option(arg):
            return True
        if arg == "-e" or arg == "--expression":
            index += 1
            if _sed_script_writes_to_file(args[index] if index < len(args) else ""):
                return True
            index += 1
            continue
        if arg.startswith("--expression="):
            if _sed_script_writes_to_file(arg[len("--expression=") :]):
                return True
            index += 1
            continue
        if arg == "-l" or arg == "--line-length":
            index += 1
            index += 1
            continue
        if arg.startswith("--line-length="):
            index += 1
            continue
        if arg == "--":
            script = args[index + 1] if index + 1 < len(args) else ""
            return _sed_script_writes_to_file(script)
        if not arg.startswith("-") and not first_script_seen:
            first_script_seen = True
            if _sed_script_writes_to_file(arg):
                return True
        index += 1
    return False


# PORT: JS ``/(?:^|[;{\n])\s*(?:[0-9,$!+~-]+)?\s*w(?:\s|$)/`` 无 g 标志，用 re.search。
_SED_WRITE_SCRIPT_RE = re.compile(r"(?:^|[;{\n])\s*(?:[0-9,$!+~-]+)?\s*w(?:\s|$)")


def _sed_script_writes_to_file(script: str) -> bool:
    return _SED_WRITE_SCRIPT_RE.search(script) is not None


def date_command_is_dangerous(_command_text: str, args: Sequence[str]) -> bool:
    """Mirrors ``dateCommandIsDangerous(commandText, args)``."""
    value_flags = frozenset(["-d", "--date", "-r", "--reference", "--rfc-3339"])
    index = 0
    while index < len(args):
        arg = args[index] if index < len(args) else ""
        if arg.startswith("--") and "=" in arg:
            index += 1
            continue
        if arg.startswith("-"):
            index += 2 if arg in value_flags else 1
            continue
        if not arg.startswith("+"):
            return True
        index += 1
    return False


def ps_command_is_dangerous(_command_text: str, args: Sequence[str]) -> bool:
    """Mirrors ``psCommandIsDangerous(commandText, args)``."""
    return any(not arg.startswith("-") and _PS_DANGEROUS_ARG_RE.search(arg) is not None for arg in args)


# PORT: JS ``/^[a-zA-Z]*e[a-zA-Z]*$/``。
_PS_DANGEROUS_ARG_RE = re.compile(r"^[a-zA-Z]*e[a-zA-Z]*$")


def pyright_command_is_dangerous(_command_text: str, args: Sequence[str]) -> bool:
    """Mirrors ``pyrightCommandIsDangerous(commandText, args)``."""
    return any(arg == "--watch" or arg == "-w" for arg in args)


def man_command_is_dangerous(_command_text: str, args: Sequence[str]) -> bool:
    """Mirrors ``manCommandIsDangerous(commandText, args)``."""
    apropos_flags = frozenset(["-k", "-f", "--apropos", "--whatis"])
    value_flags = frozenset(["-S", "-s"])
    is_apropos = False
    after_double_dash = False
    index = 0
    while index < len(args):
        arg = args[index] if index < len(args) else ""
        if not after_double_dash and arg == "--":
            after_double_dash = True
            index += 1
            continue
        if not after_double_dash and arg.startswith("-") and arg != "-":
            if arg in apropos_flags:
                is_apropos = True
            if arg in value_flags:
                index += 1
            index += 1
            continue
        after_double_dash = True
        if "/" in arg:
            return not is_apropos
        index += 1
    return False


# PORT: JS ``/^-[a-zA-Z]*i\S*@/``。
_LSOF_ATTACHED_INET_RE = re.compile(r"^-[a-zA-Z]*i\S*@")
# PORT: JS ``/^-[a-zA-Z]*i$/``。
_LSOF_BARE_INET_RE = re.compile(r"^-[a-zA-Z]*i$")
# PORT: JS ``/[a-zA-Z]/``。
_HAS_ASCII_LETTER_RE = re.compile(r"[a-zA-Z]")


def lsof_command_is_dangerous(_command_text: str, args: Sequence[str]) -> bool:
    """Mirrors ``lsofCommandIsDangerous(commandText, args)``."""
    for index in range(len(args)):
        arg = args[index] if index < len(args) else ""
        if arg == "+m" or arg.startswith("+m"):
            return True
        if _LSOF_ATTACHED_INET_RE.search(arg) is not None:
            host = arg[arg.index("@") + 1 :].split(":")[0] if arg.index("@") + 1 < len(arg) else ""
            if _HAS_ASCII_LETTER_RE.search(host):
                return True
        if _LSOF_BARE_INET_RE.search(arg) is not None:
            next_arg = args[index + 1] if index + 1 < len(args) else ""
            if "@" in next_arg:
                host = (
                    next_arg[next_arg.index("@") + 1 :].split(":")[0]
                    if next_arg.index("@") + 1 < len(next_arg)
                    else ""
                )
                if _HAS_ASCII_LETTER_RE.search(host):
                    return True
    return False


TPUT_DANGEROUS_CAPABILITIES = frozenset(
    [
        "clear",
        "flash",
        "if",
        "init",
        "iprog",
        "is1",
        "is2",
        "is3",
        "mc0",
        "mc4",
        "mc5",
        "mc5i",
        "mc5p",
        "pfkey",
        "pfloc",
        "pfx",
        "pfxl",
        "reset",
        "rf",
        "rmcup",
        "rs1",
        "rs2",
        "rs3",
        "smcup",
    ]
)


def tput_command_is_dangerous(_command_text: str, args: Sequence[str]) -> bool:
    """Mirrors ``tputCommandIsDangerous(commandText, args)``."""
    after_double_dash = False
    index = 0
    while index < len(args):
        arg = args[index] if index < len(args) else ""
        if arg == "--":
            after_double_dash = True
            index += 1
            continue
        if not after_double_dash and arg.startswith("-"):
            if arg == "-S":
                return True
            if not arg.startswith("--") and len(arg) > 2 and "S" in arg:
                return True
            index += 2 if arg == "-T" else 1
            continue
        if arg in TPUT_DANGEROUS_CAPABILITIES:
            return True
        index += 1
    return False


# PORT: JS ``/^(dst|src|dport|sport|and|or|not|eq|ne|ge|le|gt|lt|autobound|state|exclude|dev|fwmark|cgroup)$/``。
_SS_KEYWORDS_RE = re.compile(
    r"^(dst|src|dport|sport|and|or|not|eq|ne|ge|le|gt|lt|autobound|state|exclude|dev|fwmark|cgroup)$"
)
# PORT: JS ``/^(state|exclude|dport|sport|dev|fwmark|cgroup)$/``。
_SS_VALUE_KEYWORDS_RE = re.compile(r"^(state|exclude|dport|sport|dev|fwmark|cgroup)$")
# PORT: JS ``/^(-f|--family|-A|--query|--socket)$/``。
_SS_VALUE_FLAGS_RE = re.compile(r"^(-f|--family|-A|--query|--socket)$")
# PORT: JS ``/[\s()=!<>&|,]+/``（用于 split）。
_SS_TOKEN_SPLIT_RE = re.compile(r"[\s()=!<>&|,]+")
# PORT: JS ``/[g-zG-Z]/``。
_SS_UPPER_OR_LATE_RE = re.compile(r"[g-zG-Z]")
# PORT: JS ``/[a-fA-F]/``。
_SS_EARLY_HEX_RE = re.compile(r"[a-fA-F]")


def ss_command_is_dangerous(_command_text: str, args: Sequence[str]) -> bool:
    """Mirrors ``ssCommandIsDangerous(commandText, args)``."""
    positional: list[str] = []
    after_double_dash = False
    index = 0
    while index < len(args):
        arg = args[index] if index < len(args) else ""
        if not after_double_dash and arg == "--":
            after_double_dash = True
            index += 1
            continue
        if not after_double_dash and arg.startswith("-"):
            if _SS_VALUE_FLAGS_RE.search(arg):
                index += 1
            index += 1
            continue
        positional.append(arg)
        index += 1

    tokens = [token for token in _SS_TOKEN_SPLIT_RE.split(" ".join(positional)) if token]
    skip_value = False
    for token in tokens:
        if skip_value:
            skip_value = False
            continue
        if _SS_KEYWORDS_RE.search(token):
            skip_value = _SS_VALUE_KEYWORDS_RE.search(token) is not None
            continue
        if _SS_UPPER_OR_LATE_RE.search(token) or (
            _SS_EARLY_HEX_RE.search(token) and ("." in token or ":" not in token)
        ):
            return True
    return False


TEST_NUMERIC_OPERATORS = frozenset(["-eq", "-ne", "-lt", "-le", "-gt", "-ge"])
# PORT: JS ``/^-?(0[xX][0-9a-fA-F]+|[0-9]+#[0-9a-zA-Z]+|[0-9]+)$/``。
TEST_SAFE_NUMBER = re.compile(r"^-?(0[xX][0-9a-fA-F]+|[0-9]+#[0-9a-zA-Z]+|[0-9]+)$")
# PORT: JS ``/\[/``。
_TEST_BRACKET_RE = re.compile(r"\[")


def test_command_is_dangerous(_command_text: str, args: Sequence[str]) -> bool:
    """Mirrors ``testCommandIsDangerous(commandText, args)``."""
    if any(
        arg == "-v"
        or arg == "-R"
        or arg == "-a"
        or arg == "-o"
        or _TEST_BRACKET_RE.search(arg) is not None
        for arg in args
    ):
        return True
    for index in range(len(args)):
        arg = args[index] if index < len(args) else ""
        if arg in TEST_NUMERIC_OPERATORS:
            for value in [
                args[index - 1] if index - 1 >= 0 else None,
                args[index + 1] if index + 1 < len(args) else None,
            ]:
                if value is not None and TEST_SAFE_NUMBER.search(value) is None:
                    return True
        if arg == "-t":
            value = args[index + 1] if index + 1 < len(args) else None
            if value is not None and TEST_SAFE_NUMBER.search(value) is None:
                return True
    return False


XARGS_TARGET_COMMANDS = frozenset(
    [
        "echo",
        "printf",
        "wc",
        "grep",
        "egrep",
        "fgrep",
        "head",
        "tail",
    ]
)
_XARGS_VALUE_FLAGS = frozenset(["-I", "-n", "-P", "-L", "-s", "-E", "-d"])


def xargs_command_is_dangerous(_command_text: str, args: Sequence[str]) -> bool:
    """Mirrors ``xargsCommandIsDangerous(commandText, args)``."""
    index = 0
    while index < len(args):
        arg = args[index] if index < len(args) else ""
        if not arg:
            index += 1
            continue
        if arg == "--" and index + 1 < len(args):
            index += 1
            arg = args[index] if index < len(args) else ""
        if arg.startswith("-") and arg != "-":
            if arg in _XARGS_VALUE_FLAGS:
                index += 1
            index += 1
            continue
        return arg not in XARGS_TARGET_COMMANDS
    return False


def gh_command_is_dangerous(_command_text: str, args: Sequence[str]) -> bool:
    """Mirrors ``ghCommandIsDangerous(commandText, args)``."""
    for arg in args:
        if not arg:
            continue
        value = arg
        if arg.startswith("-"):
            equals_index = arg.index("=") if "=" in arg else -1
            if equals_index == -1:
                continue
            value = arg[equals_index + 1 :]
            if not value:
                continue
        if "/" not in value and "://" not in value and "@" not in value:
            continue
        if "://" in value or "@" in value:
            return True
        if value.count("/") >= 2:
            return True
    return False


_DOCKER_DANGEROUS_GLOBAL_FLAGS = [
    "-H",
    "-c",
    "--config",
    "--context",
    "--host",
    "--tlscacert",
    "--tlscert",
    "--tlskey",
]
_DOCKER_DANGEROUS_SHORT_FLAGS = frozenset(
    flag[1] for flag in _DOCKER_DANGEROUS_GLOBAL_FLAGS if len(flag) == 2
)


def docker_command_is_dangerous(_command_text: str, args: Sequence[str]) -> bool:
    """Mirrors ``dockerCommandIsDangerous(commandText, args)``."""
    return has_dangerous_docker_option(args)


# PORT: JS ``/^-([A-Za-z]+)/``（用于 ``.match(...)?.[1]``）。
_DOCKER_SHORT_CLUSTER_RE = re.compile(r"^-([A-Za-z]+)")


def has_dangerous_docker_option(args: Sequence[str]) -> bool:
    """Mirrors ``hasDangerousDockerOption(args)``."""
    for arg in args:
        if any(
            arg == flag
            or arg.startswith(f"{flag}=")
            or (len(flag) == 2 and len(arg) > 2 and arg.startswith(flag))
            for flag in _DOCKER_DANGEROUS_GLOBAL_FLAGS
        ):
            return True
        match = _DOCKER_SHORT_CLUSTER_RE.search(arg)
        short_flags = match.group(1) if match is not None else None
        if short_flags is not None and len(short_flags) >= 2:
            for flag in short_flags:
                if flag in _DOCKER_DANGEROUS_SHORT_FLAGS:
                    return True
    return False


# PORT: TS 文件末尾 ``export * from "./bash-readonly-policy-git-callbacks.js"``。
from .bash_readonly_policy_git_callbacks import *  # noqa: E402,F401,F403

__all__ = [
    "is_sed_in_place_option",
    "jq_command_is_dangerous",
    "sed_command_is_dangerous",
    "date_command_is_dangerous",
    "ps_command_is_dangerous",
    "pyright_command_is_dangerous",
    "man_command_is_dangerous",
    "lsof_command_is_dangerous",
    "TPUT_DANGEROUS_CAPABILITIES",
    "tput_command_is_dangerous",
    "ss_command_is_dangerous",
    "TEST_NUMERIC_OPERATORS",
    "TEST_SAFE_NUMBER",
    "test_command_is_dangerous",
    "XARGS_TARGET_COMMANDS",
    "xargs_command_is_dangerous",
    "gh_command_is_dangerous",
    "docker_command_is_dangerous",
    "has_dangerous_docker_option",
    # re-exported from bash-readonly-policy-git-callbacks
    "git_revision_format_command_is_dangerous",
    "git_reflog_command_is_dangerous",
    "git_ls_remote_command_is_dangerous",
    "git_remote_show_command_is_dangerous",
    "git_tag_command_is_dangerous",
    "git_branch_command_is_dangerous",
    "git_list_like_command_is_dangerous",
]
