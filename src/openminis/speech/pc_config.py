"""语音引擎配置：本地模型 / 模型 API 的取舍，以及「现在用哪个」。

从 ``G:\\Ekko\\语音识别\\stt\\stt_config.py`` 移植，但存储换成 OpenMinis 的
``speech.*`` 偏好项（设置页可改、可写进 prefs 文件），不再单独维护
``stt_config.json``。

引擎选择规则（``speech.engine``）：

* ``local``（默认）—— 固定用本机 sherpa-onnx 模型，离线、不要 Key；
* ``api`` —— 固定用模型 API；没配好直接报错，不悄悄退回本地；
* ``auto`` —— 有 Key（或 Studio 代理在线）且地址、模型齐全就用 API，否则本地。

API Key 的取值顺序：设置里填的 > 环境变量 EKKO_STT_API_KEY /
OPENAI_API_KEY 等 > 本机 Ekko Studio 的语音代理（只读它自己的鉴权文件）。
"""

from __future__ import annotations

import json
import os
import re
import socket
from pathlib import Path
from typing import Any, Optional

from ..core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "API_PRESETS",
    "api_settings",
    "effective_engine",
    "model_preset",
    "speech_prefs",
    "slot_api_settings",
    "STUDIO_PORT",
    "STUDIO_OFFLINE_HINT",
]

#: 常用服务商的默认参数（base_url 指到 /audio/transcriptions）。``local`` /
#: ``studio`` 不是真正的服务商，只是把「切回离线」「用 Studio 代理」存成默认值。
API_PRESETS: dict[str, dict[str, str]] = {
    "local": {
        "label": "本地模型（离线，不要 Key）",
        "base_url": "",
        "model": "",
        "key_env": "",
    },
    "model": {
        "label": "模型服务里「音频」槽绑定的模型（自动读取）",
        "base_url": "",
        "model": "",
        "key_env": "",
    },
    "studio": {
        "label": "Ekko Studio 语音代理（自动读取）",
        "base_url": "",
        "model": "hermes-studio",
        "key_env": "",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1/audio/transcriptions",
        "model": "gpt-4o-transcribe",
        "key_env": "OPENAI_API_KEY",
    },
    "groq": {
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1/audio/transcriptions",
        "model": "whisper-large-v3-turbo",
        "key_env": "GROQ_API_KEY",
    },
    "siliconflow": {
        "label": "硅基流动",
        "base_url": "https://api.siliconflow.cn/v1/audio/transcriptions",
        "model": "FunAudioLLM/SenseVoiceSmall",
        "key_env": "SILICONFLOW_API_KEY",
    },
    "dashscope": {
        "label": "阿里云百炼（Paraformer）",
        # 百炼的同步接口，OpenAI 兼容路径
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/audio/transcriptions",
        "model": "paraformer-v2",
        "key_env": "DASHSCOPE_API_KEY",
    },
    "custom": {
        "label": "自定义 OpenAI 兼容接口",
        "base_url": "",
        "model": "",
        "key_env": "EKKO_STT_API_KEY",
    },
}

#: 按顺序检查的通用 Key 环境变量
GENERIC_KEY_ENVS = ("EKKO_STT_API_KEY", "OPENAI_API_KEY")

#: Ekko Studio 默认端口（config-sync.ts 里的 config.port）
STUDIO_PORT = 8748

STUDIO_OFFLINE_HINT = (
    f"Ekko Studio 语音代理没连上（127.0.0.1:{STUDIO_PORT}）。\n"
    "  打开 Ekko Studio 后重试；或者切回本地模型（engine = local），"
    "或者填一个云端服务商的 Key。"
)

_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "engine": "local",
    "preset": "local",
    "baseUrl": "",
    "model": "",
    "apiKey": "",
    "language": "zh",
    "prompt": "",
    "timeoutSeconds": 120,
    "numThreads": 2,
    "decodingMethod": "greedy_search",
    "flushSilenceSeconds": 0.8,
}


