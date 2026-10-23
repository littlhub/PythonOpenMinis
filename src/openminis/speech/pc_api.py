"""模型 API 引擎：任意 OpenAI 兼容的 ``/audio/transcriptions`` 接口。

从 ``G:\\Ekko\\语音识别\\stt\\stt_engine.py`` 的 ``ApiEngine`` 移植。只用标准库
（``urllib`` + 手写 multipart），不额外拉依赖 —— 本地模型的 sherpa-onnx 已经够重。

与本地引擎的区别：

* 文件原样上传，接口自己解码，所以不需要 ffmpeg；
* 麦克风是「说完一段再识别」，没有实时字幕（网页端本来就是录完一段再上传，
  所以对 🎤 按钮没有差别）；
* 要联网、要 Key（或者用本机 Ekko Studio 的语音代理，见 ``pc_config``）。

不做自动重试：识别失败大多是因为 Key、额度或模型名，重试只会让人多等。
"""

from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Optional

from ..core.logging import get_logger

logger = get_logger(__name__)

__all__ = ["ApiError", "transcribe_api_bytes", "transcribe_api_path"]

#: 按文件头猜扩展名 —— 接口靠扩展名/文件名决定 Content-Type。
_MAGIC = (
    (b"RIFF", ".wav"),
    (b"ID3", ".mp3"),
    (b"fLaC", ".flac"),
    (b"OggS", ".ogg"),
    (b"\x1a\x45\xdf\xa3", ".webm"),
    (b"#!AMR", ".amr"),
)


class ApiError(RuntimeError):
    """接口调用失败；消息已经是给人看的中文。"""


def _sniff_suffix(data: bytes, fallback: str = ".wav") -> str:
    for magic, suffix in _MAGIC:
        if data.startswith(magic):
            return suffix
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return ".m4a"
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return ".mp3"
    return fallback


def _build_multipart(
    fields: dict[str, str],
    filename: str,
    data: bytes,
) -> tuple[bytes, str]:
    boundary = "----openminis" + uuid.uuid4().hex
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            .encode("utf-8")
            + str(value).encode("utf-8")
            + b"\r\n"
        )
    chunks.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode("utf-8")
    )
    chunks.append(data)
    chunks.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _mask(value: str) -> str:
    value = str(value or "")
    if len(value) <= 10:
        return "***" if value else ""
    return f"{value[:6]}***{value[-4:]}"


def _http_hint(status: int, detail: str) -> str:
    """把状态码翻成人话；回显内容里出现的 Key 一律打码。"""
    detail = str(detail or "").strip()
    if len(detail) > 500:
        detail = detail[:500] + "…"
    if status == 400:
        if "no speech" in detail.lower() or "no_speech" in detail.lower():
            return "接口没有检测到人声（录音太短或太安静）"
        return f"接口拒绝了请求（HTTP 400）：{detail or '参数不对，检查模型名和接口地址'}"
    if status == 401:
        return (
            "API Key 无效或过期（HTTP 401）—— 填的 Key 不对、厂商实例上没填 Key，"
            "或者借用的 Ekko Studio 代理 token 过期了（重开 Studio 让它刷新）。"
        )
    if status == 403:
        return f"没有权限（HTTP 403）：{detail or '检查 Key 的权限/额度'}"
    if status == 404:
        return f"接口地址或模型名不对（HTTP 404）：{detail or '地址要写到 /audio/transcriptions 结尾'}"
    if status == 413:
        return "音频太大，接口拒收（HTTP 413）"
    if status == 429:
        return "请求太频繁或额度用完了（HTTP 429），稍后再试"
    if 500 <= status < 600:
        return f"服务端出错（HTTP {status}）：{detail or '稍后重试'}"
    return f"接口返回 HTTP {status}：{detail}"


def _post(settings: Any, data: bytes, filename: str) -> dict[str, Any]:
    base_url = str(settings["base_url"] or "").strip()
    key = str(settings["api_key"] or "").strip()
    if not base_url:
        if settings.get("preset") == "model":
            raise ApiError(
                "语音用「音频」槽位的模型，但那个槽还没绑定："
                "去 设置 → 模型服务 → 用途分槽 → 音频，选厂商实例 + 模型；"
                "或者把 设置 → 语音识别 切回本地模型。"
            )
        raise ApiError("没有配置语音接口地址（speech.apiBaseUrl 为空）")

    fields: dict[str, str] = {}
    if settings["model"]:
        fields["model"] = str(settings["model"])
    if settings["language"]:
        fields["language"] = str(settings["language"])
    if settings["prompt"]:
        fields["prompt"] = str(settings["prompt"])
    if settings.get("send_response_format", True):
        fields.setdefault("response_format", "json")

    body, content_type = _build_multipart(fields, filename, data)
    headers = {"Content-Type": content_type, "Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    headers.update({str(k): str(v) for k, v in (settings.get("extra_headers") or {}).items()})

    request = urllib.request.Request(base_url, data=body, headers=headers, method="POST")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=float(settings["timeout"] or 120)) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        try:
            detail = error.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            detail = ""
        raise ApiError(_http_hint(error.code, detail)) from error
    except urllib.error.URLError as error:
        reason = getattr(error, "reason", error)
        raise ApiError(
            f"连不上语音接口：{reason}\n"
            "  检查网络；如果用 Ekko Studio 代理，确认 Studio 开着，"
            "或者切回本地模型（speech.engine = local）"
        ) from error
    except TimeoutError as error:
        raise ApiError(f"接口超时（{settings['timeout']} 秒），音频太长或网络太慢") from error

    elapsed = (time.perf_counter() - started) * 1000
    text = _extract_text(raw)
    logger.info(
        "api stt: %s @ %s → %d chars in %dms",
        settings["model"] or "?",
        base_url.split("/")[2] if "//" in base_url else base_url,
        len(text),
        elapsed,
    )
    return {"text": text, "model": str(settings["model"] or "api"), "durationMs": round(elapsed)}


def _extract_text(raw: bytes) -> str:
    body = raw.decode("utf-8", "replace").strip()
    if not body:
        return ""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body  # text / srt 之类的纯文本返回
    if isinstance(payload, dict):
        for key in ("text", "result", "transcription"):
            value = payload.get(key)
            if isinstance(value, str):
                return value.strip()
        error = payload.get("error")
        if error:
            message = error.get("message") if isinstance(error, dict) else error
            raise ApiError(f"接口返回错误：{_mask(str(message))}")
    return body


def transcribe_api_bytes(data: bytes, *, settings: Any, filename: Optional[str] = None) -> dict[str, Any]:
    """识别内存里的音频。``filename`` 决定接口看到的类型，未给就按文件头猜。"""
    if not data:
        raise ApiError("音频为空")
    suffix = Path(filename or "").suffix.lower() or _sniff_suffix(data)
    name = f"audio{suffix}"
    return _post(settings, data, name)


def transcribe_api_path(path: str | Path, *, settings: Any) -> dict[str, Any]:
    """识别磁盘上的文件：原样上传，不转码。"""
    audio = Path(path)
    if not audio.is_file():
        raise ApiError(f"音频文件不存在：{audio}")
    data = audio.read_bytes()
    suffix = audio.suffix.lower() or _sniff_suffix(data)
    return _post(settings, data, audio.name or f"audio{suffix}")
