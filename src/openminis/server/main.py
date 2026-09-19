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
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
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
from ..settings.catalog import MODEL_GROUPS, PROVIDER_TYPES, all_tool_catalog
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
from ..agent.repeat_guard import looks_like_image_generation
from . import chat_api, compaction, fs_api, scheduled_api, workspaces, chat_store
from .media_scan import append_image_refs, collect_recent_images
from .sub_turn_log import SubTurnRecorder
from .knowledge_api import router as knowledge_router
from .guard_api import router as guard_router
from .marketplace_api import router as marketplace_router
from .scheduled_api import router as scheduled_router
from .skills_api import router as skills_router
from .subagents_api import router as subagents_router
from .system_api import router as system_router
from .appearance_api import router as appearance_router
from .plugins_api import router as plugins_router
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
    # 记忆五分类目录 + 旧布局归位（memory/YYYY-MM-DD.md → daily/、
    # GLOBAL.md → long-term/、wiki/ → knowledge/）。幂等，启动时跑一次。
    try:
        from ..tools.memory_tools import ensure_memory_layout

        ensure_memory_layout()
    except Exception:  # pragma: no cover - 启动绝不能因记忆布局失败
        logger.exception("memory layout setup failed")
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
    # 记忆时间兜底整理：后台循环每 6h 醒一次，有比上次整理更新的每日日志、
    # 且距上次 ≥20h 才真正调模型（提问数触发为主，这条兜住「很少提问」的用法）。
    organiser_task: asyncio.Task | None = None
    try:
        from .memory_organizer import auto_organize_loop

        organiser_task = asyncio.create_task(auto_organize_loop())
    except Exception:  # pragma: no cover - never fail startup over memory
        logger.exception("memory organiser failed to start")
    logger.info("OpenMinis server starting")
    # [T-plugins-manager] 上次启用的插件（QQ 机器人这类通道）自动拉起来 ——
    # 否则重启服务之后机器人就悄悄掉线了。
    try:
        from ..plugins import get_runtime

        await get_runtime().start_enabled()
    except Exception:  # pragma: no cover - 插件起不来不该拦住服务
        logger.exception("plugin auto-start failed")
    yield
    if organiser_task is not None:
        organiser_task.cancel()
    if runner is not None:
        await runner.stop()
    try:
        from ..plugins import get_runtime

        await get_runtime().stop_all()
    except Exception:  # pragma: no cover - 关服清理失败不影响退出
        logger.debug("plugin shutdown failed", exc_info=True)
    # Provider 连接池是跨轮复用的（省掉每次 TLS 握手），关服时要显式收掉。
    try:
        from ..settings.chat_service import close_provider_cache

        await close_provider_cache()
    except Exception:  # pragma: no cover - 关服绝不能因清理失败而报错
        logger.debug("provider cache close failed", exc_info=True)
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
app.include_router(plugins_router)
app.include_router(marketplace_router)
app.include_router(scheduled_router)
app.include_router(usage_router)
app.include_router(appearance_router)
app.include_router(upload_router)
app.include_router(guard_router)


#: 访问闸门放行的路径前缀（解锁接口本身 + 静态资源）。
_ACCESS_EXEMPT = ("/api/guard/access",)


