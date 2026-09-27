"""Bash provider-visible description.

Ported from: packages/core/src/tool/handlers/bash-prompt.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "BashProviderDescriptionInput",
    "create_bash_provider_description",
]


# PORT: TS 形参是匿名对象字面量 ``{ defaultTimeoutMs; embeddedSearchEnabled?; maxTimeoutMs }``。
# Python 用具名 dataclass 承载（同 §1「匿名结构类型 → dataclass」的既有做法）。
@dataclass(slots=True)
class BashProviderDescriptionInput:
    """Mirrors the inline ``input`` shape of ``createBashProviderDescription``."""

    default_timeout_ms: int
    max_timeout_ms: int
    embedded_search_enabled: bool | None = None


def create_bash_provider_description(input: BashProviderDescriptionInput) -> str:
    """Mirrors ``createBashProviderDescription(input)``.

    # PORT: TS ``[...].join("\\n")`` → ``"\\n".join([...])``。
    """
    avoid_commands = (
        "`cat`, `head`, `tail`, `sed`, `awk`, or `echo`"
        if input.embedded_search_enabled
        else "`find`, `grep`, `cat`, `head`, `tail`, `sed`, `awk`, or `echo`"
    )

    return "\n".join(
        [
            "Executes a bash command and returns its output.",
            "",
            "- Working directory persists between calls, but prefer absolute paths — `cd` in a compound command can trigger a permission prompt. Shell state (env vars, functions) does not persist; the shell is initialized from the user's profile.",
            f"- IMPORTANT: Avoid using this tool to run {avoid_commands} commands, unless explicitly instructed or after you have verified that a dedicated tool cannot accomplish your task. Instead, use the appropriate dedicated tool as this will provide a much better experience for the user.",
            f"- `timeout` is in milliseconds: default {input.default_timeout_ms}, max {input.max_timeout_ms}.",
            "- `run_in_background` runs the command detached: it keeps running across turns and re-invokes you when it exits. No `&` needed.",
            "",
            "# Git",
            "- Interactive flags (`-i`, e.g. `git rebase -i`, `git add -i`) are not supported in this environment.",
            "- Use the `gh` CLI for GitHub operations (PRs, issues, API).",
            "- Commit or push only when the user asks. If on the default branch, branch first.",
        ]
    )
