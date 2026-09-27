"""Diff utilities — patch line accounting.

Ported from: packages/core/src/tool/diff.ts
Original package: @zcode/core (tool/diff)
"""

from __future__ import annotations


def count_patch_lines(patch: str) -> int:
    """Count the number of changed lines in a unified *patch* string.

    PORT: mirrors ``countPatchLines``. Counts ``+``/``-`` content lines, ignoring
    the ``@@`` header and context lines.
    """
    if not patch:
        return 0
    count = 0
    for line in patch.splitlines():
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            count += 1
    return count


__all__ = ["count_patch_lines"]
