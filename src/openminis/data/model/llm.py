"""LLM message + content-part model types.

Ported from: src/android/app/src/main/java/com/openminis/app/data/model/LLMMessage.kt
Original package: com.openminis.app.data.model

# PORT: forward-reference stub. Defines the symbols the agent/tools port
# imports (``LLMMessage``, ``LLMMessage.Role``, ``AgentContentPart``,
# ``AgentContentPart.Text/ToolUse/ToolResult``, ``ImagePart``). The full
# data.model port owns serialization; here we keep the shape + field names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "LLMMessage",
    "AgentContentPart",
    "ImagePart",
]


class _Role(Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass(slots=True)
class ImagePart:
    """A binary image attachment (vision input)."""

    data: bytes
    mimeType: str


@dataclass(slots=True)
class _TextPart:
    text: str


@dataclass(slots=True)
class _ToolUsePart:
    toolName: str
    toolCallId: str
    input: dict[str, Any]


@dataclass(slots=True)
class _ToolResultPart:
    toolCallId: str | None = None
    content: str | None = None
    isError: bool = False


class AgentContentPart:
    """Sealed-style union of message content parts (Kotlin sealed class).

    PORT: modelled as a namespace with subclasses, matching the original's
    ``AgentContentPart.Text`` / ``.ToolUse`` / ``.ToolResult`` member access.
    """

    Text = _TextPart
    ToolUse = _ToolUsePart
    ToolResult = _ToolResultPart


@dataclass(slots=True)
class LLMMessage:
    """A single conversation message.

    ``role`` is the :class:`_Role` enum (``USER``/``ASSISTANT``/``SYSTEM``).
    ``content`` is plain text; ``contentParts`` carries structured parts
    (text / tool-use / tool-result) used by the loop detection code.
    """

    class Role(Enum):
        USER = "user"
        ASSISTANT = "assistant"
        SYSTEM = "system"

    role: Role
    content: str = ""
    contentParts: list[Any] = field(default_factory=list)
    imageParts: list[ImagePart] = field(default_factory=list)
