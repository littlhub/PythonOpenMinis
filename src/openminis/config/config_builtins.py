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
