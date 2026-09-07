"""Strips ANSI escape sequences and handles CR-based line overwrites.

Ported from: src/android/app/src/main/java/com/openminis/app/sandbox/TerminalSanitizer.kt
Original package: com.openminis.app.sandbox

Corresponds to iOS AIChatViewModel.sanitizeTerminalOutput().
"""

from __future__ import annotations

import re

__all__ = ["TerminalSanitizer"]

# Matches ANSI/VT escape sequences:
#   ESC [ ... final_byte (CSI sequences)
#   ESC ] ... ST (OSC sequences terminated by BEL or ESC\)
#   ESC followed by single character (simple escapes)
_ANSI_REGEX = re.compile(
    r"\x1B(?:\[[0-9;]*[A-Za-z\]|\][^\x07]*(?:\x07|\x1B\\)|\[[0-9;]*m|[()][0-2AB]|[A-Za-z])"
)


class TerminalSanitizer:
    """Strips ANSI escape sequences and handles CR-based line overwrites.

    Sanitize terminal output in two passes:
    1. CR folding — simulate carriage return overwriting
    2. Strip remaining ANSI/VT escape sequences
    """

    # PORT: Kotlin `object TerminalSanitizer` (singleton) -> Python class with
    # static-style methods; call as TerminalSanitizer.sanitize(...) mirroring
    # the original's object-method invocation.

    @staticmethod
    def sanitize(raw: str) -> str:
        if raw == "":
            return raw

        # Pass 1: CR folding
        cr_folded = TerminalSanitizer._fold_carriage_returns(raw)

        # Pass 2: Strip ANSI sequences
        stripped = _ANSI_REGEX.sub("", cr_folded)

        # Pass 3: Remove null bytes and non-printable control chars (except \n \t)
        cleaned = "".join(
            ch for ch in stripped if ch == "\n" or ch == "\t" or ord(ch) >= 0x20
        )

        # Pass 4: Remove "null" artifacts from PRoot/pipe issues
        # - Lines that are entirely "null"
        # - Runs of repeated "null" (e.g., "nullnullnull" -> "")
        # - Lines that are just "null" appended to a prefix (e.g., "file:nullnullnull")
        no_null_lines = re.sub(
            r"(?:null){2,}",
            "",
            "\n".join(
                line for line in cleaned.splitlines() if line.strip() != "null"
            ),
        )  # Remove runs of 2+ consecutive "null"

        # Pass 5: Collapse excessive blank lines (3+ consecutive -> 2)
        return re.sub(r"\n{3,}", "\n\n", no_null_lines).strip()

    @staticmethod
    def truncate_if_needed(output: str, max_chars: int = 50_000) -> str:
        if len(output) <= max_chars:
            return output

        keep_each = max_chars // 2
        head = output[:keep_each]
        tail = output[len(output) - keep_each:]
        omitted = len(output) - max_chars
        return f"{head}\n\n[... {omitted} characters omitted ...]\n\n{tail}"

    @staticmethod
    def _fold_carriage_returns(text: str) -> str:
        """Simulate CR (\\r) behavior: when a line contains \\r (without \\n),
        the text after \\r overwrites from the beginning of the line.
        Each \\r resets the cursor to position 0, so only the last segment's
        content (up to its length) is visible.
        """
        lines = text.split("\n")
        result: list[str] = []

        for index, line in enumerate(lines):
            if index > 0:
                result.append("\n")

            if "\r" not in line:
                result.append(line)
                continue

            # Split on CR and simulate overwriting.
            # Each CR resets cursor to column 0. The last non-empty segment wins.
            segments = line.split("\r")
            last_non_empty = next(
                (s for s in reversed(segments) if s != ""), None
            )
            if last_non_empty is not None:
                result.append(last_non_empty)

        return "".join(result)
