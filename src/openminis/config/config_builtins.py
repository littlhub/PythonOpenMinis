"""Built-in config field registrations.

Ported from: src/android/app/src/main/java/com/openminis/app/config/ConfigBuiltins.kt
Original package: com.openminis.app.config

PORT-STATUS: partial

The Kotlin file is 1165 lines and registers every built-in setting
(appearance, browser, sandbox, speech, providers collections, models
collections, groups collections, env vars, thinking rules, ...). This port
currently registers the platform-independent scalar fields and the
``appearance`` topic; the collection-backed topics (``models``, ``groups``,
``providers``, ``env``, ``thinking-rules``) are wired in as their collection
classes get ported. Registration order follows the original file.
"""

from __future__ import annotations

from typing import Any

from ..core.logging import get_logger
from .config_field import ConfigField
from .config_registry import ConfigRegistry
from .config_schema import (
    BoolSchema,
    ConfigAccess,
    ConfigRisk,
    ConfigSchema,
    DoubleSchema,
    IntSchema,
    StrEnumSchema,
    StrSchema,
)
from .config_value import Bool, ConfigValue, Double, Int, Str

logger = get_logger(__name__)

__all__ = ["register_into"]


class PrefsBackedField(ConfigField):
    """Scalar field stored in :class:`openminis.core.prefs.Prefs`.

    PORT: This is the Python counterpart of the ``PrefsBoolField`` /
    ``PrefsIntField`` / ``PrefsStrField`` family in
    ``config/fields/PrefsFields.kt``. Kotlin defines one class per primitive
    because of static typing; Python folds them into a single class parameterised
    by schema, then subclasses for readability.
    """

    def __init__(
        self,
        path: str,
        display_name: str,
        description: str,
        schema: ConfigSchema,
        *,
        prefs_name: str = "minis_prefs",
        default: ConfigValue,
        access: ConfigAccess = ConfigAccess.READWRITE,
        risk: ConfigRisk = ConfigRisk.NORMAL,
        revertable: bool = True,
    ) -> None:
        self._path = path
        self._display_name = display_name
        self._description = description
        self._schema = schema
        self._default = default
        self._access = access
        self._risk = risk
        self._revertable = revertable
        from ..core.prefs import get_prefs

        self._prefs = get_prefs(prefs_name)

    # --- ConfigField -----------------------------------------------------
    @property
    def path(self) -> str:
        return self._path

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def description(self) -> str:
        return self._description

    @property
    def value_schema(self) -> ConfigSchema:
        return self._schema

    @property
    def access(self) -> ConfigAccess:
        return self._access

    @property
    def risk(self) -> ConfigRisk:
        return self._risk

    @property
    def revertable(self) -> bool:
        return self._revertable

    # --- storage ---------------------------------------------------------
    def read(self) -> ConfigValue:
        key = self._path
        if isinstance(self._schema, BoolSchema):
            return Bool(self._prefs.get_boolean(key, _unwrap_bool(self._default)))
        if isinstance(self._schema, IntSchema):
            return Int(self._prefs.get_int(key, _unwrap_int(self._default)))
        if isinstance(self._schema, DoubleSchema):
            return Double(self._prefs.get_float(key, _unwrap_float(self._default)))
        return Str(self._prefs.get_string(key, _unwrap_str(self._default)) or "")

    def write(self, value: ConfigValue) -> None:
        if isinstance(value, Bool):
            self._prefs.set_now(self._path, value.value)
        elif isinstance(value, Int):
            self._prefs.set_now(self._path, value.value)
        elif isinstance(value, Double):
            self._prefs.set_now(self._path, value.value)
        elif isinstance(value, Str):
            self._prefs.set_now(self._path, value.value)
        else:
            # Objects/arrays persist as their JSON text, matching the audit
            # row format the bridge expects.
            self._prefs.set_now(self._path, value.json_string())


def _unwrap_bool(v: ConfigValue) -> bool:
    return v.value if isinstance(v, Bool) else False


def _unwrap_int(v: ConfigValue) -> int:
    return v.value if isinstance(v, Int) else 0


def _unwrap_float(v: ConfigValue) -> float:
    if isinstance(v, Double):
        return v.value
    return float(v.value) if isinstance(v, Int) else 0.0


def _unwrap_str(v: ConfigValue) -> str:
    return v.value if isinstance(v, Str) else ""


