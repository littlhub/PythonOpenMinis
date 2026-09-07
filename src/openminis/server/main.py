"""FastAPI application — the Web UI backend.

Ported from: there is no direct Kotlin counterpart. The Android app serves its
UI in-process via Compose and exposes a subset of capabilities to the sandbox
through ``minis-mcp-cli`` (HTTP + stdio transports). This module is the Python
port's equivalent surface: the same kernel, reachable over HTTP and WebSocket
so the React frontend can drive it.

Design rules:

* Every route is a thin adapter over the kernel — no business logic here.
* Streaming (chat, shell) goes over WebSocket; everything else is REST.
* Config writes go through the same field + schema validation the CLI uses.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config.config_error import ConfigError
from ..config.config_registry import ConfigRegistry
from ..config.config_value import ConfigValue
from ..core.context import app_context
from ..core.logging import get_logger, setup_logging
from ..soul import SoulStore
from ..data.model import LLMMessage, LLMStreamChunk
from ..data.model.agent_content_part import Text
from ..settings.catalog import MODEL_GROUPS, PROVIDER_TYPES, TOOL_CATALOG
from ..settings.chat_service import ChatSetupError, build_chat_setup
from ..settings.remote_models import (
    ModelsFetchError,
    default_base_url,
    fetch_remote_models,
)
from ..settings.store import SettingsError, SettingsStore
from ..skills import SkillStore
from . import chat_api, compaction, fs_api, workspaces, chat_store
from .knowledge_api import router as knowledge_router
from .skills_api import router as skills_router
from .system_api import router as system_router

logger = get_logger(__name__)

# file: python/src/openminis/server/main.py -> parents[3] = python/ root
WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    setup_logging()
    app_context().ensure_dirs()
    ConfigRegistry.init()
    # Seed SOUL.md on first launch so the system prompt builder always
    # has something to read; subsequent calls are no-ops.
    SoulStore.ensure_exists()
    # Bundled skills + the generated builtin-tool manifest. Idempotent: an
    # existing (possibly user-edited) bundle is never overwritten.
    try:
        if installed := SkillStore().ensure_installed():
            logger.info("installed builtin skills: %s", ", ".join(installed))
    except Exception:  # pragma: no cover - never fail startup over skills
        logger.exception("skill installation failed")
    logger.info("OpenMinis server starting")
    yield
    logger.info("OpenMinis server stopping")


app = FastAPI(
    title="OpenMinis",
    description="Python port of the OpenMinis on-device AI agent.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_api.router)
app.include_router(workspaces.router)
app.include_router(fs_api.router)
app.include_router(system_router)
app.include_router(skills_router)
app.include_router(knowledge_router)


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health() -> dict[str, Any]:
    ctx = app_context()
    return {
        "status": "ok",
        "data_dir": str(ctx.data_dir),
        "workspace": str(ctx.external_files_dir),
    }


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
def _registry() -> ConfigRegistry:
    try:
        return ConfigRegistry.get()
    except RuntimeError:
        return ConfigRegistry.init()


@app.get("/api/config/topics")
async def config_topics() -> list[str]:
    return _registry().topics()


@app.get("/api/config")
async def config_list(topic: str | None = None) -> list[dict[str, Any]]:
    registry = _registry()
    fields = (
        registry.fields(topic)
        if topic
        else [registry.resolve_field(p) for p in registry.all_visible_field_paths()]
    )
    out: list[dict[str, Any]] = []
    for f in fields:
        if f is None:
            continue
        out.append(
            {
                "path": f.path,
                "displayName": f.display_name,
                "description": f.description,
                "schema": f.value_schema.help_description,
                "access": f.access.value,
                "risk": f.risk.value,
                "scope": f.scope,
                "unavailableReason": f.unavailable_reason,
                "value": json.loads(f.read().json_string()),
            }
        )
    return out


@app.get("/api/config/{path:path}")
async def config_get(path: str) -> Any:
    field = _registry().resolve_field(path)
    if field is None:
        raise HTTPException(status_code=404, detail=f"unknown_path: {path}")
    return json.loads(field.read().json_string())


@app.put("/api/config/{path:path}")
async def config_set(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Body: ``{"value": <json>}``."""
    field = _registry().resolve_field(path)
    if field is None:
        raise HTTPException(status_code=404, detail=f"unknown_path: {path}")
    if "value" not in payload:
        raise HTTPException(status_code=400, detail="invalid_value: missing 'value'")

    parsed = ConfigValue.decode(json.dumps(payload["value"]))
    if parsed is None:
        raise HTTPException(status_code=400, detail="invalid_value: unparseable JSON")
    try:
        field.value_schema.validate(parsed)
    except ConfigError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    old = field.read()
    field.write(parsed)
    return {
        "path": path,
        "oldValue": json.loads(old.json_string()),
        "newValue": json.loads(parsed.json_string()),
    }


