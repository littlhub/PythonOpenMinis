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

## 语音输入（语音识别）

聊天框的 🎤 是**真的转写**，不是塞一个本地 `blob:` 链接：浏览器录音 → 服务端识别 →
文字回填到输入框。两条引擎，在「设置 → 语音识别」里切：

| 引擎 | 说明 |
|---|---|
| 本地 sherpa-onnx（默认） | 离线，热词多语言中英混合；首次要在设置页点「下载模型」（约 199 MB，落盘到 `<data_dir>/models/`） |
| 模型 API | 任意 OpenAI 兼容的 `/audio/transcriptions`（OpenAI / Groq / 硅基流动 / 百炼 / 自建），需要 Key |

模型下载按顺序试 GitHub Release → `gh-proxy.com` / `ghproxy.net` 加速镜像 →
HuggingFace 镜像（按文件取，199 MB 比 487 MB 的压缩包省一半多）。断流会从断点
续传，同一台机器重来只补缺的文件；装完逐个校验字节数。也可以手动把模型目录放进
`<data_dir>/models/<MODEL_ID>`，或设环境变量 `EKKO_STT_MODEL_DIR`。

服务端路由：`GET /api/speech/status`、`POST /api/speech/transcribe`（multipart
`file`）、`POST|GET /api/speech/model/download`。识别走 `asyncio.to_thread`，
不阻塞事件循环。

## 预编译包

[Releases](https://github.com/littlhub/PythonOpenMinis/releases) 里有 Windows 包
（`OpenMinis-<版本>-win64.zip`）：解压后运行 `OpenMinis\OpenMinis.exe`，浏览器打
开它打印的地址即可，不需要装 Python。当前为 **1.0.4**（含上面的语音输入）。

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
