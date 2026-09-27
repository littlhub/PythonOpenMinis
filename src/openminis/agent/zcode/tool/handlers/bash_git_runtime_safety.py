"""Bash git runtime safety — decide whether a git read is safe in the current cwd.

Ported from: packages/core/src/tool/handlers/bash-git-runtime-safety.ts
Original package: @zcode/core (tool/handlers)

# PORT: TS 直接使用 ``node:fs`` 同步 API（``lstatSync`` / ``readFileSync`` /
# ``realpathSync`` …）。本函数签名不接收 FileSystemPort，故按等价语义落到 Python
# ``os`` / ``os.path`` 同步调用；这是 ZCORE_PORTING.md §5「node:fs/promises → FileSystemPort」
# 之外的同步只读路径，行为与 Node 一致。
"""

from __future__ import annotations

import os
import re
import stat as stat_module
import unicodedata
from dataclasses import dataclass
from typing import Literal, Optional, Protocol, Sequence

__all__ = [
    "BashReadonlyRuntimeContext",
    "GitCommandPart",
    "analysis_contains_git_command",
    "analysis_contains_git_and_directory_change",
    "is_git_runtime_context_unsafe",
]


@dataclass(slots=True)
class BashReadonlyRuntimeContext:
    """Mirrors ``interface BashReadonlyRuntimeContext``."""

    working_directory: Optional[str] = None
    workspace_root: Optional[str] = None


class GitCommandPart(Protocol):
    """Mirrors the inline ``{ argv: readonly string[]; name: string }`` shape.

    # PORT: TS 是匿名结构类型；Python 需要一个具名 ``Protocol``。
    """

    argv: Sequence[str]
    name: str


def analysis_contains_git_command(commands: Sequence[GitCommandPart]) -> bool:
    """Mirrors ``analysisContainsGitCommand(commands)``."""
    for command_part in commands:
        name = _normalized_simple_command_name(command_part.argv) or command_part.name
        if name == "git":
            return True
    return False


def analysis_contains_git_and_directory_change(commands: Sequence[GitCommandPart]) -> bool:
    """Mirrors ``analysisContainsGitAndDirectoryChange(commands)``."""
    has_git = False
    has_directory_change = False
    for command_part in commands:
        name = _normalized_simple_command_name(command_part.argv) or command_part.name
        has_git = has_git or name == "git"
        has_directory_change = has_directory_change or name in ("cd", "pushd", "popd")

    # 因为 git 可能在目标目录加载 hooks/config；普通 `cd && grep/find` 仍可只读放行。
    return has_git and has_directory_change


# PORT: JS ``/^-p+$/`` —— 匹配 ``-p``、``-pp`` 等。
_COMMAND_P_FLAGS_RE = re.compile(r"^-p+$")


def _normalized_simple_command_name(argv: Sequence[str]) -> Optional[str]:
    """Mirrors the non-exported ``normalizedSimpleCommandName(argv)``."""
    words = list(argv)
    while True:
        if not words:
            return None
        if words[0] == "command":
            index = 1
            while index < len(words) and _COMMAND_P_FLAGS_RE.match(words[index] or "") is not None:
                index += 1
            if index < len(words) and words[index] == "--":
                index += 1
            if index >= len(words) or (words[index] or "").startswith("-"):
                return words[0]
            words = words[index:]
            continue
        if words[0] == "builtin":
            index = 2 if (len(words) > 1 and words[1] == "--") else 1
            if index >= len(words):
                return words[0]
            words = words[index:]
            continue
        if words[0] == "noglob":
            if len(words) <= 1:
                return words[0]
            words = words[1:]
            continue
        return words[0]


GitDirectoryState = Literal["none", "trusted", "unsafe"]

MAX_GITDIR_FILE_BYTES = 32 * 1024


def is_git_runtime_context_unsafe(
    context: Optional[BashReadonlyRuntimeContext],
) -> bool:
    """Mirrors ``isGitRuntimeContextUnsafe(context)``."""
    cwd = context.working_directory if context is not None else None
    if not cwd:
        return False

    cwd_canonical = _canonical_path(cwd)
    if not cwd_canonical:
        return True

    cwd_dot_git = _classify_dot_git_directory(cwd, cwd_canonical)
    if cwd_dot_git == "trusted":
        return False
    if cwd_dot_git == "unsafe":
        return True

    current = cwd
    while True:
        if _has_bare_git_indicators(current):
            return True

        parent = os.path.dirname(current)
        if parent == current:
            return False

        parent_state = _classify_dot_git_directory(parent, cwd_canonical)
        if parent_state == "trusted":
            return False
        if parent_state == "unsafe":
            return True
        current = parent


