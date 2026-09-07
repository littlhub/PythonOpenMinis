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
cd web && npm install && npm run dev   # http://localhost:5173 (proxies to :8765)
```

Build the frontend into `web/dist` and FastAPI serves it directly from
`http://127.0.0.1:8765` — no dev server needed.

## Tests

```bash
uv run pytest
```

## Port status

See `PORTING_MAP.md`. Modules are marked `done`, `partial`, or `n-a`
(Android-only, no Python counterpart). The kernel is being ported
module-by-module; UI code splits into Python ViewModels (shared by TUI and
FastAPI) and React components (see `PORTING.md` §6).
