"""Dynamically-keyed group of config fields.

Ported from: src/android/app/src/main/java/com/openminis/app/config/ConfigCollection.kt
Original package: com.openminis.app.config
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .config_field import ConfigField
from .config_schema import ConfigRisk, ConfigSchema, JsonSchema
from .config_value import ConfigValue

__all__ = ["ConfigCollection"]


class ConfigCollection(ABC):
    """A dynamically-keyed group of fields, e.g. one ProviderInstance per id,
    one ModelEntry per uuid, one ModelGroup per id. Mirrors iOS
    ``ConfigCollection`` (Shared/Config/Fields/ConfigCollection.swift).

    Static fields go through the registry's flat field map. A collection
    adds two more capabilities:

    1. Enumerate runtime children (:meth:`child_ids`) — used for dynamic path
       resolution like ``models.<uuid>.maxOutputTokens``.
    2. Define ``add`` / ``remove`` semantics — these create or dispose the
       whole child rather than mutate a single field.
    """

    @property
    @abstractmethod
    def base_path(self) -> str:
        """First segment of every member's path, e.g. ``models`` or ``groups``."""

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    def addable(self) -> bool:
        return True

    @property
    def removable(self) -> bool:
        return True

    @property
    def risk(self) -> ConfigRisk:
        """Risk applied to add/remove operations (children declare per-field
        risk). Defaults to :attr:`ConfigRisk.SENSITIVE` because creating /
        destroying structured records is more impactful than tweaking a
        scalar.
        """
        return ConfigRisk.SENSITIVE

    @abstractmethod
    def child_ids(self) -> list[str]:
        """Currently-existing child ids. Order is meaningful where user-visible."""

    @abstractmethod
    def fields(self, for_id: str) -> list[ConfigField]:
        """Field set exposed for one child id. Empty if id missing."""

    @property
    def add_payload_schema(self) -> ConfigSchema:
        """JSON schema accepted by :meth:`add`. ``Json`` allows free-form parsing."""
        return JsonSchema()

    @abstractmethod
    def add(self, payload: ConfigValue) -> str:
        """Create a new child. Returns the new id."""

    @abstractmethod
    def remove(self, child_id: str) -> None:
        """Remove a child. Raises on missing id or non-removable."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} base_path={self.base_path!r}>"
