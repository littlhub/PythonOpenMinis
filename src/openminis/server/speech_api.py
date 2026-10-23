"""Speech-to-text routes — the 🎤 button's backend.

Why this exists: ``ChatView.tsx`` used to record a clip and insert
``🎤 [语音消息](blob:…)`` into the draft. A ``blob:`` URL only resolves inside
the tab that created it, so the backend could never read it — the message
reached the model as a dead link. This router closes that loop: the browser
posts the recorded bytes, the server transcribes them (local sherpa-onnx by
default, or a model API when configured) and returns plain text the composer
inserts instead.

Routes:

* ``GET  /api/speech/status``          — engine, model/API readiness, loaded flag
* ``POST /api/speech/transcribe``      — multipart ``file`` → ``{"text": ...}``
* ``POST /api/speech/model/download``  — fetch the local model in the background
* ``GET  /api/speech/model/download``  — download progress (poll while it runs)

Transcription is CPU-bound and synchronous (≈0.9 s per second of audio locally),
so it runs in a worker thread via ``asyncio.to_thread`` — blocking the event loop
here would freeze every other WebSocket frame in flight. The API engine already
blocks on HTTP, which is why it shares the thread pool path.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..core.logging import get_logger
from ..speech import (
    SpeechError,
    download_model,
    model_download_state,
    status,
    transcribe_bytes,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/speech", tags=["speech"])

#: 25 MB ≈ 25 minutes of 16 kHz mono WAV, or a long compressed clip. The 🎤
#: button records seconds, so this is a safety net, not a working limit.
MAX_BYTES = 25 * 1024 * 1024

#: What browsers record plus what users may upload by hand. Anything else is
#: rejected before it reaches ffmpeg (local engine) or the endpoint (API engine).
ALLOWED_SUFFIXES = {
    ".wav", ".wave", ".webm", ".ogg", ".oga", ".mp3", ".m4a", ".mp4",
    ".flac", ".aac", ".opus", ".amr",
}


@router.get("/status")
async def speech_status() -> dict[str, object]:
    """Engine + model availability — drives whether the UI shows 🎤 as usable.

    ``ok: false`` with a populated ``error`` means "no model installed" (local)
    or "API not configured" (api); the error text already says what to do.
    """
    return await asyncio.to_thread(status)


@router.post("/transcribe")
async def transcribe(file: UploadFile = File(...)) -> dict[str, object]:
    """Transcribe an uploaded audio clip and return the text.

    The frontend posts a 16 kHz mono WAV built by ``wavRecorder.ts``; other
    containers are accepted too (ffmpeg for the local engine).
    """
    suffix = Path(file.filename or "clip.webm").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"不支持的音频格式：{suffix or '未知'}")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="音频为空")
    if len(data) > MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"音频过大（{len(data) // 1024 // 1024}MB），上限 {MAX_BYTES // 1024 // 1024}MB",
        )

    try:
        result = await asyncio.to_thread(transcribe_bytes, data, suffix=suffix)
    except SpeechError as error:
        # 503: the capability is missing (no model / bad API config), not the request.
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:  # noqa: BLE001 - never leak a bare 500 to the UI
        logger.exception("speech transcribe failed")
        raise HTTPException(status_code=500, detail=f"识别失败：{error}") from error

    text = str(result.get("text") or "").strip()
    logger.info(
        "speech transcribe (%s): %s bytes → %d chars in %sms",
        result.get("engine", "local"),
        len(data),
        len(text),
        result.get("durationMs"),
    )
    return {
        "ok": True,
        "text": text,
        "engine": result.get("engine"),
        "model": result.get("model"),
        "durationMs": result.get("durationMs"),
        "audioMs": result.get("audioMs"),
    }


@router.post("/model/download")
async def start_download(sources: str = "") -> dict[str, object]:
    """Start (or report) the background download of the local model.

    ``sources`` is an optional comma list, e.g. ``github,hf-mirror``; default
    tries GitHub, then a GitHub mirror, then the Hugging Face mirror.
    """
    chosen = [s.strip() for s in sources.split(",") if s.strip()] or None
    return await asyncio.to_thread(download_model, chosen)


@router.get("/model/download")
async def download_progress() -> dict[str, object]:
    return await asyncio.to_thread(model_download_state)
