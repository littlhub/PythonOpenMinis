"""Config value union.

Ported from: src/android/app/src/main/java/com/openminis/app/config/ConfigValue.kt
Original package: com.openminis.app.config
"""

from __future__ import annotations

import json
from abc import ABC
from dataclasses import dataclass, field
from typing import Any

# Sentinel mirroring ``org.json.JSONObject.NULL``. Kotlin compares against it
# by identity (``a === JSONObject.NULL``); we need the same distinguishable
# null for round-tripping into audit rows.
_JSON_NULL = object()

__all__ = ["ConfigValue", "Bool", "Int", "Double", "Str", "Arr", "Obj", "Null"]


@dataclass(frozen=True, slots=True)
class ConfigValue(ABC):
    """JSON-serializable union covering every value type a ``ConfigField``
    can hold. Mirrors iOS ``ConfigValue`` (Shared/Config/ConfigField.swift).

    The CLI bridge encodes/decodes these as JSON; the confirm dialog
    renders them via :attr:`display_string`; the audit log persists them as
    JSON text in the ``old_value`` / ``new_value`` columns.
    """

    @property
    def display_string(self) -> str:
        """Compact human-readable rendering used by the confirm dialog and
        ``--help`` examples. Long strings are truncated; arrays/objects show
        their JSON form. Mirrors iOS ``displayString``.
        """
        raise NotImplementedError

    def redacting_secrets(self) -> ConfigValue:
        """Return a copy with every value under a :data:`SECRET_KEYS` key masked.

        ``$$VAR`` references pass through (they're a pointer, not the secret);
        any other non-empty literal string becomes ``"••• (hidden)"``. Recurses
        into nested objects/arrays so a secret nested in an add-payload is
        caught too. Mirrors iOS ``ConfigValue.redactingSecrets()`` —
        [T-minis-config-provider-add].

        Apply this to add-payload / audit-row JSON when the path resolves to a
        collection that may contain credentials. The real (un-redacted) value
        still reaches the collection's ``add()`` so it can persist the secret to
        the encrypted store; only the displayed / audit-logged copy is masked.
        """
        return self

    def json_string(self) -> str:
        """JSON-text representation suitable for storing in audit rows."""
        any_value = self._to_json_any()

        def _to_none(o: Any) -> Any:
            """``default=`` hook — nested _JSON_NULL sentinels serialize as null."""
            if o is _JSON_NULL:
                return None
            raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")

        return json.dumps(any_value, ensure_ascii=False, default=_to_none)

    def _to_json_any(self) -> Any:
        """Recursively convert to a JSON-compatible value."""
        raise NotImplementedError

    def to_json(self) -> str:
        """Alias used by the FastAPI layer."""
        return self.json_string()

    # --- decoding --------------------------------------------------------
    @staticmethod
    def decode(text: str) -> ConfigValue | None:
        """Parse a JSON string into a ConfigValue. Returns None on malformed JSON."""
        try:
            return _from_json_any(json.loads(text))
        except (json.JSONDecodeError, ValueError, TypeError):
            return None


@dataclass(frozen=True, slots=True)
class Bool(ConfigValue):
    value: bool

    @property
    def display_string(self) -> str:
        return "true" if self.value else "false"

    def _to_json_any(self) -> Any:
        return self.value


@dataclass(frozen=True, slots=True)
class Int(ConfigValue):
    value: int

    @property
    def display_string(self) -> str:
        return str(self.value)

    def _to_json_any(self) -> Any:
        return self.value


@dataclass(frozen=True, slots=True)
class Double(ConfigValue):
    value: float

    @property
    def display_string(self) -> str:
        return str(self.value)

    def _to_json_any(self) -> Any:
        return self.value


@dataclass(frozen=True, slots=True)
class Str(ConfigValue):
    value: str

    @property
    def display_string(self) -> str:
        # PORT: Kotlin takes 60 + "…" + last 15 for strings over 80 chars.
        if len(self.value) > 80:
            return f'"{self.value[:60]}…{self.value[-15:]}"'
        return f'"{self.value}"'

    def _to_json_any(self) -> Any:
        return self.value


@dataclass(frozen=True, slots=True)
class Arr(ConfigValue):
    value: tuple[ConfigValue, ...] = field(default_factory=tuple)

    # PORT: Kotlin's `data class Arr(val value: List<ConfigValue>)` is a plain
    # list; we accept any sequence in __init__ coercion below and store a tuple
    # so instances stay hashable (frozen dataclass).
    def __init__(self, value: Any = ()) -> None:
        object.__setattr__(self, "value", tuple(value))

    @property
    def display_string(self) -> str:
        return self.json_string()

    def redacting_secrets(self) -> ConfigValue:
        return Arr([v.redacting_secrets() for v in self.value])

    def _to_json_any(self) -> Any:
        return [v._to_json_any() for v in self.value]


@dataclass(frozen=True, slots=True)
class Obj(ConfigValue):
    value: dict[str, ConfigValue] = field(default_factory=dict)

    def __init__(self, value: Any = None) -> None:
        object.__setattr__(self, "value", dict(value or {}))

    @property
    def display_string(self) -> str:
        return self.json_string()

    def redacting_secrets(self) -> ConfigValue:
        out: dict[str, ConfigValue] = {}
        for k, v in self.value.items():
            if (
                k in SECRET_KEYS
                and isinstance(v, Str)
                and not v.value.startswith("$$")
                and v.value != ""
            ):
                out[k] = Str("••• (hidden)")
            else:
                out[k] = v.redacting_secrets()
        return Obj(out)

    def _to_json_any(self) -> Any:
        return {k: v._to_json_any() for k, v in self.value.items()}


class _Null(ConfigValue):
    """Singleton ``Null`` — Kotlin ``object Null : ConfigValue()``."""

    @property
    def display_string(self) -> str:
        return "null"

    def _to_json_any(self) -> Any:  # noqa: D102
        return _JSON_NULL

    def __repr__(self) -> str:
        return "Null"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Null)

    def __hash__(self) -> int:
        return hash("ConfigValue.Null")


Null = _Null()


# --- companion object -------------------------------------------------------

#: Object keys whose values are credential-sensitive and must be
#: masked in any audit / display / confirm-sheet surface. Mirrors
#: iOS ``ConfigValue.secretObjectKeys``. Used by
#: :meth:`ConfigValue.redacting_secrets`.
SECRET_KEYS: frozenset[str] = frozenset({"apiKey", "oauthToken", "manualOAuthToken"})


def _from_json_any(any_value: Any) -> ConfigValue:
    """Kotlin: private ``fromJsonAny``.

    Note the original widens a ``Long`` outside Int range to ``Double`` —
    Python has no int width, so we keep ints as ints unless they arrive as
    floats.
    """
    if any_value is None or any_value is _JSON_NULL:
        return Null
    if isinstance(any_value, bool):  # bool before int: JSON true/false
        return Bool(any_value)
    if isinstance(any_value, int):
        return Int(any_value)
    if isinstance(any_value, float):
        return Double(any_value)
    if isinstance(any_value, str):
        return Str(any_value)
    if isinstance(any_value, (list, tuple)):
        return Arr([_from_json_any(v) for v in any_value])
    if isinstance(any_value, dict):
        return Obj({str(k): _from_json_any(v) for k, v in any_value.items()})
    return Null
