"""GitHub API rate-limit hint for bash output.

Ported from: packages/core/src/tool/handlers/bash-gh-rate-limit.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

import re
import time

__all__ = [
    "GH_RATE_LIMIT_HINT_COOLDOWN_MS",
    "GH_COMMAND_RE",
    "GH_RATE_LIMIT_RE",
    "GH_RATE_LIMIT_HINT",
    "get_gh_rate_limit_hint",
]

GH_RATE_LIMIT_HINT_COOLDOWN_MS = 60_000

# PORT: TS ``/(?:^|[;&|]|\b(?:then|do)\b)\s*gh\s+(?!auth\b|help\b|version\b|alias\b|completion\b|config\b)/
# 单行 flag-less。Python ``re`` 的 ``^`` 默认只匹配串首（同 JS 无 m flag），``\b`` 语义一致。
# 负向先行断言 ``(?!...)`` 等价。
GH_COMMAND_RE = re.compile(
    r"(?:^|[;&|]|\b(?:then|do)\b)\s*gh\s+(?!auth\b|help\b|version\b|alias\b|completion\b|config\b)"
)

# PORT: TS 尾部 ``/i`` → ``re.IGNORECASE``。
GH_RATE_LIMIT_RE = re.compile(
    r"API rate limit (?:already )?exceeded|exceeded a secondary rate limit|\bRATE_LIMITED\b",
    re.IGNORECASE,
)

GH_RATE_LIMIT_HINT = (
    "<system-reminder>GitHub API rate limit exceeded (5,000/hr shared across all tools and agents). "
    "Run `gh api rate_limit --jq .resources` and sleep until reset before further gh calls. "
    "If polling in a loop, use ScheduleWakeup instead of retrying.</system-reminder>"
)

# PORT: TS 模块级可变变量 ``let nextGhRateLimitHintAt = 0``。Python 同名模块级变量。
next_gh_rate_limit_hint_at = 0


def get_gh_rate_limit_hint(command: str, output: str) -> str | None:
    """Mirrors ``getGhRateLimitHint(command, output)``."""
    global next_gh_rate_limit_hint_at

    if not GH_COMMAND_RE.search(command) or not GH_RATE_LIMIT_RE.search(output):
        return None
    now = int(time.time() * 1000)
    if now < next_gh_rate_limit_hint_at:
        return None
    next_gh_rate_limit_hint_at = now + GH_RATE_LIMIT_HINT_COOLDOWN_MS
    return GH_RATE_LIMIT_HINT
