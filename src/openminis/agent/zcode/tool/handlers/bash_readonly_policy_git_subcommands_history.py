"""Bash readonly policy — git read-only subcommand policy entries (history).

Ported from: packages/core/src/tool/handlers/bash-readonly-policy-git-subcommands-history.ts
Original package: @zcode/core (tool/handlers)

PORT: TS ``[...] as const satisfies readonly (readonly [string, BashReadonlyCommandPolicy])[]``
→ Python ``list[tuple[str, BashReadonlyCommandPolicy]]``（有序，逐条等价）。
"""

from __future__ import annotations

from typing import Sequence

from .bash_readonly_policy_callbacks import (
    git_ls_remote_command_is_dangerous,
    git_reflog_command_is_dangerous,
    git_remote_show_command_is_dangerous,
    git_revision_format_command_is_dangerous,
    git_tag_command_is_dangerous,
)

# PORT: TS 从 barrel ``./bash-readonly-policy-flags.js`` 导入；Python 侧直接引用具体模块。
from .bash_readonly_policy_flags_git import (
    COMMON_GIT_COLOR_FLAGS,
    COMMON_GIT_COUNT_FLAGS,
    COMMON_GIT_DATE_FLAGS,
    COMMON_GIT_LOG_STYLE_FLAGS,
    COMMON_GIT_PATCH_FLAGS,
    COMMON_GIT_REF_FLAGS,
    COMMON_GIT_STAT_FLAGS,
    GIT_SHOW_SAFE_FLAGS,
    GIT_STATUS_SAFE_FLAGS,
)
from .bash_readonly_policy_types import BashReadonlyCommandPolicy


# PORT: 对应 TS ``git remote`` 条目里的**内联箭头函数**
# ``(commandText, args) => args.some((arg) => arg !== "-v" && arg !== "--verbose")``。
# 该逻辑只在本文件数据表内联使用，未单独抽出为具名回调，故按原样就地落成模块级函数。
def _git_remote_command_is_dangerous(
    _command_text: str,
    args: Sequence[str],
) -> bool:
    return any(arg != "-v" and arg != "--verbose" for arg in args)


