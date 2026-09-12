"""file_read tool.

Ported from: src/android/app/src/main/java/com/openminis/app/tools/FileReadTool.kt
Original package: com.openminis.app.tools
"""

from __future__ import annotations

import json
from pathlib import Path

from ..core.context import app_context
from ..core.logging import get_logger
from ..data.model.agent_tool_definition import AgentToolDefinition, AgentToolParam
from .tool_execution_result import ToolExecutionResult

logger = get_logger(__name__)

__all__ = ["FileReadTool", "MAX_LENGTH_HARD_CAP"]

#: T-FILEREAD-CAP: hard upper bound on returned content length.
#:
#: Pre-cap, the agent could ask for ``max_length=1_000_000`` and we would
#: happily inline a 400 KB base64 image into a tool_result — which then renders
#: as a single user-message bubble and locks up Compose's StaticLayout /
#: LineBreaker for tens of seconds (see HangDetector report for session
#: e84882d7-2087-47f8-9300-ff2c897fe0b4: 820 KB partsJson, 43 s hang in
#: nComputeLineBreaks). Cap at 80 KB regardless of requested value; the
#: truncation tail below tells the agent the full file size so it can paginate
#: with offset/lines if needed. iOS mirrors this cap in
#: AIChatViewModel.executeFileRead.
MAX_LENGTH_HARD_CAP = 80_000

_DEFAULT_MAX_LENGTH = 15_000
_BINARY_SNIFF_BYTES = 8192


class FileReadTool:
    """Kotlin: ``object FileReadTool`` — a stateless namespace, so Python keeps
    it as a class with only static/class methods (no instantiation).
    """

    NAME = "file_read"

    @staticmethod
    def definition() -> AgentToolDefinition:
        return AgentToolDefinition(
            name=FileReadTool.NAME,
            description=(
                "Read a file from the Linux filesystem. Faster than shell_execute "
                "for reading files — no shell overhead. Returns file content with "
                "metadata. Rejects binary files."
            ),
            parameters={
                "tool_title": AgentToolParam(
                    "string",
                    "A concise 5-10 word summary of what this tool call does, shown to "
                    "the user (e.g. 'Read Python script contents', 'Check system "
                    "configuration file'). Use the same language as the user.",
                ),
                "path": AgentToolParam(
                    "string", "Absolute Linux path to read (e.g. /var/minis/workspace/data.csv)"
                ),
                "offset": AgentToolParam(
                    "integer",
                    "1-based line number to start reading from (default: 1). Ignored "
                    "when direction is 'tail'. If a previous read was truncated, its "
                    "header ends with next_offset=N — pass that as offset to continue "
                    "from where it stopped.",
                ),
                "lines": AgentToolParam(
                    "integer", "Maximum number of lines to return (default: all lines up to max_length)"
                ),
                "max_length": AgentToolParam(
                    "integer", "Maximum character length of returned content (default: 15000)"
                ),
                "direction": AgentToolParam(
                    "string", "Read direction: 'head' (from start, default) or 'tail' (from end of file)"
                ),
            },
            required=["tool_title", "path"],
            property_ordering=["tool_title", "path", "offset", "lines", "direction", "max_length"],
        )

    @staticmethod
    def execute(args_json: str, session_id: str) -> ToolExecutionResult:
        """Kotlin: ``execute(argsJson, sessionId, context)``.

        PORT: the Android ``Context`` is dropped — the Python port resolves
        paths through the process-wide :func:`app_context` instead.
        """
        try:
            args = json.loads(args_json)
            path = str(args.get("path", ""))
            tool_title = str(args.get("tool_title", FileReadTool.NAME))
            offset = max(int(args.get("offset", 1)), 1)
            max_length = min(int(args.get("max_length", _DEFAULT_MAX_LENGTH)), MAX_LENGTH_HARD_CAP)
            direction = str(args.get("direction", "head"))

            if not path.strip():
                return ToolExecutionResult("Error: 'path' is required", False, tool_title=tool_title)

            # T123: per-session resolver — see FileWriteTool for rationale.
            host = _resolve_session_host_path(session_id, path)
            if host is None:
                return ToolExecutionResult(
                    f"Error: Cannot resolve path: {path}", False, tool_title=tool_title
                )

            if not host.exists():
                return ToolExecutionResult(
                    f"Error: File not found: {path}", False, tool_title=tool_title
                )
            if host.is_dir():
                return ToolExecutionResult(
                    f"Error: Path is a directory: {path}", False, tool_title=tool_title
                )

            size = host.stat().st_size

            # Binary detection: check first 8192 bytes for null bytes
            is_binary = _looks_binary(host, size)
            if is_binary:
                return ToolExecutionResult(
                    f"[{path} | {size} bytes | binary file — cannot display contents]",
                    True,
                    tool_title=tool_title,
                )

            all_lines = host.read_text(encoding="utf-8", errors="replace").splitlines()
            total_lines = len(all_lines)

            requested_lines = args.get("lines")
            requested_lines = int(requested_lines) if requested_lines is not None else None

            if direction == "tail":
                count = requested_lines if requested_lines is not None else total_lines
                start = max(total_lines - count, 0)
                selected = all_lines[start:total_lines]
            else:
                start = min(max(offset - 1, 0), total_lines)
                end = (
                    min(start + requested_lines, total_lines)
                    if requested_lines is not None
                    else total_lines
                )
                selected = all_lines[start:end]

            show_start = (total_lines - len(selected)) + 1 if direction == "tail" else offset
            show_end = show_start + len(selected) - 1

            content = "\n".join(selected)

            # [T-fileread-truncation-header] The header used to report the line
            # range chosen BEFORE truncation, and said nothing about having
            # truncated at all — only the body gained a trailing
            # "... (truncated)". So a cut-off read still announced
            # "showing 1-1324 of 1324", which the agent took as the whole file
            # and never paged on.
            #
            # Recompute the range that actually survived and hand back the
            # offset to resume from. Confined to the truncating branch; a read
            # that fits is byte-identical to before.
            effective_start = show_start
            effective_end = show_end
            next_offset: int | None = None
            was_truncated = False

            if len(content) > max_length:
                was_truncated = True
                if direction == "tail":
                    # tail asks for the END of the file; take() returned the
                    # start of the tail window instead — the opposite.
                    content = content[-max_length:]
                    # Drop a leading partial line so the first line is whole.
                    first_newline = content.find("\n")
                    if 0 <= first_newline < len(content) - 1:
                        content = content[first_newline + 1 :]
                    effective_start = effective_end - content.count("\n")
                    # No next_offset for tail: paging forward from the end of
                    # the file is meaningless.
                else:
                    content = content[:max_length]
                    # Back off to the last complete line, so the next page does
                    # not re-read or split a line.
                    last_newline = content.rfind("\n")
                    if last_newline > 0:
                        content = content[:last_newline]
                    effective_end = show_start + content.count("\n")
                    if effective_end < total_lines:
                        next_offset = effective_end + 1

            header = (
                f"[{path} | {size} bytes | {total_lines} lines | "
                f"showing {effective_start}-{effective_end} of {total_lines}"
            )
            if was_truncated:
                header += f" | truncated at {max_length} chars"
                # Named to match the tool's own `offset` parameter so the model
                # can copy it straight into the next call.
                header += (
                    f", next_offset={next_offset}"
                    if next_offset is not None
                    else ", retry with a smaller lines value"
                )
            header += "]"

            return ToolExecutionResult(f"{header}\n{content}", True, tool_title=tool_title)
        except Exception as e:
            logger.exception("file_read failed")
            return ToolExecutionResult(f"Error reading file: {e}", False)


