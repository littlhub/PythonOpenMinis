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

from .. import __version__ as _package_version

__all__ = ["AppContext", "app_context", "set_app_context", "reset_app_context"]


def _writable(path: Path) -> bool:
    """能在 ``path`` 里建目录并写文件吗（只读漫游配置、企业策略、受限沙箱都会拒）。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".openminis-write-test"
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def _pick_writable(preferred: Path, fallback_name: str) -> Path:
    """优先用常规目录；不可写就退到系统临时目录，别让一个只读的 home 卡死启动。

    退到临时目录只是「能跑起来」，重启后数据可能被系统清掉 —— 所以打一条
    warning，并提示用 ``MINIS_HOME`` 指到真正的可写位置。
    """
    if _writable(preferred):
        return preferred
    import logging  # noqa: PLC0415 - 局部导入避免与 .logging 的循环依赖
    import tempfile  # noqa: PLC0415

    fallback = Path(tempfile.gettempdir()) / fallback_name
    if _writable(fallback):
        logging.getLogger("openminis.core.context").warning(
            "当前进程写不进数据目录 %s（只读配置 / 权限 / 沙箱限制都可能导致）"
            "—— 本次改用 %s，重启后可能被清理；若这是正常桌面环境，请检查目录权限，"
            "或用环境变量 MINIS_HOME 指到一个可写目录",
            preferred,
            fallback,
        )
        return fallback
    return preferred


def _default_data_dir() -> Path:
    """Pick a sane per-user data directory for the current platform."""
    # MINIS_HOME wins — used by tests and by the sandbox test-suite.
    if env := os.environ.get("MINIS_HOME"):
        return _pick_writable(Path(env).expanduser(), "openminis")

    if sys.platform == "win32":
        base = Path.home()
        preferred = base / "openminis"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
        preferred = base / "openminis"
    else:
        base = Path(
            os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
        )
        preferred = base / "openminis"
    return _pick_writable(preferred, "openminis")


def _default_cache_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        preferred = base / "openminis" / "cache"
    elif sys.platform == "darwin":
        preferred = Path.home() / "Library" / "Caches" / "openminis"
    else:
        preferred = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "openminis"
    return _pick_writable(preferred, "openminis-cache")


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
    # Sourced from ``openminis.__version__`` so the About page, the FastAPI
    # metadata and the packaged binary can never disagree. ``version_code``
    # mirrors Android's monotonically increasing integer (major*10000 + …).
    version_name: str = _package_version
    version_code: int = 10000
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
        """Create every managed directory. Safe to call repeatedly.

        单个子目录建不出来（只读、被占、盘满）只记 warning，不打断启动：
        ``data_dir`` 已经由 ``_pick_writable`` 保证可写，剩下的失败通常是
        暂时的，后面第一次真正用到时还会再试。
        """
        import logging  # noqa: PLC0415 - 避免与 .logging 循环导入

        log = logging.getLogger("openminis.core.context")
        # 直接拼路径，不走 files_dir / databases_dir 那两个「顺手 mkdir」的
        # property —— 否则它们会在 try 外面抛，等于没兜住。
        targets = [
            self.data_dir,
            self.cache_dir,
            self.data_dir / "files",
            self.data_dir / "databases",
            self.workspace_dir or (self.data_dir / "workspace"),
        ]
        for p in targets:
            try:
                Path(p).mkdir(parents=True, exist_ok=True)
            except OSError as error:
                log.warning("目录建不出来（%s）：%s", p, error)

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


def reset_app_context() -> None:
    """忘掉当前上下文，下次 :func:`app_context` 重新按环境变量推导。

    测试用：``app_context`` 是**进程级单例**，一个测试把它指到 tmp 目录之后，
    后面的测试如果直接读它就会看到别人留下的数据目录（曾经表现为
    「单独跑通、一起跑挂」）。
    """
    global _context
    _context = None
