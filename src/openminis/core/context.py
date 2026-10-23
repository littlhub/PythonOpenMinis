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


def _temp_roots() -> list[Path]:
    """候选临时目录，按优先级。

    刻意不用 ``tempfile.gettempdir()``：它在 ``TEMP`` / ``TMP`` 都没设时会退到
    **当前工作目录**，于是数据就落在 exe 旁边 —— 看起来像「绿色 / U 盘版」，
    其实只是环境变量丢了。这里宁可退到 home 下的隐藏目录，也不碰 cwd。
    """
    out: list[Path] = []
    for name in ("TEMP", "TMP", "TMPDIR"):
        value = os.environ.get(name)
        if value:
            out.append(Path(value).expanduser())
    home = Path.home()
    if sys.platform == "win32":
        out.append(home / "AppData" / "Local" / "Temp")
    else:
        out.append(Path("/tmp"))
    out.append(home / ".openminis-tmp")
    return out


def _pick_temp(fallback_name: str) -> Path | None:
    for root in _temp_roots():
        candidate = root / fallback_name
        if _writable(candidate):
            return candidate
    return None


def _pick_writable(preferred: Path, fallback_name: str) -> Path:
    """优先用常规目录；只有「目录还不存在且建不出来」才退到临时目录。

    关键约束：**已经有数据的目录永远优先**。哪怕当前进程写不进去，也要沿用它
    把原来的数据读出来 —— 换到临时目录等于让用户看到一个空应用，比写失败更糟。
    只有全新安装、又碰上只读 home 时，才临时兜底。
    """
    import logging  # noqa: PLC0415 - 局部导入避免与 .logging 的循环依赖

    log = logging.getLogger("openminis.core.context")

    if preferred.exists():
        if not _writable(preferred):
            log.warning(
                "数据目录 %s 已存在但当前进程写不进去（只读 / 权限 / 沙箱）—— "
                "仍按原路径使用，原有数据照常读取，新增写入会失败；"
                "要恢复写入请修目录权限或用 MINIS_HOME 换位置（会另起一份数据）",
                preferred,
            )
        return preferred

    if _writable(preferred):
        return preferred

    fallback = _pick_temp(fallback_name)
    if fallback is not None:
        log.warning(
            "数据目录 %s 不存在也建不出来（只读配置 / 权限 / 沙箱限制）"
            "—— 本次临时改用 %s，重启后可能被清理；"
            "要长期使用请用 MINIS_HOME 指到一个可写目录",
            preferred,
            fallback,
        )
        return fallback
    log.warning(
        "数据目录 %s 建不出来，也没有可用的临时目录兜底 —— 会继续用原路径，"
        "写入失败的地方会逐条报错",
        preferred,
    )
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
