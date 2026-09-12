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
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config.config_error import ConfigError
from ..config.config_registry import ConfigRegistry
from ..config.config_value import ConfigValue
from ..core.context import app_context
from ..core.logging import get_logger, setup_logging
from ..scheduled.runner import ScheduledRunner
from ..soul import SoulStore
from ..data.model import LLMMessage, LLMStreamChunk
from ..data.model.agent_content_part import Text
from ..settings.catalog import MODEL_GROUPS, PROVIDER_TYPES, TOOL_CATALOG
from ..settings.model_capability import (
    SLOT_CAPABILITIES,
    SLOT_LABELS,
    SLOT_ORDER,
    capabilities_catalog,
    capability_label,
    capability_labels,
    label_map,
    pick_slot_model,
)
from ..settings.chat_service import ChatSetupError, attribution_snapshot, build_chat_setup
from ..settings.attachments import build_attachment_context
from ..settings.remote_models import (
    ModelsFetchError,
    default_base_url,
    fetch_remote_models,
)
from ..settings.store import SettingsError, SettingsStore
from ..skills import SkillStore
from . import chat_api, compaction, fs_api, scheduled_api, workspaces, chat_store
from .knowledge_api import router as knowledge_router
from .marketplace_api import router as marketplace_router
from .scheduled_api import router as scheduled_router
from .skills_api import router as skills_router
from .subagents_api import router as subagents_router
from .system_api import router as system_router
from .appearance_api import router as appearance_router
from .upload_api import router as upload_router
from .usage_api import router as usage_router

logger = get_logger(__name__)

def _resolve_web_dist() -> Path:
    """Locate the built frontend (``web/dist``).

    Three possible homes, in priority order:

    1. ``<exe dir>/web/dist`` — lets someone drop in a rebuilt frontend
       without repacking the binary.
    2. ``<_MEIPASS>/web/dist`` — the copy PyInstaller bundled.
    3. ``<checkout>/web/dist`` — running from source.

    The first one that actually holds an ``index.html`` wins. If none does we
    return the checkout path, so the "frontend not built" page names the
    directory the user is expected to build.
    """
    checkout = Path(__file__).resolve().parents[3] / "web" / "dist"
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "web" / "dist")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "web" / "dist")
    candidates.append(checkout)
    for c in candidates:
        if (c / "index.html").is_file():
            return c
    return checkout


WEB_DIST = _resolve_web_dist()


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
    # Scheduled tasks: a poller replaces Android's AlarmManager. Without a
    # persisted store + runner the UI rows were mock data that came back on
    # every refresh.
    runner = ScheduledRunner(execute_fn=scheduled_api.execute_prompt)
    try:
        await runner.start()
    except Exception:  # pragma: no cover - never fail startup over scheduling
        logger.exception("scheduled runner failed to start")
        runner = None
    logger.info("OpenMinis server starting")
    yield
    if runner is not None:
        await runner.stop()
    logger.info("OpenMinis server stopping")


