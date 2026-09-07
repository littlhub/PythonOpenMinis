"""Config operation failure modes.

Ported from: src/android/app/src/main/java/com/openminis/app/config/ConfigError.kt
Original package: com.openminis.app.config
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "ConfigError",
    "TypeMismatch",
    "InvalidValue",
    "OutOfRange",
    "RegexMismatch",
    "PermissionDenied",
    "UnknownPath",
    "AlreadyExists",
    "IoError",
    "FeatureUnavailable",
]


class ConfigError(Exception):
    """Failure modes a ``ConfigField`` operation may surface. Matches iOS
    ``ConfigError``. Bridge-level error codes:

    ::

        TypeMismatch / InvalidValue / OutOfRange / RegexMismatch  -> 1
        PermissionDenied                                          -> 126
        UnknownPath                                               -> 1
        IoError                                                   -> 1
    """

    #: Exit-code style bridge code, used by the ``minis-config`` CLI bridge.
    code: int = 1

    def __init__(self, message: str, *args: Any) -> None:
        super().__init__(message, *args)
        self.message = message


class TypeMismatch(ConfigError):
    def __init__(self, expected: str) -> None:
        super().__init__(f"type_mismatch: expected {expected}")
        self.expected = expected


class InvalidValue(ConfigError):
    def __init__(self, msg: str) -> None:
        super().__init__(f"invalid_value: {msg}")


class OutOfRange(ConfigError):
    def __init__(self, min_value: float | None, max_value: float | None) -> None:
        parts: list[str] = []
        if min_value is not None:
            parts.append(f"min={min_value}")
        if max_value is not None:
            parts.append(f"max={max_value}")
        super().__init__("out_of_range: " + ", ".join(parts))
        self.min_value = min_value
        self.max_value = max_value


class RegexMismatch(ConfigError):
    def __init__(self, pattern: str) -> None:
        super().__init__(f"regex_mismatch: must match /{pattern}/")
        self.pattern = pattern


class PermissionDenied(ConfigError):
    code = 126

    def __init__(self, reason: str = "Hidden from minis-config") -> None:
        super().__init__(f"permission_denied: {reason}")
        self.reason = reason


class UnknownPath(ConfigError):
    def __init__(self, path: str) -> None:
        super().__init__(f"unknown_path: {path}")
        self.path = path


class AlreadyExists(ConfigError):
    """Raised by collection.add when a child with the same natural key already exists."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"already_exists: {reason}")


class IoError(ConfigError):
    def __init__(self, msg: str) -> None:
        super().__init__(f"io_error: {msg}")


class FeatureUnavailable(ConfigError):
    """[T-android-config-feature-unavailable] Raised when a field's underlying
    feature does not exist on this device / platform.

    The Kotlin side reuses ``ConfigError`` + a separate bridge code for this;
    Python gets its own subclass so callers can catch it precisely.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"feature_unavailable: {reason}")
        self.reason = reason
