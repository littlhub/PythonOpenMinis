"""transcribe_audio tool — local, offline speech-to-text for audio files.

Ported from: none — the Android tree has no equivalent (it delegates speech to
the platform ``SpeechRecognizer``, which only transcribes live microphone
input). This tool is the Python port's own addition, sitting on top of
``openminis.speech`` (see PORTING.md §speech).

Why it exists: when the user attaches an audio clip, the composer only sends
the model a *path* (path-only attachment mode, same as images). Without this
tool the model can see that ``meeting.m4a`` exists but has no way to read what
was said — it would have to guess or ask the user to type it out.

Nothing here touches the event loop: transcription is CPU-bound (≈0.9 s per
second of audio), so the work runs in a worker thread via ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import json

from ..core.logging import get_logger
from ..data.model.agent_tool_definition import AgentToolDefinition, AgentToolParam
from .file_read_tool import _resolve_session_host_path
from .read_image_tool import _resolve_global, _strip_minis_scheme
from .tool_execution_result import ToolExecutionResult

logger = get_logger(__name__)

__all__ = ["TranscribeAudioTool"]

#: Containers the engine can take. WAV is parsed in-process; the rest go through
#: ffmpeg, which may not be installed — the engine says so in its error text.
_SUPPORTED_EXTS = {
    ".wav", ".wave", ".webm", ".ogg", ".oga", ".mp3", ".m4a", ".mp4",
    ".flac", ".aac", ".opus", ".amr",
}


class TranscribeAudioTool:
    """Kotlin: no counterpart — Python-port addition (see module docstring)."""

    NAME = "transcribe_audio"

    @staticmethod
    def definition() -> AgentToolDefinition:
        return AgentToolDefinition(
            name=TranscribeAudioTool.NAME,
            description=(
                "Transcribe a speech audio file to text with the local, offline "
                "speech recognition engine (sherpa-onnx bilingual Chinese/English). "
                "No network and no API key are involved — the audio never leaves "
                "this machine. Use it to find out what was said in a voice memo, "
                "meeting recording, or an audio file the user attached. Supports "
                "WAV natively; MP3/M4A/WEBM/OGG/FLAC need ffmpeg installed. "
                "Returns the transcript plus the audio duration. For very long "
                "recordings the transcript may be truncated with a note — this "
                "engine is tuned for short clips, not hours of audio."
            ),
            parameters={
                "tool_title": AgentToolParam(
                    "string",
                    "A concise 5-10 word summary of what this tool call does, "
                    "shown to the user (e.g. 'Transcribe voice memo', "
                    "'Read meeting recording'). Use the same language as the user.",
                ),
                "path": AgentToolParam(
                    "string",
                    "Path to the audio file (e.g. /tmp/memo.m4a) or minis:// URL "
                    "(e.g. minis://attachments/memo.m4a)",
                ),
            },
            required=["tool_title", "path"],
            property_ordering=["tool_title", "path"],
        )

    @staticmethod
    async def execute(args_json: str, session_id: str) -> ToolExecutionResult:
        try:
            args = json.loads(args_json)
            raw_path = str(args.get("path", ""))
            tool_title = str(args.get("tool_title", TranscribeAudioTool.NAME))

            if not raw_path.strip():
                return ToolExecutionResult(
                    "Error: 'path' is required", False, tool_title=tool_title
                )

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
                    f"Error: Unsupported audio format: {host.suffix} "
                    f"(supported: {', '.join(sorted(_SUPPORTED_EXTS))})",
                    False,
                    tool_title=tool_title,
                )

            # Imported here, not at module top: ``openminis.speech`` pulls numpy
            # and sherpa-onnx, which are an optional extra. A plain install must
            # still be able to import the tool registry.
            from ..speech import SpeechError, transcribe_path

            try:
                result = await asyncio.to_thread(transcribe_path, host)
            except SpeechError as error:
                # Missing model / missing ffmpeg / disabled in settings — all
                # actionable messages already written for a human reader.
                return ToolExecutionResult(
                    f"Error: {error}", False, tool_title=tool_title
                )

            text = str(result.get("text") or "").strip()
            if not text:
                return ToolExecutionResult(
                    f"[{path} | no speech detected]", True, tool_title=tool_title
                )

            seconds = round(int(result.get("audioMs") or 0) / 1000, 1)
            return ToolExecutionResult(
                f"[{path} | {seconds}s]\n{text}", True, tool_title=tool_title
            )
        except Exception as error:  # noqa: BLE001 - never crash the agent loop
            logger.exception("transcribe_audio failed")
            return ToolExecutionResult(
                f"Error transcribing audio: {error}", False
            )
