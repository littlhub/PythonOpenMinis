"""Structured patch — unified-diff hunk accounting.

Ported from: packages/core/src/tool/diff.ts (``createStructuredPatch``)
Original package: @zcode/core (tool/diff)

# PORT: TS uses the npm ``diff`` library's ``structuredPatch`` (LCS + 3 lines of
# context). Python's ``difflib.unified_diff`` produces the same unified-diff shape;
# we parse its ``@@`` hunk headers back into ``DiffHunk`` dicts
# (``oldStart`` / ``oldLines`` / ``newStart`` / ``newLines`` / ``lines``), where
# ``lines`` keeps the leading ``+`` / ``-`` / `` `` markers exactly like the JS
# library output.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

__all__ = ["create_structured_patch", "CONTEXT_LINES"]

CONTEXT_LINES = 3
_DIFF_TIMEOUT_MS = 5_000  # kept for parity; Python difflib has no timeout knob

_HUNK_HEADER_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@"
)


def create_structured_patch(
    *,
    file_path: str,
    old_content: str,
    new_content: str,
) -> list[dict[str, Any]]:
    """Mirrors ``createStructuredPatch({ filePath, oldContent, newContent })``."""
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    if old_content == "":
        old_lines = []
    if new_content == "":
        new_lines = []

    unified = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=file_path,
        tofile=file_path,
        n=CONTEXT_LINES,
    )
    text = "".join(unified)
    return _parse_unified_hunks(text)


def _parse_unified_hunks(text: str) -> list[dict[str, Any]]:
    """Convert a unified-diff string into ``DiffHunk`` dicts."""
    hunks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in text.split("\n"):
        header = _HUNK_HEADER_RE.match(line)
        if header:
            old_start = int(header.group(1))
            old_lines_count = int(header.group(2)) if header.group(2) else 1
            new_start = int(header.group(3))
            new_lines_count = int(header.group(4)) if header.group(4) else 1
            current = {
                "oldStart": old_start,
                "oldLines": old_lines_count,
                "newStart": new_start,
                "newLines": new_lines_count,
                "lines": [],
            }
            hunks.append(current)
            continue
        # Skip the ``---`` / ``+++`` file header lines and anything before the
        # first hunk.
        if current is None:
            continue
        if line.startswith(("---", "+++")):
            continue
        current["lines"].append(line)
    return hunks
