"""Result envelope returned by every agent tool.

Ported from: src/android/app/src/main/java/com/openminis/app/tools/ToolExecutionResult.kt
Original package: com.openminis.app.tools
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ToolExecutionResult"]


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    """Outcome of one tool call.

    PORT: Kotlin declares a custom ``equals``/``hashCode`` purely so that
    ``ByteArray`` members compare by content. A frozen dataclass already gives
    us structural equality, and ``bytes`` compares by value in Python — so the
    hand-written pair is unnecessary here.
    """

    output: str
    success: bool
    image_data: bytes | None = None
    image_mime_type: str | None = None
    tool_title: str = ""

    #: Page URL at time of browser action (for display in preview).
    page_url: str | None = None

    #: Local file path to screenshot JPEG (for thumbnail/detail view).
    image_file_path: str | None = None

    #: iSH-visible linux path the image bytes were persisted to (e.g.
    #: ``/var/minis/browser/<sid>/screenshot_<ts>.jpg``,
    #: ``/var/minis/attachments/generated/...``). Used by request-level image
    #: budgeting to emit a re-fetchable text placeholder instead of the
    #: bytes when the cumulative payload would exceed the per-request cap.
    #: Distinct from ``image_file_path`` which is a host-OS absolute path that
    #: agents cannot directly address.
    image_linux_path: str | None = None

    #: True when the tool did not return within its per-tool-category timeout.
    #: Distinct from generic ``not success`` so the UI can render a TIMEOUT
    #: status (clock icon) instead of a FAILED status (error icon). Mirrors a
    #: failure subclassification iOS handles inline via error-message parsing.
    timed_out: bool = False
