"""Application paths and context, replacing Android's ``Context``.

Ported from: the ``android.content.Context`` usages scattered through
``com.openminis.app`` (``context.filesDir``, ``context.cacheDir``,
``context.getExternalFilesDir``, ``context.packageName``).

Android hands every component a ``Context`` for filesystem roots, assets and
package metadata. There is no such object in Python, so we keep a single
process-wide :class:`AppContext` that TUI, CLI and the FastAPI server all
share. Keeping it explicit (instead of module-level globals) means tests can
point it at a tmp dir.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["AppContext", "app_context", "set_app_context"]


def _default_data_dir() -> Path:
    """Pick a sane per-user data directory for the current platform."""
    # MINIS_HOME wins — used by tests and by the sandbox test-suite.
    if env := os.environ.get("MINIS_HOME"):
        return Path(env).expanduser()

    if sys.platform == "win32":
        base = Path.home()
        return base / "openminis"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(
            os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
        )
    return base / "openminis"


def _default_cache_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "openminis" / "cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "openminis"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "openminis"


@dataclass(slots=True)
class AppContext:
    """Python stand-in for ``android.content.Context``.

    Attributes mirror the Android directories the original code reaches for:
    ``filesDir`` / ``cacheDir`` / ``getExternalFilesDir(null)`` / ``assets``.
    """

    data_dir: Path = field(default_factory=_default_data_dir)
    cache_dir: Path = field(default_factory=_default_cache_dir)
    # ``None`` = derive from ``data_dir`` (see ``__post_init__``). A plain
    # ``default_factory`` can't do that: it is evaluated before ``__init__``
    # sees an explicitly-passed ``data_dir``, which would pin the workspace to
    # the *default* data dir and break every test that relocates it.
    workspace_dir: Path | None = None
    # Root of the checkout — used to locate the bundled ``default_mount``
    # assets (the Alpine rootfs / MCP CLI that ship inside the APK as assets).
    project_root: Path | None = None
    package_name: str = "com.openminis.app"
    version_name: str = "0.1.0"
    version_code: int = 1
    debug: bool = field(default_factory=lambda: bool(os.environ.get("MINIS_DEBUG")))

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir).expanduser()
        self.cache_dir = Path(self.cache_dir).expanduser()
        # Android's ``getExternalFilesDir(null)`` lives under the app's private
        # storage, so the workspace follows ``data_dir`` (and honours
        # ``MINIS_HOME``). A hard-coded per-user path would leak reads/writes
        # into the real home directory whenever tests or the sandbox relocate
        # ``data_dir``.
        self.workspace_dir = (
            self.data_dir / "workspace"
            if self.workspace_dir is None
            else Path(self.workspace_dir).expanduser()
        )

    # --- Android parity accessors ---------------------------------------
    @property
    def files_dir(self) -> Path:
        """Kotlin: ``context.filesDir``."""
        p = self.data_dir / "files"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def databases_dir(self) -> Path:
        """Kotlin: ``context.getDatabasePath(name).parent``."""
        p = self.data_dir / "databases"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def external_files_dir(self) -> Path:
        """Kotlin: ``context.getExternalFilesDir(null)`` — the workspace root."""
        # Defensive: ``__post_init__`` always fills this in, but a context built
        # via ``dataclasses.replace``/manual mutation could still hand back None.
        return self.workspace_dir or (self.data_dir / "workspace")

    @property
    def assets_dir(self) -> Path:
        """Kotlin: ``context.assets`` — bundled read-only files.

        Resolves to the Android ``src/main/assets`` tree in the checkout so the
        port can load the same ``default_mount`` payload as the app does.
        """
        if self.project_root is None:
            return self.data_dir / "assets"
        return self.project_root / "src" / "android" / "app" / "src" / "main" / "assets"

    def database_path(self, name: str) -> Path:
        """Kotlin: ``context.getDatabasePath(name)``."""
        return self.databases_dir / name

    def ensure_dirs(self) -> None:
        """Create every managed directory. Safe to call repeatedly."""
        for p in (
            self.data_dir,
            self.cache_dir,
            self.files_dir,
            self.databases_dir,
            self.workspace_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)

    def cache_file(self, *parts: str) -> Path:
        p = self.cache_dir.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


_context: AppContext | None = None


def app_context() -> AppContext:
    """Process-wide context (lazily created)."""
    global _context
    if _context is None:
        _context = AppContext()
        _context.ensure_dirs()
    return _context


def set_app_context(ctx: AppContext) -> None:
    """Install a context — tests call this with a tmp path."""
    global _context
    _context = ctx
    ctx.ensure_dirs()
