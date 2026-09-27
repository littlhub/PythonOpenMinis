"""Bash read-file sources — recover file reads implied by a bash command.

Ported from: packages/core/src/tool/handlers/bash-read-file-sources.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from .bash_command_parser import (
    BashCommandInvocation,
    analyze_bash_command,
    is_bash_command_permission_safe,
)

__all__ = [
    "BashReadFileSource",
    "SelectedReadContent",
    "collect_bash_read_file_sources",
    "select_read_content",
]

DEFAULT_HEAD_LINES = 10
DEFAULT_TAIL_LINES = 10
SED_RANGE_PRINT_RE = re.compile(r"^(\d+),(\d+)p$")
SED_SINGLE_PRINT_RE = re.compile(r"^(\d+)p$")
SIMPLE_IGNORED_COMMAND_RE = re.compile(r"^\s*(echo|printf|true|:)\b")
GREP_SHORT_FLAGS_RE = re.compile(r"^-[niwxEFGPHh]+$")
GREP_CONTEXT_SHORT_RE = re.compile(r"^-[ABC]\d+$")
GREP_CONTEXT_LONG_RE = re.compile(r"^--(?:after-context|before-context|context)=\d+$")
GREP_ALLOWED_LONG_FLAGS = frozenset(
    [
        "--line-number",
        "--ignore-case",
        "--word-regexp",
        "--line-regexp",
        "--extended-regexp",
        "--fixed-strings",
        "--basic-regexp",
        "--perl-regexp",
        "--with-filename",
        "--no-filename",
        "--color=never",
        "--color=auto",
    ]
)

_PIPE_OR_REDIRECT_RE = re.compile(r"[|<>]")
_HEAD_TAIL_ATTACHED_LINES_RE = re.compile(r"^-n\d+$")
_DASH_NUMBER_RE = re.compile(r"^-\d+$")
_POSITIVE_INT_RE = re.compile(r"^\d+$")
_GREP_GLOB_CHARS_RE = re.compile(r"[*?[{]")


@dataclass(slots=True)
class BashReadFileSource:
    """Mirrors ``interface BashReadFileSource``."""

    file_path: str
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    tail_lines: Optional[int] = None
    requires_exit_zero: Optional[bool] = None


@dataclass(slots=True)
class SelectedReadContent:
    """Mirrors ``interface SelectedReadContent``."""

    content: str
    offset: Optional[int] = None
    limit: Optional[int] = None


def collect_bash_read_file_sources(command: str) -> list[BashReadFileSource]:
    """Mirrors ``collectBashReadFileSources(command)``."""
    if _PIPE_OR_REDIRECT_RE.search(command):
        return []
    analysis = analyze_bash_command(command)
    if not is_bash_command_permission_safe(analysis) or analysis.has_redirects:
        return []
    if len(analysis.commands) == 0:
        return []

    sources: list[BashReadFileSource] = []
    for command_part in analysis.commands:
        # PORT: TS 用 ``??`` 短路链挑选第一个命中的解析器；Python 逐级回调同序。
        source = (
            _parse_sed_print_source(command_part)
            or _parse_cat_source(command_part)
            or _parse_head_source(command_part)
            or _parse_tail_source(command_part)
            or (
                _parse_grep_source(command_part)
                if len(analysis.commands) == 1
                else None
            )
        )
        if source is not None:
            sources.append(source)
            continue
        if len(analysis.commands) > 1 and SIMPLE_IGNORED_COMMAND_RE.search(
            command_part.command_text
        ):
            continue
        return []
    return sources


def select_read_content(
    content: str,
    source: BashReadFileSource,
) -> Optional[SelectedReadContent]:
    """Mirrors ``selectReadContent(content, source)``."""
    if source.tail_lines is not None:
        lines = content.split("\n")
        # PORT: TS ``lines.at(-1) === ""`` → ``lines[-1]``（先判非空，避免 IndexError）。
        if len(lines) > 0 and lines[-1] == "":
            lines.pop()
        if len(lines) == 0:
            return None
        limit = min(source.tail_lines, len(lines))
        offset = len(lines) - limit + 1
        return SelectedReadContent(
            content="\n".join(lines[offset - 1 :]),
            offset=offset,
            limit=limit,
        )

    if source.start_line is None:
        return SelectedReadContent(content=content)

    lines = content.split("\n")
    start = max(1, source.start_line)
    # PORT: TS ``source.endLine ?? start``
    end = max(start, source.end_line if source.end_line is not None else start)
    if start > len(lines):
        return None
    return SelectedReadContent(
        content="\n".join(lines[start - 1 : end]),
        offset=start,
        limit=end - start + 1,
    )


def _parse_cat_source(command_part: BashCommandInvocation) -> Optional[BashReadFileSource]:
    """Mirrors the non-exported ``parseCatSource(commandPart)``."""
    argv = command_part.argv
    if not argv or argv[0] != "cat":
        return None
    file_path: Optional[str] = None
    for arg in argv[1:]:
        if arg.startswith("-"):
            if arg != "-n" and arg != "--number":
                return None
            continue
        if file_path is not None:
            return None
        file_path = arg
    if not _is_concrete_file_path(file_path):
        return None
    return BashReadFileSource(file_path=file_path)


def _parse_head_source(command_part: BashCommandInvocation) -> Optional[BashReadFileSource]:
    """Mirrors the non-exported ``parseHeadSource(commandPart)``."""
    parsed = _parse_head_tail_count(command_part.argv, DEFAULT_HEAD_LINES)
    if parsed is None or not command_part.argv or command_part.argv[0] != "head":
        return None
    return BashReadFileSource(
        file_path=parsed["file_path"],
        start_line=1,
        end_line=parsed["count"],
    )


def _parse_tail_source(command_part: BashCommandInvocation) -> Optional[BashReadFileSource]:
    """Mirrors the non-exported ``parseTailSource(commandPart)``."""
    parsed = _parse_head_tail_count(command_part.argv, DEFAULT_TAIL_LINES)
    if parsed is None or not command_part.argv or command_part.argv[0] != "tail":
        return None
    return BashReadFileSource(
        file_path=parsed["file_path"],
        tail_lines=parsed["count"],
    )


def _parse_head_tail_count(
    argv: Sequence[str],
    default_count: int,
) -> Optional[dict[str, object]]:
    """Mirrors the non-exported ``parseHeadTailCount(argv, defaultCount)``.

    # PORT: TS 返回 ``{ count; filePath }``；Python 用 ``dict``（``count`` 可能为 None，
    # 与 TS 里 ``count ?? defaultCount`` 的兜底位置一致）。
    """
    count: Optional[int] = None
    file_path: Optional[str] = None
    index = 1
    while index < len(argv):
        arg = argv[index]
        if arg == "-n" or arg == "--lines":
            index += 1
            # PORT: TS ``argv[++index]`` 越界得 ``undefined``，由类型守卫判假返回。
            value = argv[index] if index < len(argv) else None
            if not _is_positive_integer_string(value):
                return None
            count = int(value)
            index += 1
            continue
        if arg.startswith("--lines="):
            value = arg[len("--lines=") :]
            if not _is_positive_integer_string(value):
                return None
            count = int(value)
            index += 1
            continue
        if _HEAD_TAIL_ATTACHED_LINES_RE.match(arg):
            count = int(arg[2:])
            index += 1
            continue
        if _DASH_NUMBER_RE.match(arg):
            count = int(arg[1:])
            index += 1
            continue
        if arg.startswith("-"):
            return None
        if file_path is not None:
            return None
        file_path = arg
        index += 1
    if not _is_concrete_file_path(file_path):
        return None
    return {
        "count": count if count is not None else default_count,
        "file_path": file_path,
    }


def _parse_sed_print_source(command_part: BashCommandInvocation) -> Optional[BashReadFileSource]:
    """Mirrors the non-exported ``parseSedPrintSource(commandPart)``."""
    argv = command_part.argv
    if not argv or argv[0] != "sed":
        return None
    quiet = False
    expression: Optional[str] = None
    file_path: Optional[str] = None

    for arg in argv[1:]:
        if arg.startswith("-"):
            if arg.startswith("--"):
                if arg == "--in-place" or arg.startswith("--in-place="):
                    return None
                if arg == "--expression":
                    return None
                if arg == "--quiet" or arg == "--silent":
                    quiet = True
            else:
                if "i" in arg:
                    return None
                if arg == "-e":
                    return None
                if "n" in arg:
                    quiet = True
            continue
        if expression is None:
            expression = arg
        elif file_path is None:
            file_path = arg
        else:
            return None

    if not quiet or expression is None or not _is_concrete_file_path(file_path):
        return None
    range_match = SED_RANGE_PRINT_RE.match(expression)
    if range_match:
        return BashReadFileSource(
            file_path=file_path,
            start_line=int(range_match.group(1)),
            end_line=int(range_match.group(2)),
        )
    single_match = SED_SINGLE_PRINT_RE.match(expression)
    if not single_match:
        return None
    line = int(single_match.group(1))
    return BashReadFileSource(
        file_path=file_path,
        start_line=line,
        end_line=line,
    )


def _parse_grep_source(command_part: BashCommandInvocation) -> Optional[BashReadFileSource]:
    """Mirrors the non-exported ``parseGrepSource(commandPart)``."""
    argv = command_part.argv
    if not argv or argv[0] not in ("grep", "egrep", "fgrep"):
        return None
    pattern: Optional[str] = None
    file_path: Optional[str] = None

    index = 1
    while index < len(argv):
        arg = argv[index]
        if arg.startswith("-") and arg != "-":
            if arg == "-A" or arg == "-B" or arg == "-C":
                index += 1
                # PORT: TS ``argv[++index]`` 越界得 ``undefined``（非负整数守卫判假）。
                value = argv[index] if index < len(argv) else None
                if not _is_non_negative_integer_string(value):
                    return None
                index += 1
                continue
            if (
                GREP_CONTEXT_SHORT_RE.match(arg)
                or GREP_CONTEXT_LONG_RE.match(arg)
                or GREP_SHORT_FLAGS_RE.match(arg)
                or arg in GREP_ALLOWED_LONG_FLAGS
            ):
                index += 1
                continue
            return None
        if pattern is None:
            pattern = arg
        elif file_path is None:
            file_path = arg
        else:
            return None
        index += 1

    if pattern is None or not _is_concrete_file_path(file_path):
        return None
    if _GREP_GLOB_CHARS_RE.search(file_path):
        return None
    return BashReadFileSource(file_path=file_path, requires_exit_zero=True)


def _is_concrete_file_path(value: Optional[str]) -> bool:
    """Mirrors the non-exported ``isConcreteFilePath(value): value is string``."""
    return value is not None and len(value) > 0 and value != "-"


def _is_positive_integer_string(value: Optional[str]) -> bool:
    """Mirrors the non-exported ``isPositiveIntegerString(value): value is string``."""
    return (
        value is not None and bool(_POSITIVE_INT_RE.match(value)) and int(value) > 0
    )


def _is_non_negative_integer_string(value: Optional[str]) -> bool:
    """Mirrors the non-exported ``isNonNegativeIntegerString(value): value is string``."""
    return value is not None and bool(_POSITIVE_INT_RE.match(value))
