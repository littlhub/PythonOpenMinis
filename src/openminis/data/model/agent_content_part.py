"""Structured content parts for agent-loop messages.

Ported from: src/android/app/src/main/java/com/openminis/app/data/model/AgentContentPart.kt
Original package: com.openminis.app.data.model

Represents tool_use / tool_result / image blocks in conversation history, which
each provider serializes into its native wire format.

# PORT: Kotlin ``sealed class`` -> ABC + dataclass subclasses. ``JSONObject``
# becomes ``dict``; ``ByteArray`` becomes ``bytes``. ``ToolResult`` and
# ``ImageData`` override ``equals``/``hashCode`` in Kotlin to compare ``ByteArray``
# by content — Python ``bytes`` already compares by value, so the inherited
# dataclass ``__eq__`` is correct and no override is needed.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from typing import Optional

__all__ = ["AgentContentPart", "Text", "ToolUse", "ToolResult", "ImageData"]


class AgentContentPart(ABC):
    """Kotlin ``sealed class AgentContentPart``."""


@dataclass
class Text(AgentContentPart):
    """Kotlin ``data class Text(val text: String)``."""

    text: str


@dataclass
class ToolUse(AgentContentPart):
    """Kotlin ``data class ToolUse`` — an assistant tool invocation.

    ``input`` is the parsed tool arguments dict (Kotlin ``JSONObject``).
    """

    id: str
    name: str
    input: dict = field(default_factory=dict)
    # [T-android-gemini3-thoughtsig / #179] Opaque Gemini 3.x thought signature
    # captured at tool-call time; replayed on the historical functionCall part
    # (required by gemini-3.x or the request 400s). None for non-Gemini
    # providers and for pre-fix / migrated history.
    thought_signature: Optional[str] = None


@dataclass
class ToolResult(AgentContentPart):
    """Kotlin ``data class ToolResult`` — the outcome of a tool invocation."""

    id: str
    name: str
    content: str
    is_error: bool = False
    image_data: Optional[bytes] = None
    image_mime_type: Optional[str] = None
    # iSH-visible linux path for image_data, if it was persisted. Used by
    # request-level image budgeting to emit a re-fetchable text placeholder
    # when the bytes are elided.
    image_linux_path: Optional[str] = None


@dataclass
class ImageData(AgentContentPart):
    """Kotlin ``data class ImageData``."""

    data: bytes
    mime_type: str
    # iSH-visible linux path the bytes were originally persisted to, if any.
    linux_path: Optional[str] = None
    # [T-android-vision-group / GH#182] Provider substitutes this for the pixels
    # on the T264 no-native-vision path.
    no_vision_placeholder: Optional[str] = None
