"""Bash timeout policy — default/max timeout resolution.

Ported from: packages/core/src/tool/bash-timeout-policy.ts
Original package: @zcode/core (tool)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Optional

__all__ = [
    "BashTimeoutPolicy",
    "DEFAULT_BASH_TIMEOUT_MS",
    "DEFAULT_BASH_MAX_TIMEOUT_MS",
    "DEFAULT_BASH_TIMEOUT_POLICY",
    "resolve_bash_timeout_policy",
    "resolve_bash_timeout_ms",
]


@dataclass(slots=True)
class BashTimeoutPolicy:
    """Mirrors ``interface BashTimeoutPolicy``."""

    default_timeout_ms: int
    max_timeout_ms: int


DEFAULT_BASH_TIMEOUT_MS = 120_000
DEFAULT_BASH_MAX_TIMEOUT_MS = 600_000

DEFAULT_BASH_TIMEOUT_POLICY = BashTimeoutPolicy(
    default_timeout_ms=DEFAULT_BASH_TIMEOUT_MS,
    max_timeout_ms=DEFAULT_BASH_MAX_TIMEOUT_MS,
)


def resolve_bash_timeout_policy(
    env: Mapping[str, Optional[str]],
) -> BashTimeoutPolicy:
    """Mirrors ``resolveBashTimeoutPolicy(env)``."""
    default_timeout_ms = (
        _parse_positive_timeout(env.get("BASH_DEFAULT_TIMEOUT_MS")) or DEFAULT_BASH_TIMEOUT_MS
    )
    configured_max_timeout_ms = _parse_positive_timeout(env.get("BASH_MAX_TIMEOUT_MS"))

    return BashTimeoutPolicy(
        default_timeout_ms=default_timeout_ms,
        max_timeout_ms=(
            max(configured_max_timeout_ms, default_timeout_ms)
            if configured_max_timeout_ms is not None
            else max(DEFAULT_BASH_MAX_TIMEOUT_MS, default_timeout_ms)
        ),
    )


def resolve_bash_timeout_ms(
    input_timeout_ms: Optional[int],
    policy: BashTimeoutPolicy,
) -> int:
    """Mirrors ``resolveBashTimeoutMs(inputTimeoutMs, policy)``."""
    return min(input_timeout_ms or policy.default_timeout_ms, policy.max_timeout_ms)


# PORT: TS ``Number.parseInt(value, 10)`` 解析前导整数（忽略前导空白，遇非数字即停）；
# Python 的 ``int()`` 要求整串可解析，故用正则复刻 parseInt 的宽松语义。
_LEADING_INT_RE = re.compile(r"^\s*([+-]?\d+)")


def _parse_positive_timeout(value: Optional[str]) -> Optional[int]:
    """Mirrors the non-exported ``parsePositiveTimeout(value)``."""
    if value is None or not value.strip():
        return None

    match = _LEADING_INT_RE.match(value)
    if match is None:
        return None
    parsed = int(match.group(1), 10)
    return None if parsed <= 0 else parsed
