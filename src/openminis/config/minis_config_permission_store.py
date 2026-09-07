"""Master switch for whether the agent can use minis-config at all.

Ported from: src/android/app/src/main/java/com/openminis/app/config/MinisConfigPermissionStore.kt
Original package: com.openminis.app.config
"""

from __future__ import annotations

from typing import Optional

from openminis.core.flow import MutableStateFlow, StateFlow
from openminis.core.prefs import Prefs, get_prefs

# PORT: `Context.getSharedPreferences(name, MODE_PRIVATE)` → `get_prefs(name)` (JSON file
# under AppContext.files_dir). See openminis.core.prefs mapping in PORTING.md §5.
PREFS = "minis_config_permission"
KEY = "minis_config_enabled"

# Default: true (preserves existing behaviour for upgrade users).
DEFAULT_ENABLED = True


class MinisConfigPermissionStore:
    """Master switch controlling whether the agent can use the `minis-config` CLI at all.

    Disabling short-circuits every CLI call before any field lookup, ConfirmationGate
    enqueue, or audit write. The flag itself is intentionally not settable through
    minis-config: the registry registers a hidden placeholder on the same path so any
    agent attempt to flip the switch returns `permission_denied`.

    PORT: Kotlin `object` → Python module-level singleton (exactly one instance, see
    ``minis_config_permission_store`` at the bottom of this file). Mirrors iOS
    `MinisConfigPermissionStore`.
    """

    _prefs: Optional[Prefs] = None
    _enabled: "MutableStateFlow[bool]" = MutableStateFlow(DEFAULT_ENABLED)

    @classmethod
    def init(cls, context) -> None:
        """Call once early — typically from MinisApp.onCreate. Idempotent."""
        if cls._prefs is not None:
            return
        p = get_prefs(PREFS)
        cls._prefs = p
        cls._enabled.value = p.get_boolean(KEY, DEFAULT_ENABLED)

    @classmethod
    def is_enabled(cls) -> bool:
        """Hot-path read used by the offload bridge before any work."""
        return cls._enabled.value

    @classmethod
    def enabled(cls) -> "StateFlow[bool]":
        """StateFlow mirror — UI/viewmodels can observe the master switch."""
        return cls._enabled

    @classmethod
    def set_enabled(cls, value: bool) -> None:
        """UI toggle. Persists eagerly."""
        if cls._prefs is not None:
            cls._prefs.set_now(KEY, value)
        cls._enabled.value = value


# The single process-wide instance (Kotlin `object` semantics).
minis_config_permission_store = MinisConfigPermissionStore()