def _classify_dot_git_directory(directory: str, cwd_canonical: str) -> GitDirectoryState:
    dot_git_path = os.path.join(directory, ".git")
    try:
        dot_git_stat = os.lstat(dot_git_path)
        if stat_module.S_ISLNK(dot_git_stat.st_mode):
            target = _read_file_system_link_target(dot_git_path, directory)
            return _classify_git_dir_target(target, cwd_canonical) if target else "unsafe"
        if stat_module.S_ISREG(dot_git_stat.st_mode):
            if dot_git_stat.st_size > MAX_GITDIR_FILE_BYTES:
                return "unsafe"
            content = _read_text_file(dot_git_path)
            if "\0" in content:
                return "unsafe"
            if not content.startswith("gitdir: "):
                return "none"
            target_text = re.sub(r"[\r\n]+$", "", content[len("gitdir: ") :])
            return _classify_git_dir_target(
                target_text
                if os.path.isabs(target_text)
                else os.path.join(directory, target_text),
                cwd_canonical,
            )
        if stat_module.S_ISDIR(dot_git_stat.st_mode):
            return "trusted" if _has_trusted_git_directory(dot_git_path) else "none"
    except OSError:
        return "none"
    return "none"


def _classify_git_dir_target(target: str, cwd_canonical: str) -> GitDirectoryState:
    target_canonical = _canonical_path(target)
    if not target_canonical:
        return "unsafe"
    if _path_is_same_or_inside(target_canonical, cwd_canonical):
        return "unsafe"
    if not _path_has_git_segment(target_canonical):
        return "unsafe"
    return "trusted" if _has_valid_git_head(target_canonical) else "none"


def _read_file_system_link_target(path: str, directory: str) -> Optional[str]:
    try:
        target = os.readlink(path)
        return target if os.path.isabs(target) else os.path.join(directory, target)
    except OSError:
        return None


def _has_trusted_git_directory(directory: str) -> bool:
    if not _has_valid_git_head(directory):
        return False
    try:
        for child in ("objects", "refs"):
            child_path = os.path.join(directory, child)
            if not stat_module.S_ISDIR(os.stat(child_path).st_mode):
                return False
            if not os.access(child_path, os.X_OK):
                # PORT: JS ``accessSync(path, X_OK)`` 失败会抛错，被外层 catch 收口为 false。
                raise OSError(f"EACCES: permission denied, access '{child_path}'")
        try:
            os.stat(os.path.join(directory, "commondir"))
            return False
        except OSError:
            return True
    except OSError:
        return False


# PORT: JS ``/^ref:[ \t]*refs\//`` 与 ``/^[0-9a-f]{40}([0-9a-f]{24})?[ \t\n\r]*$/``（无 flags）。
_VALID_HEAD_REF_RE = re.compile(r"^ref:[ \t]*refs/")
_VALID_HEAD_SHA_RE = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?[ \t\n\r]*$")


def _has_valid_git_head(directory: str) -> bool:
    try:
        head_path = os.path.join(directory, "HEAD")
        head_stat = os.lstat(head_path)
        if not stat_module.S_ISREG(head_stat.st_mode) or head_stat.st_size > 4096:
            return False
        head = _read_text_file(head_path)[:255]
        return (
            _VALID_HEAD_REF_RE.search(head) is not None
            or _VALID_HEAD_SHA_RE.search(head) is not None
        )
    except OSError:
        return False


def _has_bare_git_indicators(directory: str) -> bool:
    head_path = os.path.join(directory, "HEAD")
    try:
        head = os.lstat(head_path)
        if stat_module.S_ISREG(head.st_mode) or stat_module.S_ISLNK(head.st_mode):
            return True
    except OSError:
        # 缺少 HEAD 不是裸仓库信号，继续检查 objects/refs。
        pass
    return any(_path_exists(os.path.join(directory, child)) for child in ("objects", "refs"))


def _canonical_path(path: str) -> Optional[str]:
    try:
        # PORT: JS ``realpathSync.native(resolve(path))`` 在路径不存在时抛错；
        # Python ``strict=True`` 复刻同一行为（默认真实路径解析不会报错）。
        return _normalize_canonical_path(os.path.realpath(os.path.abspath(path), strict=True))
    except OSError:
        return None


def _normalize_canonical_path(path: str) -> str:
    return unicodedata.normalize("NFC", path.replace("\\", "/")).lower()


def _path_is_same_or_inside(path: str, base: str) -> bool:
    normalized_base = base if base.endswith("/") else f"{base}/"
    return path == base or path.startswith(normalized_base)


def _path_has_git_segment(path: str) -> bool:
    return any(segment.lower() == ".git" for segment in re.split(r"[\\/]+", path))


def _path_exists(path: str) -> bool:
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _read_text_file(path: str) -> str:
    # PORT: Node ``readFileSync(path, "utf8")`` 对非法字节做替换解码；matching
    # ``errors="replace"`` 保持同一语义。
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()
