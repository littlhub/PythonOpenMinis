"""read_image tool.

Ported from: src/android/app/src/main/java/com/openminis/app/tools/ReadImageTool.kt
Original package: com.openminis.app.tools

PORT: Android ``Bitmap`` becomes PIL ``Image``; the 2000px max-edge downscale
and JPEG/85 re-encode are kept identical so a downstream LLM that expects a
small image still gets one.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from ..core.context import app_context
from ..core.logging import get_logger
from ..data.model.agent_tool_definition import AgentToolDefinition, AgentToolParam
from .file_read_tool import _resolve_session_host_path
from .tool_execution_result import ToolExecutionResult

logger = get_logger(__name__)

__all__ = ["ReadImageTool"]

_MAX_EDGE = 2000
_SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}


class ReadImageTool:
    """Kotlin: ``object ReadImageTool`` — stateless namespace."""

    NAME = "read_image"

    @staticmethod
    def definition() -> AgentToolDefinition:
        return AgentToolDefinition(
            name=ReadImageTool.NAME,
            description=(
                "Read an image file from the filesystem and return it for "
                "visual analysis. Supports PNG, JPEG, GIF, WEBP, and other "
                "common image formats. Use this to inspect generated charts, "
                "downloaded images, screenshots, or any visual output. If the "
                "model natively supports vision the image is returned directly; "
                "if not, it is routed through a configured Vision Group that "
                "returns a text description — pass a `prompt` to focus the "
                "description on what you actually need. Metadata (dimensions, "
                "file size) is always included."
            ),
            parameters={
                "tool_title": AgentToolParam(
                    "string",
                    "A concise 5-10 word summary of what this tool call does, "
                    "shown to the user (e.g. 'View generated bar chart', "
                    "'Inspect downloaded screenshot'). Use the same language as "
                    "the user.",
                ),
                "path": AgentToolParam(
                    "string",
                    "Path (e.g. /tmp/chart.png) or minis:// URL (e.g. "
                    "minis://attachments/chart.png)",
                ),
                "prompt": AgentToolParam(
                    "string",
                    "Optional. A custom instruction describing what you want to "
                    "understand from the image (e.g. 'transcribe the table "
                    "text', 'describe the people and their expressions', 'what "
                    "error message is in this screenshot'). Most useful when you "
                    "lack native vision and the image is described by a Vision "
                    "Group. If omitted, a generic 'describe this image in "
                    "detail' instruction is used.",
                ),
            },
            required=["tool_title", "path"],
            property_ordering=["tool_title", "path", "prompt"],
        )

    @staticmethod
    def execute(args_json: str, session_id: str) -> ToolExecutionResult:
        """Kotlin: ``execute(argsJson, sessionId)``."""
        try:
            args = json.loads(args_json)
            raw_path = str(args.get("path", ""))
            prompt = str(args.get("prompt", ""))
            tool_title = str(args.get("tool_title", ReadImageTool.NAME))

            if not raw_path.strip():
                return ToolExecutionResult(
                    "Error: 'path' is required", False, tool_title=tool_title
                )

            # T-android: minis:// URLs collapse into /var/minis/...
            path = _strip_minis_scheme(raw_path)
            host = _resolve_session_host_path(session_id, path) or _resolve_global(path)
            if host is None:
                return ToolExecutionResult(
                    f"Error: Cannot resolve path: {path}", False, tool_title=tool_title
                )
            if not host.exists():
                return ToolExecutionResult(
                    f"Error: File not found: {path}", False, tool_title=tool_title
                )
            if host.suffix.lower() not in _SUPPORTED_EXTS:
                return ToolExecutionResult(
                    f"Error: Unsupported image format: {host.suffix}",
                    False,
                    tool_title=tool_title,
                )

            from PIL import Image  # Pillow — optional dep, lazy import

            with Image.open(host) as im:
                im.load()
                original_w, original_h = im.size
                # 2000px max edge downscale → same as the Kotlin path.
                if max(original_w, original_h) > _MAX_EDGE:
                    scale = _MAX_EDGE / max(original_w, original_h)
                    new_size = (int(original_w * scale), int(original_h * scale))
                    im = im.resize(new_size, Image.LANCZOS)
                # Normalise mode to RGB so JPEG re-encode is lossless.
                if im.mode not in ("RGB", "L"):
                    im = im.convert("RGB")
                import io

                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=85)
                image_bytes = buf.getvalue()

            file_size = host.stat().st_size
            metadata = (
                f"[{path} | {original_w}x{original_h} | {file_size} bytes]"
            )
            # When the model passes a `prompt` AND we know the main chat has
            # no native vision, the calling layer will route the bytes through
            # the Vision Group resolver. We surface the prompt in the result
            # so the agent loop can pick it up.
            if prompt.strip():
                return ToolExecutionResult(
                    output=f"{metadata}\n[prompt: {prompt.strip()}]",
                    success=True,
                    image_data=image_bytes,
                    image_mime_type="image/jpeg",
                    image_file_path=str(host),
                    tool_title=tool_title,
                )
            return ToolExecutionResult(
                output=metadata,
                success=True,
                image_data=image_bytes,
                image_mime_type="image/jpeg",
                image_file_path=str(host),
                tool_title=tool_title,
            )
        except ImportError:
            return ToolExecutionResult(
                "Error: read_image requires Pillow (`pip install Pillow`).",
                False,
                tool_title=str(ReadImageTool.NAME),
            )
        except Exception as e:
            logger.exception("read_image failed")
            return ToolExecutionResult(f"Error reading image: {e}", False)


def _strip_minis_scheme(raw_path: str) -> str:
    """T-android: `minis://attachments/foo.png` → `/var/minis/attachments/foo.png`."""
    if raw_path.startswith("minis://"):
        from urllib.parse import unquote

        tail = raw_path[len("minis://") :]
        return "/var/minis/" + unquote(tail)
    return raw_path


def _resolve_global(path: str) -> Path | None:
    """Fallback resolver for callers that don't pass a sessionId — kept narrow
    so the per-session resolver remains the primary path.
    """
    workspace = app_context().external_files_dir
    candidate = path.strip()
    if not candidate:
        return None
    for prefix in ("/var/minis/workspace", "/workspace", "/var/minis"):
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix) :]
            break
    candidate = candidate.lstrip("/") or ""
    if not candidate:
        return None
    resolved = (workspace / candidate).resolve()
    root = workspace.resolve()
    if resolved != root and root not in resolved.parents:
        logger.warning("read_image rejected path outside workspace: %s", path)
        return None
    return resolved