def speech_prefs() -> dict[str, Any]:
    """读 ``speech.*`` 偏好，读不到的用默认值。

    故意容错：CLI / TUI 路径不会调 ``ConfigRegistry.init()``，注册表没起来时
    也不能让语音整个罢工 —— 那就按默认（本地引擎）来。
    """
    values = dict(_DEFAULTS)
    names = {
        "enabled": "enabled",
        "engine": "engine",
        "preset": "apiPreset",
        "baseUrl": "apiBaseUrl",
        "model": "apiModel",
        "apiKey": "apiKey",
        "language": "apiLanguage",
        "prompt": "apiPrompt",
        "timeoutSeconds": "apiTimeoutSeconds",
        "numThreads": "numThreads",
        "decodingMethod": "decodingMethod",
        "flushSilenceSeconds": "flushSilenceSeconds",
    }
    try:
        from ..config.config_registry import ConfigRegistry

        registry = ConfigRegistry.get()
    except Exception:  # noqa: BLE001 - registry not initialized: defaults are fine
        registry = None

    if registry is None:
        return values

    for key, suffix in names.items():
        field = registry.resolve_field(f"speech.{suffix}")
        if field is None:
            continue
        try:
            value = field.read()
        except Exception:  # noqa: BLE001 - an unreadable pref is not fatal
            logger.debug("speech.%s unreadable, using default", suffix)
            continue
        raw = getattr(value, "value", value)
        # 只有空字符串要跳过（见下）；False / 0 是真实设置，不能当「没设」。
        if raw is not None and raw != "":
            values[key] = raw
    # 空字符串是合法的（清空 Key / 地址），上面那行会跳过 —— 单独补一次
    for key, suffix in (("baseUrl", "apiBaseUrl"), ("model", "apiModel"), ("apiKey", "apiKey")):
        field = registry.resolve_field(f"speech.{suffix}")
        if field is None:
            continue
        try:
            values[key] = str(getattr(field.read(), "value", "") or "")
        except Exception:  # noqa: BLE001
            pass
    return values


# ---------------------------------------------------------------- Studio 自动发现


