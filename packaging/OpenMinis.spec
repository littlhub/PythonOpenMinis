# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the OpenMinis web server.

Driven by ``build.bat`` — see the header there. Two shapes:

    onedir (default)   dist/OpenMinis/OpenMinis.exe + _internal/
    onefile            dist/OpenMinis.exe

The shape is chosen with the ``OPENMINIS_ONEFILE=1`` environment variable,
which build.bat sets when you pass ``onefile``.

Entry point is ``app.py`` (the FastAPI + uvicorn launcher). The frontend is
bundled as data rather than compiled in, so the running binary resolves
``web/dist`` through ``server.main._resolve_web_dist``.
"""

import os
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent  # python/
PKG = ROOT / "src" / "openminis"

# ---------------------------------------------------------------------------
# bundled data
# ---------------------------------------------------------------------------
datas: list[tuple[str, str]] = []

# Frontend. ``web/dist`` is a gitignored build artifact, so a fresh checkout
# has none — ship whatever exists and let the server render its "frontend not
# built" page otherwise. Source maps are debug-only and account for most of
# the 22 MB that accumulates there.
web_dist = ROOT / "web" / "dist"
if web_dist.is_dir():
    for p in sorted(web_dist.rglob("*")):
        if p.is_file() and p.suffix != ".map":
            datas.append((str(p), str(Path("web/dist") / p.relative_to(web_dist).parent)))

# Package data: SKILL.md bundles, the knowledge wiki, scheduler notes …
for p in sorted(PKG.rglob("*")):
    if p.is_file() and p.suffix != ".py" and "__pycache__" not in p.parts:
        datas.append((str(p), str(Path("openminis") / p.relative_to(PKG).parent)))

# ---------------------------------------------------------------------------
# hidden imports
# ---------------------------------------------------------------------------
# uvicorn picks its event loop, HTTP protocol and lifespan implementations by
# string name at runtime, so static analysis cannot see them. Without these
# the binary starts and then dies with "no module named uvicorn.loops.auto".
hiddenimports = [
    # SQLAlchemy's `sqlite+aiosqlite://` driver is imported dynamically inside
    # sqlalchemy/dialects/sqlite/aiosqlite.py at engine-creation time, so
    # PyInstaller's static scan never sees it. Without this the frozen binary
    # dies with "ModuleNotFoundError: No module named 'aiosqlite'".
    "aiosqlite",
    # read_image 用 Pillow 做 2000px 缩放 + JPEG 重编码。PIL.Image 是函数内
    # 延迟导入、且图片插件（JpegImagePlugin 等）按需加载 —— 显式列出来，
    # 免得冻结版 read_image 一调用就 "requires Pillow"。
    "PIL",
    "PIL.Image",
    "PIL.ImageFile",
    "PIL.JpegImagePlugin",
    "PIL.PngImagePlugin",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
]

# Nothing in this project needs a GUI toolkit or a numerical stack. Excluding
# them keeps the bundle honest instead of shipping dead weight — and a
# stray import inside a try/except just warns, it does not break anything.
excludes = [
    "tkinter",
    "matplotlib",
    "numpy",
    "pandas",
    "scipy",
    "IPython",
    "pytest",
    "_pytest",
    "pytest_asyncio",
]

onefile = os.environ.get("OPENMINIS_ONEFILE") == "1"

a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

if onefile:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="OpenMinis",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        runtime_tmpdir=None,
        console=True,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="OpenMinis",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=True,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="OpenMinis",
    )
