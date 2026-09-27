"""Bash readonly policy — multi-word ``gh`` command policy entries.

Ported from: packages/core/src/tool/handlers/bash-readonly-policy-multiword-gh.ts
Original package: @zcode/core (tool/handlers)

PORT: TS ``[...] as const satisfies readonly (readonly [string, BashReadonlyCommandPolicy])[]``
→ Python ``list[tuple[str, BashReadonlyCommandPolicy]]``（有序，逐条等价）。
"""

from __future__ import annotations

from .bash_readonly_policy_callbacks import gh_command_is_dangerous

# PORT: TS 从 barrel ``./bash-readonly-policy-flags.js`` 导入；Python 侧直接引用具体模块。
from .bash_readonly_policy_flags_process import (
    GH_JSON_REPO_FLAGS,
    GH_LIMIT_REPO_FLAGS,
    GH_REPO_FLAGS,
)
from .bash_readonly_policy_types import BashReadonlyCommandPolicy

READONLY_MULTIWORD_POLICY_ENTRIES_GH: list[tuple[str, BashReadonlyCommandPolicy]] = [
    (
        "gh issue list",
        BashReadonlyCommandPolicy(
            safe_flags={
                **GH_LIMIT_REPO_FLAGS,
                "-s": "string",
                "--app": "string",
                "--assignee": "string",
                "--author": "string",
                "--label": "string",
                "--milestone": "string",
                "--search": "string",
                "--state": "string",
            },
            additional_command_is_dangerous_callback=gh_command_is_dangerous,
        ),
    ),
    (
        "gh issue status",
        BashReadonlyCommandPolicy(
            safe_flags=GH_JSON_REPO_FLAGS,
            additional_command_is_dangerous_callback=gh_command_is_dangerous,
        ),
    ),
    (
        "gh issue view",
        BashReadonlyCommandPolicy(
            safe_flags={**GH_JSON_REPO_FLAGS, "--comments": "none"},
            additional_command_is_dangerous_callback=gh_command_is_dangerous,
        ),
    ),
    (
        "gh label list",
        BashReadonlyCommandPolicy(
            safe_flags={
                **GH_LIMIT_REPO_FLAGS,
                "-S": "string",
                "--order": "string",
                "--search": "string",
                "--sort": "string",
            },
            additional_command_is_dangerous_callback=gh_command_is_dangerous,
        ),
    ),
    (
        "gh pr checks",
        BashReadonlyCommandPolicy(
            safe_flags={
                **GH_JSON_REPO_FLAGS,
                "--fail-fast": "none",
                "--interval": "number",
                "--required": "none",
                "--watch": "none",
            },
            additional_command_is_dangerous_callback=gh_command_is_dangerous,
        ),
    ),
    (
        "gh pr diff",
        BashReadonlyCommandPolicy(
            safe_flags={
                **GH_REPO_FLAGS,
                "--color": "string",
                "--name-only": "none",
                "--patch": "none",
            },
            additional_command_is_dangerous_callback=gh_command_is_dangerous,
        ),
    ),
    (
        "gh pr list",
        BashReadonlyCommandPolicy(
            safe_flags={
                **GH_LIMIT_REPO_FLAGS,
                "-L": "number",
                "-s": "string",
                "--app": "string",
                "--assignee": "string",
                "--author": "string",
                "--base": "string",
                "--draft": "none",
                "--head": "string",
                "--label": "string",
                "--search": "string",
                "--state": "string",
            },
            additional_command_is_dangerous_callback=gh_command_is_dangerous,
        ),
    ),
    (
        "gh pr status",
        BashReadonlyCommandPolicy(
            safe_flags={**GH_JSON_REPO_FLAGS, "-c": "none", "--conflict-status": "none"},
            additional_command_is_dangerous_callback=gh_command_is_dangerous,
        ),
    ),
    (
        "gh pr view",
        BashReadonlyCommandPolicy(
            safe_flags={**GH_JSON_REPO_FLAGS, "--comments": "none"},
            additional_command_is_dangerous_callback=gh_command_is_dangerous,
        ),
    ),
    (
        "gh release list",
        BashReadonlyCommandPolicy(
            safe_flags={
                **GH_LIMIT_REPO_FLAGS,
                "-O": "string",
                "--exclude-drafts": "none",
                "--exclude-pre-releases": "none",
                "--order": "string",
            },
            additional_command_is_dangerous_callback=gh_command_is_dangerous,
        ),
    ),
    (
        "gh release view",
        BashReadonlyCommandPolicy(
            safe_flags=GH_JSON_REPO_FLAGS,
            additional_command_is_dangerous_callback=gh_command_is_dangerous,
        ),
    ),
    (
        "gh repo view",
        BashReadonlyCommandPolicy(
            safe_flags={"--json": "string"},
            additional_command_is_dangerous_callback=gh_command_is_dangerous,
        ),
    ),
    (
        "gh run list",
        BashReadonlyCommandPolicy(
            safe_flags={
                **GH_LIMIT_REPO_FLAGS,
                "-b": "string",
                "-c": "string",
                "-e": "string",
                "-s": "string",
                "-u": "string",
                "-w": "string",
                "--branch": "string",
                "--commit": "string",
                "--created": "string",
                "--event": "string",
                "--status": "string",
                "--user": "string",
                "--workflow": "string",
            },
            additional_command_is_dangerous_callback=gh_command_is_dangerous,
        ),
    ),
    (
        "gh run view",
        BashReadonlyCommandPolicy(
            safe_flags={
                **GH_JSON_REPO_FLAGS,
                "-a": "number",
                "-j": "string",
                "-v": "none",
                "--attempt": "number",
                "--exit-status": "none",
                "--job": "string",
                "--log": "none",
                "--log-failed": "none",
                "--verbose": "none",
            },
            additional_command_is_dangerous_callback=gh_command_is_dangerous,
        ),
    ),
    (
        "gh search code",
        BashReadonlyCommandPolicy(
            safe_flags={
                **GH_LIMIT_REPO_FLAGS,
                "--extension": "string",
                "--filename": "string",
                "--language": "string",
                "--match": "string",
                "--owner": "string",
                "--size": "string",
            },
        ),
    ),
    (
        "gh search commits",
        BashReadonlyCommandPolicy(
            safe_flags={
                **GH_LIMIT_REPO_FLAGS,
                "--author": "string",
                "--author-date": "string",
                "--author-email": "string",
                "--author-name": "string",
                "--committer": "string",
                "--committer-date": "string",
                "--committer-email": "string",
                "--committer-name": "string",
                "--hash": "string",
                "--merge": "none",
                "--order": "string",
                "--owner": "string",
                "--parent": "string",
                "--sort": "string",
                "--tree": "string",
                "--visibility": "string",
            },
        ),
    ),
    (
        "gh search issues",
        BashReadonlyCommandPolicy(
            safe_flags={
                **GH_LIMIT_REPO_FLAGS,
                "-R": "string",
                "--app": "string",
                "--assignee": "string",
                "--author": "string",
                "--closed": "string",
                "--commenter": "string",
                "--comments": "string",
                "--created": "string",
                "--include-prs": "none",
                "--interactions": "string",
                "--involves": "string",
                "--label": "string",
                "--language": "string",
                "--locked": "none",
                "--match": "string",
                "--mentions": "string",
                "--milestone": "string",
                "--no-assignee": "none",
                "--no-label": "none",
                "--no-milestone": "none",
                "--no-project": "none",
                "--order": "string",
                "--owner": "string",
                "--project": "string",
                "--reactions": "string",
                "--sort": "string",
                "--state": "string",
                "--team-mentions": "string",
                "--updated": "string",
                "--visibility": "string",
            },
        ),
    ),
    (
        "gh search prs",
        BashReadonlyCommandPolicy(
            safe_flags={
                **GH_LIMIT_REPO_FLAGS,
                "-B": "string",
                "-H": "string",
                "-R": "string",
                "--app": "string",
                "--assignee": "string",
                "--author": "string",
                "--base": "string",
                "--checks": "string",
                "--closed": "string",
                "--commenter": "string",
                "--comments": "string",
                "--created": "string",
                "--draft": "none",
                "--head": "string",
                "--interactions": "string",
                "--involves": "string",
                "--label": "string",
                "--language": "string",
                "--locked": "none",
                "--match": "string",
                "--mentions": "string",
                "--merged": "none",
                "--merged-at": "string",
                "--milestone": "string",
                "--no-assignee": "none",
                "--no-label": "none",
                "--no-milestone": "none",
                "--no-project": "none",
                "--order": "string",
                "--owner": "string",
                "--project": "string",
                "--reactions": "string",
                "--review": "string",
                "--review-requested": "string",
                "--reviewed-by": "string",
                "--sort": "string",
                "--state": "string",
                "--team-mentions": "string",
                "--updated": "string",
                "--visibility": "string",
            },
        ),
    ),
    (
        "gh search repos",
        BashReadonlyCommandPolicy(
            safe_flags={
                **GH_LIMIT_REPO_FLAGS,
                "--archived": "none",
                "--created": "string",
                "--followers": "string",
                "--forks": "string",
                "--good-first-issues": "string",
                "--help-wanted-issues": "string",
                "--include-forks": "string",
                "--language": "string",
                "--license": "string",
                "--match": "string",
                "--number-topics": "string",
                "--order": "string",
                "--owner": "string",
                "--size": "string",
                "--sort": "string",
                "--stars": "string",
                "--topic": "string",
                "--updated": "string",
                "--visibility": "string",
            },
        ),
    ),
    (
        "gh workflow list",
        BashReadonlyCommandPolicy(
            safe_flags={**GH_LIMIT_REPO_FLAGS, "-a": "none", "--all": "none"},
            additional_command_is_dangerous_callback=gh_command_is_dangerous,
        ),
    ),
    (
        "gh workflow view",
        BashReadonlyCommandPolicy(
            safe_flags={
                **GH_REPO_FLAGS,
                "-r": "string",
                "-y": "none",
                "--ref": "string",
                "--yaml": "none",
            },
            additional_command_is_dangerous_callback=gh_command_is_dangerous,
        ),
    ),
]

__all__ = ["READONLY_MULTIWORD_POLICY_ENTRIES_GH"]
