"""Bash command parser — parse a shell command into a permission-safe analysis.

Ported from: packages/core/src/tool/handlers/bash-command-parser.ts
Original package: @zcode/core (tool/handlers)

# PORT-STATUS: partial

# PORT: TS 依赖第三方 ``unbash`` 包把命令解析成 AST（``parse(command)``）。本包没有等价
# 依赖，故把 ``parse`` 保留为端口函数（未配置时抛 ``NotImplementedError``，由
# ``analyze_bash_command`` 的 ``try/except`` 收口为 ``has_parse_errors=True`` —— 与 TS 中
# parse 抛错时的行为一致：解析不出来即判定为「权限不安全」，保守拒绝）。除该端口外，
# 本文件所有 AST 遍历、动态词判定、不支持的节点收集逻辑均逐行照搬。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Sequence

__all__ = [
    "BashCommandOperator",
    "BashCommandEnvAssignment",
    "BashCommandRedirect",
    "BashCommandInvocation",
    "BashCommandAnalysis",
    "analyze_bash_command",
    "is_bash_command_permission_safe",
]


# ---------------------------------------------------------------------------
# unbash AST — 只声明本文件实际读取的字段
# ---------------------------------------------------------------------------


class WordPart(Protocol):
    """Mirrors the subset of ``unbash`` ``WordPart`` this module reads."""

    type: str
    # 仅 ``DoubleQuoted`` / ``LocaleString`` 带嵌套 parts。
    parts: Optional[Sequence["WordPart"]]


class Word(Protocol):
    """Mirrors the subset of ``unbash`` ``Word`` this module reads."""

    text: str
    value: Optional[str]
    parts: Optional[Sequence[WordPart]]


class Redirect(Protocol):
    """Mirrors the subset of ``unbash`` ``Redirect`` this module reads."""

    file_descriptor: Optional[int]
    operator: str
    target: Optional[Word]
    content: Optional[str]
    body: Optional[Word]


class Assignment(Protocol):
    """Mirrors the subset of ``unbash`` ``Assignment`` (command prefix) read here."""

    name: Optional[str]
    value: Optional[Word]


class Node(Protocol):
    """Mirrors ``unbash`` ``Node`` — discriminated on ``type``."""

    type: str


class Command(Node, Protocol):
    name: Optional[Word]
    suffix: Sequence[Word]
    prefix: Sequence[Assignment]
    redirects: Sequence[Redirect]
    pos: int
    end: int


class AndOr(Node, Protocol):
    commands: Sequence[Node]
    operators: Sequence[str]


class Pipeline(Node, Protocol):
    commands: Sequence[Node]
    operators: Sequence[str]


class Statement(Node, Protocol):
    background: bool
    redirects: Sequence[Redirect]
    command: Node


class Script(Protocol):
    commands: Sequence[Statement]
    # PORT: ``Script & { errors?: unknown[] }``。
    errors: Optional[Sequence[object]]


def parse(command: str) -> Script:
    """PORT: 端口占位，对应 TS ``import { parse } from "unbash"``。

    真实实现由调用方（OpenMinis 侧适配层）提供；未配置时抛
    ``NotImplementedError``，调用方 ``analyze_bash_command`` 会将其视为解析失败。
    """
    raise NotImplementedError("unbash parse port is not configured")


# ---------------------------------------------------------------------------
# Public shapes
# ---------------------------------------------------------------------------

BashCommandOperator = str  # "&&" | "||" | "|" | "|&" | "sequence"


@dataclass(frozen=True, slots=True)
class BashCommandEnvAssignment:
    """Mirrors ``interface BashCommandEnvAssignment``."""

    name: Optional[str]
    value: Optional[str]


@dataclass(frozen=True, slots=True)
class BashCommandRedirect:
    """Mirrors ``interface BashCommandRedirect``."""

    file_descriptor: Optional[int]
    operator: str
    target: str


@dataclass(frozen=True, slots=True)
class BashCommandInvocation:
    """Mirrors ``interface BashCommandInvocation``."""

    argv: list[str]
    command_text: str
    env_assignments: list[BashCommandEnvAssignment]
    has_assignment_prefix: bool
    has_dynamic_words: bool
    has_redirects: bool
    name: str
    redirects: list[BashCommandRedirect]
    operator_before: Optional[BashCommandOperator] = None


@dataclass(frozen=True, slots=True)
class BashCommandAnalysis:
    """Mirrors ``interface BashCommandAnalysis``."""

    commands: list[BashCommandInvocation]
    has_dynamic_words: bool
    has_parse_errors: bool
    has_redirects: bool
    has_unsupported_syntax: bool
    unsupported_node_types: list[str]


MAX_BASH_PARSE_LENGTH = 10_000
SUPPORTED_CONTAINER_NODES = frozenset(["AndOr", "Pipeline", "Statement"])


def analyze_bash_command(command: str) -> BashCommandAnalysis:
    """Mirrors ``analyzeBashCommand(command)``."""
    trimmed = command.strip()
    if len(trimmed) == 0:
        return _empty_analysis()
    if len(command) > MAX_BASH_PARSE_LENGTH:
        return _with_parse_error()

    try:
        script = parse(command)
    except Exception:
        return _with_parse_error()

    analysis = _MutableBashCommandAnalysis(
        commands=[],
        has_dynamic_words=False,
        has_parse_errors=bool(script.errors),
        has_redirects=False,
        unsupported_node_types={},
    )

    for index in range(len(script.commands)):
        _collect_statement_commands(
            command,
            script.commands[index],
            _CollectContext(
                analysis=analysis,
                operator_before=None if index == 0 else "sequence",
                statement_redirects=[],
            ),
        )

    return _freeze_analysis(analysis)


def is_bash_command_permission_safe(analysis: BashCommandAnalysis) -> bool:
    """Mirrors ``isBashCommandPermissionSafe(analysis)``."""
    return (
        not analysis.has_parse_errors
        and not analysis.has_unsupported_syntax
        and not analysis.has_dynamic_words
    )


@dataclass(slots=True)
class _MutableBashCommandAnalysis:
    commands: list[BashCommandInvocation]
    has_dynamic_words: bool
    has_parse_errors: bool
    has_redirects: bool
    # PORT: TS 用 ``Set<string>`` 累积（插入序 + JSON 去重）；Python ``set`` 无序，
    # 故用 ``dict`` 当有序集合，保持 ``[...set]`` 的插入顺序。
    unsupported_node_types: dict[str, None]


@dataclass(slots=True)
class _CollectContext:
    analysis: _MutableBashCommandAnalysis
    statement_redirects: list[Redirect]
    operator_before: Optional[BashCommandOperator] = None


def _collect_statement_commands(
    source: str,
    statement: Statement,
    context: _CollectContext,
) -> None:
    if statement.background:
        context.analysis.unsupported_node_types["background"] = None
    if len(statement.redirects) > 0:
        context.analysis.has_redirects = True
        if _redirects_have_dynamic_words(list(statement.redirects)):
            context.analysis.has_dynamic_words = True

    _collect_node_commands(
        source,
        statement.command,
        _CollectContext(
            analysis=context.analysis,
            operator_before=context.operator_before,
            statement_redirects=[*context.statement_redirects, *statement.redirects],
        ),
    )


def _collect_node_commands(source: str, node: Node, context: _CollectContext) -> None:
    node_type = node.type
    if node_type == "Command":
        _collect_simple_command(source, node, context)  # type: ignore[arg-type]
        return
    if node_type == "AndOr":
        _collect_and_or_commands(source, node, context)  # type: ignore[arg-type]
        return
    if node_type == "Pipeline":
        _collect_pipeline_commands(source, node, context)  # type: ignore[arg-type]
        return
    if node_type == "Statement":
        _collect_statement_commands(source, node, context)  # type: ignore[arg-type]
        return
    context.analysis.unsupported_node_types[node_type] = None


def _collect_and_or_commands(source: str, node: AndOr, context: _CollectContext) -> None:
    for index in range(len(node.commands)):
        _collect_node_commands(
            source,
            node.commands[index],
            _CollectContext(
                analysis=context.analysis,
                operator_before=(
                    context.operator_before if index == 0 else node.operators[index - 1]
                ),
                statement_redirects=context.statement_redirects,
            ),
        )


def _collect_pipeline_commands(source: str, node: Pipeline, context: _CollectContext) -> None:
    for index in range(len(node.commands)):
        _collect_node_commands(
            source,
            node.commands[index],
            _CollectContext(
                analysis=context.analysis,
                operator_before=(
                    context.operator_before if index == 0 else node.operators[index - 1]
                ),
                statement_redirects=context.statement_redirects,
            ),
        )


def _collect_simple_command(source: str, command: Command, context: _CollectContext) -> None:
    redirects = [*context.statement_redirects, *command.redirects]
    words = [word for word in [command.name, *command.suffix] if word is not None]
    argv = [_word_value(word) for word in words]
    env_assignments = [
        BashCommandEnvAssignment(
            name=assignment.name,
            value=_word_value(assignment.value) if assignment.value is not None else None,
        )
        for assignment in command.prefix
    ]
    has_dynamic_words = (
        any(_word_has_dynamic_parts(word) for word in words)
        or any(
            assignment.value is not None and _word_has_dynamic_parts(assignment.value)
            for assignment in command.prefix
        )
        or _redirects_have_dynamic_words(redirects)
    )

    if has_dynamic_words:
        context.analysis.has_dynamic_words = True
    if len(redirects) > 0:
        context.analysis.has_redirects = True

    name = _word_value(command.name) if command.name is not None else ""
    context.analysis.commands.append(
        BashCommandInvocation(
            argv=argv,
            command_text=source[command.pos : command.end],
            env_assignments=env_assignments,
            has_assignment_prefix=len(command.prefix) > 0,
            has_dynamic_words=has_dynamic_words,
            has_redirects=len(redirects) > 0,
            name=name,
            operator_before=context.operator_before,
            redirects=[
                BashCommandRedirect(
                    file_descriptor=redirect.file_descriptor,
                    operator=redirect.operator,
                    target=_redirect_target_value(redirect),
                )
                for redirect in redirects
            ],
        )
    )


def _redirect_target_value(redirect: Redirect) -> str:
    if redirect.target is not None:
        return _word_value(redirect.target)
    return redirect.content if redirect.content is not None else ""


def _redirects_have_dynamic_words(redirects: list[Redirect]) -> bool:
    return any(
        (redirect.target is not None and _word_has_dynamic_parts(redirect.target))
        or (redirect.body is not None and _word_has_dynamic_parts(redirect.body))
        for redirect in redirects
    )


def _word_has_dynamic_parts(word: Word) -> bool:
    # 命令替换和进程替换会在主命令前执行，权限判断不能把它们当成普通 argv。
    parts = word.parts
    if parts is None:
        return False
    return any(_part_has_dynamic_execution(part) for part in parts)


def _part_has_dynamic_execution(part: WordPart) -> bool:
    part_type = part.type
    if part_type in ("AnsiCQuoted", "Literal", "SingleQuoted"):
        return False
    if part_type in ("DoubleQuoted", "LocaleString"):
        parts = part.parts
        if parts is None:
            return False
        return any(_part_has_dynamic_execution(inner) for inner in parts)
    if part_type in ("CommandExpansion", "ProcessSubstitution"):
        return True
    if part_type in (
        "ArithmeticExpansion",
        "BraceExpansion",
        "ExtendedGlob",
        "ParameterExpansion",
        "SimpleExpansion",
    ):
        return True
    return True


def _word_value(word: Word) -> str:
    return word.value if word.value is not None else word.text


def _empty_analysis() -> BashCommandAnalysis:
    return BashCommandAnalysis(
        commands=[],
        has_dynamic_words=False,
        has_parse_errors=False,
        has_redirects=False,
        has_unsupported_syntax=False,
        unsupported_node_types=[],
    )


def _with_parse_error() -> BashCommandAnalysis:
    """``{ ...emptyAnalysis(), hasParseErrors: true }``."""
    return BashCommandAnalysis(
        commands=[],
        has_dynamic_words=False,
        has_parse_errors=True,
        has_redirects=False,
        has_unsupported_syntax=False,
        unsupported_node_types=[],
    )


def _freeze_analysis(analysis: _MutableBashCommandAnalysis) -> BashCommandAnalysis:
    unsupported_node_types = [
        node_type
        for node_type in analysis.unsupported_node_types
        if node_type not in SUPPORTED_CONTAINER_NODES
    ]

    return BashCommandAnalysis(
        commands=analysis.commands,
        has_dynamic_words=analysis.has_dynamic_words,
        has_parse_errors=analysis.has_parse_errors,
        has_redirects=analysis.has_redirects,
        has_unsupported_syntax=len(unsupported_node_types) > 0,
        unsupported_node_types=unsupported_node_types,
    )
