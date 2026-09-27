"""Bash readonly policy — argv-level flag/positional gate.

Ported from: packages/core/src/tool/handlers/bash-readonly-policy-argv-flags.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

from .bash_readonly_policy_callbacks import XARGS_TARGET_COMMANDS
from .bash_readonly_policy_flags import OPTION_PATTERN
from .bash_readonly_policy_types import BashReadonlyCommandPolicy, SafeFlagValue

__all__ = [
    "is_argv_allowed_by_policy",
]

_HEAD_TAIL_COUNT_FLAG_RE = re.compile(r"^-\d+$")
_NUMBER_RE = re.compile(r"^\d+$")
_GIT_SORT_FLAG_RE = re.compile(r"^-[a-zA-Z]")


def is_argv_allowed_by_policy(
    argv: Sequence[str],
    policy: BashReadonlyCommandPolicy,
    command_name: str,
    start_index: int = 1,
) -> bool:
    """Mirrors ``isArgvAllowedByPolicy(argv, policy, commandName, startIndex = 1)``."""
    if len(argv) == 0:
        return False
    if policy.allow_any_args:
        return True
    if policy.command_only:
        return len(argv) == start_index
    if policy.safe_flags:
        return _flags_and_positionals_allowed(argv, policy, command_name, start_index)
    return False


def _flags_and_positionals_allowed(
    argv: Sequence[str],
    policy: BashReadonlyCommandPolicy,
    command_name: str,
    start_index: int,
) -> bool:
    """Mirrors the non-exported ``flagsAndPositionalsAllowed(...)``."""
    safe_flags = policy.safe_flags if policy.safe_flags is not None else {}
    index = start_index
    while index < len(argv):
        word = argv[index]
        if not word:
            index += 1
            continue

        if command_name == "xargs" and (not word.startswith("-") or word == "--"):
            target_command = argv[index + 1] if word == "--" else word
            # PORT: TS ``!targetCommand || !XARGS_TARGET_COMMANDS.has(targetCommand)``
            # —— 越界读取在 Python 会 IndexError，故显式判长度。
            if not target_command or target_command not in XARGS_TARGET_COMMANDS:
                return False
            break

        if word == "--":
            if policy.respects_double_dash is False:
                index += 1
                continue
            break

        if _is_head_tail_compact_count_flag(command_name, word):
            index += 1
            continue

        if policy.allow_compact_numeric_count_flag and _is_compact_numeric_count_flag(word):
            index += 1
            continue

        if word.startswith("-") and len(word) > 1 and OPTION_PATTERN.search(word):
            parsed = _parse_option_word(word)
            flag_value = safe_flags.get(parsed["flag"])
            if not flag_value:
                short_attached = _parse_short_flag_with_attached_value(word, safe_flags)
                if short_attached:
                    if not _is_option_like_string_value_allowed(
                        short_attached["value"], command_name, short_attached["flag"]
                    ):
                        return False
                    if not _matches_flag_value_kind(short_attached["value"], short_attached["kind"]):
                        return False
                    index += 1
                    continue
                if _is_short_flag_cluster_allowed(parsed["flag"], safe_flags):
                    index += 1
                    continue
                return False

            if flag_value == "none":
                if parsed["has_inline_value"]:
                    return False
                index += 1
                continue

            if flag_value == "optionalString":
                # 只拦截独立 argv 里看起来像 option 的字符串，避免误吞下一段参数。
                index += 1
                continue

            value = (
                parsed["inline_value"]
                if parsed["has_inline_value"]
                else (argv[index + 1] if index + 1 < len(argv) else None)
            )
            if not _is_usable_flag_value(value):
                return False
            if (
                flag_value == "string"
                and not parsed["has_inline_value"]
                and not _is_option_like_string_value_allowed(value, command_name, parsed["flag"])
            ):
                return False
            if not _matches_flag_value_kind(value, flag_value):
                return False
            index += 1 if parsed["has_inline_value"] else 2
            continue

        index += 1

    return True


def _parse_short_flag_with_attached_value(
    word: str,
    safe_flags: dict[str, SafeFlagValue],
) -> Optional[dict[str, str]]:
    """Mirrors ``parseShortFlagWithAttachedValue(word, safeFlags)``.

    # PORT: TS 返回 ``{ flag; kind; value } | undefined``；Python 用 ``dict[str, str]``
    # （``kind`` 是 ``SafeFlagValue`` 字面量，本质仍是 str）或 ``None``。
    """
    if not word.startswith("-") or word.startswith("--") or len(word) <= 2:
        return None
    flag = word[:2]
    kind = safe_flags.get(flag)
    if not kind or kind == "none":
        return None
    return {"flag": flag, "kind": kind, "value": word[2:]}


def _is_head_tail_compact_count_flag(command_name: str, word: str) -> bool:
    """Mirrors ``isHeadTailCompactCountFlag(commandName, word)``."""
    return (command_name == "head" or command_name == "tail") and bool(
        _HEAD_TAIL_COUNT_FLAG_RE.match(word)
    )


def _is_compact_numeric_count_flag(word: str) -> bool:
    """Mirrors ``isCompactNumericCountFlag(word)``."""
    return bool(_HEAD_TAIL_COUNT_FLAG_RE.match(word))


def _parse_option_word(word: str) -> dict[str, object]:
    """Mirrors ``parseOptionWord(word)``."""
    equals_index = word.find("=")
    if equals_index < 0:
        return {"flag": word, "has_inline_value": False, "inline_value": ""}
    return {
        "flag": word[:equals_index],
        "has_inline_value": True,
        "inline_value": word[equals_index + 1 :],
    }


def _is_short_flag_cluster_allowed(flag: str, safe_flags: dict[str, SafeFlagValue]) -> bool:
    """Mirrors ``isShortFlagClusterAllowed(flag, safeFlags)``."""
    if not flag.startswith("-") or flag.startswith("--") or len(flag) <= 2:
        return False
    for flag_name in [f"-{name}" for name in flag[1:]]:
        if safe_flags.get(flag_name) != "none":
            return False
    return True


def _is_usable_flag_value(value: Optional[str]) -> bool:
    """Mirrors ``isUsableFlagValue(value): value is string``."""
    return value is not None


def _is_option_like_string_value_allowed(value: str, command_name: str, flag: str) -> bool:
    """Mirrors ``isOptionLikeStringValueAllowed(value, commandName, flag)``."""
    if not value.startswith("-") or len(value) <= 1 or not OPTION_PATTERN.search(value):
        return True

    return command_name == "git" and flag == "--sort" and bool(_GIT_SORT_FLAG_RE.match(value))


def _matches_flag_value_kind(value: str, kind: SafeFlagValue) -> bool:
    """Mirrors ``matchesFlagValueKind(value, kind)``.

    # PORT: TS ``switch`` 穷尽 ``SafeFlagValue`` 且无 default。Python 用 ``match`` +
    # ``case _:`` 兜底（对应联合类型外的一支，返回 False 与 TS 的不可达语义一致）。
    """
    match kind:
        case "none":
            return False
        case "number":
            return bool(_NUMBER_RE.match(value))
        case "optionalString":
            return True
        case "string":
            return True
        case "char":
            return len(value) == 1
        case "{}":
            return value == "{}"
        case "EOF":
            return value == "EOF"
        case _:
            return False
