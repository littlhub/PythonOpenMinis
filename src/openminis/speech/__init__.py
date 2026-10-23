"""Mirrors ``com.openminis.app.speech`` — local, offline speech recognition.

Ported from: src/android/app/src/main/java/com/openminis/app/speech
Original package: com.openminis.app.speech

PORT-STATUS: partial

The Android tree is not on this machine, so the on-device engine is written
fresh rather than ported: Android delegates speech to the system recognizer
(``SpeechRecognizer``), which has no desktop equivalent. On the Python side
the equivalent capability is a local sherpa-onnx model, so the module keeps the
same *role* (audio in → text out) with a different engine.

Modules:

* :mod:`openminis.speech.stt_core` — model discovery, model download, WAV/audio
  decoding, recognizer construction, transcription primitives.
* :mod:`openminis.speech.pc_config` — engine choice (``local`` / ``api`` /
  ``auto``), API presets, API key lookup, Ekko Studio voice-proxy discovery.
* :mod:`openminis.speech.pc_api` — the model-API engine (stdlib multipart
  upload to any OpenAI-compatible ``/audio/transcriptions`` endpoint).
* :mod:`openminis.speech.engine` — engine dispatch + the process-wide local
  recognizer singleton + the bytes/path entry points the server and tools call.

The local sherpa-onnx engine is the default; the model API is opt-in from
「设置 → 语音识别」(``speech.engine = api``).

``speech.correction`` (Android's post-recognition text fixups) stays an empty
stub: the sherpa zipformer model already emits punctuated-ish bilingual text
and there is no Kotlin source to port the rules from.
"""

from __future__ import annotations

from . import pc_api, pc_config, stt_core
from .engine import (
    SpeechError,
    download_model,
    get_recognizer,
    model_download_state,
    reset_engine,
    status,
    drop_recognizer_if_loaded,
    transcribe_bytes,
    transcribe_path,
)

__all__ = [
    "stt_core",
    "pc_api",
    "pc_config",
    "SpeechError",
    "download_model",
    "drop_recognizer_if_loaded",
    "get_recognizer",
    "model_download_state",
    "reset_engine",
    "status",
    "transcribe_bytes",
    "transcribe_path",
]