# ---------------------------------------------------------------------------
# settings — model providers + role identities (Web 设置 → 模型服务/身份)
# ---------------------------------------------------------------------------
def _provider_models(
    provider_type: str, conf_dict: dict[str, Any] | None
) -> list[dict[str, str]]:
    """Static catalogued models, extended with remote hints fetched from the
    provider's Base URL (``modelHints``), tagged so the UI can tell them
    apart from the built-in list."""
    builtin = MODEL_GROUPS.get(provider_type, [])
    models = [{"id": mid, "name": name} for mid, name in builtin]
    known = {mid for mid, _ in builtin}
    for hint in (conf_dict or {}).get("modelHints") or []:
        if isinstance(hint, str) and hint and hint not in known:
            models.append({"id": hint, "name": f"{hint}(远程)"})
    return models


def _settings_view(store: SettingsStore | None = None) -> dict[str, Any]:
    store = store or SettingsStore.get()
    data = store.load()
    providers: list[dict[str, Any]] = []
    for meta in PROVIDER_TYPES:
        conf = data["providers"].get(meta.type)
        providers.append(
            {
                "type": meta.type,
                "label": meta.label,
                "engine": meta.engine,
                "note": meta.note,
                "hasKey": bool(conf and conf.get("apiKey")),
                "baseUrl": (conf or {}).get("baseUrl", ""),
                "model": ((conf or {}).get("model") or meta.default_model),
                "defaultModel": meta.default_model,
                "models": _provider_models(meta.type, conf),
                "isActive": data.get("activeProviderId") == meta.type,
            }
        )
    identities = [
        {
            "id": i.id,
            "name": i.name,
            "emoji": i.emoji,
            "description": i.description,
            "persona": i.persona,
            "builtin": i.builtin,
            "recommendedTools": i.recommended_tools,
            "enabledTools": i.effective_tools(),
        }
        for i in store.identities_all()
    ]
    return {
        "activeProviderId": data.get("activeProviderId"),
        "activeIdentityId": data.get("activeIdentityId"),
        "providers": providers,
        "identities": identities,
        "toolCatalog": TOOL_CATALOG,
    }


@app.get("/api/settings")
async def settings_get() -> dict[str, Any]:
    try:
        return _settings_view()
    except Exception as e:  # pragma: no cover - defensive
        logger.exception("settings read failed")
        raise HTTPException(status_code=500, detail=str(e)) from None


@app.put("/api/settings")
async def settings_put(payload: dict[str, Any]) -> dict[str, Any]:
    store = SettingsStore.get()
    try:
        store.apply_full(payload)
    except SettingsError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return _settings_view(store)


@app.post("/api/settings/fetch-models")
async def settings_fetch_models(payload: dict[str, Any]) -> dict[str, Any]:
    """Probe a provider's Base URL and return the model ids it advertises.

    Body: ``{type, baseUrl?, apiKey?}`` — blank baseUrl/apiKey fall back to the
    stored config (or the vendor default for baseUrl). The fetched list is
    persisted as ``modelHints`` and merged into the GET settings view.
    """
    store = SettingsStore.get()
    ptype = str(payload.get("type") or "").strip()
    if not any(m.type == ptype for m in PROVIDER_TYPES):
        raise HTTPException(status_code=400, detail=f"未知厂商: {ptype}")
    conf = store.load()["providers"].get(ptype) or {}
    api_key = str(payload.get("apiKey") or "").strip() or conf.get("apiKey", "")
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="需要 API Key:请在卡片中填写,或先在设置里保存过该厂商的 Key。",
        )
    base_url = (
        str(payload.get("baseUrl") or "").strip()
        or conf.get("baseUrl", "")
        or default_base_url(ptype)
    )
    try:
        result = await fetch_remote_models(ptype, base_url, api_key)
    except ModelsFetchError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    store.set_model_hints(ptype, result.models)
    logger.info("[fetch-models] %s ← %s (%d models)", ptype, result.source,
                len(result.models))
    return {"models": result.models, "source": result.source}


