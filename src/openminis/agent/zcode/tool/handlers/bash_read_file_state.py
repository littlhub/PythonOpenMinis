"""Bash read-file state effects — stale hints + read-state backfill.

Ported from: packages/core/src/tool/handlers/bash-read-file-state.ts
Original package: @zcode/core (tool/handlers)

两条副作用：

1. **stale hint**：命令命中写命令标记（formatter/linter/``--in-place`` 等）时，stat 一遍
   read-state 里的文件；若某文件 mtime 落在命令 ``startedAt`` 之后、且晚于模型上次读到的
   mtime，就给 ``BashOutput.staleReadFileStateHint`` 挂一条「你读过的文件已被本次命令改动，
   编辑前请重新 Read」的提示。
2. **read-state 回填**：命令形如 ``cat`` / ``head`` / ``tail`` / ``sed -n`` / ``grep`` 时，
   把「模型经由 Bash 实际看到的内容」写进 read-state，使 Edit/Write 的 read-before-write
   校验不会把这些文件误判为未读。
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from ...contracts.execution import ExecutionResult
from ...contracts.tracing import TraceContext
from ..path_policy import ToolWorkspacePathOptions, resolve_workspace_path
from ..read_file_state import (
    create_read_file_state_key,
    find_latest_read_file_state,
    normalize_read_file_state_mtime_ms,
)
from ..types import ReadFileStateEntry
from .bash_read_file_sources import collect_bash_read_file_sources, select_read_content
from .bash_semantics import BashProviderErrorStatusInput, is_bash_provider_error_status

__all__ = [
    "BashReadFileStateEffectsInput",
    "apply_bash_read_file_state_effects",
]

MAX_BASH_READ_STATE_FILE_BYTES = 10 * 1024 * 1024
MAX_STALE_HINT_DISPLAY_PATHS = 5

# PORT: TS ``new RegExp([...].join("|"))`` —— 无锚点，``.test()`` 等价于 Python
# ``.search()``（无 ``g`` 标志故无 lastIndex 状态，两侧都无副作用）。
WRITE_COMMAND_MARKERS = re.compile(
    "|".join(
        [
            "--write",
            "--fix",
            "--in-place",
            "--auto-correct",
            r"\brun\s+format\b",
            r"\brun\s+fix\b",
            r"\b(yarn|pnpm)\s+format\b",
            r"\blint:file\b",
            r"\blint:fix\b",
            r"\bblack\b",
            r"\bisort\b",
            r"\bruff\s+format\b",
            r"\bcargo\s+(fmt|fix)\b",
            r"\brustfmt\b",
            r"\bgo\s+fmt\b",
            r"\bterraform\s+fmt\b",
            r"\bdprint\s+fmt\b",
            r"\bswiftformat\b",
            r"\bphpcbf\b",
        ]
    )
)


@dataclass(slots=True)
class BashReadFileStateEffectsInput:
    """Mirrors ``interface BashReadFileStateEffectsInput``."""

    command: str
    context: Any  # ToolExecutionContext
    # PORT: TS 的 ``output: BashOutput`` 由 ``toBashOutput`` 产出的**对象字面量**满足
    # （camelCase 键）。本仓 ``to_bash_output`` 同样返回 camelCase ``dict``，故此处放宽为
    # ``Any``，读写一律走 ``_get_field`` / ``_set_field`` 以同时兼容 dict 与 dataclass。
    output: Any
    result: ExecutionResult


async def apply_bash_read_file_state_effects(input: BashReadFileStateEffectsInput) -> None:
    """Mirrors ``applyBashReadFileStateEffects(input)``."""
    stale_hint = await create_stale_read_file_state_hint(input)
    if stale_hint:
        _set_field(input.output, "stale_read_file_state_hint", stale_hint)
    await backfill_read_file_state_from_bash(input)


async def create_stale_read_file_state_hint(
    input: BashReadFileStateEffectsInput,
) -> Optional[str]:
    """Mirrors the non-exported ``createStaleReadFileStateHint(input)``."""
    if should_skip_bash_read_file_state_effects(input.output):
        return None
    if not WRITE_COMMAND_MARKERS.search(input.command):
        return None
    file_system_port = input.context.file_system_port
    read_file_state = input.context.read_file_state
    if not file_system_port or not read_file_state:
        return None

    changed_paths: set[str] = set()
    trace = create_bash_read_file_state_trace(input.context)
    # PORT: TS ``Promise.all(Array.from(readFileState.values(), async (entry) => {...}))``
    # —— 并发 stat 全部已读条目。条目内的 ``try`` 只包住 ``stat``，其余分支不会抛。
    await asyncio.gather(
        *(
            _collect_stale_path(
                entry,
                file_system_port=file_system_port,
                trace=trace,
                result=input.result,
                abort_signal=input.context.abort_signal,
                changed_paths=changed_paths,
            )
            for entry in list(read_file_state.values())
        )
    )

    paths = list(changed_paths)
    if len(paths) == 0:
        return None
    return format_stale_read_file_state_hint(paths, input.context.working_directory)


async def _collect_stale_path(
    entry: ReadFileStateEntry,
    *,
    file_system_port: Any,
    trace: TraceContext,
    result: ExecutionResult,
    abort_signal: Any,
    changed_paths: set[str],
) -> None:
    """Body of the ``readFileState.values()`` async mapper."""
    entry_mtime_ms = normalize_read_file_state_mtime_ms(entry.mtime_ms)
    if entry_mtime_ms is None:
        return
    try:
        stat = await file_system_port.stat(
            {"path": entry.path, "trace": trace},
            {"signal": abort_signal},
        )
        current_mtime_ms = current_stat_mtime_ms(stat)
        if current_mtime_ms is None:
            return
        # PORT: TS ``input.result.startedAt.getTime()`` —— JS Date 毫秒 epoch。
        started_at_ms = result.started_at.timestamp() * 1000
        if current_mtime_ms > started_at_ms and current_mtime_ms > entry_mtime_ms:
            changed_paths.add(entry.path)
    except Exception:  # noqa: BLE001
        # 无法 stat 的已读文件不阻断 Bash result。
        pass


async def backfill_read_file_state_from_bash(
    input: BashReadFileStateEffectsInput,
) -> None:
    """Mirrors the non-exported ``backfillReadFileStateFromBash(input)``."""
    if should_skip_bash_read_file_state_effects(input.output):
        return
    # Bash cat/head/tail/sed 的 read-state 只能代表模型已经看到的内容；
    # stdout 被截断时回读完整文件会绕过 Edit/Write 的 read-before-write 语义。
    if _get_field(input.output, "stdout_truncated") is True:
        return
    file_system_port = input.context.file_system_port
    read_file_state = input.context.read_file_state
    if not file_system_port or not read_file_state:
        return

    sources = [
        source
        for source in collect_bash_read_file_sources(input.command)
        # PORT: TS ``!source.requiresExitZero || input.result.exitCode === 0``——
        # ``requiresExitZero`` 缺省（undefined）时 ``!undefined`` 为真，故 Python 用
        # ``not bool(...)``。
        if not bool(source.requires_exit_zero) or input.result.exit_code == 0
    ]
    if len(sources) == 0:
        return

    trace = create_bash_read_file_state_trace(input.context)
    # PORT: TS ``sources.map(async ...)`` + ``Promise.all``。注意 ``resolveWorkspacePath``
    # 位于 mapper 的 ``try`` **之外**，故其抛错会沿 ``Promise.all`` 冒泡出本函数——这里
    # 保持一致（``asyncio.gather`` 同样不吞异常，且不取消其余任务）。
    await asyncio.gather(
        *(
            _backfill_source(
                source,
                file_system_port=file_system_port,
                read_file_state=read_file_state,
                trace=trace,
                context=input.context,
            )
            for source in sources
        )
    )


async def _backfill_source(
    source: Any,
    *,
    file_system_port: Any,
    read_file_state: dict[str, ReadFileStateEntry],
    trace: TraceContext,
    context: Any,
) -> None:
    """Body of the ``sources.map`` async mapper in ``backfillReadFileStateFromBash``."""
    resolved_path = resolve_workspace_path(
        ToolWorkspacePathOptions(
            input_path=source.file_path,
            operation="read",
            working_directory=context.working_directory,
            workspace_root=context.workspace_root,
        )
    )
    if find_latest_read_file_state(read_file_state, resolved_path):
        return

    abort_signal = context.abort_signal
    try:
        stat = await file_system_port.stat(
            {"path": resolved_path, "trace": trace},
            {"signal": abort_signal},
        )
        if _get_field(stat, "kind") != "file":
            return
        if (_get_field(stat, "size_bytes") or 0) > MAX_BASH_READ_STATE_FILE_BYTES:
            return
        if _is_aborted(abort_signal):
            return

        read = await file_system_port.read_text_file(
            {
                "path": resolved_path,
                "maxBytes": MAX_BASH_READ_STATE_FILE_BYTES,
                "trace": trace,
            },
            {"signal": abort_signal},
        )
        if _get_field(read, "truncated"):
            return
        selected = select_read_content(_get_field(read, "content") or "", source)
        if selected is None:
            return

        stat_revision = _get_field(stat, "revision")
        read_revision = _get_field(read, "revision")
        entry = ReadFileStateEntry(
            path=resolved_path,
            content=selected.content,
            # PORT: ``selected.offset`` 在 TS 里是 ``offset?: number``（可空），原样透传；
            # ``createReadFileStateKey`` 内部才做 ``offset ?? 1`` 的归一。
            offset=selected.offset,
            limit=selected.limit,
            is_partial_view=False,
            # PORT: TS ``readAt: new Date()`` → 带时区的 UTC now。
            read_at=datetime.now(timezone.utc),
            # PORT: TS ``read.revision?.id ?? stat.revision?.id`` —— 用 ``??`` 而非 ``||``，
            # 空串是合法 revision id。
            revision_id=_first_not_none(
                _get_field(read_revision, "id"),
                _get_field(stat_revision, "id"),
            ),
            mtime_ms=normalize_read_file_state_mtime_ms(
                _first_not_none(
                    _get_field(read_revision, "mtime_ms"),
                    _get_field(stat_revision, "mtime_ms"),
                    _get_field(stat, "mtime_ms"),
                )
            ),
            size_bytes=_first_not_none(
                _get_field(read_revision, "size_bytes"),
                _get_field(stat_revision, "size_bytes"),
                _get_field(stat, "size_bytes"),
            ),
        )
        read_file_state[
            create_read_file_state_key(
                resolved_path,
                1 if selected.offset is None else selected.offset,
                selected.limit,
            )
        ] = entry
    except Exception:  # noqa: BLE001
        # Bash 已经返回，read-state 回填失败不能改变 Bash 结果。
        pass


def should_skip_bash_read_file_state_effects(output: Any) -> bool:
    """Mirrors the non-exported ``shouldSkipBashReadFileStateEffects(output)``."""
    # provider 错误应在 stale/backfill 前返回，避免失败结果污染 read-state。
    return (
        _get_field(output, "background_task_id") is not None
        or _get_field(output, "is_image") is True
        or _get_field(output, "interrupted") is True
        or is_bash_provider_error_status(
            BashProviderErrorStatusInput(
                status=_get_field(output, "status"),
                exit_code=_get_field(output, "exit_code"),
                return_code_interpretation=_get_field(
                    output, "return_code_interpretation"
                ),
            )
        )
    )


def current_stat_mtime_ms(stat: Any) -> Optional[int]:
    """Mirrors the non-exported ``currentStatMtimeMs(stat)``."""
    # PORT: TS ``stat.revision?.mtimeMs ?? stat.mtimeMs``。
    return normalize_read_file_state_mtime_ms(
        _first_not_none(
            _get_field(_get_field(stat, "revision"), "mtime_ms"),
            _get_field(stat, "mtime_ms"),
        )
    )


def format_stale_read_file_state_hint(paths: list[str], working_directory: str) -> str:
    """Mirrors the non-exported ``formatStaleReadFileStateHint(paths, workingDirectory)``."""
    display_paths = [
        _to_display_path(path, working_directory)
        for path in paths[:MAX_STALE_HINT_DISPLAY_PATHS]
    ]
    hidden_count = len(paths) - MAX_STALE_HINT_DISPLAY_PATHS
    hidden_suffix = f" and {hidden_count} more" if hidden_count > 0 else ""
    file_word = "file" if len(paths) == 1 else "files"
    return (
        f"[This command modified {len(paths)} {file_word} you've previously read: "
        f"{', '.join(display_paths)}{hidden_suffix}. Call Read before editing.]"
    )


def create_bash_read_file_state_trace(context: Any) -> TraceContext:
    """Mirrors the non-exported ``createBashReadFileStateTrace(context)``."""
    return TraceContext(
        trace_id=context.trace_id,
        span_id=context.span_id,
        parent_span_id=context.parent_span_id,
        session_id=context.session_id,
        turn_id=context.turn_id,
    )


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _to_display_path(file_path: str, working_directory: str) -> str:
    """Mirrors JS ``relative(workingDirectory, path) || path``."""
    # PORT: JS ``relative(a, a) === ""``（假值）走回落分支；Python 的等价物是 ``"."``，
    # 故两者一并排除。``os.path.relpath`` 在 Windows 跨盘符时抛 ``ValueError``，原实现的
    # ``node:path.relative`` 不会抛，这里保留兜底并回落原路径。
    try:
        rel_path = os.path.relpath(file_path, working_directory)
        if rel_path not in ("", "."):
            return rel_path
    except (ValueError, OSError):
        pass
    return file_path


def _first_not_none(*values: Any) -> Any:
    """Mirrors TS ``a ?? b ?? c``（只跳过 ``None``，保留 ``0`` / ``""``）。"""
    for value in values:
        if value is not None:
            return value
    return None


def _is_aborted(abort_signal: Any) -> bool:
    """Read ``abortSignal.aborted``，容忍 dict 形态的注入实现。"""
    if isinstance(abort_signal, dict):
        return bool(abort_signal.get("aborted"))
    return bool(getattr(abort_signal, "aborted", False))


def _get_field(obj: Any, snake_name: str) -> Any:
    """Read a field that may be snake_case (Python) or camelCase (raw literal).

    PORT: Bash output 由 ``to_bash_output`` 按 TS 对象字面量形态产出（camelCase 键），
    FileSystemPort 的返回体在宿主适配层也可能是 dataclass（``snake_case``）或原始对象
    字面量（``camelCase``）；沿用 ``tool/handlers/read.py`` 的同款防御读法。
    """
    if obj is None:
        return None
    if isinstance(obj, dict):
        if snake_name in obj:
            return obj[snake_name]
        camel = _to_camel(snake_name)
        return obj.get(camel)
    if hasattr(obj, snake_name):
        return getattr(obj, snake_name)
    camel = _to_camel(snake_name)
    return getattr(obj, camel, None)


def _set_field(obj: Any, snake_name: str, value: Any) -> None:
    """Write ``snake_name`` / its camelCase twin onto a dict or an object.

    PORT: TS ``output.staleReadFileStateHint = hint`` 写的是对象字面量的 camelCase 键。
    """
    if isinstance(obj, dict):
        obj[_to_camel(snake_name)] = value
        return
    setattr(obj, snake_name, value)


def _to_camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)