@app.middleware("http")
async def _access_gate(request: Request, call_next: Any) -> Any:
    """页面上锁时的兜底：没设密码就完全不拦（默认行为不变）。

    设了「进入密码」且请求没带有效令牌时，``/api/*`` 一律 423 —— 前端会弹
    解锁浮层。静态资源照常放行（不然浮层自己都加载不出来）。
    """
    from ..sandbox.console_auth import ACCESS_COOKIE, access_auth

    path = request.url.path
    # 注意：闸门自身的逻辑**不要**包在宽 except 里 —— 曾经因为 cookie 名导入
    # 失败被静默吞掉，导致「设了密码却完全不拦」。开关本身出错按放行处理
    # （与旧行为一致），但要留下 warning 级别的痕迹。
    try:
        locked = (
            access_auth.has_password()
            and not access_auth.is_unlocked()
            and path.startswith("/api/")
            and not path.startswith(_ACCESS_EXEMPT)
        )
    except Exception:  # pragma: no cover - 闸门故障不该挡住整个站点
        logger.warning("access gate check failed（按放行处理）", exc_info=True)
        return await call_next(request)

    if locked:
        token = request.cookies.get(ACCESS_COOKIE) or request.headers.get(
            "x-minis-access"
        )
        try:
            valid = access_auth.token_valid(token)
        except Exception:  # pragma: no cover
            logger.warning("access token check failed（按放行处理）", exc_info=True)
            valid = True
        if not valid:
            return JSONResponse(
                {"detail": "locked", "locked": True, "error": "页面已锁定：请输入进入密码"},
                status_code=423,
            )
    return await call_next(request)


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
        "toolCatalog": all_tool_catalog(),
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

    def attach(self, client_id: str, sink: Any) -> None:
        """挂一个非 WebSocket 的订阅者（只需要有 ``send_json``）。

        [T-plugins-manager] 机器人通道用它接同一条推流：这样 IM 那边也能有
        流式输出、工具过程、兜底提示，而不是另写一套「跑完再取结果」的旁路。
        """
        self.active[client_id] = sink

    async def send_json(self, client_id: str, payload: dict[str, Any]) -> None:
        ws = self.active.get(client_id)
        if ws is not None:
            await ws.send_json(payload)

    async def broadcast_web(self, payload: dict[str, Any], *, exclude: str = "") -> None:
        """把一帧抄送给所有浏览器客户端。

        为什么需要：一轮对话可能由**机器人通道**发起（``client_id`` 是
        ``bot:…`` 那个虚拟订阅者），此时浏览器只是另一个旁观者 —— 它的帧收不到
        任何东西，只能等回合结束重新拉历史，看上去就是「工具调用没有实时流式
        显示，只在最后一轮结束后才出现」。抄送一份过去，前端本来就是按
        ``sessionId`` 分桶的，不是它关心的会话自然会被忽略。
        """
        for cid, ws in list(self.active.items()):
            if cid == exclude or not str(cid).startswith("c"):
                continue  # 只发给浏览器（``c<n>``），别把机器人订阅者串起来
            try:
                await ws.send_json(payload)
            except Exception:  # pragma: no cover - 个别连接坏了不该影响别人
                logger.debug("broadcast to %s failed", cid, exc_info=True)


manager = ConnectionManager()


