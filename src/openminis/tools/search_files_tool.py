"""``search_files`` tool — search inside workspace files, or find files by name.

Ported semantics from ``agent.tools.search_files`` (first-agent toolset):
one tool answers two questions — ``target='content'`` greps file contents with
a regex, ``target='files'`` finds files by a name glob. The first-agent
implementation tried ripgrep → grep → PowerShell → pure Python; this port keeps
the *behavior* (denylist, results shape) with a single pure-Python backend so it
runs identically everywhere, in a worker thread so it never blocks the agent
loop. ``no_ignore=true`` lifts the denylist.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import re
import time
from pathlib import Path

from ..core.logging import get_logger
from ..data.model.agent_tool_definition import AgentToolDefinition, AgentToolParam
from .path_utils import resolve_workspace_path, workspace_root
from .tool_execution_result import ToolExecutionResult

logger = get_logger(__name__)

__all__ = ["SearchFilesTool"]

DEFAULT_MAX_RESULTS = 100
MAX_RESULTS_CAP = 500
MAX_FILE_BYTES = 2 * 1024 * 1024  # files larger than this are skipped
TIME_BUDGET_SECONDS = 25.0
MAX_LINE_CHARS = 300

#: Dependency / VCS / build directories skipped unless ``no_ignore`` is set.
SKIP_DIR_NAMES = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", "dist", "build", ".next", "target", ".tox", ".idea",
    ".ruff_cache", ".workbuddy",
}

#: Files that look like credentials — content search skips them unless
#: ``no_ignore`` is set, mirroring the read tool's credential guard.
_SECRET_GLOBS = (".env", ".env.*", "*.pem", "*.key", "id_rsa", "id_ed25519",
                 "id_ecdsa", "credentials.json", "secrets.*")


def _is_secret_name(name: str) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatchcase(low, g) for g in _SECRET_GLOBS)


def _search_content(root: Path, pattern: str, *, file_glob: str | None,
                    ignore_case: bool, no_ignore: bool, output_mode: str,
                    max_results: int, deadline: float) -> tuple[list[str], int, int, bool]:
    """Return ``(lines, skipped_secrets, scanned_files, timed_out)`` for a content search."""
    flags = re.IGNORECASE if ignore_case else 0
    try:
        rx = re.compile(pattern, flags)
    except re.error as exc:
        return [f"Error: invalid regex: {exc}"], 0, 0, False

    globs = [g for g in (file_glob or "").split(",") if g.strip()]
    out: list[str] = []
    skipped = 0
    file_count = 0
    hits = 0
    done = False

    def walk(roots: list[Path]) -> None:
        nonlocal skipped, file_count, hits, done
        for base in roots:
            if done:
                return
            if base.is_file():
                _scan_file(base)
                continue
            for dirpath, dirnames, filenames in os_walk(base):
                if done:
                    return
                if not no_ignore:
                    dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
                dirnames.sort(key=str.lower)
                for fn in sorted(filenames, key=str.lower):
                    _scan_file(Path(dirpath) / fn)
                if hits >= max_results or time.monotonic() > deadline:
                    done = True
                    return

    def _scan_file(p: Path) -> None:
        nonlocal skipped, file_count, hits, done
        if done or hits >= max_results or time.monotonic() > deadline:
            done = True
            return
        name = p.name
        if not no_ignore and _is_secret_name(name):
            skipped += 1
            return
        try:
            size = p.stat().st_size
        except OSError:
            return
        if size == 0 or size > MAX_FILE_BYTES:
            return
        try:
            with p.open("rb") as fh:
                head = fh.read(8192)
                if b"\x00" in head:
                    return  # binary
                fh.seek(0)
                data = fh.read(MAX_FILE_BYTES)
        except OSError:
            return
        text = data.decode("utf-8", errors="replace")
        rel = _rel(p)
        if not matches_glob(rel, name, globs):
            return
        file_count += 1
        if output_mode == "count":
            count = sum(1 for _ in rx.finditer(text))
            if count:
                out.append(f"{rel}: {count}")
                hits += 1
            return
        if output_mode == "files":
            for line in text.splitlines():
                if rx.search(line):
                    out.append(rel)
                    hits += 1
                    break  # one path per file
            if hits >= max_results:
                done = True
            return
        # output_mode == "content": line-by-line matches
        n = 0
        for line in text.splitlines():
            n += 1
            if not rx.search(line):
                continue
            shown = line.strip()
            if len(shown) > MAX_LINE_CHARS:
                shown = shown[:MAX_LINE_CHARS] + "…"
            out.append(f"{rel}:{n}: {shown}")
            hits += 1
            if hits >= max_results or time.monotonic() > deadline:
                done = True
                return

    start = time.monotonic()
    walk([root])
    timed_out = time.monotonic() > deadline
    return out, skipped, file_count, timed_out


def matches_glob(rel: str, name: str, globs: list[str]) -> bool:
    """True when the file passes the (optional) ``file_glob`` filter."""
    if not globs:
        return True
    rel_l = rel.replace("\\", "/")
    for g in globs:
        g = g.strip()
        if fnmatch.fnmatch(name, g) or fnmatch.fnmatch(rel_l, g):
            return True
    return False


def os_walk(base: Path):
    """Yield ``(dirpath, dirnames, filenames)`` with symlinked dirs skipped."""
    stack = [base]
    while stack:
        current = stack.pop()
        try:
            dirnames: list[str] = []
            filenames: list[str] = []
            for entry in current.iterdir():
                try:
                    if entry.is_dir(follow_symlinks=False):
                        dirnames.append(entry.name)
                    elif entry.is_file(follow_symlinks=False):
                        filenames.append(entry.name)
                except OSError:
                    continue
        except OSError:
            continue
        yield current, dirnames, filenames
        # push child dirs in reverse so ascending lexical order when popped
        for d in sorted(dirnames, key=str.lower, reverse=True):
            stack.append(current / d)


def _search_names(root: Path, pattern: str, *, ignore_case: bool,
                  no_ignore: bool, max_results: int,
                  deadline: float) -> tuple[list[str], int]:
    """Find files whose *name* matches a glob, most-recently-modified first."""
    found: list[Path] = []

    def walk(base: Path) -> None:
        if len(found) >= max_results or time.monotonic() > deadline:
            return
        try:
            entries = list(base.iterdir())
        except OSError:
            return
        for entry in entries:
            if len(found) >= max_results or time.monotonic() > deadline:
                return
            try:
                if entry.is_dir() and not entry.is_symlink():
                    if not no_ignore and entry.name in SKIP_DIR_NAMES:
                        continue
                    walk(entry)
                elif entry.is_file():
                    name = entry.name
                    if ignore_case:
                        name = name.lower()
                    if fnmatch.fnmatchcase(name, pattern.lower() if ignore_case else pattern):
                        found.append(entry)
            except OSError:
                continue

    walk(root if root.is_dir() else root.parent)
    found.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    out = [_rel(p) for p in found[:max_results]]
    timed_out = time.monotonic() > deadline
    return out, timed_out


def _rel(p: Path) -> str:
    try:
        return p.relative_to(workspace_root()).as_posix()
    except ValueError:
        return p.name


class SearchFilesTool:
    """Search inside workspace files, or find files by name."""

    NAME = "search_files"

    @staticmethod
    def definition() -> AgentToolDefinition:
        return AgentToolDefinition(
            name=SearchFilesTool.NAME,
            description=(
                "Search file contents, or find files by name. Prefer this over "
                "running grep/rg/find in shell_execute.\n"
                "Content search (target='content', default): regex search inside "
                "files under the workspace, returning matching lines with their "
                "file path and line number. Narrow with file_glob, choose the "
                "result shape with output_mode.\n"
                "File search (target='files'): find files by a name glob such as "
                "'*.py' or '*report*', recursively, most recently modified first."
            ),
            parameters={
                "tool_title": AgentToolParam(
                    "string",
                    "A concise 5-10 word summary of what this tool call does, shown "
                    "to the user (e.g. 'Find chat handler code'). Use the same "
                    "language as the user.",
                ),
                "pattern": AgentToolParam(
                    "string",
                    "Regex to search for inside files, or — when target='files' — a "
                    "glob matched against the file name, e.g. '*.py' or '*report*'.",
                ),
                "target": AgentToolParam(
                    "string",
                    "'content' searches inside files (default); 'files' finds "
                    "files by name.",
                    enum_values=["content", "files"],
                ),
                "path": AgentToolParam(
                    "string",
                    "File or directory to search in (default: workspace root). "
                    "Relative paths are based on the workspace root.",
                ),
                "file_glob": AgentToolParam(
                    "string",
                    "Comma-separated globs to filter which files are searched, "
                    "e.g. '*.py' or '*.{ts,tsx}' (default: all files).",
                ),
                "output_mode": AgentToolParam(
                    "string",
                    "content = matching lines with line numbers (default); files = "
                    "only file paths that contain a match; count = matches per file.",
                    enum_values=["content", "files", "count"],
                ),
                "ignore_case": AgentToolParam(
                    "boolean", "Case-insensitive match (default false)."
                ),
                "no_ignore": AgentToolParam(
                    "boolean",
                    "When true, search everywhere: dependency/build directories "
                    "(node_modules, dist, .venv, ...) and credential-looking files "
                    "are searched too. Default false.",
                ),
                "max_results": AgentToolParam(
                    "integer",
                    f"Maximum number of results to return (default: {DEFAULT_MAX_RESULTS}, "
                    f"capped at {MAX_RESULTS_CAP}).",
                ),
            },
            required=["tool_title", "pattern"],
            property_ordering=[
                "tool_title", "pattern", "target", "path", "file_glob",
                "output_mode", "ignore_case", "no_ignore", "max_results",
            ],
        )

    @staticmethod
    async def execute(args_json: str, session_id: str) -> ToolExecutionResult:
        try:
            args = json.loads(args_json)
            tool_title = str(args.get("tool_title", SearchFilesTool.NAME))
            pattern = str(args.get("pattern", "")).strip()
            target = str(args.get("target", "content"))
            output_mode = str(args.get("output_mode", "content"))
            ignore_case = bool(args.get("ignore_case", False))
            no_ignore = bool(args.get("no_ignore", False))
            max_results = max(
                1, min(int(args.get("max_results", DEFAULT_MAX_RESULTS)), MAX_RESULTS_CAP)
            )
        except (ValueError, TypeError):
            return ToolExecutionResult(
                "Error: invalid JSON args", False, tool_title=SearchFilesTool.NAME
            )
        if not pattern:
            return ToolExecutionResult(
                "Error: 'pattern' is required", False, tool_title=tool_title
            )
        target_path = await asyncio.to_thread(resolve_workspace_path, args.get("path"))
        if target_path is None:
            return ToolExecutionResult(
                "Error: path escapes the workspace root", False, tool_title=tool_title
            )
        deadline = time.monotonic() + TIME_BUDGET_SECONDS

        if target == "files":
            out, timed_out = await asyncio.to_thread(
                _search_names, target_path, pattern, ignore_case=ignore_case,
                no_ignore=no_ignore, max_results=max_results, deadline=deadline,
            )
            skipped = 0
        else:
            out, skipped, _scanned, timed_out = await asyncio.to_thread(
                _search_content, target_path, pattern,
                file_glob=args.get("file_glob"), ignore_case=ignore_case,
                no_ignore=no_ignore, output_mode=output_mode,
                max_results=max_results, deadline=deadline,
            )
        if out and out[0].startswith("Error:"):
            return ToolExecutionResult(out[0], False, tool_title=tool_title)
        if not out:
            hint = ""
            if skipped and not no_ignore:
                hint = f"（跳过 {skipped} 个疑似密钥文件，可用 no_ignore=true 包含）"
            return ToolExecutionResult(
                f"No matches for {pattern!r} in {target_path.name or '.'}{hint}",
                True, tool_title=tool_title,
            )
        body = "\n".join(out)
        footer = []
        if timed_out:
            footer.append("(达到搜索时限，结果不完整)")
        if len(out) >= max_results:
            footer.append(f"(达到 {max_results} 条上限)")
        if skipped and not no_ignore:
            footer.append(f"(跳过 {skipped} 个疑似密钥文件)")
        return ToolExecutionResult(
            "\n".join([body, *footer]), True, tool_title=tool_title,
        )
