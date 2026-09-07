"""Single source of truth for every configurable setting.

Ported from: src/android/app/src/main/java/com/openminis/app/config/ConfigRegistry.kt
Original package: com.openminis.app.config
"""

from __future__ import annotations

import threading
from typing import Any

from ..core.logging import get_logger
from .config_collection import ConfigCollection
from .config_field import ConfigField
from .config_schema import ConfigAccess

logger = get_logger(__name__)

__all__ = ["ConfigRegistry"]


class ConfigRegistry:
    """Single source of truth for every configurable setting in the app.

    Add a new setting in three steps:

    1. Pick a dot-path id (``appearance.theme``, ``browser.uaProfile``, ...).
    2. Construct a :class:`ConfigField` (or a :class:`ConfigCollection` for
       dynamic children) and register it in ``ConfigBuiltins``.
    3. Done — the offload bridge, confirmation gate, audit log, revert
       flow, and topic-help output all derive from this registry.

    Mirrors iOS ``ConfigRegistry``. The Android side is plain mutable maps
    (no actor isolation needed — handler threads are the writers and the
    registry is initialized once at boot before any reads).

    PORT: Python keeps the same shape but guards the maps with a lock, because
    the FastAPI server can register/serve from multiple threads.
    """

    def __init__(self) -> None:
        self._fields: dict[str, ConfigField] = {}
        self._collections: dict[str, ConfigCollection] = {}
        self._initialized = False
        self._lock = threading.RLock()

    # --- registration ----------------------------------------------------
    def register(self, entry: ConfigField | ConfigCollection) -> None:
        """Kotlin has two overloads (``register(field)`` / ``register(collection)``).

        PORT: Python merges them into one entrypoint that dispatches on type —
        same behaviour, one name to remember.
        """
        with self._lock:
            if isinstance(entry, ConfigField):
                self._fields[entry.path] = entry
            elif isinstance(entry, ConfigCollection):
                self._collections[entry.base_path] = entry
            else:
                raise TypeError(f"cannot register {type(entry).__name__}")

    def register_field(self, field: ConfigField) -> None:
        with self._lock:
            self._fields[field.path] = field

    def register_collection(self, collection: ConfigCollection) -> None:
        with self._lock:
            self._collections[collection.base_path] = collection

    # --- lookup ----------------------------------------------------------
    def resolve_field(self, path: str) -> ConfigField | None:
        """Look up a field by path. Handles both flat fields and collection
        children (``<base>.<id>.<sub>``). Returns None for unknown paths.
        """
        with self._lock:
            field = self._fields.get(path)
            if field is not None:
                return field
            # Collection child lookup: split into [base, id, leaf]. The leaf
            # may itself contain dots (e.g. `models.<uuid>.modality.video`),
            # so cap maxsplit at 2.
            segments = path.split(".", 2)
            if len(segments) != 3:
                return None
            collection = self._collections.get(segments[0])
            if collection is None:
                return None
            return next(
                (f for f in collection.fields(for_id=segments[1]) if f.path == path),
                None,
            )

    def collection(self, base_path: str) -> ConfigCollection | None:
        with self._lock:
            return self._collections.get(base_path)

    def all_visible_field_paths(self) -> list[str]:
        """All registered top-level field paths (excluding hidden), sorted."""
        with self._lock:
            return sorted(
                f.path for f in self._fields.values() if f.access != ConfigAccess.HIDDEN
            )

    def topics(self) -> list[str]:
        """Topic names = unique first segments of every visible path /
        collection base. Sorted.
        """
        with self._lock:
            out: dict[str, None] = {}
            for f in self._fields.values():
                if f.access == ConfigAccess.HIDDEN:
                    continue
                head = f.path.split(".", 1)[0]
                if head:
                    out[head] = None
            for c in self._collections.values():
                out[c.base_path] = None
            return sorted(out)

    def fields(self, topic: str) -> list[ConfigField]:
        """All visible fields whose path equals ``<topic>`` (the bare topic
        name — e.g. an aggregate ``providers`` summary) or starts with
        ``<topic>.``. When ``topic`` matches a registered collection, a
        representative child's fields (using the first child id) are
        also included so ``topic-help <collection>`` surfaces the per-
        child schema instead of an empty list. Mirrors iOS.
        """
        with self._lock:
            out: list[ConfigField] = [
                f
                for f in self._fields.values()
                if f.access != ConfigAccess.HIDDEN
                and (f.path == topic or f.path.startswith(f"{topic}."))
            ]
            collection = self._collections.get(topic)
            if collection is not None:
                first_id = next(iter(collection.child_ids()), None)
                if first_id is not None:
                    out.extend(
                        f
                        for f in collection.fields(for_id=first_id)
                        if f.access != ConfigAccess.HIDDEN
                    )
            return sorted(out, key=lambda f: f.path)

    # --- singleton -------------------------------------------------------
    _instance: ConfigRegistry | None = None
    _class_lock = threading.RLock()

    @classmethod
    def get(cls) -> ConfigRegistry:
        """Process-wide singleton.

        Kotlin raises ``error("ConfigRegistry not initialized; call init() from
        Application.onCreate")`` — same message here for parity.
        """
        if cls._instance is None:
            raise RuntimeError(
                "ConfigRegistry not initialized; call init() from Application.onCreate"
            )
        return cls._instance

    @classmethod
    def init(cls, **dependencies: Any) -> ConfigRegistry:
        """Process-wide singleton. The first caller to invoke :meth:`init`
        wins; subsequent calls are no-ops so idempotent registration is safe.

        PORT: Kotlin's signature takes ``(context, providerRepository,
        envVarRepository, chatRepository)``. Python accepts them as keyword
        arguments and forwards them to ``ConfigBuiltins.register_into``, so the
        port can boot with a partial dependency set.
        """
        if cls._instance is not None:
            return cls._instance
        with cls._class_lock:
            if cls._instance is not None:
                return cls._instance
            registry = cls()
            if not registry._initialized:
                registry._initialized = True
                from .config_builtins import register_into

                register_into(registry, **dependencies)
            cls._instance = registry
            logger.debug("ConfigRegistry initialized with %d fields", len(registry._fields))
            return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        """Test-only escape hatch (no Kotlin counterpart)."""
        cls._instance = None