# ---------------------------------------------------------------------------
# websocket — chat + shell streaming
# ---------------------------------------------------------------------------
class ConnectionManager:
    """Tracks live WebSocket clients.

    Mirrors the sandbox's HTTP transport re-init behaviour: if a client
    reconnects, the old socket is torn down rather than left dangling.
    """

    def __init__(self) -> None:
        self.active: dict[str, WebSocket] = {}
        self._seq = 0

    async def connect(self, ws: WebSocket) -> str:
        await ws.accept()
        self._seq += 1
        client_id = f"c{self._seq}"
        self.active[client_id] = ws
        logger.debug("ws connect %s (%d active)", client_id, len(self.active))
        return client_id

    def disconnect(self, client_id: str) -> None:
        self.active.pop(client_id, None)
        logger.debug("ws disconnect %s (%d active)", client_id, len(self.active))

    async def send_json(self, client_id: str, payload: dict[str, Any]) -> None:
        ws = self.active.get(client_id)
        if ws is not None:
            await ws.send_json(payload)


manager = ConnectionManager()


async def _safe_send(client_id: str, payload: dict[str, Any]) -> None:
    """send_json that swallows WebSocketDisconnect so chat runs survive a
    mid-stream client drop (e.g. user closes the browser tab)."""
    try:
        await manager.send_json(client_id, payload)
    except WebSocketDisconnect:
        manager.disconnect(client_id)
    except Exception:  # pragma: no cover - defensive
        logger.debug("ws send failed for %s", client_id, exc_info=True)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    """Bidirectional channel.

    Client → server ``{"type": "chat"|"shell"|"ping", ...}``
    Server → client ``{"type": "delta"|"done"|"error", ...}``

    The agent kernel isn't ported yet, so ``chat`` currently echoes through the
    same frame shape the real implementation will use.
    """
    client_id = await manager.connect(ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _safe_send(client_id, {"type": "error", "error": "invalid JSON frame"}
                )
                continue

            msg_type = msg.get("type")
            if msg_type == "ping":
                await _safe_send(client_id, {"type": "pong"})
            elif msg_type == "chat":
                await _handle_chat(client_id, msg)
            elif msg_type == "shell":
                await _handle_shell(client_id, msg)
            else:
                await _safe_send(client_id, {"type": "error", "error": f"unknown type: {msg_type}"}
                )
    except WebSocketDisconnect:
        manager.disconnect(client_id)
    except Exception as e:  # pragma: no cover - defensive
        logger.exception("ws handler crashed")
        manager.disconnect(client_id)
        raise


async def _handle_chat(client_id: str, msg: dict[str, Any]) -> None:
    """Stream an assistant reply through the real agent kernel.

    Chat is organised into persisted sessions (``/api/chats/*``): the frame
    carries ``session_id``; when absent/unknown a session is created on the
    fly and its id is echoed back as a ``chatSession`` frame. User + final
    assistant text are persisted per turn so history survives restarts.
    """
    text = str(msg.get("text", ""))
    if not text.strip():
        await _safe_send(client_id, {"type": "error", "error": "empty message"})
        return

    # -- resolve / create the session ------------------------------------
    session_id = str(msg.get("session_id") or msg.get("sessionId") or "").strip()
    if session_id and await chat_store.get_session(session_id) is not None:
        sid = session_id
    else:
        created = await chat_store.create_session()
        sid = created.id
        await _safe_send(client_id,
            {"type": "chatSession", "sessionId": sid, "title": created.title},
        )

    store = SettingsStore.get()
    try:
        provider, runtime, options, identity, conf = build_chat_setup(store)
    except ChatSetupError as e:
        await _safe_send(client_id, {"type": "delta", "text": str(e)})
        await _safe_send(client_id, {"type": "done", "sessionId": sid})
        return
    except Exception as e:  # pragma: no cover - defensive
        logger.exception("chat setup failed")
        await _safe_send(client_id, {"type": "error", "error": str(e)})
        return

    async def sink(chunk: object) -> None:
        if isinstance(chunk, LLMStreamChunk.Text):
            await _safe_send(client_id, {"type": "delta", "text": chunk.text})
        elif isinstance(chunk, LLMStreamChunk.ToolCallComplete):
            # Frontend renders this as a "tool starting" card.
            await _safe_send(client_id, {
                "type": "toolStart",
                "id": chunk.id,
                "name": chunk.name,
                "input": chunk.args or {},
            })
        elif isinstance(chunk, LLMStreamChunk.ToolResult):
            # Final state of that tool call: success / error + truncated body.
            body = chunk.content or ""
            if len(body) > 4000:
                body = body[:4000] + "\n…(输出过长已截断)"
            await _safe_send(client_id, {
                "type": "toolEnd",
                "id": chunk.id,
                "name": chunk.name,
                "ok": not chunk.is_error,
                "output": body,
            })
        elif isinstance(chunk, LLMStreamChunk.Usage):
            # Token usage of a finished assistant turn — surfaced to the
            # Web client so the chat can display per-step counts.
            u = chunk.usage
            await _safe_send(client_id, {
                "type": "usage",
                "inputTokens": u.input_tokens,
                "outputTokens": u.output_tokens,
                "cacheCreation": getattr(u, "cache_creation_input_tokens", None),
                "cacheRead": getattr(u, "cache_read_input_tokens", None),
            })
        # Thinking/reasoning deltas are not forwarded to the Web UI (v1).

    runtime.chunk_sink = sink  # type: ignore[assignment]
    model_label = (conf.get("model") or "").strip() or None

    messages = await chat_store.load_runtime_history(sid)
    messages.append(LLMMessage(LLMMessage.Role.USER, text))
    # persist the user turn before streaming so a crash never loses it
    await chat_store.append_turn(sid, "user", text, model_label=model_label)

    # Root this session's shell inside its workspace sandbox (filed sessions
    # only; ungrouped sessions keep the global default directory).
    try:
        ws_dir = await workspaces.sandbox_dir_for_session(sid)
    except Exception:  # pragma: no cover - never block a chat over cosmetics
        ws_dir = None
    if ws_dir is not None:
        try:
            from ..tools.shell_execute_tool import get_coordinator

            get_coordinator().set_session_cwd(f"db-{sid}", ws_dir)
        except Exception:  # pragma: no cover
            logger.debug("sandbox dir override failed for %s", sid)

    try:
        await runtime.run(
            provider,
            messages,
            session_id=f"db-{sid}",
            options=options,
        )
        # persist the final assistant text (intermediate tool rounds live only
        # in the in-process transcript cache)
        tail = messages[-1] if messages else None
        if tail is not None and tail.role == LLMMessage.Role.ASSISTANT:
            final_text = "".join(
                p.text for p in (tail.content_parts or []) if isinstance(p, Text)
            )
            if final_text.strip():
                await chat_store.append_turn(
                    sid, "assistant", final_text, model_label=model_label
                )
        # 20 轮一压缩:顺带把值得长期保留的事实写进记忆日志。压缩失败不影响
        # 本轮回答,所以 maybe_compact 内部吞掉异常。
        compacted = await compaction.maybe_compact(sid, provider)
        if compacted is not None and compacted.applied:
            logger.info(
                "auto-compacted %s (%d messages → summary, %d memories)",
                sid, compacted.compacted, len(compacted.memories),
            )
        await _safe_send(client_id, {"type": "done", "sessionId": sid})
    except Exception as e:  # pragma: no cover - defensive
        logger.exception("chat run crashed")
        await _safe_send(client_id, {"type": "error", "error": f"对话出错: {e}"}
        )
    finally:
        close = getattr(provider, "aclose", None)
        if callable(close):
            try:
                await close()
            except Exception:  # pragma: no cover
                pass


async def _handle_shell(client_id: str, msg: dict[str, Any]) -> None:
    """Run a command in the sandbox and stream output.

    PORT: placeholder until ``sandbox`` is ported. Runs locally with a hard
    timeout so the endpoint is safe by default.
    """
    command = str(msg.get("command", ""))
    if not command:
        await _safe_send(client_id, {"type": "error", "error": "empty command"})
        return

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as e:
        await _safe_send(client_id, {"type": "error", "error": str(e)})
        return

    assert proc.stdout is not None
    try:
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=30)
            if not line:
                break
            await _safe_send(client_id, {"type": "delta", "text": line.decode(errors="replace")}
            )
    except TimeoutError:
        proc.kill()
        await _safe_send(client_id, {"type": "error", "error": "timeout"})
        return

    code = await proc.wait()
    await _safe_send(client_id, {"type": "done", "exitCode": code})


# ---------------------------------------------------------------------------
# static frontend (mounted last so /api and /ws win)
# ---------------------------------------------------------------------------
if WEB_DIST.exists():
    assets_dir = WEB_DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    index = WEB_DIST / "index.html"

    async def _serve_index():
        if index.exists():
            return FileResponse(index)
        return JSONResponse({"detail": "frontend not built"}, status_code=404)

    @app.get("/")
    async def spa_index():  # noqa: ANN201
        return await _serve_index()

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):  # noqa: ANN201
        # unknown /api or /ws paths -> JSON 404, everything else -> SPA index
        if full_path.startswith(("api/", "ws")):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return await _serve_index()


def main() -> None:
    """``minis-server`` console script."""
    import uvicorn

    setup_logging()
    uvicorn.run(app, host="127.0.0.1", port=8765)


if __name__ == "__main__":  # pragma: no cover
    main()
