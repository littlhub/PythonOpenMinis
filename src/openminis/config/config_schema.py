"""Value schema: type tag + constraints, driving pre-write validation.

Ported from: src/android/app/src/main/java/com/openminis/app/config/ConfigSchema.kt
Original package: com.openminis.app.config
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .config_error import ConfigError, InvalidValue, OutOfRange, RegexMismatch, TypeMismatch
from .config_value import Arr, Bool, ConfigValue, Double, Int, Null, Str

__all__ = [
    "ConfigSchema",
    "BoolSchema",
    "IntSchema",
    "DoubleSchema",
    "StrSchema",
    "StrEnumSchema",
    "PathSchema",
    "OptionalSchema",
    "ArraySchema",
    "JsonSchema",
    "ConfigAccess",
    "ConfigRisk",
]

_INF = "∞"


@dataclass(frozen=True, slots=True)
class ConfigSchema(ABC):
    """Type tag + per-type constraints for a ``ConfigField``. Drives the
    pre-write validation in the bridge and ``--help`` rendering. Mirrors
    iOS ``ConfigValueSchema`` (Shared/Config/ConfigField.swift).
    """

    @property
    @abstractmethod
    def help_description(self) -> str:
        """Human-readable schema description for ``topic-help`` output."""

    @abstractmethod
    def validate(self, value: ConfigValue) -> None:
        """Pure validation. Raises :class:`ConfigError` on failure; never mutates."""


class BoolSchema(ConfigSchema):
    """Kotlin: ``object Bool : ConfigSchema()``."""

    @property
    def help_description(self) -> str:
        return "bool"

    def validate(self, value: ConfigValue) -> None:
        if not isinstance(value, Bool):
            raise TypeMismatch("bool")

    def __repr__(self) -> str:
        return "BoolSchema()"


@dataclass(frozen=True, slots=True)
class IntSchema(ConfigSchema):
    min: int | None = None
    max: int | None = None

    @property
    def help_description(self) -> str:
        lo = str(self.min) if self.min is not None else f"-{_INF}"
        hi = str(self.max) if self.max is not None else f"+{_INF}"
        return f"int ({lo}..{hi})"

    def validate(self, value: ConfigValue) -> None:
        if not isinstance(value, Int):
            raise TypeMismatch("int")
        i = value.value
        if self.min is not None and i < self.min:
            raise OutOfRange(float(self.min), float(self.max) if self.max is not None else None)
        if self.max is not None and i > self.max:
            raise OutOfRange(float(self.min) if self.min is not None else None, float(self.max))


@dataclass(frozen=True, slots=True)
class DoubleSchema(ConfigSchema):
    min: float | None = None
    max: float | None = None

    @property
    def help_description(self) -> str:
        lo = str(self.min) if self.min is not None else f"-{_INF}"
        hi = str(self.max) if self.max is not None else f"+{_INF}"
        return f"double ({lo}..{hi})"

    def validate(self, value: ConfigValue) -> None:
        if isinstance(value, Double):
            d = value.value
        elif isinstance(value, Int):
            d = float(value.value)
        else:
            raise TypeMismatch("double")
        if self.min is not None and d < self.min:
            raise OutOfRange(self.min, self.max)
        if self.max is not None and d > self.max:
            raise OutOfRange(self.min, self.max)


@dataclass(frozen=True, slots=True)
class StrSchema(ConfigSchema):
    max_length: int | None = None
    regex: str | None = None

    @property
    def help_description(self) -> str:
        out = "string"
        if self.max_length is not None:
            out += f" max {self.max_length} chars"
        if self.regex is not None:
            out += f" /{self.regex}/"
        return out

    def validate(self, value: ConfigValue) -> None:
        if not isinstance(value, Str):
            raise TypeMismatch("string")
        s = value.value
        if self.max_length is not None and len(s) > self.max_length:
            raise InvalidValue(f"string longer than {self.max_length} chars")
        if self.regex is not None:
            compiled = _safe_regex(self.regex)
            # PORT: Kotlin only applies the regex if it compiles — a bad pattern
            # degrades to "no regex check" rather than failing the write.
            if compiled is not None and not compiled.fullmatch(s):
                raise RegexMismatch(self.regex)


@dataclass(frozen=True, slots=True)
class StrEnumSchema(ConfigSchema):
    cases: tuple[str, ...] = ()

    def __init__(self, cases: Any = ()) -> None:
        object.__setattr__(self, "cases", tuple(cases))

    @property
    def help_description(self) -> str:
        return "one of: " + ", ".join(self.cases)

    def validate(self, value: ConfigValue) -> None:
        if not isinstance(value, Str):
            raise TypeMismatch("string")
        if value.value not in self.cases:
            raise InvalidValue("must be one of: " + ", ".join(self.cases))


class PathSchema(ConfigSchema):
    @property
    def help_description(self) -> str:
        return "path"

    def validate(self, value: ConfigValue) -> None:
        if not isinstance(value, Str):
            raise TypeMismatch("path")

    def __repr__(self) -> str:
        return "PathSchema()"


@dataclass(frozen=True, slots=True)
class OptionalSchema(ConfigSchema):
    inner: ConfigSchema

    @property
    def help_description(self) -> str:
        return f"{self.inner.help_description}?"

    def validate(self, value: ConfigValue) -> None:
        if value is Null or isinstance(value, type(Null)):
            return
        self.inner.validate(value)


@dataclass(frozen=True, slots=True)
class ArraySchema(ConfigSchema):
    inner: ConfigSchema

    @property
    def help_description(self) -> str:
        return f"[{self.inner.help_description}]"

    def validate(self, value: ConfigValue) -> None:
        if not isinstance(value, Arr):
            raise TypeMismatch("array")
        for v in value.value:
            self.inner.validate(v)


class JsonSchema(ConfigSchema):
    """Free-form: validation is the writer's responsibility."""

    @property
    def help_description(self) -> str:
        return "json"

    def validate(self, value: ConfigValue) -> None:
        return None

    def __repr__(self) -> str:
        return "JsonSchema()"


def _safe_regex(pattern: str) -> re.Pattern[str] | None:
    """Kotlin: ``try { Regex(regex) } catch (_: Throwable) { null }``."""
    try:
        return re.compile(pattern)
    except re.error:
        return None


class ConfigAccess(Enum):
    """Mirrors Kotlin ``enum class ConfigAccess``."""

    HIDDEN = "HIDDEN"
    READONLY = "READONLY"
    READWRITE = "READWRITE"


class ConfigRisk(Enum):
    """Risk classification surfaced in the confirm dialog."""

    NORMAL = "NORMAL"
    SENSITIVE = "SENSITIVE"
    DESTRUCTIVE = "DESTRUCTIVE"
