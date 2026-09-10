# OpenMinis — Python port

Python port of the OpenMinis Android client (Kotlin). Same kernel, three
front-ends: **CLI/TUI**, **FastAPI**, **Web UI**.

```
python/
├── PORTING.md          # Kotlin -> Python translation rules (read this first)
├── PORTING_MAP.md      # original path -> python path -> status
├── src/openminis/      # mirrors com.openminis.app.* one-for-one
│   ├── core/           # Python-only foundations (flow, result, prefs, context)
│   ├── cli/            # Typer CLI
│   ├── tui/            # Textual terminal UI
│   ├── server/         # FastAPI backend
│   └── <module>/       # ported Kotlin modules (config, data, provider, ...)
├── web/                # Vite + React frontend (mirrors the Compose UI)
└── tests/
```

The original Kotlin lives at `../src/android/app/src/main/java/com/openminis/app/`
and is never modified — this directory is a sibling, not a replacement.

## Setup

Managed with [uv](https://docs.astral.sh/uv/):

```bash
uv sync                    # create .venv and install everything
```

**The web UI has to be built once.** `web/dist` is a build artifact and is
not committed (see `.gitignore`), so a fresh clone has no page to serve until
you build it:

```bash
cd web && npm install && npm run build
```

On Windows, `setup.bat` does the same thing in one step (needs Node 18+).
Skipping this is the usual cause of "the server starts but the browser shows
`{"detail":"Not Found"}`" — the backend is fine, it just has no frontend yet.

## Run

```bash
# CLI
uv run minis doctor
uv run minis config list
uv run minis config get appearance.theme
uv run minis config set appearance.theme '"dark"'

# Terminal UI
uv run minis tui

# Server + Web UI
uv run minis serve                     # http://127.0.0.1:8765
```

On Windows you can use `run.bat` (backend + built UI) and `stop.bat` instead;
`run.bat dev` also starts Vite on http://localhost:5173 for hot reload.

Once `web/dist` exists, FastAPI serves it directly from
`http://127.0.0.1:8765` — no dev server needed. If you start the server
before building, `/` now returns a page telling you the command to run
rather than a bare 404.

## Tests

```bash
uv run pytest
```

## Build a Windows executable

```bat
build.bat              rem onedir  -> dist\OpenMinis\OpenMinis.exe
build.bat onefile      rem onefile -> dist\OpenMinis.exe
build.bat clean        rem wipe build\ and dist\
```

Uses the project `.venv` (falling back to a system Python 3.10) and installs
PyInstaller on demand. The recipe lives in `packaging/OpenMinis.spec`; the
version stamped on the binary is read from `src/openminis/__init__.py`.

The web UI is bundled as data, so run `setup.bat` first if you want the exe
to serve a real page — otherwise it starts and shows the "frontend not
built" hint. The running binary resolves `web/dist` from, in order: a
`web\dist` folder next to the exe, the copy inside `_internal`, then the
checkout. Dropping a rebuilt frontend next to the exe therefore takes effect
without repacking.

The port and bind address can be changed from the UI (设置 → 后台运行与服务)
or with `--port` / `--host`; a command-line flag always wins over the saved
setting.

## Port status

See `PORTING_MAP.md`. Modules are marked `done`, `partial`, or `n-a`
(Android-only, no Python counterpart). The kernel is being ported
module-by-module; UI code splits into Python ViewModels (shared by TUI and
FastAPI) and React components (see `PORTING.md` §6).
