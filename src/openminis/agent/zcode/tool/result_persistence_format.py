"""Tool result persistence format — the ``<persisted-output>`` envelope.

Ported from: packages/core/src/tool/result-persistence-format.ts
Original package: @zcode/core (tool/result-persistence-format)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

__all__ = [
    "PERSISTED_OUTPUT_OPEN_TAG",
    "PERSISTED_OUTPUT_CLOSE_TAG",
    "PERSISTED_OUTPUT_PREVIEW_CHARS",
    "PersistedOutputEnvelopeInput",
    "GenericPersistedOutputContentInput",
    "format_generic_persisted_output_content",
    "format_persisted_output_envelope",
    "is_persisted_output_content",
]

PERSISTED_OUTPUT_OPEN_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSE_TAG = "</persisted-output>"
# Artifact strategy uses resultBudget as the persistence trigger; once persisted, this independent
# preview budget controls the provider-visible <persisted-output> snippet.
PERSISTED_OUTPUT_PREVIEW_CHARS = 2_000


@dataclass(slots=True)
class PersistedOutputEnvelopeInput:
    """Mirrors ``interface PersistedOutputEnvelopeInput``."""

    content: str
    # PORT: TS ``(bytes: number) => string``
    format_bytes: Callable[[int], str]
    original_bytes: int
    persisted_path: str
    preview_chars: int


@dataclass(slots=True)
class GenericPersistedOutputContentInput:
    """Mirrors the inline ``input`` shape of ``formatGenericPersistedOutputContent``."""

    content: str
    original_bytes: int
    persisted_path: str


def format_generic_persisted_output_content(
    input: GenericPersistedOutputContentInput,
) -> str:
    """Mirrors ``formatGenericPersistedOutputContent(input)``."""
    return format_persisted_output_envelope(
        PersistedOutputEnvelopeInput(
            content=input.content,
            format_bytes=_format_decimal_bytes,
            original_bytes=input.original_bytes,
            persisted_path=input.persisted_path,
            preview_chars=PERSISTED_OUTPUT_PREVIEW_CHARS,
        )
    )


def format_persisted_output_envelope(input: PersistedOutputEnvelopeInput) -> str:
    """Mirrors ``formatPersistedOutputEnvelope(input)``.

    # PORT: TS ``[...].filter((line): line is string => line !== undefined).join("\\n")``
    # → 列表推导过滤 ``None`` 后 ``"\\n".join(...)``。
    """
    preview = _preview_first_chars(input.content, input.preview_chars)
    return "\n".join(
        line
        for line in [
            PERSISTED_OUTPUT_OPEN_TAG,
            f"Output too large ({input.format_bytes(input.original_bytes)}). Full output saved to: {input.persisted_path}",
            "",
            f"Preview (first {input.format_bytes(input.preview_chars)}):",
            preview.preview,
            "..." if preview.has_more else None,
            PERSISTED_OUTPUT_CLOSE_TAG,
        ]
        if line is not None
    )


def is_persisted_output_content(content: str) -> bool:
    """Mirrors ``isPersistedOutputContent(content)``."""
    return content.startswith(PERSISTED_OUTPUT_OPEN_TAG) and (
        PERSISTED_OUTPUT_CLOSE_TAG in content
    )


@dataclass(slots=True)
class _PreviewSlice:
    """Mirrors the inline ``{ preview; hasMore }`` shape."""

    preview: str
    has_more: bool


def _preview_first_chars(content: str, max_chars: int) -> _PreviewSlice:
    """Mirrors the non-exported ``previewFirstChars(content, maxChars)``."""
    if len(content) <= max_chars:
        return _PreviewSlice(preview=content, has_more=False)

    # PORT: TS ``content.slice(0, maxChars).lastIndexOf("\\n")`` —— 空串时得 ``-1``，
    # 与 Python ``str.rfind`` 一致。
    newline_index = content[:max_chars].rfind("\n")
    end_index = newline_index if newline_index > max_chars * 0.5 else max_chars
    return _PreviewSlice(preview=content[:end_index], has_more=True)


def _format_decimal_bytes(bytes: int) -> str:
    """Mirrors the non-exported ``formatDecimalBytes(bytes)``.

    # PORT: JS ``Math.round`` 对 ``.5`` 向 +∞ 取整；Python 内置 ``round`` 是银行家舍入，
    # 故用 ``math.floor(x + 0.5)`` 对齐。
    """
    import math

    if bytes < 1_000:
        return f"{bytes} B"
    if bytes < 1_000_000:
        return f"{math.floor(bytes / 1_000 + 0.5)} KB"
    if bytes < 1_000_000_000:
        return f"{math.floor(bytes / 1_000_000 + 0.5)} MB"
    return f"{math.floor(bytes / 1_000_000_000 + 0.5)} GB"
