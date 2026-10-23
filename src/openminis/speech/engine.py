"""Speech-to-text entry points: local sherpa-onnx model or a model API.

Ported from: G:\\Ekko\\语音识别\\stt\\stt_cli.py (serve/file paths) and
``stt_engine.py`` (LocalEngine / ApiEngine).

Two engines, picked by ``speech.engine`` (see :mod:`.pc_config`):

* ``local`` (default) — the process-wide sherpa-onnx recognizer below;
* ``api`` — any OpenAI-compatible ``/audio/transcriptions`` endpoint.

One recognizer for the whole process: building it loads ~200 MB of ONNX
weights and takes 2–3 s. The chat 🎤 button transcribes short clips back to
back, so the cost must be paid once, not per request. Construction is guarded
by a lock because the FastAPI server runs route handlers on a thread pool.

Nothing here touches the event loop — callers wrap :func:`transcribe_bytes`
in ``asyncio.to_thread`` (see ``server/speech_api.py``).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Optional, Sequence

from ..core.logging import get_logger
from . import pc_api, pc_config, stt_core

logger = get_logger(__name__)

__all__ = [
    "SpeechError",
    "get_recognizer",
    "reset_engine",
    "drop_recognizer_if_loaded",
    "status",
    "transcribe_bytes",
    "transcribe_path",
    "download_model",
    "model_download_state",
]

#: Suffixes we can hand to the recognizer without a temp file.
_WAV_SUFFIXES = {".wav", ".wave"}


class SpeechError(RuntimeError):
    """Any failure surfaced to the caller as a user-readable message."""


_lock = threading.RLock()
_recognizer: Optional[Any] = None
#: The options the cached recognizer was built with. A request that asks for
#: different options (e.g. a different decoding method) rebuilds it once.
_signature: Optional[tuple] = None


def _speech_config() -> dict[str, Any]:
    return pc_config.speech_prefs()


def get_recognizer(
    *,
    num_threads: Optional[int] = None,
    decoding_method: Optional[str] = None,
) -> Any:
    """Return the shared local recognizer, building it on first use.

    Raises :class:`SpeechError` with the model-search explanation attached when
    no model is installed, so the UI can tell the user exactly what to do.
    """
    global _recognizer, _signature

    config = _speech_config()
    threads = int(num_threads if num_threads is not None else config["numThreads"] or 2)
    decoding = str(decoding_method or config["decodingMethod"] or "greedy_search")
    signature = (threads, decoding)

    with _lock:
        if _recognizer is not None and _signature == signature:
            return _recognizer

        started = time.perf_counter()
        try:
            _recognizer = stt_core.create_recognizer(
                num_threads=threads,
                decoding_method=decoding,
            )
        except stt_core.ModelNotFoundError as error:
            raise SpeechError(str(error)) from error
        except stt_core.SttError as error:
            raise SpeechError(str(error)) from error
        except Exception as error:  # noqa: BLE001 - sherpa-onnx raises bare RuntimeError
            raise SpeechError(f"语音模型加载失败：{error}") from error

        _signature = signature
        logger.info(
            "speech recognizer ready in %.2fs (threads=%d, decoding=%s)",
            time.perf_counter() - started,
            threads,
            decoding,
        )
        return _recognizer


def reset_engine() -> None:
    """Drop the cached recognizer (used by tests and after a model swap)."""
    global _recognizer, _signature
    with _lock:
        _recognizer = None
        _signature = None


def drop_recognizer_if_loaded() -> bool:
    """Make the engine follow a settings change.

    Switching *to* the API engine should free the ~200 MB of ONNX weights a
    previous local transcription left loaded; switching decoding options or
    back to local should rebuild on the next call. Either way the cached
    recognizer is stale, so this is what ``/api/config`` PUT calls for
    ``speech.*``. Returns whether anything was actually loaded.
    """
    dropped = _recognizer is not None
    if dropped:
        reset_engine()
    return dropped


def status() -> dict[str, Any]:
    """Engine availability for the UI. Never raises."""
    config = _speech_config()
    kind = pc_config.effective_engine(config)
    base: dict[str, Any] = {
        "ok": False,
        "enabled": bool(config.get("enabled", True)),
        "engine": kind,
        "engineSetting": str(config.get("engine") or "local"),
        "engines": {
            "local": {"label": "本地模型（离线）"},
            "api": {"label": "模型 API（云端 / 可边走边识别）"},
            "auto": {"label": "自动（配好 API 就用 API）"},
        },
        # 「音频」槽位绑定的模型 —— 设置里选「音频槽位模型」时用的就是它
        "slotModel": _slot_model_view(),
        "presets": {name: preset["label"] for name, preset in pc_config.API_PRESETS.items()},
        # 换预设时前端要把地址/模型填成预设默认值，所以两份都发过去。
        "presetDefaults": pc_config.preset_defaults(),
        "model": None,
        "root": None,
        "files": {},
        "warnings": [],
        "error": None,
        "loaded": False,
        "sampleRate": stt_core.SAMPLE_RATE,
        "languages": ["zh", "en"],
        "sherpaOnnxVersion": "unknown",
        "download": stt_core.download_state(),
        "managedDir": str(stt_core.model_download_target()),
    }

    if kind == "api":
        settings = pc_config.api_settings(config)
        has_auth = bool(settings["api_key"]) or bool(settings["extra_headers"])
        configured = bool(settings["base_url"] and settings["model"] and has_auth)
        base.update(
            {
                "ok": configured,
                "model": settings["model"] or None,
                "root": settings["base_url"] or None,
                "api": {
                    "preset": settings["preset"],
                    "baseUrl": settings["base_url"],
                    "model": settings["model"],
                    "auth": "studio" if settings["extra_headers"] else ("key" if settings["api_key"] else ""),
                    "language": settings["language"],
                    "timeoutSeconds": settings["timeout"],
                },
                "localReady": _local_ready_quietly(),
            }
        )
        if not configured and settings["preset"] == "model":
            base["error"] = (
                "语音用的是「音频」槽位的模型，但槽位还没绑定或厂商实例缺地址："
                "去 设置 → 模型服务 → 用途分槽 → 音频 选一个厂商实例 + 模型；"
                "或把 设置 → 语音识别 切回「本地模型」。"
            )
        elif not configured:
            base["error"] = (
                "模型 API 没配好：需要接口地址 + 模型名 + Key。"
                "在设置里选一个预设（OpenAI / Groq / 硅基流动…）并填 Key，"
                "或改为「Ekko Studio 语音代理」；也可以把引擎切回「本地模型」。"
            )
        return base

    # local
    info = stt_core.model_status()
    base.update(
        {
            "ok": bool(info.get("ok")) and bool(info.get("engineReady", True)),
            "engineReady": bool(info.get("engineReady", True)),
            "model": stt_core.MODEL_ID,
            "root": info.get("root"),
            "files": info.get("files", {}),
            "warnings": info.get("warnings", []),
            "error": info.get("error"),
            "loaded": _recognizer is not None,
            "sherpaOnnxVersion": info.get("sherpa_onnx_version", "unknown"),
        }
    )
    return base


def _slot_model_view() -> dict[str, Any]:
    """「设置 → 模型服务 → 用途分槽 → 音频」绑到了什么（给设置页显示）。"""
    try:
        binding = pc_config.slot_api_settings()
    except Exception:  # noqa: BLE001 - 设置存储没起来不是致命问题
        logger.debug("audio slot lookup failed", exc_info=True)
        return {"bound": False, "instanceId": None, "instanceType": None, "model": None, "endpoint": None}
    if not binding:
        return {"bound": False, "instanceId": None, "instanceType": None, "model": None, "endpoint": None}
    return {
        "bound": True,
        "instanceId": binding.get("instance_id"),
        "instanceType": binding.get("instance_type"),
        "model": binding["model"],
        "endpoint": binding["base_url"],
    }


def _local_ready_quietly() -> bool:
    try:
        stt_core.resolve_model_dir()
    except Exception:  # noqa: BLE001
        return False
    return True


def _gate() -> dict[str, Any]:
    config = _speech_config()
    if not config.get("enabled", True):
        raise SpeechError("语音识别已在设置里关闭（speech.enabled = false）")
    return config


def transcribe_bytes(
    data: bytes,
    *,
    suffix: str = ".wav",
    hotwords: Sequence[str] = (),
) -> dict[str, Any]:
    """Transcribe an uploaded audio blob. Blocking — call from a worker thread.

    ``data`` is what the composer's 🎤 button sends: a 16 kHz mono WAV encoded
    client-side by ``web/src/wavRecorder.ts`` (``MediaRecorder`` was no use —
    it only emits webm/ogg Opus, and this machine has no ffmpeg to transcode
    it). WAV is parsed in-process by the local engine; the API engine uploads
    the bytes as-is and lets the endpoint decode them.
    """
    if not data:
        raise SpeechError("音频为空")

    config = _gate()
    kind = pc_config.effective_engine(config)

    if kind == "api":
        settings = pc_config.api_settings(config)
        try:
            return pc_api.transcribe_api_bytes(data, settings=settings, filename="audio" + _dot(suffix))
        except pc_api.ApiError as error:
            raise SpeechError(str(error)) from error

    recognizer = get_recognizer()
    flush = float(config["flushSilenceSeconds"] or 0.0)

    suffix = _dot(suffix)
    if suffix.lower() in _WAV_SUFFIXES:
        # Parse straight from memory — a short clip needs no disk round-trip.
        try:
            samples, rate = stt_core._parse_riff(data)  # noqa: SLF001 - same package
        except stt_core.AudioDecodeError:
            # Not actually RIFF despite the name; let ffmpeg try.
            samples, rate = _decode_via_tempfile(data, suffix)
    else:
        samples, rate = _decode_via_tempfile(data, suffix)

    started = time.perf_counter()
    try:
        text = stt_core.transcribe_samples(recognizer, samples, rate, flush)
    except Exception as error:  # noqa: BLE001 - surface a readable message
        raise SpeechError(f"识别失败：{error}") from error

    return {
        "text": text,
        "model": stt_core.MODEL_ID,
        "engine": "local",
        "durationMs": round((time.perf_counter() - started) * 1000),
        "audioMs": round(len(samples) / stt_core.SAMPLE_RATE * 1000),
    }


def _dot(suffix: str) -> str:
    suffix = suffix or ".wav"
    return suffix if suffix.startswith(".") else f".{suffix}"


def _decode_via_tempfile(data: bytes, suffix: str) -> tuple[Any, int]:
    """Write to a scratch file so ffmpeg can probe the container."""
    import os
    import tempfile

    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        handle.write(data)
        handle.close()
        # ``load_audio`` re-parses RIFF itself, then falls back to ffmpeg.
        return stt_core.load_audio(handle.name), stt_core.SAMPLE_RATE
    except stt_core.AudioDecodeError as error:
        raise SpeechError(str(error)) from error
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


def transcribe_path(path: str | Path, *, hotwords: Sequence[str] = ()) -> dict[str, Any]:
    """Transcribe a file already on disk (used by tools and the CLI)."""
    audio = Path(path)
    if not audio.is_file():
        raise SpeechError(f"音频文件不存在：{audio}")

    config = _gate()
    kind = pc_config.effective_engine(config)

    if kind == "api":
        settings = pc_config.api_settings(config)
        started = time.perf_counter()
        try:
            result = pc_api.transcribe_api_path(audio, settings=settings)
        except pc_api.ApiError as error:
            raise SpeechError(str(error)) from error
        result["engine"] = "api"
        if result.get("audioMs") is None:
            result["audioMs"] = 0
        result["durationMs"] = round((time.perf_counter() - started) * 1000)
        return result

    recognizer = get_recognizer()
    flush = float(config["flushSilenceSeconds"] or 0.0)
    started = time.perf_counter()
    try:
        samples = stt_core.load_audio(audio)
    except stt_core.AudioDecodeError as error:
        raise SpeechError(str(error)) from error
    try:
        text = stt_core.transcribe_samples(recognizer, samples, stt_core.SAMPLE_RATE, flush)
    except Exception as error:  # noqa: BLE001
        raise SpeechError(f"识别失败：{error}") from error
    return {
        "text": text,
        "model": stt_core.MODEL_ID,
        "engine": "local",
        "durationMs": round((time.perf_counter() - started) * 1000),
        "audioMs": round(len(samples) / stt_core.SAMPLE_RATE * 1000),
    }


# ---------------------------------------------------------------- model download


def download_model(sources: Optional[Sequence[str]] = None) -> dict[str, Any]:
    return stt_core.start_model_download(sources)


def model_download_state() -> dict[str, Any]:
    return stt_core.download_state()