def _read_curlrc_auth(path: Path) -> Optional[dict[str, str]]:
    """从 Studio 的 curl 配置里取出 Authorization 头（只读）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r'header\s*=\s*"Authorization:\s*Bearer\s+([^"]+)"', text, re.IGNORECASE)
    if not match:
        return None
    return {"Authorization": f"Bearer {match.group(1).strip()}"}


def _studio_voice_endpoint(port: int = STUDIO_PORT) -> str:
    return f"http://127.0.0.1:{port}/api/studio/voice/proxy/default/v1/audio/transcriptions"


def discover_from_studio() -> Optional[dict[str, Any]]:
    """找 Ekko Studio 设置里已经配好的语音模型；没开/没配返回 None。"""
    home = Path.home()
    curlrc = home / ".hermes-web-ui" / "runtime" / "hermes-studio-voice.curlrc"
    config_yaml = home / ".hermes" / "config.yaml"

    if not curlrc.is_file():
        return None
    headers = _read_curlrc_auth(curlrc)
    if not headers:
        return None

    # Studio 没启动时别让用户等满超时：1 秒探一下端口
    try:
        with socket.create_connection(("127.0.0.1", STUDIO_PORT), timeout=1.0):
            pass
    except OSError:
        return None

    model = "hermes-studio"
    try:
        text = config_yaml.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"stt:\s*\n(?:.*\n)*?\s+command:.*?--form\s+\"?model=([^\s\"]+)", text)
        if match:
            model = match.group(1)
    except OSError:
        pass

    return {
        "base_url": _studio_voice_endpoint(),
        "model": model,
        "extra_headers": headers,
        "source": str(curlrc),
    }


def _discover_key(prefs: dict[str, Any]) -> str:
    key = str(prefs.get("apiKey") or "").strip()
    if key:
        return key
    preset_name = str(prefs.get("preset") or "")
    # 选了 Studio 代理 / 音频槽位 / 本地，就别拿通用 OPENAI_API_KEY 顶上去
    if preset_name in ("studio", "local", MODEL_PRESET):
        return os.environ.get("EKKO_STT_API_KEY", "").strip()
    for env_name in GENERIC_KEY_ENVS:
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    env_name = API_PRESETS.get(preset_name, {}).get("key_env")
    if env_name:
        return os.environ.get(env_name, "").strip()
    return ""


def api_settings(prefs: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """设置 + 环境变量 + Studio 自动发现 合并成一份接口参数。"""
    prefs = prefs or speech_prefs()
    preset_name = str(prefs.get("preset") or "local")
    preset = API_PRESETS.get(preset_name, {})
    key = _discover_key(prefs)

    base_url = str(prefs.get("baseUrl") or "").strip()
    model = str(prefs.get("model") or "").strip()
    if not base_url and preset_name not in ("local",):
        base_url = preset.get("base_url", "")
    if not model:
        model = preset.get("model", "")
    if preset_name == "local":
        # 「本地」预设不需要接口：地址和模型都清空，免得 auto 误判成 API
        base_url, model = "", ""

    # 没有 Key 时试着用 Ekko Studio 的语音代理（它自己的鉴权头在 curlrc 里）
    extra_headers: dict[str, str] = {}
    source = None
    if preset_name == MODEL_PRESET:
        # 用「音频」槽位绑定的模型：地址 / 模型 / Key 全从那个厂商实例来
        bound = slot_api_settings()
        if bound:
            bound["language"] = str(prefs.get("language") or "").strip()
            bound["prompt"] = str(prefs.get("prompt") or "").strip()
            bound["timeout"] = float(prefs.get("timeoutSeconds") or 120)
            return bound
        base_url, model = "", ""
    elif not key and preset_name != "local":
        discovered = discover_from_studio()
        if discovered:
            if preset_name in ("", "studio") or not base_url:
                base_url = discovered["base_url"]
                model = discovered["model"]
            extra_headers = discovered["extra_headers"]
            source = discovered["source"]

    return {
        "base_url": base_url,
        "model": model,
        "api_key": key,
        "language": str(prefs.get("language") or "").strip(),
        "prompt": str(prefs.get("prompt") or "").strip(),
        "timeout": float(prefs.get("timeoutSeconds") or 120),
        "extra_headers": extra_headers,
        "source": source,
        "preset": preset_name,
        "send_response_format": True,
    }


def preset_defaults() -> dict[str, dict[str, str]]:
    """每个预设的地址 / 模型 / 该去哪个环境变量找 Key（给设置页用）。"""
    return {
        name: {
            "base_url": preset.get("base_url", ""),
            "model": preset.get("model", ""),
            "key_env": preset.get("key_env", ""),
        }
        for name, preset in dict(API_PRESETS, **{MODEL_PRESET: {"label": "音频槽位模型"}}).items()
    }


# ---------------------------------------------------------------- 模型槽位（音频）

#: 槽位接口的后缀：只写 ``/v1`` 或 ``/v1/`` 的地址，补成转写端点。
_TRANSCRIBE_SUFFIX = "/audio/transcriptions"

#: ``preset == "model"``：用「设置 → 模型服务 → 用途分槽 → 音频」绑定的模型。
MODEL_PRESET = "model"


def transcribe_endpoint(base_url: str) -> str:
    """把厂商实例的 Base URL 补成 ``…/audio/transcriptions``。

    实例地址是给 chat 用的根（``https://api.openai.com/v1``），直接 POST
    音频只会 404；已经是完整端点的原样返回。
    """
    url = (base_url or "").strip().rstrip("/")
    if not url or url.endswith(_TRANSCRIBE_SUFFIX):
        return url
    if url.endswith("/audio") or url.endswith("/audio/transcribe"):
        return url
    return url + _TRANSCRIBE_SUFFIX


def _slot_preset(conf: dict[str, Any]) -> str:
    """厂商实例的类型 → 语音预设名（决定去哪儿找默认 Key）。

    OpenMinis 的厂商类型是 ``openAI``（驼峰），旧 ``stt_config.json`` 里可能是
    ``openai``；按小写归一后比。认不出来的就当 OpenAI 兼容网关。
    """
    kind = str(conf.get("type") or "").strip().lower()
    table = {
        "studio": "studio",
        "groq": "groq",
        "siliconflow": "siliconflow",
        "dashscope": "dashscope",
        "openai": "openai",
    }
    return table.get(kind, "openai" if "openai" in kind else "custom")


def _provider_key(conf: dict[str, Any], preset_name: str) -> str:
    # 实例上的 Key 优先；没填就按厂商类型找环境变量
    key = str(conf.get("apiKey") or "").strip()
    if key:
        return key
    env_name = API_PRESETS.get(preset_name, {}).get("key_env")
    if env_name:
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    if preset_name != "studio":
        for generic in GENERIC_KEY_ENVS:
            value = os.environ.get(generic, "").strip()
            if value:
                return value
    return ""


def _same_host(a: str, b: str) -> bool:
    """同一个 host:port（用来判断该不该借用 Studio 的代理鉴权）。"""
    from urllib.parse import urlsplit

    left, right = urlsplit(a or ""), urlsplit(b or "")
    return bool(left.netloc) and left.netloc == right.netloc


def slot_api_settings() -> Optional[dict[str, Any]]:
    """「音频」槽位绑定的模型 → 一份 :func:`api_settings` 同形状的接口参数。

    没绑定 / 设置存储还没起来就返回 ``None``（调用方退回本地模型）。
    """
    try:
        from ..settings.store import SettingsStore

        store = SettingsStore.get()
        binding = store.slot_binding("audio")
    except Exception:  # noqa: BLE001 - 没有设置存储的路径（CLI / 测试）就用默认
        logger.debug("audio slot unavailable", exc_info=True)
        return None
    if binding is None:
        return None
    conf, model = binding
    base_url = str(conf.get("baseUrl") or "").strip()
    if not base_url or not model:
        return None

    preset_name = _slot_preset(conf)
    key = _provider_key(conf, preset_name)
    extra_headers: dict[str, str] = {}
    source: Optional[str] = None
    # 实例没填 Key（Studio 代理 / 本机网关常见）就借 Studio 的语音代��鉴权；
    # 外部服务商（openai.com 等）不要乱塞 Studio 的 token，免得被拒成 401。
    if not key:
        discovered = discover_from_studio()
        if discovered:
            extra_headers = discovered["extra_headers"]
            source = discovered["source"]
            if not _same_host(discovered["base_url"], base_url):
                # 指向别的网关：地址/模型归实例，鉴权归 Studio
                logger.debug("audio slot uses a foreign base_url; borrowing studio auth")
    if not extra_headers:
        extra_headers = dict(conf.get("headers") or {})

    prefs = speech_prefs()
    return {
        "base_url": transcribe_endpoint(base_url),
        "model": model,
        "api_key": key,
        "language": str(prefs.get("language") or "").strip(),
        "prompt": str(prefs.get("prompt") or "").strip(),
        "timeout": float(prefs.get("timeoutSeconds") or 120),
        "extra_headers": extra_headers,
        "source": source,
        # 槽位绑定的实例可能是 OpenAI 兼容网关，其 ID 未必认得 —— 不再补
        # ``response_format``（万一网关拒绝会整个转写失败，文本照样能解）。
        "send_response_format": False,
        "preset": MODEL_PRESET,
        "instance_id": str(conf.get("id") or ""),
        "instance_type": str(conf.get("type") or ""),
    }


def model_preset() -> str:
    """``speech.apiPreset = model`` 时，用户实际绑的是哪个厂商实例/模型。"""
    settings = slot_api_settings()
    if not settings:
        return ""
    return f"{settings.get('instance_id') or '?'} · {settings['model']}"


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 10:
        return "***"
    return f"{key[:6]}***{key[-4:]}"


def effective_engine(prefs: Optional[dict[str, Any]] = None) -> str:
    """把 local/api/auto 变成确定的 local 或 api。"""
    prefs = prefs or speech_prefs()
    choice = str(prefs.get("engine") or "local").lower()
    if choice in ("local", "api"):
        return choice
    # auto
    settings = api_settings(prefs)
    has_auth = bool(settings["api_key"]) or bool(settings["extra_headers"])
    return "api" if has_auth and settings["base_url"] and settings["model"] else "local"


def describe_engine(kind: str, prefs: Optional[dict[str, Any]] = None) -> str:
    if kind == "local":
        return "本地模型（离线，不要 Key）"
    settings = api_settings(prefs)
    host = settings["base_url"].split("/")[2] if "//" in settings["base_url"] else settings["base_url"]
    return f"模型 API（{settings['model'] or '?'} @ {host}）"


def settings_json(prefs: Optional[dict[str, Any]] = None) -> str:
    """给日志 / 诊断用的一份脱敏配置。"""
    prefs = prefs or speech_prefs()
    settings = api_settings(prefs)
    return json.dumps(
        {
            "engine": prefs.get("engine"),
            "effective": effective_engine(prefs),
            "preset": settings["preset"],
            "base_url": settings["base_url"],
            "model": settings["model"],
            "api_key": _mask(settings["api_key"]),
            "auth": "studio" if settings["extra_headers"] else ("key" if settings["api_key"] else ""),
        },
        ensure_ascii=False,
    )