app = FastAPI(
    title="OpenMinis",
    description="Python port of the OpenMinis on-device AI agent.",
    version="1.0.0",
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
app.include_router(subagents_router)
app.include_router(marketplace_router)
app.include_router(scheduled_router)
app.include_router(usage_router)
app.include_router(appearance_router)
app.include_router(upload_router)


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


@app.get("/api/config/")
async def config_list_slash(topic: str | None = None) -> list[dict[str, Any]]:
    """Alias for ``/api/config``.

    Without this the trailing-slash form falls through to
    ``/api/config/{path:path}`` with an empty path and 404s as
    ``unknown_path: `` — which is exactly how the 外观 topic editor broke.
    Registered BEFORE the catch-all below so it wins.
    """
    return await config_list(topic)


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
def _extra_labels(store: SettingsStore) -> dict[str, str]:
    """Label map of built-ins + user custom types (for display strings)."""
    return label_map(store.custom_model_types())


def _provider_models(
    store: SettingsStore, provider_type: str, conf_dict: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Static catalogued models, extended with remote hints fetched from the
    provider's Base URL (``modelHints``), tagged so the UI can tell them
    apart from the built-in list.

    Each entry also carries ``capabilities`` (a list of purpose tags: 对话 /
    识图 / 生图 / 3D / 音频 / 视频 / 生音频 / 生视频, plus any user-defined
    custom types) and ``capabilitySource`` (``auto`` = id 推断, ``user`` =
    用户覆盖) so the settings UI can badge each model and let the user re-tag it.
    """
    conf_dict = conf_dict or {}
    pid = str(conf_dict.get("id") or "")

    def entry(mid: str, name: str) -> dict[str, Any]:
        caps = store.resolve_model_capabilities(pid, mid)
        src = "user" if pid and mid in store.model_types(pid) else "auto"
        return {
            "id": mid,
            "name": name,
            "capabilities": caps,
            "capability": caps[0] if caps else None,
            "capabilityLabels": capability_labels(caps, _extra_labels(store)),
            "capabilitySource": src,
        }

    builtin = MODEL_GROUPS.get(provider_type, [])
    models = [entry(mid, name) for mid, name in builtin]
    known = {mid for mid, _ in builtin}
    for hint in conf_dict.get("modelHints") or []:
        if isinstance(hint, str) and hint and hint not in known:
            models.append(entry(hint, f"{hint}(远程)"))
            known.add(hint)
    # the instance's currently-selected model might be a fully custom id that
    # is neither catalogued nor fetched — surface it so it can be classified
    current = str(conf_dict.get("model") or "").strip()
    if current and current not in known:
        models.append(entry(current, current))
    return models


def _capability_candidates(
    store: SettingsStore,
) -> list[tuple[str, str, list[str]]]:
    """``[(instance_id, model_id, capabilities), …]`` across every configured
    instance — the pool used to auto-suggest a model per purpose slot."""
    out: list[tuple[str, str, list[str]]] = []
    for conf in store.provider_instances():
        iid = str(conf.get("id") or "")
        seen: set[str] = set()
        for mid, _ in MODEL_GROUPS.get(str(conf.get("type") or ""), []):
            if mid in seen:
                continue
            seen.add(mid)
            out.append((iid, mid, store.resolve_model_capabilities(iid, mid)))
        for hint in conf.get("modelHints") or []:
            if isinstance(hint, str) and hint and hint not in seen:
                seen.add(hint)
                out.append((iid, hint, store.resolve_model_capabilities(iid, hint)))
        cur = str(conf.get("model") or "").strip()
        if cur and cur not in seen:
            out.append((iid, cur, store.resolve_model_capabilities(iid, cur)))
    return out


def _slots_view(
    store: SettingsStore, labels: dict[str, str]
) -> list[dict[str, Any]]:
    """Purpose-slot view: what is bound + whether it fits + an auto suggestion."""
    cands = _capability_candidates(store)
    slots: list[dict[str, Any]] = []
    for slot in SLOT_ORDER:
        accepts = list(SLOT_CAPABILITIES[slot])
        entry: dict[str, Any] = {
            "slot": slot,
            "label": SLOT_LABELS[slot],
            "accepts": accepts,
            "acceptsLabel": " 或 ".join(capability_label(c) for c in accepts),
        }
        binding = store.slot_binding(slot)
        if binding is None:
            entry.update({
                "configured": False, "instanceId": None, "instanceLabel": "",
                "model": "", "capabilities": [], "capability": None,
                "capabilityOk": False,
            })
        else:
            conf, model = binding
            iid = str(conf.get("id") or "")
            caps = store.resolve_model_capabilities(iid, model)
            entry.update({
                "configured": True,
                "instanceId": iid,
                "instanceLabel": labels.get(iid, iid),
                "model": model,
                "capabilities": caps,
                "capability": caps[0] if caps else None,
                "capabilityOk": any(c in accepts for c in caps),
            })
        pick = pick_slot_model(cands, slot)
        entry["suggested"] = (
            {"instanceId": pick[0], "model": pick[1]} if pick else None
        )
        slots.append(entry)
    return slots


def _settings_view(store: SettingsStore | None = None) -> dict[str, Any]:
    store = store or SettingsStore.get()
    data = store.load()
    providers: list[dict[str, Any]] = []
    for conf in store.provider_instances():
        ptype = str(conf.get("type") or "")
        meta = next((m for m in PROVIDER_TYPES if m.type == ptype), None)
        label = str(conf.get("label") or "").strip()
        models = _provider_models(store, ptype, conf)
        current = str(conf.get("model") or "").strip()
        pid = str(conf.get("id") or "")
        current_caps = store.resolve_model_capabilities(pid, current) if current else []
        current_src = "user" if (current and current in store.model_types(pid)) else "auto"
        providers.append(
            {
                "id": str(conf.get("id") or ""),
                "type": ptype,
                "typeLabel": meta.label if meta else ptype,
                "label": label or (meta.label if meta else ptype),
                "engine": meta.engine if meta else None,
                "note": meta.note if meta else "",
                "hasKey": bool(conf.get("apiKey")),
                "baseUrl": conf.get("baseUrl", ""),
                "model": current or (meta.default_model if meta else ""),
                "defaultModel": meta.default_model if meta else "",
                "models": models,
                "modelTypes": store.model_types(str(conf.get("id") or "")),
                "modelCapabilities": current_caps if current else [],
                "modelCapability": (current_caps[0] if current_caps and current else None),
                "modelCapabilityLabels": (
                    capability_labels(current_caps, _extra_labels(store))
                    if current else ""
                ),
                "modelCapabilitySource": current_src if current else None,
                "isActive": data.get("activeProviderId") == conf.get("id"),
            }
        )
    labels = {str(p["id"]): str(p["label"]) for p in providers}
    provider_types = [
        {
            "type": m.type,
            "label": m.label,
            "engine": m.engine,
            "note": m.note,
            "defaultModel": m.default_model,
        }
        for m in PROVIDER_TYPES
    ]
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
        "providerTypes": provider_types,
        "identities": identities,
        "toolCatalog": TOOL_CATALOG,
        "agent": store.agent_config(),
        "capabilities": capabilities_catalog(store.custom_model_types()),
        "customModelTypes": store.custom_model_types(),
        "modelSlots": _slots_view(store, labels),
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

    Body: ``{id, type, baseUrl?, apiKey?}`` — ``id`` selects a stored provider
    instance (``type`` is used as a fallback: the first instance of that type).
    Blank baseUrl/apiKey fall back to the stored config (or the vendor default
    for baseUrl). The fetched list is persisted as ``modelHints`` on that
    instance and merged into the GET settings view.
    """
    store = SettingsStore.get()
    instances = store.provider_instances()
    pid = str(payload.get("id") or "").strip()
    ptype = str(payload.get("type") or "").strip()
    conf = None
    if pid:
        conf = next((c for c in instances if c.get("id") == pid), None)
        if conf is None:
            raise HTTPException(status_code=400, detail=f"未知厂商实例: {pid}")
        ptype = str(conf.get("type") or ptype)
    else:
        conf = next((c for c in instances if c.get("type") == ptype), None) or {}
    if not any(m.type == ptype for m in PROVIDER_TYPES):
        raise HTTPException(status_code=400, detail=f"未知厂商: {ptype}")
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
    # persist on the concrete instance (fall back to its type when it has no id)
    store.set_model_hints(conf.get("id") or ptype, result.models)
    logger.info("[fetch-models] %s (%s) ← %s (%d models)", ptype,
                conf.get("id") or ptype, result.source, len(result.models))
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

    # [T-token-attribution-snapshot] Freeze which model is serving THIS turn
    # into the message row, and open a meter for the run. Without the snapshot
    # the Usage page had to join sessions.model_id — one mutable column, so a
    # later model switch silently re-attributed the whole history.
    snapshot = attribution_snapshot(store, conf)
    usage_meter: dict[str, int] = {
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheCreationTokens": 0,
        "cacheReadTokens": 0,
    }

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
            # Web client so the chat can display per-step counts, and summed
            # into ``usage_meter``: one agent turn can make several LLM calls
            # (tool rounds) and every one of them is billed.
            u = chunk.usage
            usage_meter["inputTokens"] += int(u.input_tokens or 0)
            usage_meter["outputTokens"] += int(u.output_tokens or 0)
            usage_meter["cacheCreationTokens"] += int(
                getattr(u, "cache_creation_input_tokens", 0) or 0
            )
            usage_meter["cacheReadTokens"] += int(
                getattr(u, "cache_read_input_tokens", 0) or 0
            )
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
    # 附件（图片）：默认只把**路径**带进上下文，真正的「看懂」交给 read_image /
    # 识图槽；inline 模式才会把字节附上。顺带剥掉历史遗留的内联 base64 —— 它
    # 既会拖死前端输入框排版，也会按图片体积烧 token。
    bundle = await build_attachment_context(store, text)
    messages.append(
        LLMMessage(LLMMessage.Role.USER, bundle.prompt, image_parts=bundle.image_parts or None)
    )
    # persist the user turn before streaming so a crash never loses it
    await chat_store.append_turn(sid, "user", bundle.text, model_label=model_label)

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
                    sid, "assistant", final_text,
                    model_label=model_label,
                    token_usage=(
                        json.dumps(usage_meter) if any(usage_meter.values()) else None
                    ),
                    **snapshot,
                )
        # 到达轮次(默认 30)或接近上下文预算(默认 511998 tokens)时压缩:
        # 顺带把值得长期保留的事实写进记忆日志。压缩失败不影响本轮回答,
        # 所以 maybe_compact 内部吞掉异常。
        _ag = SettingsStore.get().agent_config()
        compacted = await compaction.maybe_compact(
            sid,
            provider,
            threshold=int(_ag.get("maxMemoryRounds") or 30),
            context_limit=int(_ag.get("maxContextTokens") or 0) or None,
        )
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
# 这段**不能**包在 ``if WEB_DIST.exists()`` 里。包住的话,从仓库 clone 下来
# (web/dist 被 .gitignore 排除)启动后,``/`` 压根不会注册,访问首页只会撞上
# FastAPI 默认的 {"detail":"Not Found"} —— 既没说是前端没构建,也没说该怎么
# 构建,一个死胡同。现在路由总是注册,缺产物时给出能照做的指引。
assets_dir = WEB_DIST / "assets"
if assets_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

index = WEB_DIST / "index.html"

_BUILD_HINT_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>OpenMinis — 前端尚未构建</title><style>
body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:#f6f7f9;
     color:#1c1f23;display:flex;align-items:center;justify-content:center;
     min-height:100vh;margin:0}