GIT_READONLY_SUBCOMMAND_POLICY_ENTRIES_HISTORY: list[
    tuple[str, BashReadonlyCommandPolicy]
] = [
    (
        "git ls-remote",
        BashReadonlyCommandPolicy(
            safe_flags={
                "-b": "none",
                "-h": "none",
                "-q": "none",
                "-t": "none",
                "--branches": "none",
                "--exit-code": "none",
                "--get-url": "none",
                "--heads": "none",
                "--quiet": "none",
                "--refs": "none",
                "--sort": "string",
                "--symref": "none",
                "--tags": "none",
            },
            additional_command_is_dangerous_callback=git_ls_remote_command_is_dangerous,
        ),
    ),
    (
        "git merge-base",
        BashReadonlyCommandPolicy(
            safe_flags={
                "--all": "none",
                "--fork-point": "none",
                "--independent": "none",
                "--is-ancestor": "none",
                "--octopus": "none",
            },
        ),
    ),
    (
        "git reflog",
        BashReadonlyCommandPolicy(
            safe_flags={
                **COMMON_GIT_LOG_STYLE_FLAGS,
                **COMMON_GIT_REF_FLAGS,
                **COMMON_GIT_DATE_FLAGS,
                **COMMON_GIT_COUNT_FLAGS,
                **COMMON_GIT_STAT_FLAGS,
                "--grep": "string",
            },
            additional_command_is_dangerous_callback=git_reflog_command_is_dangerous,
        ),
    ),
    (
        "git remote show",
        BashReadonlyCommandPolicy(
            safe_flags={"-n": "none"},
            additional_command_is_dangerous_callback=git_remote_show_command_is_dangerous,
        ),
    ),
    (
        "git remote",
        BashReadonlyCommandPolicy(
            safe_flags={"-v": "none", "--verbose": "none"},
            additional_command_is_dangerous_callback=_git_remote_command_is_dangerous,
        ),
    ),
    (
        "git rev-list",
        BashReadonlyCommandPolicy(
            safe_flags={
                **COMMON_GIT_REF_FLAGS,
                **COMMON_GIT_DATE_FLAGS,
                **COMMON_GIT_COUNT_FLAGS,
                "--abbrev": "number",
                "--abbrev-commit": "none",
                "--ancestry-path": "none",
                "--count": "none",
                "--dense": "none",
                "--first-parent": "none",
                "--format": "string",
                "--full-history": "none",
                "--graph": "none",
                "--grep": "string",
                "--max-age": "number",
                "--max-parents": "number",
                "--merges": "none",
                "--min-age": "number",
                "--min-parents": "number",
                "--no-max-parents": "none",
                "--no-merges": "none",
                "--no-min-parents": "none",
                "--oneline": "none",
                "--pretty": "string",
                "--reverse": "none",
                "--skip": "number",
                "--source": "none",
                "--sparse": "none",
                "--walk-reflogs": "none",
            },
            additional_command_is_dangerous_callback=git_revision_format_command_is_dangerous,
        ),
    ),
    (
        "git rev-parse",
        BashReadonlyCommandPolicy(
            safe_flags={
                "--abbrev-ref": "none",
                "--absolute-git-dir": "none",
                "--git-common-dir": "none",
                "--git-dir": "none",
                "--is-bare-repository": "none",
                "--is-inside-git-dir": "none",
                "--is-inside-work-tree": "none",
                "--is-shallow-repository": "none",
                "--is-shallow-update": "none",
                "--path-prefix": "none",
                "--short": "string",
                "--show-cdup": "none",
                "--show-prefix": "none",
                "--show-superproject-working-tree": "none",
                "--show-toplevel": "none",
                "--symbolic": "none",
                "--symbolic-full-name": "none",
                "--verify": "none",
            },
        ),
    ),
    (
        "git shortlog",
        BashReadonlyCommandPolicy(
            safe_flags={
                **COMMON_GIT_REF_FLAGS,
                **COMMON_GIT_DATE_FLAGS,
                "-c": "none",
                "-e": "none",
                "-n": "none",
                "-s": "none",
                "--author": "string",
                "--committer": "none",
                "--email": "none",
                "--format": "string",
                "--group": "string",
                "--no-merges": "none",
                "--numbered": "none",
                "--summary": "none",
            },
            additional_command_is_dangerous_callback=git_revision_format_command_is_dangerous,
        ),
    ),
    (
        "git show",
        BashReadonlyCommandPolicy(
            safe_flags=GIT_SHOW_SAFE_FLAGS,
            additional_command_is_dangerous_callback=git_revision_format_command_is_dangerous,
        ),
    ),
    (
        "git stash list",
        BashReadonlyCommandPolicy(
            safe_flags={
                **COMMON_GIT_LOG_STYLE_FLAGS,
                **COMMON_GIT_REF_FLAGS,
                **COMMON_GIT_COUNT_FLAGS,
            },
        ),
    ),
    (
        "git stash show",
        BashReadonlyCommandPolicy(
            safe_flags={
                **COMMON_GIT_STAT_FLAGS,
                **COMMON_GIT_COLOR_FLAGS,
                **COMMON_GIT_PATCH_FLAGS,
                "--abbrev": "number",
                "--diff-filter": "string",
                "--word-diff": "none",
                "--word-diff-regex": "string",
            },
        ),
    ),
    ("git status", BashReadonlyCommandPolicy(safe_flags=GIT_STATUS_SAFE_FLAGS)),
    (
        "git tag",
        BashReadonlyCommandPolicy(
            safe_flags={
                "-i": "none",
                "-l": "none",
                "-n": "number",
                "--column": "none",
                "--contains": "string",
                "--format": "string",
                "--ignore-case": "none",
                "--list": "none",
                "--merged": "string",
                "--no-column": "none",
                "--no-contains": "string",
                "--no-merged": "string",
                "--points-at": "string",
                "--sort": "string",
            },
            additional_command_is_dangerous_callback=git_tag_command_is_dangerous,
        ),
    ),
    (
        "git worktree list",
        BashReadonlyCommandPolicy(
            safe_flags={
                "-v": "none",
                "--expire": "string",
                "--porcelain": "none",
                "--verbose": "none",
            },
        ),
    ),
]

__all__ = ["GIT_READONLY_SUBCOMMAND_POLICY_ENTRIES_HISTORY"]
