"""Preferences & secret storage, replacing Android's SharedPreferences.

Ported from: ``android.content.SharedPreferences`` and
``androidx.security.crypto.EncryptedSharedPreferences`` usage in
``com.openminis.app`` (``PrefsFields.kt``, ``MinisConfigPermissionStore.kt``,
``AutoCompactPrefs.kt``).

Mapping:
* ``context.getSharedPreferences(name, MODE_PRIVATE)`` → :class:`Prefs` backed
  by a JSON file under ``AppContext.files_dir``.
* ``EncryptedSharedPreferences`` → :class:`SecretStore`, which keeps values in
  an AES-GCM encrypted file. ``keyring`` is used when available (system
  keychain / DPAPI / Secret Service); if it is unavailable we fall back to a
  machine-local key file so the port still runs in CI and containers.
* ``edit().putString(...).apply()`` → ``set(...)`` (async) / ``set_now(...)``
  (synchronous, mirrors ``commit()``).
* ``registerOnSharedPreferenceChangeListener`` → :meth:`Prefs.changes`
  (async iterator, mirroring the original's Flow-based observers).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import threading
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from .context import app_context
from .flow import MutableSharedFlow

__all__ = ["Prefs", "SecretStore", "get_prefs", "get_secret_store"]


class Prefs:
    """JSON-file ``SharedPreferences`` replacement.

    Thread-safe for the sync path (a lock, like Android's own implementation),
    and async-first for the rest of the port.
    """

    def __init__(self, name: str, directory: Path | None = None) -> None:
        self.name = name
        self._dir = directory if directory is not None else app_context().files_dir
        self._path = self._dir / f"{name}.json"
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}
        self._changes = MutableSharedFlow[tuple[str, Any | None]](replay=0)
        self._load()

    # --- lifecycle ------------------------------------------------------
    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Android would hand back a fresh empty prefs file too.
            self._data = {}

    def _persist(self) -> None:
        """``apply()`` semantics — write through, don't block the caller."""
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(self._path)

    # --- reads ----------------------------------------------------------
    def get_string(self, key: str, default: str | None = None) -> str | None:
        v = self._data.get(key)
        return default if v is None else str(v)

    def get_int(self, key: str, default: int = 0) -> int:
        v = self._data.get(key)
        if v is None:
            return default
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def get_long(self, key: str, default: int = 0) -> int:
        """Kotlin ``getLong`` — Python has no int width distinction."""
        return self.get_int(key, default)

    def get_float(self, key: str, default: float = 0.0) -> float:
        v = self._data.get(key)
        if v is None:
            return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def get_boolean(self, key: str, default: bool = False) -> bool:
        v = self._data.get(key)
        if v is None:
            return default
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() in ("true", "1", "yes")
        return bool(v)

    def get_string_set(self, key: str, default: set[str] | None = None) -> set[str]:
        v = self._data.get(key)
        if not isinstance(v, list):
            return default if default is not None else set()
        return {str(x) for x in v}

    def get_json(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def contains(self, key: str) -> bool:
        return key in self._data

    def get_all(self) -> dict[str, Any]:
        return dict(self._data)

    # --- writes ---------------------------------------------------------
    def set_now(self, key: str, value: Any) -> None:
        """``commit()`` semantics — synchronous."""
        with self._lock:
            self._data[key] = value
            self._persist()
        self._changes.try_emit((key, value))

    async def set(self, key: str, value: Any) -> None:
        """``apply()`` semantics — off the calling coroutine."""
        await asyncio.to_thread(self.set_now, key, value)

    def remove(self, key: str) -> None:
        """Kotlin: ``edit().remove(key).apply()``."""
        with self._lock:
            self._data.pop(key, None)
            self._persist()
        self._changes.try_emit((key, None))

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._persist()
        self._changes.try_emit(("*", None))

    # --- observation ----------------------------------------------------
    def changes(self) -> AsyncIterator[tuple[str, Any | None]]:
        """Kotlin: ``prefs.data.map { it[key] }.distinctUntilChanged()``."""
        return self._changes.collect()

    # --- pythonic sugar -------------------------------------------------
    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.set_now(key, value)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __repr__(self) -> str:
        return f"Prefs({self.name!r}, {len(self._data)} keys)"


class SecretStore:
    """``EncryptedSharedPreferences`` replacement.

    Values are encrypted with AES-256-GCM. The key is taken from the OS
    keychain when ``keyring`` can provide one, otherwise from a
    ``.secret_key`` file (mode 600) in the data dir.
    """

    _SERVICE = "openminis"

    def __init__(self, name: str = "secrets", directory: Path | None = None) -> None:
        from .context import app_context as _ctx  # local import: avoids cycle at import time

        self.name = name
        self._dir = directory if directory is not None else _ctx().files_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / f"{name}.bin"
        self._key = self._resolve_key()
        self._data: dict[str, str] = {}
        self._load()

    # --- key management -------------------------------------------------
    def _resolve_key(self) -> bytes:
        key: str | None = None
        try:
            import keyring  # type: ignore[import-not-found]

            key = keyring.get_password(self._SERVICE, self.name)
            if key is None:
                key = base64.b64encode(os.urandom(32)).decode()
                keyring.set_password(self._SERVICE, self.name, key)
        except Exception:
            # headless CI / container without a keyring backend
            key = None

        if key is None:
            keyfile = self._dir / ".secret_key"
            if keyfile.exists():
                key = keyfile.read_text(encoding="utf-8").strip()
            else:
                key = base64.b64encode(os.urandom(32)).decode()
                keyfile.write_text(key, encoding="utf-8")
                if os.name == "posix":
                    os.chmod(keyfile, 0o600)
        return base64.b64decode(key)

    # --- crypto ---------------------------------------------------------
    def _encrypt(self, plaintext: str) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = os.urandom(12)
        ct = AESGCM(self._key).encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.b64encode(nonce + ct).decode()

    def _decrypt(self, blob: str) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        raw = base64.b64decode(blob)
        return AESGCM(self._key).decrypt(raw[:12], raw[12:], None).decode("utf-8")

    # --- io -------------------------------------------------------------
    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._data = {}

    def _persist(self) -> None:
        tmp = self._path.with_suffix(".bin.tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    # --- api ------------------------------------------------------------
    def get(self, key: str, default: str | None = None) -> str | None:
        blob = self._data.get(key)
        if blob is None:
            return default
        try:
            return self._decrypt(blob)
        except Exception:
            # Corrupt / key-rotated entry — behave like a missing value rather
            # than crashing the whole app on startup.
            return default

    def put(self, key: str, value: str) -> None:
        self._data[key] = self._encrypt(value)
        self._persist()

    def remove(self, key: str) -> None:
        self._data.pop(key, None)
        self._persist()

    def keys(self) -> list[str]:
        return list(self._data)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __getitem__(self, key: str) -> str | None:
        return self.get(key)

    def __setitem__(self, key: str, value: str) -> None:
        self.put(key, value)


_prefs_cache: dict[str, Prefs] = {}
_secret_cache: SecretStore | None = None


def get_prefs(name: str = "minis_prefs") -> Prefs:
    """Kotlin: ``context.getSharedPreferences(name, MODE_PRIVATE)``."""
    if name not in _prefs_cache:
        _prefs_cache[name] = Prefs(name)
    return _prefs_cache[name]


def get_secret_store() -> SecretStore:
    if _secret_cache is None:
        _secret_cache = SecretStore()
    return _secret_cache


# Placeholder to keep linters from flagging the unused import when callbacks
# are added later.
_Callback = Callable[[str, Any | None], None]