.card{background:#fff;border:1px solid #e3e6ea;border-radius:12px;
      padding:28px 32px;max-width:580px;box-shadow:0 2px 12px rgba(0,0,0,.05)}
h1{font-size:18px;margin:0 0 6px}
p{font-size:13.5px;line-height:1.75;color:#4a5058;margin:8px 0}
code{background:#f1f3f5;border:1px solid #e3e6ea;border-radius:5px;
     padding:1px 6px;font-size:12.5px}
pre{background:#1c1f23;color:#e8eaed;padding:12px 14px;border-radius:8px;
    overflow-x:auto;font-size:12.5px;line-height:1.6}
</style></head><body><div class="card">
<h1>前端尚未构建</h1>
<p>后端已经起来了,但 <code>web/dist</code> 不存在 —— 它是构建产物,
不随仓库分发(见 <code>.gitignore</code>)。</p>
<p>在仓库的 <code>web</code> 目录下执行:</p>
<pre>npm install
npm run build</pre>
<p>完成后刷新本页即可。首次构建大约需要一分钟。</p>
<p style="color:#8a9099;font-size:12.5px">后端 API 不受影响,
<code>/api/health</code> 现在就是可用的。</p>
</div></body></html>"""


def _frontend_missing() -> HTMLResponse:
    return HTMLResponse(_BUILD_HINT_HTML, status_code=503)


async def _serve_index():  # noqa: ANN201
    if index.exists():
        return FileResponse(index)
    return _frontend_missing()


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