def _looks_binary(path: Path, size: int) -> bool:
    """Kotlin: read the first 8192 bytes and look for a null byte."""
    with path.open("rb") as fh:
        buf = fh.read(min(_BINARY_SNIFF_BYTES, max(size, 1)))
    return b"\x00" in buf


def _resolve_session_host_path(session_id: str, path: str) -> Path | None:
    """T123: per-session resolver.

    PORT: the original calls ``PRootKernel.resolveSessionHostPath(sessionId,
    path, context)``, which maps a sandbox-visible Linux path onto a
    session-scoped host directory inside the app's private storage. Until
    ``openminis.sandbox`` is ported we map the same way against
    :class:`AppContext`: anything under ``/var/minis/workspace`` (or a bare
    relative path) resolves inside ``AppContext.external_files_dir``.
    """
    workspace = app_context().external_files_dir
    session_root = workspace / session_id if session_id else workspace

    candidate = path.strip()
    if not candidate:
        return None
    for prefix in ("/var/minis/workspace", "/workspace", "/var/minis"):
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix) :]
            break

    # 绝对路径：只要落在工作区内就原样接受。上传的附件用的是绝对路径
    # （``<workspace>/uploads/...``），不认这一段的话 read_image 就拿不到图。
    # 工作区外的绝对路径不走这条捷径，仍按下面的相对规则处理。
    raw_path = Path(candidate)
    if raw_path.is_absolute():
        ws_resolved = workspace.resolve()
        resolved_abs = raw_path.resolve()
        if resolved_abs == ws_resolved or ws_resolved in resolved_abs.parents:
            return resolved_abs

    candidate = candidate.lstrip("/") or ""

    # Reject escape attempts before touching the filesystem.
    resolved = (session_root / candidate).resolve() if candidate else session_root.resolve()
    root = session_root.resolve()
    if resolved != root and root not in resolved.parents:
        # 技能库是只读白名单根：让模型能读 SKILL.md / 技能脚本。
        from .path_utils import readonly_roots

        for extra in readonly_roots():
            er = extra.resolve()
            if resolved == er or er in resolved.parents:
                return resolved
        logger.warning("file_read rejected path outside session root: %s", path)
        return None
    return resolved
