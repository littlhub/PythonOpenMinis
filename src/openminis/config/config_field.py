"""Config field interface.

Ported from: src/android/app/src/main/java/com/openminis/app/config/ConfigField.kt
Original package: com.openminis.app.config
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .config_schema import ConfigAccess, ConfigRisk, ConfigSchema
from .config_value import ConfigValue

__all__ = ["ConfigField", "UnavailableField"]


class ConfigField(ABC):
    """One configurable setting. Concrete kinds: ``PrefsBoolField`` etc. for
    SharedPreferences-backed scalars; ``ClosureField`` when the storage backend
    is something more bespoke (a repository, a DB row, an in-process
    singleton). Mirrors iOS ``ConfigField`` (Shared/Config/ConfigField.swift).
    """

    @property
    @abstractmethod
    def path(self) -> str:
        """Dot-path id, e.g. ``appearance.theme`` or ``models.<uuid>.maxOutputTokens``."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable label rendered in the confirm dialog and topic-help."""

    @property
    @abstractmethod
    def description(self) -> str:
        """One-line description rendered in topic-help."""

    @property
    @abstractmethod
    def value_schema(self) -> ConfigSchema: ...

    @property
    @abstractmethod
    def access(self) -> ConfigAccess: ...

    @property
    @abstractmethod
    def risk(self) -> ConfigRisk: ...

    @property
    @abstractmethod
    def revertable(self) -> bool:
        """Whether ``audit-revert`` may roll a write back."""

    @property
    def unavailable_reason(self) -> str | None:
        """[T-android-config-feature-unavailable] Non-null when the field's
        underlying feature does not exist on this device / OS version (e.g. the
        Android 16 "Live Updates" surface on a device without it). The bridge
        short-circuits BOTH reads and writes with a ``feature_unavailable``
        error BEFORE validation and before the confirmation dialog — the
        Settings UI already blocks these entries, and minis-config must not be
        a side door around that gate.

        Default None = available everywhere; only :class:`UnavailableField`
        overrides. Mirrors iOS ``ConfigField.unavailableReason`` (dba46d42).
        """
        return None

    @abstractmethod
    def read(self) -> ConfigValue:
        """Read the current value. May raise :class:`ConfigError` on backend failure."""

    @abstractmethod
    def write(self, value: ConfigValue) -> None:
        """Apply ``value``. Schema validation runs in the bridge before this."""

    @property
    def scope(self) -> str:
        """Top-level scope = first dot segment. Used for audit ``--scope`` filtering."""
        head, _, _ = self.path.partition(".")
        return head or "unknown"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} path={self.path!r}>"


class UnavailableField(ConfigField):
    """[T-android-config-feature-unavailable] Wraps a real field so its path
    stays REGISTERED (help/list keep showing it with correct metadata) while
    every read and write is refused with ``feature_unavailable`` plus ``reason``.

    Keeping the path registered is the point: the agent gets a precise "this
    device can't do that, and retrying won't help" instead of a confusing
    ``unknown_path`` that looks like a typo. Mirrors iOS ``UnavailableField``.

    PORT: Kotlin implements this with class delegation
    (``: ConfigField by delegate``). Python uses ``__getattr__`` forwarding so
    every future property added to ``ConfigField`` keeps working.
    """

    def __init__(self, delegate: ConfigField, reason: str) -> None:
        self._delegate = delegate
        self._reason = reason

    def __getattr__(self, name: str):  # noqa: ANN204
        # Only called when normal lookup fails — i.e. not one of our own attrs,
        # which is exactly the delegation case.
        return getattr(self._delegate, name)

    @property
    def unavailable_reason(self) -> str | None:
        return self._reason

    def read(self) -> ConfigValue:
        from .config_error import FeatureUnavailable

        raise FeatureUnavailable(self._reason)

    def write(self, value: ConfigValue) -> None:
        from .config_error import FeatureUnavailable

        raise FeatureUnavailable(self._reason)
