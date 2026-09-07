"""Hand-rolled and read-only config fields.

Ported from: src/android/app/src/main/java/com/openminis/app/config/fields/ClosureField.kt
Original package: com.openminis.app.config.fields
"""

from __future__ import annotations

from collections.abc import Callable

from ..config_error import ConfigError
from ..config_field import ConfigField
from ..config_schema import ConfigAccess, ConfigRisk, ConfigSchema
from ..config_value import ConfigValue


class ClosureField(ConfigField):
    """Hand-rolled field — use when the storage backend is not a simple SharedPreferences
    key (a repository, a DB row, a singleton). Mirrors iOS `ClosureField`.

    Schema validation runs in the bridge before writer is invoked; the writer may still
    throw ConfigError on extra invariants ("the new primary group must already exist").
    """

    def __init__(
        self,
        path: str,
        display_name: str,
        description: str,
        value_schema: ConfigSchema,
        access: ConfigAccess = ConfigAccess.READWRITE,
        risk: ConfigRisk = ConfigRisk.NORMAL,
        revertable: bool = True,
        reader: Callable[[], ConfigValue] | None = None,
        writer: Callable[[ConfigValue], None] | None = None,
    ) -> None:
        self.path = path
        self.display_name = display_name
        self.description = description
        self.value_schema = value_schema
        self.access = access
        self.risk = risk
        self.revertable = revertable
        self._reader = reader
        self._writer = writer

    def read(self) -> ConfigValue:
        return self._reader()

    def write(self, value: ConfigValue) -> None:
        self.value_schema.validate(value)
        self._writer(value)


class ReadOnlyField(ConfigField):
    """A read-only field. Useful for stats / status surfaces (audit log usage, summary
    aggregates). Any write attempt returns `permission_denied`; the bridge shouldn't even
    reach write since `access = READONLY`, but we throw to be safe."""

    def __init__(
        self,
        path: str,
        display_name: str,
        description: str,
        value_schema: ConfigSchema,
        risk: ConfigRisk = ConfigRisk.NORMAL,
        reader: Callable[[], ConfigValue] | None = None,
    ) -> None:
        self.path = path
        self.display_name = display_name
        self.description = description
        self.value_schema = value_schema
        self.access = ConfigAccess.READONLY
        self.risk = risk
        self.revertable = False
        self._reader = reader

    def read(self) -> ConfigValue:
        return self._reader()

    def write(self, value: ConfigValue) -> None:
        raise ConfigError.PermissionDenied("Read-only")


class HiddenField(ConfigField):
    """A field that exists purely to be hidden. The registry keeps it around so the
    bridge can return a precise `permission_denied` rather than leaking implementation
    details with `unknown_path`."""

    def __init__(
        self,
        path: str,
        display_name: str,
        description: str,
        reason: str,
    ) -> None:
        self.path = path
        self.display_name = display_name
        self.description = description
        self.value_schema = ConfigSchema.Json
        self.access = ConfigAccess.HIDDEN
        self.risk = ConfigRisk.DESTRUCTIVE
        self.revertable = False
        self._reason = reason

    def read(self) -> ConfigValue:
        raise ConfigError.PermissionDenied(self._reason)

    def write(self, value: ConfigValue) -> None:
        raise ConfigError.PermissionDenied(self._reason)
