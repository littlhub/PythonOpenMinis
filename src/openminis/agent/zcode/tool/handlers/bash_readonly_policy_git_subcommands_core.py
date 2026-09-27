"""Bash readonly policy — git read-only subcommand policy entries (core).

Ported from: packages/core/src/tool/handlers/bash-readonly-policy-git-subcommands-core.ts
Original package: @zcode/core (tool/handlers)

PORT: TS ``[...] as const satisfies readonly (readonly [string, BashReadonlyCommandPolicy])[]``
→ Python ``list[tuple[str, BashReadonlyCommandPolicy]]``（有序，逐条等价）。
"""

from __future__ import annotations

from .bash_readonly_policy_callbacks import (
    git_branch_command_is_dangerous,
    git_revision_format_command_is_dangerous,
)

# PORT: TS 从 barrel ``./bash-readonly-policy-flags.js`` 导入；Python 侧直接引用具体模块。
from .bash_readonly_policy_flags_git import (
    COMMON_GIT_COLOR_FLAGS,
    GIT_DIFF_SAFE_FLAGS,
    GIT_GREP_SAFE_FLAGS,
    GIT_LOG_SAFE_FLAGS,
)
from .bash_readonly_policy_types import BashReadonlyCommandPolicy

GIT_READONLY_SUBCOMMAND_POLICY_ENTRIES_CORE: list[tuple[str, BashReadonlyCommandPolicy]] = [
    (
        "git blame",
        BashReadonlyCommandPolicy(
            safe_flags={
                **COMMON_GIT_COLOR_FLAGS,
                "-C": "none",
                "-L": "string",
                "-M": "none",
                "-e": "none",
                "-f": "none",
                "-l": "none",
                "-n": "none",
                "-p": "none",
                "-s": "none",
                "-t": "none",
                "-w": "none",
                "--abbrev": "number",
                "--date": "string",
                "--ignore-rev": "string",
                "--ignore-revs-file": "string",
                "--incremental": "none",
                "--line-porcelain": "none",
                "--porcelain": "none",
                "--root": "none",
                "--score-debug": "none",
                "--show-email": "none",
                "--show-name": "none",
                "--show-number": "none",
                "--show-stats": "none",
            },
        ),
    ),
    (
        "git branch",
        BashReadonlyCommandPolicy(
            safe_flags={
                "-a": "none",
                "-i": "none",
                "-l": "none",
                "-r": "none",
                "-v": "none",
                "-vv": "none",
                "--abbrev": "number",
                "--all": "none",
                "--color": "none",
                "--column": "none",
                "--contains": "string",
                "--ignore-case": "none",
                "--list": "none",
                "--merged": "none",
                "--no-abbrev": "none",
                "--no-color": "none",
                "--no-column": "none",
                "--no-contains": "string",
                "--no-merged": "none",
                "--points-at": "string",
                "--remotes": "none",
                "--show-current": "none",
                "--sort": "string",
                "--verbose": "none",
            },
            additional_command_is_dangerous_callback=git_branch_command_is_dangerous,
        ),
    ),
    (
        "git cat-file",
        BashReadonlyCommandPolicy(
            safe_flags={
                "-e": "none",
                "-p": "none",
                "-s": "none",
                "-t": "none",
                "--allow-undetermined-type": "none",
                "--batch-check": "none",
            },
        ),
    ),
    (
        "git config --get",
        BashReadonlyCommandPolicy(
            safe_flags={
                "-z": "none",
                "--bool": "none",
                "--bool-or-int": "none",
                "--default": "string",
                "--expiry-date": "none",
                "--global": "none",
                "--int": "none",
                "--local": "none",
                "--name-only": "none",
                "--null": "none",
                "--path": "none",
                "--show-origin": "none",
                "--show-scope": "none",
                "--system": "none",
                "--type": "string",
                "--worktree": "none",
            },
        ),
    ),
    (
        "git describe",
        BashReadonlyCommandPolicy(
            safe_flags={
                "--abbrev": "number",
                "--always": "none",
                "--broken": "none",
                "--candidates": "number",
                "--contains": "none",
                "--dirty": "none",
                "--exclude": "string",
                "--exact-match": "none",
                "--first-match": "none",
                "--long": "none",
                "--match": "string",
                "--tags": "none",
            },
        ),
    ),
    ("git diff", BashReadonlyCommandPolicy(safe_flags=GIT_DIFF_SAFE_FLAGS)),
    (
        "git for-each-ref",
        BashReadonlyCommandPolicy(
            safe_flags={
                "--contains": "string",
                "--count": "number",
                "--format": "string",
                "--merged": "string",
                "--no-contains": "string",
                "--no-merged": "string",
                "--points-at": "string",
                "--sort": "string",
            },
            additional_command_is_dangerous_callback=git_revision_format_command_is_dangerous,
        ),
    ),
    ("git grep", BashReadonlyCommandPolicy(safe_flags=GIT_GREP_SAFE_FLAGS)),
    (
        "git log",
        BashReadonlyCommandPolicy(
            # `git log -8` / `git log -1` 是 Git 历史查询的只读 max-count 缩写；
            # 需要由 policy 明确声明，避免 Plan mode 错拦纯历史查询。
            allow_compact_numeric_count_flag=True,
            safe_flags=GIT_LOG_SAFE_FLAGS,
            additional_command_is_dangerous_callback=git_revision_format_command_is_dangerous,
        ),
    ),
    (
        "git ls-files",
        BashReadonlyCommandPolicy(
            safe_flags={
                "-c": "none",
                "-d": "none",
                "-f": "none",
                "-i": "none",
                "-k": "none",
                "-m": "none",
                "-o": "none",
                "-s": "none",
                "-t": "none",
                "-u": "none",
                "-v": "none",
                "-x": "string",
                "-X": "string",
                "-z": "none",
                "--abbrev": "number",
                "--cached": "none",
                "--debug": "none",
                "--deleted": "none",
                "--directory": "none",
                "--eol": "none",
                "--error-unmatch": "none",
                "--exclude": "string",
                "--exclude-from": "string",
                "--exclude-per-directory": "string",
                "--exclude-standard": "none",
                "--full-name": "none",
                "--ignored": "none",
                "--killed": "none",
                "--modified": "none",
                "--no-empty-directory": "none",
                "--others": "none",
                "--recurse-submodules": "none",
                "--stage": "none",
                "--unmerged": "none",
            },
        ),
    ),
]

__all__ = ["GIT_READONLY_SUBCOMMAND_POLICY_ENTRIES_CORE"]
