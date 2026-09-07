"""SharedPreferences-backed config field wrappers.

Ported from: src/android/app/src/main/java/com/openminis/app/config/fields/PrefsFields.kt
Original package: com.openminis.app.config.fields
"""

from __future__ import annotations

from openminis.core.prefs import Prefs

from ..config_error import ConfigError
from ..config_field import ConfigField
from ..config_schema import ConfigAccess, ConfigRisk, ConfigSchema
from ..config_value import Bool, Double, Int, Str, ConfigValue


class PrefsBoolField(ConfigField):
    """Convenience wrapper for a SharedPreferences boolean."""

    def __init__(
        self,
        path: str,
        display_name: str,
        description: str,
        prefs: Prefs,
        key: str,
        default_value: bool,
        risk: ConfigRisk = ConfigRisk.NORMAL,
    ) -> None:
        self.path = path
        self.display_name = display_name
        self.description = description
        self._prefs = prefs
        self._key = key
        self._default_value = default_value
        self.value_schema = ConfigSchema.Bool
        self.access = ConfigAccess.READWRITE
        self.risk = risk
        self.revertable = True

    def read(self) -> ConfigValue:
        return Bool(self._prefs.get_boolean(self._key, self._default_value))

    def write(self, value: ConfigValue) -> None:
        self.value_schema.validate(value)
        self._prefs.set_now(self._key, value.value)  # type: ignore[attr-defined]


class PrefsIntField(ConfigField):
    def __init__(
        self,
        path: str,
        display_name: str,
        description: str,
        prefs: Prefs,
        key: str,
        default_value: int,
        min_value: int | None = None,
        max_value: int | None = None,
        risk: ConfigRisk = ConfigRisk.NORMAL,
    ) -> None:
        self.path = path
        self.display_name = display_name
        self.description = description
        self._prefs = prefs
        self._key = key
        self._default_value = default_value
        self.value_schema = ConfigSchema.Int(min=min_value, max=max_value)
        self.access = ConfigAccess.READWRITE
        self.risk = risk
        self.revertable = True

    def read(self) -> ConfigValue:
        return Int(self._prefs.get_int(self._key, self._default_value))

    def write(self, value: ConfigValue) -> None:
        self.value_schema.validate(value)
        self._prefs.set_now(self._key, value.value)  # type: ignore[attr-defined]


class PrefsLongField(ConfigField):
    """Long values are surfaced as Int in the schema (Kotlin's range is wider than the
    bridge's ConfigValue.Int — for the few sync.maxFileSize-style fields we accept
    downcasting; the agent never sees > 2^31 here)."""

    def __init__(
        self,
        path: str,
        display_name: str,
        description: str,
        prefs: Prefs,
        key: str,
        default_value: int,
        min_value: int | None = None,
        max_value: int | None = None,
        risk: ConfigRisk = ConfigRisk.NORMAL,
    ) -> None:
        self.path = path
        self.display_name = display_name
        self.description = description
        self._prefs = prefs
        self._key = key
        self._default_value = default_value
        self.value_schema = ConfigSchema.Int(
            min=min_value if min_value is None else int(min_value),
            max=max_value if max_value is None else int(max_value),
        )
        self.access = ConfigAccess.READWRITE
        self.risk = risk
        self.revertable = True

    def read(self) -> ConfigValue:
        v = self._prefs.get_long(self._key, self._default_value)
        return Int(int(v))

    def write(self, value: ConfigValue) -> None:
        self.value_schema.validate(value)
        self._prefs.set_now(self._key, int(value.value))  # type: ignore[attr-defined]