async def _safe_send(client_id: str, payload: dict[str, Any]) -> None:
    """send_json that swallows WebSocketDisconnect so chat runs survive a
    mid-stream client drop (e.g. user closes the browser tab).

    出站前过一遍沙箱敏感信息守卫：发给**前端**的文本里若带明文凭据（密码/
    密钥，中英文），换成占位符/部分显示，并记一条可放行的拦截事件。
    """
    try:
        from ..sandbox.guard import sanitize_outbound

        for key in ("text", "output", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                payload[key] = sanitize_outbound(value, where="frontend")
    except Exception:  # pragma: no cover - 脱敏失败不该丢帧
        logger.debug("frontend secret scan failed", exc_info=True)
    try:
        await manager.send_json(client_id, payload)
    except WebSocketDisconnect:
        manager.disconnect(client_id)
    except Exception:  # pragma: no cover - defensive
        logger.debug("ws send failed for %s", client_id, exc_info=True)
    # 这一轮不是浏览器发起的（机器人通道跑的那一条会话）→ 抄送给浏览器，
    # 让它在网页端也能实时看到工具卡与流式文字，而不是等回合结束才刷出来。
    if not str(client_id).startswith("c") and payload.get("sessionId"):
        await manager.broadcast_web(payload, exclude=client_id)


#: 正在跑的对话轮次，key = 会话 id。对话跑在独立 task 里（不阻塞收帧循环），
#: 否则「暂停」帧要等本轮结束才被读到，按钮就形同虚设。
_RUNNING_CHATS: dict[str, asyncio.Task[None]] = {}

#: 即发即忘的后台任务（记忆整理等）。asyncio 只持弱引用，不存一份的话
#: 任务可能在跑完前被 GC 回收。
_BG_TASKS: set[asyncio.Task[Any]] = set()

#: [T-tool-cards-persist-and-fold] 工具卡落库时单条输出的上限。比推给前端的
#: 4000 大一档（展开时能看到更多原文），但也不能无限 —— 一屏几十万字符的
#: shell 输出会把库撑大，而且用户可以随时重跑那条命令。
_TOOL_RUN_OUTPUT_MAX = 8000


def _clip_tool_output(text: str) -> str:
    """工具输出落库前的收口（超出上限只留开头，并说明截断）。"""
    if len(text) <= _TOOL_RUN_OUTPUT_MAX:
        return text
    return (
        f"{text[:_TOOL_RUN_OUTPUT_MAX]}\n"
        f"…(输出过长已截断：共 {len(text)} 字符，此处保留前 {_TOOL_RUN_OUTPUT_MAX})"
    )


async def _persist_sub_turns(sid: str, recorder: SubTurnRecorder) -> None:
    """[T-subagent-log-persist] 把本轮子代理过程落库（在最终答复之前）。

    顺序很关键：这些行要插在「本条提问」之后、「最终答复」之前，回放才自然。
    落库失败绝不影响本轮对话 —— 它们只服务「回看子代理到底干了什么」。
    """
    try:
        for turn in recorder.turns():
            payload = turn.as_payload()
            await chat_store.append_sub_turn(
                sid,
                speaker=payload["speaker"],
                task=payload["task"],
                room_id=payload["roomId"],
                text=payload["text"],
                runs=payload["runs"] or None,
            )
    except Exception:  # pragma: no cover - 只记日志，不能打断对话
        logger.debug("subagent transcript persist failed", exc_info=True)


def _spawn_bg(coro: Any) -> asyncio.Task[Any]:
    """起一个后台任务并保活引用，跑完自动出清。"""
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


async def _handle_stop(client_id: str, msg: dict[str, Any]) -> None:
    """中断某会话正在生成的一轮（前端「暂停」按钮）。"""
    sid = str(msg.get("session_id") or msg.get("sessionId") or "").strip()
    task = _RUNNING_CHATS.get(sid)
    if task is not None and not task.done():
        task.cancel()
        # asyncio.wait 不会把子任务的取消抛给当前协程（`await task` 会）。
        await asyncio.wait({task})
        logger.info("chat stopped by client (%s)", sid)
    await _safe_send(
        client_id, {"type": "done", "sessionId": sid, "stopped": True}
    )


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    """Bidirectional channel.

    Client → server ``{"type": "chat"|"shell"|"ping", ...}``
    Server → client ``{"type": "delta"|"done"|"error", ...}``

    The agent kernel isn't ported yet, so ``chat`` currently echoes through the
    same frame shape the real implementation will use.
    """
    # 访问闸门也要管住 WS：只拦 ``/api/`` 的话，锁屏状态下仍能通过 WS 聊天。
    # 锁定且令牌无效 → 4401 拒绝握手，前端据此回到锁屏。
    from ..sandbox.console_auth import ACCESS_COOKIE, access_auth

    try:
        if access_auth.has_password() and not access_auth.is_unlocked():
            token = ws.cookies.get(ACCESS_COOKIE) or ws.headers.get("x-minis-access")
            if not access_auth.token_valid(token):
                await ws.close(code=4401)
                return
    except Exception:  # pragma: no cover - 闸门故障不该挡住整条通道
        logger.warning("ws access gate failed（按放行处理）", exc_info=True)

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
            elif msg_type == "stop":
                await _handle_stop(client_id, msg)
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
    """把一轮对话丢到后台 task 里跑，让收帧循环继续响应。

    这样「暂停」帧（以及用户排队追加的下一条）能在本轮生成过程中就被处理；
    同一会话已有轮次在跑时直接拒绝（前端负责排队，不在服务端并发）。
    """
    text = str(msg.get("text", ""))
    if not text.strip():
        await _safe_send(client_id, {"type": "error", "error": "empty message"})
        return
    sid = str(msg.get("session_id") or msg.get("sessionId") or "").strip()
    running = _RUNNING_CHATS.get(sid)
    if sid and running is not None and not running.done():
        await _safe_send(
            client_id, {"type": "error", "error": "上一条还在处理中"}
        )
        return
    task = asyncio.create_task(_run_chat(client_id, msg))
    if sid:
        _RUNNING_CHATS[sid] = task
        task.add_done_callback(lambda _t, key=sid: _RUNNING_CHATS.pop(key, None))


async def _auto_organize_after_chat(every: int) -> None:
    """提问数到点后的记忆整理：放后台跑，结果只进日志，失败留给下轮重试。"""
    try:
        from .memory_organizer import run_message_organize_if_due

        result = await run_message_organize_if_due(every)
    except Exception:  # pragma: no cover - 后台任务绝不能炸
        logger.debug("auto organise after chat failed", exc_info=True)
        return
    if result is not None and result.applied:
        logger.info("memory organised after chat: %s", result.kinds)


async def _run_chat(client_id: str, msg: dict[str, Any]) -> None:
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

    def _on_fallback(from_label: str, to_label: str, reason: str) -> None:
        """主模型限流/超时切到兜底模型时通知前端挂一条提示。"""
        _spawn_bg(
            _safe_send(
                client_id,
                {
                    "type": "fallback",
                    "fromModel": from_label,
                    "toModel": to_label,
                    "reason": reason,
                    "sessionId": sid,
                },
            )
        )

    # 「由哪个 agent 接待」：通道插件（QQ 等）把选定的人设带上来 —— 机器人的
    # 对话不该跟着网页端「当前身份」变脸。网页端不传，照旧用当前身份。
    identity_id = str(msg.get("identityId") or msg.get("agentId") or "").strip()

    try:
        provider, runtime, options, identity, conf = build_chat_setup(
            store,
            session_id=sid,
            identity_id=identity_id or None,
            on_fallback=_on_fallback,
        )
    except ChatSetupError as e:
        await _safe_send(client_id, {"type": "delta", "text": str(e)})
        await _safe_send(client_id, {"type": "done", "sessionId": sid})
        return
    except Exception as e:  # pragma: no cover - defensive
        logger.exception("chat setup failed")
        await _safe_send(client_id, {"type": "error", "error": str(e)})
        return

    # 「拉进群」的成员：前端把成员对象（``{id, kind, name}``）随消息带上来，这里
    # 把它们写进系统提示，主代理才知道群里有这些同事、可以委派给谁（否则拉了群
    # 也是摆设）。``kind == "human"`` 的只是真人席位 —— 标注清楚「是人、别委派」。
    # 兼容老格式（纯 id 字符串数组，那时进群的都是子代理）。
    raw_parts = msg.get("participants") or []
    sub_ids: list[str] = []
    human_names: list[str] = []
    for raw in raw_parts:
        if isinstance(raw, str):
            pid = raw.strip()
            if pid:
                sub_ids.append(pid)
        elif isinstance(raw, dict):
            pid = str(raw.get("id") or "").strip()
            if not pid:
                continue
            if str(raw.get("kind") or "agent") == "human":
                human_names.append(str(raw.get("name") or pid).strip() or pid)
            else:
                sub_ids.append(pid)
    if sub_ids or human_names:
        try:
            from ..agent.subagents import group_block

            block = group_block(store, sub_ids, human_names)
            if block:
                options.system_prompt = (options.system_prompt or "") + block
        except Exception:  # pragma: no cover - 群成员只影响提示，不该打断对话
            logger.debug("group block build failed", exc_info=True)

    # 「分配项目」：前端把「成员 id → 工作空间 id」带上来，这里解析成真实目录，
    # 交给子代理的 shell 当工作目录（每个成员可以各写各的项目）。
    member_projects = msg.get("memberProjects") or {}
    if isinstance(member_projects, dict) and member_projects:
        try:
            from ..agent.subagent_events import set_projects

            resolved: dict[str, str] = {}
            for member_id, folder_id in member_projects.items():
                if not folder_id:
                    continue
                path = await workspaces.dir_for_workspace(
                    str(folder_id), suffix=f"sub-{member_id}"
                )
                if path is not None:
                    resolved[str(member_id)] = str(path)
            if resolved:
                set_projects(resolved)
        except Exception:  # pragma: no cover - 分配失败退回默认目录
            logger.debug("member projects resolve failed", exc_info=True)

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

    #: 生图自动预览用：toolStart 记下参数与发起时刻，toolEnd 时据此判断
    #: 「这是一次生图调用」并扫出本次新落盘的图片。
    pending_tool_args: dict[str, dict] = {}
    pending_tool_started: dict[str, float] = {}
    #: 本轮自动生成的图片（绝对路径，去重），落库时补进回复正文。
    generated_images: list[str] = []
    #: [T-tool-cards-persist-and-fold] 本回合的工具调用记录（按调用先后有序）。
    #: 随助手回合一起落库 —— 界面上的工具卡因此刷新、切会话、重启后端之后
    #: 依然在、依然能展开看原文；而它们**不会进模型上下文**（那条路只读 text
    #: part，见 ``chat_store.parts_to_text``）。
    tool_runs: dict[str, dict[str, Any]] = {}

    async def sink(chunk: object) -> None:
        if isinstance(chunk, LLMStreamChunk.Text):
            await _safe_send(
                client_id, {"type": "delta", "text": chunk.text, "sessionId": sid}
            )
        elif isinstance(chunk, LLMStreamChunk.ToolCallComplete):
            # Frontend renders this as a "tool starting" card.
            # 记下参数与开始时间：toolEnd 时要判断这是不是一次生图调用，
            # 以及「本次调用新落盘的图」是哪几张（自动预览用）。
            pending_tool_args[chunk.id] = chunk.args or {}
            pending_tool_started[chunk.id] = time.time()
            tool_runs[chunk.id] = {
                "id": chunk.id,
                "name": chunk.name,
                "input": chunk.args or {},
            }
            await _safe_send(client_id, {
                "type": "toolStart",
                "id": chunk.id,
                "name": chunk.name,
                "input": chunk.args or {},
                "sessionId": sid,
            })
        elif isinstance(chunk, LLMStreamChunk.ToolResult):
            # Final state of that tool call: success / error + truncated body.
            raw = chunk.content or ""
            body = raw
            if len(body) > 4000:
                body = body[:4000] + "\n…(输出过长已截断)"
            frame: dict[str, Any] = {
                "type": "toolEnd",
                "id": chunk.id,
                "name": chunk.name,
                "ok": not chunk.is_error,
                "output": body,
            }
            elapsed_ms = None
            _started = pending_tool_started.get(chunk.id)
            if _started is not None:
                elapsed_ms = int((time.time() - _started) * 1000)
                frame["ms"] = elapsed_ms
            # 落库那份（工具卡展开时看的）：上限比线上帧宽一档。
            run = tool_runs.get(chunk.id)
            if run is not None:
                run["ok"] = not chunk.is_error
                run["output"] = _clip_tool_output(raw)
                if elapsed_ms is not None:
                    run["ms"] = elapsed_ms
            # 生图调用成功 → 顺手把本次新产生的图片路径带上，前端自动预览。
            # 生图脚本只打印文件名，模型又常常忘记写 `![](路径)` —— 前端拿不到
            # 可渲染路径时用户「图生成了但看不到」。
            args = pending_tool_args.pop(chunk.id, None)
            started = pending_tool_started.pop(chunk.id, None)
            if not chunk.is_error and looks_like_image_generation(chunk.name, args):
                images = collect_recent_images(since=started)
                if images:
                    frame["images"] = images
                    for path in images:
                        if path not in generated_images:
                            generated_images.append(path)
            await _safe_send(client_id, frame)
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

    # 后台预热这个会话的 shell：首条命令要连 bash 启动一起等（实测 ~1.1s），而带
    # 工具的回合动辄十几条命令 —— 轮次刚开始就叫起来，第一刀不白等。没入组的会话
    # 同样受益（它的 shell 落在工作区默认目录里）。
    try:
        from ..tools.shell_execute_tool import get_coordinator

        _spawn_bg(get_coordinator().warm(f"db-{sid}"))
    except Exception:  # pragma: no cover - 预热失败不该影响对话
        logger.debug("shell warm-up dispatch failed for %s", sid)

    try:
        # 群聊可视化：把「子代理活动」接到同一条推流上。装在这里（而不是 sink
        # 里）是因为 agent 派生的工具任务会继承**当前任务**的 contextvars ——
        # 于是 subagents.run_subagent 不用改签名就能把过程推出去。
        from ..agent.subagent_events import reset_emitter, set_emitter

        #: [T-subagent-log-persist] 顺路把子代理过程攒下来落库 —— 原先它只活在
        #: 推流帧里，刷新页面、重连后重拉历史、重启后端之后整段消失（连它调过的
        #: 那堆工具卡一起）。开着子代理时，绝大部分工具调用其实发生在这里，
        #: 所以「工具卡切窗口就不见了」的根因就在这一段。
        sub_turns = SubTurnRecorder()

        async def _on_agent_event(ev: dict[str, Any]) -> None:
            sub_turns.on_event(ev)
            await _safe_send(client_id, ev)

        emitter_token = set_emitter(_on_agent_event)
        # 交付台账按轮归零：`send` 靠它做「同一轮同一文件只交付一次」，以及
        # 「你交付的是较旧那张」的提醒（模型会抓错历史里的 `![生成图](…)`）。
        from ..tools import send_tool

        send_tool.begin_turn(f"db-{sid}")
        try:
            await runtime.run(
                provider,
                messages,
                session_id=f"db-{sid}",
                options=options,
            )
        finally:
            send_tool.end_turn(f"db-{sid}")
            reset_emitter(emitter_token)
            await _persist_sub_turns(sid, sub_turns)
        # persist the final assistant text (intermediate tool rounds live only
        # in the in-process transcript cache)
        tail = messages[-1] if messages else None
        if tail is not None and tail.role == LLMMessage.Role.ASSISTANT:
            final_text = "".join(
                p.text for p in (tail.content_parts or []) if isinstance(p, Text)
            )
            # 生图自动交付：模型忘了写 `![](路径)` 时补上，保证刷新/切换会话后
            # 图片依然能渲染（即时那份由前端 toolEnd 帧的自动预览负责）。
            final_text = append_image_refs(final_text, generated_images)
            runs = list(tool_runs.values())
            # 没有正文但有工具调用（模型只调工具就收工）也要落库：否则那一回合
            # 连同它的一堆工具卡一起消失，用户回头无从回看。空正文的行在重建
            # 上下文时被跳过（``load_runtime_history`` 只收有文字的），所以这
            # 不会往模型上下文里塞空气。
            if final_text.strip() or runs:
                await chat_store.append_turn(
                    sid, "assistant", final_text,
                    runs=runs or None,
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
        # 记忆自动整理（按提问数）：一问计一条，攒够 agent.memoryOrganizeEvery
        # 条就在后台蒸馏每日记忆进四类长期记忆 —— 不挡已完成的回合，绝不抛错。
        try:
            from .memory_organizer import (
                message_organize_due,
                note_user_message,
            )

            every = int(_ag.get("memoryOrganizeEvery") or 0)
            note_user_message()
            if every > 0 and message_organize_due(every):
                _spawn_bg(_auto_organize_after_chat(every))
        except Exception:  # pragma: no cover - 整理计数绝不能影响收尾
            logger.debug("memory organise trigger failed", exc_info=True)
    except Exception as e:  # pragma: no cover - defensive
        logger.exception("chat run crashed")
        await _safe_send(
            client_id,
            {"type": "error", "error": f"对话出错: {e}", "sessionId": sid},
        )
    finally:
        # 「分配项目」是**本轮**的，收工必须清掉 —— 否则同一个上下文里跑下一轮
        # 时会带着上一轮的项目目录（contextvar 只保证跨任务隔离，不保证跨轮）。
        try:
            from ..agent.subagent_events import set_projects

            set_projects(None)
        except Exception:  # pragma: no cover - 清理失败不该影响收尾
            logger.debug("member projects clear failed", exc_info=True)
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

    设了控制台密码且未解锁时直接拒绝 —— 前端「沙箱」页要先输密码解锁。
    """
    command = str(msg.get("command", ""))
    if not command:
        await _safe_send(client_id, {"type": "error", "error": "empty command"})
        return

    try:
        from ..sandbox.console_auth import console_auth

        if console_auth.has_password() and not console_auth.is_unlocked():
            await _safe_send(client_id, {
                "type": "error",
                "error": "控制台已锁定：请先在沙箱页输入控制台密码解锁",
                "locked": True,
            })
            return
    except Exception:  # pragma: no cover - 闸门故障时按放行处理（与旧行为一致）
        logger.debug("console auth check failed", exc_info=True)

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
