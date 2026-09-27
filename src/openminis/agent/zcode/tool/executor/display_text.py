"""Display text — byte-bounded display strings.

Ported from: packages/core/src/tool/executor/display-text.ts
Original package: @zcode/core (tool/executor)

display 文本的字节限长助手。从 result-display.ts 拆出：观察类工作流 display 的构造
（workflow-observation-display.ts）与既有 payload 构造共用同一道截断语义，而两个文件
互相 import 会成环，助手必须住在双方都能依赖的第三处。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "BoundedDisplayText",
    "bound_display_text",
    "fit_utf8_prefix",
]

DISPLAY_TRUNCATION_SUFFIX = "\n...[truncated]"


@dataclass(slots=True)
class BoundedDisplayText:
    """``{ value: string; truncated: boolean }`` — return shape of :func:`bound_display_text`.

    # PORT: TS 的匿名内联返回对象；Python 需要一个名字，故命名之（字段与语义不变）。
    """

    value: str
    truncated: bool


def bound_display_text(value: str, max_bytes: int) -> BoundedDisplayText:
    """Mirrors ``boundDisplayText(value, maxBytes)``."""
    if _utf8_byte_length(value) <= max_bytes:
        return BoundedDisplayText(value=value, truncated=False)

    suffix_bytes = _utf8_byte_length(DISPLAY_TRUNCATION_SUFFIX)
    prefix = fit_utf8_prefix(value, max_bytes - suffix_bytes)
    return BoundedDisplayText(value=f"{prefix}{DISPLAY_TRUNCATION_SUFFIX}", truncated=True)


def fit_utf8_prefix(value: str, max_bytes: int) -> str:
    """Mirrors ``fitUtf8Prefix(value, maxBytes)``.

    # PORT: TS ``Array.from(value)`` 按 code point 拆分；Python ``str`` 本身即 code point
    序列，故 ``list(value)`` 语义等价。
    """
    code_points = list(value)
    low = 0
    high = len(code_points)

    while low < high:
        mid = math.ceil((low + high) / 2)
        candidate = "".join(code_points[:mid])
        if _utf8_byte_length(candidate) <= max_bytes:
            low = mid
        else:
            high = mid - 1

    return "".join(code_points[:low])


def _utf8_byte_length(value: str) -> int:
    """Mirrors ``Buffer.byteLength(value, "utf8")``."""
    return len(value.encode("utf-8"))