class PrefsDoubleField(ConfigField):
    """SharedPreferences has no native Double; persist as Float bits via `Float.toRawBits`
    packed into a Long for full precision. Most call sites here use small floats
    (rate / pitch / volume) so the cast is lossless in practice."""

    def __init__(
        self,
        path: str,
        display_name: str,
        description: str,
        prefs: Prefs,
        key: str,
        default_value: float,
        min_value: float | None = None,
        max_value: float | None = None,
        risk: ConfigRisk = ConfigRisk.NORMAL,
    ) -> None:
        self.path = path
        self.display_name = display_name
        self.description = description
        self._prefs = prefs
        self._key = key
        self._default_value = default_value
        self.value_schema = ConfigSchema.Double(min=min_value, max=max_value)
        self.access = ConfigAccess.READWRITE
        self.risk = risk
        self.revertable = True

    def read(self) -> ConfigValue:
        v = self._prefs.get_float(self._key, self._default_value)
        return Double(float(v))

    def write(self, value: ConfigValue) -> None:
        self.value_schema.validate(value)
        d = value.value  # type: ignore[attr-defined]
        if isinstance(d, int):
            d = float(d)
        elif not isinstance(d, float):
            raise ConfigError.TypeMismatch("double")
        self._prefs.set_now(self._key, d)


class PrefsStringField(ConfigField):
    def __init__(
        self,
        path: str,
        display_name: str,
        description: str,
        prefs: Prefs,
        key: str,
        default_value: str,
        max_length: int | None = None,
        regex: str | None = None,
        risk: ConfigRisk = ConfigRisk.NORMAL,
    ) -> None:
        self.path = path
        self.display_name = display_name
        self.description = description
        self._prefs = prefs
        self._key = key
        self._default_value = default_value
        self.value_schema = ConfigSchema.Str(max_length=max_length, regex=regex)
        self.access = ConfigAccess.READWRITE
        self.risk = risk
        self.revertable = True

    def read(self) -> ConfigValue:
        return Str(self._prefs.get_string(self._key, self._default_value) or self._default_value)

    def write(self, value: ConfigValue) -> None:
        self.value_schema.validate(value)
        self._prefs.set_now(self._key, value.value)  # type: ignore[attr-defined]


class PrefsEnumField(ConfigField):
    """String-enum stored as a raw string in SharedPreferences. Schema's StrEnum
    validates before write."""

    def __init__(
        self,
        path: str,
        display_name: str,
        description: str,
        prefs: Prefs,
        key: str,
        cases: list[str],
        default_value: str,
        risk: ConfigRisk = ConfigRisk.NORMAL,
    ) -> None:
        self.path = path
        self.display_name = display_name
        self.description = description
        self._prefs = prefs
        self._key = key
        self._cases = cases
        self._default_value = default_value
        self.value_schema = ConfigSchema.StrEnum(cases=cases)
        self.access = ConfigAccess.READWRITE
        self.risk = risk
        self.revertable = True

    def read(self) -> ConfigValue:
        raw = self._prefs.get_string(self._key, self._default_value)
        return Str(raw if raw in self._cases else self._default_value)

    def write(self, value: ConfigValue) -> None:
        self.value_schema.validate(value)
        self._prefs.set_now(self._key, value.value)  # type: ignore[attr-defined]


class PrefsIntCodedEnumField(ConfigField):
    """Some legacy app preferences store the picker index as Int (e.g. `theme_mode` is
    0=System / 1=Light / 2=Dark) but we want the agent to see clean string tokens.
    Reads map index → case; writes map case → index. Mirrors iOS
    `AppStorageIntCodedEnumField`."""

    def __init__(
        self,
        path: str,
        display_name: str,
        description: str,
        prefs: Prefs,
        key: str,
        cases: list[str],
        default_index: int,
        risk: ConfigRisk = ConfigRisk.NORMAL,
    ) -> None:
        self.path = path
        self.display_name = display_name
        self.description = description
        self._prefs = prefs
        self._key = key
        self._cases = cases
        self._default_index = default_index
        self.value_schema = ConfigSchema.StrEnum(cases=cases)
        self.access = ConfigAccess.READWRITE
        self.risk = risk
        self.revertable = True

    def read(self) -> ConfigValue:
        idx = self._prefs.get_int(self._key, self._default_index)
        safe = idx if 0 <= idx <= len(self._cases) - 1 else self._default_index
        return Str(self._cases[safe])

    def write(self, value: ConfigValue) -> None:
        self.value_schema.validate(value)
        s = value.value  # type: ignore[attr-defined]
        idx = self._cases.index(s)
        if idx < 0:
            raise ConfigError.InvalidValue(f"must be one of: {', '.join(self._cases)}")
        self._prefs.set_now(self._key, idx)