def register_into(registry: ConfigRegistry, **dependencies: Any) -> None:
    """Kotlin: ``ConfigBuiltins.registerInto(registry, context, providers,
    envVars, chats)``.

    Registers every built-in field. Extra keyword dependencies are accepted and
    ignored for now so the registry can boot with whatever the host has wired
    up (mirrors the gradual dependency injection the Kotlin side does).
    """

    # --- appearance ------------------------------------------------------
    registry.register(
        PrefsBackedField(
            path="appearance.theme",
            display_name="Theme",
            description="UI colour theme: system, light or dark.",
            schema=StrEnumSchema(("system", "light", "dark")),
            default=Str("system"),
        )
    )
    registry.register(
        PrefsBackedField(
            path="appearance.fontScale",
            display_name="Font scale",
            description="Multiplier applied to the base font size.",
            schema=DoubleSchema(min=0.5, max=2.5),
            default=Double(1.0),
        )
    )
    registry.register(
        PrefsBackedField(
            path="appearance.reduceMotion",
            display_name="Reduce motion",
            description="Disable non-essential animation.",
            schema=BoolSchema(),
            default=Bool(False),
        )
    )
    # Web-only: Android renders through Compose, where the background follows
    # the Material theme. The Web shell needs an explicit choice, so the value
    # is stored as a small "background spec" string the frontend interprets:
    #
    #   default              → theme default
    #   preset:<name>        → one of the built-in palettes
    #   #rrggbb              → a flat colour, contrast picked automatically
    #   url:https://…        → a cover image hosted elsewhere
    #   file:<name>          → an image uploaded to this machine, served by
    #                          /api/appearance/background/<name>
    #
    # The regex is what keeps a malformed value out of the stylesheet: the
    # frontend writes these strings into CSS, so an unvalidated value is a
    # style-injection primitive (``url:javascript:`` and friends).
    registry.register(
        PrefsBackedField(
            path="appearance.background",
            display_name="Background",
            description="Chat background: preset, #rrggbb colour, url:<image> or file:<upload>.",
            schema=StrSchema(
                max_length=300,
                regex=(
                    r"(default"
                    r"|preset:[a-z0-9_-]{1,24}"
                    r"|#[0-9a-fA-F]{6}"
                    r"|url:https?://\S{1,250}"
                    r"|file:[A-Za-z0-9_-]{1,64}\.[A-Za-z0-9]{2,5})?"
                ),
            ),
            default=Str("default"),
        )
    )

    # --- agent -----------------------------------------------------------
    registry.register(
        PrefsBackedField(
            path="agent.maxToolIterations",
            display_name="Max tool iterations",
            description="Hard cap on tool round-trips in one agent turn.",
            schema=IntSchema(min=1, max=200),
            default=Int(40),
        )
    )
    registry.register(
        PrefsBackedField(
            path="agent.autoCompactThreshold",
            display_name="Auto-compact threshold",
            description="Context usage ratio at which history is compacted.",
            schema=DoubleSchema(min=0.1, max=1.0),
            default=Double(0.85),
        )
    )

    # --- sandbox ---------------------------------------------------------
    registry.register(
        PrefsBackedField(
            path="sandbox.enabled",
            display_name="Sandbox enabled",
            description="Allow the agent to run commands in the Linux sandbox.",
            schema=BoolSchema(),
            default=Bool(True),
            risk=ConfigRisk.SENSITIVE,
        )
    )
    registry.register(
        PrefsBackedField(
            path="sandbox.shellTimeoutSeconds",
            display_name="Shell timeout (seconds)",
            description="Kill a shell command after this many seconds.",
            schema=IntSchema(min=1, max=3600),
            default=Int(120),
            risk=ConfigRisk.SENSITIVE,
        )
    )
    registry.register(
        PrefsBackedField(
            path="sandbox.envExtra",
            display_name="Extra sandbox env (JSON)",
            description=(
                "JSON object merged into the sandbox process environment, e.g. "
                "{\"LANG\":\"zh_CN.UTF-8\",\"MY_API\":\"xxx\"}. "
                "Empty object = no additions."
            ),
            schema=StrSchema(max_length=8192),
            default=Str("{}"),
            risk=ConfigRisk.SENSITIVE,
        )
    )

    # --- speech (local offline STT) --------------------------------------
    # PORT: the Kotlin original registers a ``speech`` topic backed by the
    # platform recognizer. This port has no platform recognizer, so the same
    # topic now describes the local sherpa-onnx engine (see
    # ``openminis.speech``). Field names stay camelCase like their Kotlin
    # counterparts so the React settings UI can reuse the topic shape.
    registry.register(
        PrefsBackedField(
            path="speech.enabled",
            display_name="语音识别",
            description="启用输入框 🎤 按钮和 transcribe_audio 工具的语音识别。",
            schema=BoolSchema(),
            default=Bool(True),
        )
    )
    # 双引擎：本地 sherpa-onnx（默认，离线）或模型 API（OpenAI 兼容接口 /
    # Ekko Studio 语音代理）。apiKey 用 SECRET_KEYS 里的键名，读取时会被打码。
    registry.register(
        PrefsBackedField(
            path="speech.engine",
            display_name="识别引擎",
            description=(
                "local = 本地模型（离线，不要 Key，默认）；"
                "api = 模型 API（云端，要联网/Key，准确率更高）；"
                "auto = 配好 API 就用 API，否则用本地。"
            ),
            schema=StrEnumSchema(("local", "api", "auto")),
            default=Str("local"),
        )
    )
    registry.register(
        PrefsBackedField(
            path="speech.apiPreset",
            display_name="API 服务商预设",
            description=(
                "选择后自动填好接口地址和模型名：model（用「模型服务 → 用途分槽 → "
                "音频」绑定的模型）/ openai / groq / siliconflow / dashscope / "
                "studio（用本机 Ekko Studio 语音代理）/ custom / local。"
            ),
            schema=StrEnumSchema(
                ("local", "model", "studio", "openai", "groq", "siliconflow", "dashscope", "custom")
            ),
            default=Str("local"),
        )
    )
    registry.register(
        PrefsBackedField(
            path="speech.apiBaseUrl",
            display_name="API 接口地址",
            description="完整的 /audio/transcriptions 地址。留空则用预设的。",
            schema=StrSchema(max_length=500),
            default=Str(""),
            risk=ConfigRisk.SENSITIVE,
        )
    )
    registry.register(
        PrefsBackedField(
            path="speech.apiModel",
            display_name="API 模型名",
            description="例如 gpt-4o-transcribe、whisper-large-v3-turbo。留空则用预设的。",
            schema=StrSchema(max_length=200),
            default=Str(""),
        )
    )
    registry.register(
        PrefsBackedField(
            path="speech.apiKey",
            display_name="API Key",
            description=(
                "云端服务商的 Key（输出一律打码）。留空则读环境变量；"
                "预设为 studio 时自动用 Ekko Studio 的语音代理，不用填。"
            ),
            schema=StrSchema(max_length=500),
            default=Str(""),
            risk=ConfigRisk.SENSITIVE,
        )
    )
    registry.register(
        PrefsBackedField(
            path="speech.apiLanguage",
            display_name="识别语言",
            description="例如 zh、en；留空让接口自己判断。",
            schema=StrSchema(max_length=20),
            default=Str("zh"),
        )
    )
    registry.register(
        PrefsBackedField(
            path="speech.apiPrompt",
            display_name="提示词",
            description="给模型的上下文（专有名词等），留空即可。",
            schema=StrSchema(max_length=2000),
            default=Str(""),
        )
    )
    registry.register(
        PrefsBackedField(
            path="speech.apiTimeoutSeconds",
            display_name="API 超时（秒）",
            description="等接口返回的最长秒数。",
            schema=IntSchema(min=5, max=600),
            default=Int(120),
        )
    )
    registry.register(
        PrefsBackedField(
            path="speech.numThreads",
            display_name="Decoding threads",
            description="CPU threads used by the speech decoder. 2 is enough for short clips.",
            schema=IntSchema(min=1, max=16),
            default=Int(2),
        )
    )
    registry.register(
        PrefsBackedField(
            path="speech.decodingMethod",
            display_name="Decoding method",
            description="greedy_search is fastest; modified_beam_search is slightly more accurate.",
            schema=StrEnumSchema(("greedy_search", "modified_beam_search")),
            default=Str("greedy_search"),
        )
    )
    registry.register(
        PrefsBackedField(
            path="speech.flushSilenceSeconds",
            display_name="Trailing silence (seconds)",
            description=(
                "Silence appended before finishing a clip, so the last syllable "
                "is not swallowed. Raise to 1.0 if words get cut."
            ),
            schema=DoubleSchema(min=0.0, max=3.0),
            default=Double(0.8),
        )
    )

    # --- browser ---------------------------------------------------------
    registry.register(
        PrefsBackedField(
            path="browser.userAgentProfile",
            display_name="User-agent profile",
            description="Which UA string the automation browser advertises.",
            schema=StrEnumSchema(("default", "mobile", "desktop")),
            default=Str("default"),
        )
    )

    # --- server (Python-only additions) ----------------------------------
    # PORT: no Kotlin counterpart — these back the FastAPI entrypoint.
    registry.register(
        PrefsBackedField(
            path="server.host",
            display_name="Server host",
            description="Bind address for the FastAPI server.",
            schema=StrSchema(max_length=253),
            default=Str("127.0.0.1"),
            risk=ConfigRisk.SENSITIVE,
        )
    )
    registry.register(
        PrefsBackedField(
            path="server.port",
            display_name="Server port",
            description="Bind port for the FastAPI server.",
            schema=IntSchema(min=1, max=65535),
            default=Int(8765),
            risk=ConfigRisk.SENSITIVE,
        )
    )

    logger.debug("ConfigBuiltins registered %d fields", len(registry._fields))
