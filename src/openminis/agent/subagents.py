"""Subagent registry + LLM planner.

A subagent is a reusable worker profile for the main agent: a persona, its own
model (provider type + model id), an enabled tool set, a skill list and an MCP
server list. Configs live in ``settings.json`` under ``subagents`` and are
created three ways:

- manually through the UI / REST,
- by the LLM planner (:func:`plan_subagent`) which is handed the live
  **registry** — providers with keys, catalogue models, registered tools,
  installed skills — and a user request ("我要一个写作 subagent") and returns
  a structured candidate config,
- programmatically.

Delegation at runtime lives in ``openminis.tools.subagent_tool``
(``subagent_delegate``); this module only owns the configuration surface.

Note: the engine has no MCP client ported yet, so ``mcpServers`` is stored for
forward compatibility and always comes back empty in the registry.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from ..core.logging import get_logger
from ..data.model import LLMMessage, LLMStreamChunk

logger = get_logger(__name__)

__all__ = [
    "SubagentError",
    "DEFAULT_FIELDS",
    "slugify_name",
    "list_subagents",
    "get_subagent",
    "upsert_subagent",
    "delete_subagent",
    "build_registry",
    "plan_subagent",
    "run_subagent",
    "find_vision_subagent",
    "find_image_subagent",
    "group_block",
]

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

DEFAULT_FIELDS: dict[str, Any] = {
    "emoji": "🤖",
    "description": "",
    "persona": "",
    # providerId = provider instance id (see providers registry); providerType
    # is derived by _validate for compatibility with older configs/clients.
    "providerId": "",
    "providerType": "",
    "model": "",
    "tools": [],
    "skills": [],
    "mcpServers": [],
    "maxRounds": 6,
}


class SubagentError(Exception):
    """Raised for invalid subagent payloads (surfaced as HTTP 400)."""


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def _table(store) -> dict[str, Any]:
    data = store.load()
    sub = data.get("subagents")
    if not isinstance(sub, dict):
        sub = {}
        data["subagents"] = sub
    return sub


def _with_instance_id(store, cfg: dict[str, Any]) -> dict[str, Any]:
    """Backfill ``providerId`` on configs written before multi-vendor.

    Old subagents only carry ``providerType`` (the protocol). The UI needs a
    concrete instance id, so resolve the type to its first instance (legacy
    data keeps ``id == type``, so this is usually a no-op rename).
    """
    out = dict(cfg)
    if str(out.get("providerId") or "").strip():
        return out
    pid = str(out.get("providerType") or "").strip()
    instances = store.provider_instances()
    match = next((c for c in instances if c.get("id") == pid), None)
    if match is None:
        match = next((c for c in instances if c.get("type") == pid), None)
    if match is not None:
        pid = str(match.get("id") or pid)
    out["providerId"] = pid
    return out


def list_subagents(store) -> list[dict[str, Any]]:
    return [_with_instance_id(store, cfg) for cfg in _table(store).values()]


def get_subagent(store, sid: str) -> dict[str, Any] | None:
    cfg = _table(store).get(sid)
    return _with_instance_id(store, cfg) if cfg else None


def slugify_name(name: str) -> str:
    """Turn a Chinese/user-facing name into a slug id ('' when unusable)."""
    base = re.sub(r"[^a-zA-Z0-9]+", "-", (name or "").strip().lower()).strip("-")
    base = re.sub(r"-+", "-", base)[:40]
    if not base:
        base = "subagent"
    if base[0].isdigit():
        base = f"s-{base}"
    return base


def _clean_tools(tools: Any, catalog_ids: set[str], sid: str) -> list[str]:
    if not tools:
        return []
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        raise SubagentError("tools 必须是字符串数组")
    bad = [t for t in tools if t not in catalog_ids]
    if bad:
        raise SubagentError(f"subagent {sid} 包含未知工具: {', '.join(bad)}")
    return list(dict.fromkeys(tools))


def _clean_strings(field: str, value: Any, sid: str, label: str) -> list[str]:
    if not value:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip()
                                              for v in value):
        raise SubagentError(f"{label} 必须是字符串数组")
    return list(dict.fromkeys(v.strip() for v in value))


def _validate(store, cfg: dict[str, Any], existing: bool) -> None:
    sid = str(cfg.get("id") or "").strip()
    if not _SLUG_RE.match(sid):
        raise SubagentError(
            "id 需为小写字母/数字开头，可用 a-z0-9_-（≤40 字符）"
        )
    name = str(cfg.get("name") or "").strip()
    if not name:
        raise SubagentError("name 不能为空")
    if not existing and sid in _table(store):
        raise SubagentError(f"subagent 已存在: {sid}")

    # model service: new configs carry ``providerId`` (a provider *instance*
    # id — several OpenAI-compatible instances may exist). Legacy configs only
    # have ``providerType`` (the wire protocol); resolve it to the first
    # instance of that type so old subagents keep working.
    from ..settings.catalog import ENGINE_READY, PROVIDER_TYPES, known_tool_ids
    from ..settings.store import provider_type_label

    pid = str(cfg.get("providerId") or cfg.get("providerType") or "").strip()
    if not pid:
        raise SubagentError("providerId 不能为空（模型服务实例里选一个）")
    instances = store.provider_instances()
    conf = next((c for c in instances if c.get("id") == pid), None)
    if conf is None:
        conf = next((c for c in instances if c.get("type") == pid), None)
    if conf is None or not str(conf.get("apiKey") or "").strip():
        raise SubagentError(f"模型服务未配置或未填 Key: {pid}")
    ptype = str(conf.get("type") or "")
    engine = next((p.engine for p in PROVIDER_TYPES if p.type == ptype), None)
    if engine not in ENGINE_READY:
        raise SubagentError(
            f"{provider_type_label(ptype)} 的引擎尚未移植，还不能用于 subagent"
        )
    model = str(cfg.get("model") or "").strip()
    if not model:
        raise SubagentError("model 不能为空")
    # normalise: providerId = the concrete instance; providerType kept as the
    # protocol type for older clients / planner output.
    cfg["providerId"] = str(conf.get("id") or pid)
    if ptype:
        cfg["providerType"] = ptype

    cfg["tools"] = _clean_tools(cfg.get("tools"), known_tool_ids(), sid)
    cfg["skills"] = _clean_strings("skills", cfg.get("skills"), sid, "skills")
    cfg["mcpServers"] = _clean_strings("mcpServers", cfg.get("mcpServers"),
                                       sid, "mcpServers")
    try:
        cfg["maxRounds"] = max(1, min(int(cfg.get("maxRounds", 6)), 12))
    except (TypeError, ValueError) as exc:
        raise SubagentError("maxRounds 必须是整数(1-12)") from exc
    cfg["name"] = name
    cfg["emoji"] = str(cfg.get("emoji") or "🤖")[:4]
    cfg["description"] = str(cfg.get("description") or "")[:200]
    cfg["persona"] = str(cfg.get("persona") or "").strip()
    if not cfg["persona"]:
        cfg["persona"] = f"你是「{name}」。基于给定的任务专注、可靠地完成工作，用中文回复。"


def upsert_subagent(store, payload: dict[str, Any], sid: str | None = None) -> dict[str, Any]:
    """Create (sid None or new) or replace an existing subagent config."""
    data = store.load()
    sub = data.setdefault("subagents", {})
    cfg = {
        **DEFAULT_FIELDS,
        **(payload or {}),
    }
    if sid is not None:
        if sid not in sub:
            raise SubagentError(f"subagent 不存在: {sid}")
        cfg["id"] = sid
        _validate(store, cfg, existing=True)
    else:
        _validate(store, cfg, existing=False)
    sub[cfg["id"]] = cfg
    store.save(data)
    return dict(cfg)


def delete_subagent(store, sid: str) -> bool:
    data = store.load()
    sub = data.get("subagents") or {}
    if sid not in sub:
        return False
    del sub[sid]
    store.save(data)
    return True


# ---------------------------------------------------------------------------
# Registry — what the planner (and the UI) can offer a subagent.
# ---------------------------------------------------------------------------
def build_registry(store) -> dict[str, Any]:
    """Live snapshot: providers w/ keys, catalog models, tools, skills, MCP.

    This is exactly what gets embedded in the planner prompt so the main
    agent's LLM only ever picks from things that actually exist.
    """
    from ..settings.catalog import (
        ENGINE_READY,
        MODEL_GROUPS,
        PROVIDER_TYPES,
        all_tool_catalog,
    )
    from ..skills import SkillStore

    data = store.load()
    active_pid = data.get("activeProviderId")

    # providers = configured *instances* (several may share one protocol type)
    providers = []
    for conf in store.provider_instances():
        ptype = str(conf.get("type") or "")
        meta = next((p for p in PROVIDER_TYPES if p.type == ptype), None)
        engine = meta.engine if meta else None
        has_key = bool(str(conf.get("apiKey") or "").strip())
        label = str(conf.get("label") or "").strip() or (
            meta.label if meta else ptype
        )
        cid = str(conf.get("id") or "")
        providers.append({
            "id": cid,
            "type": ptype,
            "label": label,
            "engine": engine,
            "hasKey": has_key,
            "ready": bool(engine in ENGINE_READY),
            "usable": has_key and bool(engine in ENGINE_READY),
            "isActive": cid == active_pid,
            "model": str(conf.get("model") or ""),
        })

    models = []
    for ptype, group in MODEL_GROUPS.items():
        label = next((p.label for p in PROVIDER_TYPES if p.type == ptype), ptype)
        for mid, display in group:
            models.append({"providerType": ptype, "providerLabel": label,
                           "id": mid, "display": display})
    # providers may have fetched extra model ids — surface them too.
    for conf in store.provider_instances():
        ptype = str(conf.get("type") or "")
        for mid in (conf.get("modelHints") or []):
            if not any(m["providerType"] == ptype and m["id"] == mid
                       for m in models):
                models.append({"providerType": ptype,
                               "providerLabel": ptype, "id": mid, "display": mid})

    skills = []
    try:
        for entry in SkillStore().list():
            skills.append({"name": entry.name, "description": entry.description})
    except Exception:  # pragma: no cover - never break registry over skills
        logger.debug("skill list unavailable", exc_info=True)

    return {
        "providers": providers,
        "models": models,
        "tools": all_tool_catalog(),
        "skills": skills,
        "mcpServers": [],
        # human note for the UI / planner about the MCP gap
        "mcpNote": "本引擎暂未移植 MCP 客户端，mcpServers 字段仅作预留",
    }


# ---------------------------------------------------------------------------
# Planner — ask the main agent's LLM to design a subagent from the registry.
# ---------------------------------------------------------------------------
_PLAN_SYSTEM = (
    "你是子代理规划师。根据「可用资源注册表」和用户的自然语言需求，设计一个"
    "subagent 配置。只输出一个 JSON 对象，不要输出任何其它文字。"
)

_PLAN_INSTRUCTIONS = """需求：{request}

可用资源注册表（只许从里面选，不要发明不存在的 id/名称）：
{registry}

输出 JSON（字段与取值约束）：
{{
  "name": "简短中文名，如 写作助手",
  "emoji": "单个 emoji",
  "description": "一句话职责",
  "persona": "系统提示/人设，200 字内，中文，说明身份、工作方式、输出风格",
  "providerId": "从注册表 providers 中选 usable=true 的一个 id（= 模型服务实例）",
  "model": "从注册表 models 中选与 providerId 所属协议匹配的一个 id",
  "tools": ["只选能帮助完成该职责的工具 id，从注册表 tools 的 id 中选，1-6 个"],
  "skills": ["从注册表 skills 的 name 中选相关的，0-4 个"],
  "maxRounds": 6
}}

关于工具选择：写作类不需要 shell_execute/浏览器等执行类工具；
代码类需要 file_read/file_edit/search_files/shell_execute。按职责判断。
"""


def _summarize(provider: Any, text: str, max_tokens: int = 2_000) -> str:
    messages = [LLMMessage(LLMMessage.Role.USER, text)]
    out: list[str] = []
    stream = provider.stream_message(messages, _PLAN_SYSTEM, max_tokens, None,
                                     tools=None)
    async def _collect():
        async for chunk in stream:
            if isinstance(chunk, LLMStreamChunk.Text):
                out.append(chunk.text)
        return "".join(out)
    import asyncio
    return asyncio.run(_collect())


def _extract_json(raw: str) -> dict[str, Any]:
    m = _JSON_FENCE.search(raw)
    if m:
        raw = m.group(1)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise SubagentError("规划结果不是 JSON")
    obj = json.loads(raw[start : end + 1])
    if not isinstance(obj, dict):
        raise SubagentError("规划结果不是对象")
    return obj


async def plan_subagent(store, request: str, *, auto_save: bool = False,
                        provider: Any = None) -> dict[str, Any]:
    """Ask the main agent's LLM to design a subagent for ``request``.

    Returns the candidate config (not yet saved unless ``auto_save``).
    """
    from ..settings.chat_service import build_chat_setup

    request = (request or "").strip()
    if not request:
        raise SubagentError("请描述你想要的 subagent，例如：我要一个写作 subagent")

    registry = build_registry(store)
    if not any(p.get("usable") for p in registry["providers"]):
        raise SubagentError(
            "还没有可用的模型服务：请先在 设置 → 模型服务 配好 API Key"
        )

    owned = provider is None
    if owned:
        provider, _r, _o, _i, _c = build_chat_setup(SettingsStore_get())
    try:
        import asyncio
        raw = await asyncio.to_thread(
            _summarize, provider,
            _PLAN_INSTRUCTIONS.format(
                request=request,
                registry=json.dumps(registry, ensure_ascii=False, indent=1),
            ),
        )
        obj = _extract_json(raw)
    finally:
        if owned:
            close = getattr(provider, "aclose", None)
            if callable(close):
                try:
                    await close()
                except Exception:  # pragma: no cover
                    pass

    # Fill required keys that the model may have omitted.
    for k, v in DEFAULT_FIELDS.items():
        obj.setdefault(k, v)
    name = str(obj.get("name") or request).strip()
    obj["name"] = name
    obj["id"] = str(obj.get("id") or slugify_name(name)).strip()
    obj["providerId"] = str(
        obj.get("providerId") or obj.get("providerType") or ""
    ).strip()
    obj["model"] = str(obj.get("model") or "").strip()
    # Drop fabricated ids: tools/skills must exist in the registry.
    tool_ids = {t["id"] for t in registry["tools"]}
    obj["tools"] = [t for t in (obj.get("tools") or []) if t in tool_ids]
    skill_names = {s["name"] for s in registry["skills"]}
    obj["skills"] = [s for s in (obj.get("skills") or []) if s in skill_names]
    obj["mcpServers"] = []

    if auto_save:
        saved = upsert_subagent(store, obj, sid=None)
        return {"saved": True, "subagent": saved}
    return {"saved": False, "subagent": obj}


def SettingsStore_get():
    from ..settings.store import SettingsStore
    return SettingsStore.get()


# ---------------------------------------------------------------------------
# 群聊成员 —— 用户把子代理「拉进群」后，主代理要认识这些同事。
# ---------------------------------------------------------------------------
def group_block(store, ids: list[str], humans: list[str] | None = None) -> str:
    """把「拉进群的成员」写成一段系统提示。

    用户在聊天页把成员拉进群后，主代理得知道这些同事在场、各自擅长什么，
    才会在合适的时候委派 —— 否则「拉群」只是个摆设。只列**真实存在**的 id
    （前端可能带着陈旧或编造的 id），并把 id 原样写清楚，模型才能直接拿去
    ``subagent_delegate(subagent="…")``。

    ``humans`` 是真人席位（只是名字，不是可委派的子代理）—— 明确告诉模型
    「这些是人、别拿去委派」，否则它可能把「产品经理」也当成一个子代理。
    """
    rows: list[str] = []
    for raw in ids:
        cfg = get_subagent(store, str(raw))
        if cfg is None:
            continue
        bits = [f"- `{cfg.get('id')}`（{cfg.get('name') or cfg.get('id')}"
                f"{cfg.get('emoji') or ''}）"]
        desc = (cfg.get("description") or "").strip()
        if desc:
            bits.append(f"：{desc}")
        rows.append("".join(bits))
    human_rows: list[str] = []
    for raw in humans or []:
        name = str(raw).strip()
        if name:
            human_rows.append(f"- {name}（真人）")
    if not rows and not human_rows:
        return ""
    parts: list[str] = ["\n\n【群聊成员】"]
    if rows:
        parts.append(
            "用户把下面这些子代理拉进了本会话，它们是你可以委派的同事：\n"
            + "\n".join(rows)
            + "\n需要它们的专长时用 `subagent_delegate` 指派（`subagent` 填上面的 id）；"
            "任务是它们自己的活儿、与主线无关时不要硬派。用户的提问若是直接点名某位成员，"
            "就委派给那一位。"
        )
    if human_rows:
        parts.append(
            "\n群里还有真人参与者（**不是子代理、不可委派**，"
            "只是和你一起看这段对话的人）：\n"
            + "\n".join(human_rows)
            + "\n提到他们时按「人」对待，绝不要对这些人名调用 `subagent_delegate`。"
        )
    return "".join(parts)


# ---------------------------------------------------------------------------
# 运行子代理 —— 主 agent 的 subagent_delegate 工具与后端「识图」都走这里。
# ---------------------------------------------------------------------------
async def run_subagent(
    store, subagent_id: str, task: str, session_id: str
) -> str:
    """跑一个子代理直到它给出最终答复，返回它的文本。

    子代理有自己的模型、人设、工具集，且永远拿不到 ``subagent_delegate``
    本身（否则会无限委派）。空产出时返回一句可读的说明而不是抛异常 ——
    调用方（工具 / 识图链路）都要能把「没看到东西」如实告诉用户。
    """
    from ..settings.catalog import build_tool_registry
    from ..settings.chat_service import build_provider
    from ..tools.subagent_tool import SubagentDelegateTool
    from .agent_runtime import AgentRuntime, AgentRuntimeOptions

    cfg = get_subagent(store, subagent_id)
    if cfg is None:
        raise SubagentError(f"subagent 不存在: {subagent_id}")

    # cfg.providerId 指的是模型服务**实例**；老配置只有 providerType（协议），
    # 那就退回该协议下的第一个实例。
    pid = str(cfg.get("providerId") or cfg.get("providerType") or "")
    data = store.load()
    conf = dict(data["providers"].get(pid) or {})
    if not conf:
        conf = next(
            (dict(c) for c in store.provider_instances() if c.get("type") == pid),
            {},
        )
    if not conf:
        raise SubagentError(f"subagent {subagent_id} 的模型服务实例不存在: {pid or '(空)'}")

    conf["model"] = cfg.get("model") or conf.get("model", "")
    provider = build_provider(pid, conf)

    inner_tools = {
        name: t
        for name, t in build_tool_registry(cfg.get("tools") or []).items()
        if name != SubagentDelegateTool.NAME
    }
    persona = cfg.get("persona") or f"你是「{cfg.get('name', subagent_id)}」。用中文回复。"

    # ── 群聊可视化：把内层循环的过程接出去 ─────────────────────────────────
    # 前端把每个子代理当作群聊里的一个「发言人」。没有听众时（测试/CLI/子代理
    # 内部的识图链路）整套事件是空操作，内层循环照旧静默。
    from .subagent_events import active as _events_active
    from .subagent_events import current_tool_use, emit as emit_event, project_for
    from .repeat_guard import looks_like_image_generation

    emit_events = _events_active()
    #: 这次委派挂在哪个外层工具调用下面（前端据此归到那张工具卡）。
    room = current_tool_use().get("id") or f"sub-{subagent_id}-{int(time.time() * 1000)}"

    # 「分配项目」：这个成员被指定了项目 → 它的 shell root 到那个目录，
    # 而不是会话默认的工作空间 —— 群聊里每个成员可以各写各的项目目录。
    project_dir = project_for(subagent_id)
    if project_dir:
        try:
            from pathlib import Path

            from ..tools.shell_execute_tool import get_coordinator

            get_coordinator().set_session_cwd(
                f"{session_id}:sub:{subagent_id}", Path(project_dir)
            )
        except Exception:  # pragma: no cover - 分配失败不该打断委派
            logger.debug("subagent project cwd failed", exc_info=True)

    speaker = {
        "id": room,
        "subagentId": cfg.get("id") or subagent_id,
        "name": cfg.get("name") or subagent_id,
        "emoji": cfg.get("emoji") or "🤖",
        "model": conf.get("model", ""),
        "project": project_dir or "",
    }

    inner_sink = None
    if emit_events:
        #: 子代理自己的生图调用也要能自动预览 —— 与主代理同一条规矩：脚本只打印
        #: 文件名，模型又常忘记写 `![](路径)`，光靠它自觉用户就看不到图。
        sub_args: dict[str, dict] = {}
        sub_started: dict[str, float] = {}

        async def inner_sink(chunk: object) -> None:  # type: ignore[misc]
            if isinstance(chunk, LLMStreamChunk.Text):
                await emit_event({**speaker, "type": "subagentDelta",
                                  "text": chunk.text})
            elif isinstance(chunk, LLMStreamChunk.ToolCallComplete):
                sub_args[chunk.id] = chunk.args or {}
                sub_started[chunk.id] = time.time()
                await emit_event({**speaker, "type": "subagentToolStart",
                                  "callId": chunk.id, "name": chunk.name,
                                  "input": chunk.args or {}})
            elif isinstance(chunk, LLMStreamChunk.ToolResult):
                body = chunk.content or ""
                if len(body) > 4000:
                    body = body[:4000] + "\n…(输出过长已截断)"
                event = {**speaker, "type": "subagentToolEnd",
                         "callId": chunk.id, "name": chunk.name,
                         "ok": not chunk.is_error, "output": body}
                args = sub_args.pop(chunk.id, None)
                started = sub_started.pop(chunk.id, None)
                if not chunk.is_error and looks_like_image_generation(
                    chunk.name, args
                ):
                    # 延迟 import：media_scan 住在 server 包，顶层 import 会造成
                    # agent → server → agent 的循环。
                    from ..server.media_scan import collect_recent_images

                    images = collect_recent_images(since=started)
                    if images:
                        event["images"] = images
                await emit_event(event)

        await emit_event({**speaker, "type": "subagentStart", "task": task})

    # 内层循环：默认静默；有听众时把过程推出去（子代理自己不会再委派 ——
    # ``subagent_delegate`` 已在上面被剔掉）。
    runtime = AgentRuntime(tools=inner_tools, chunk_sink=inner_sink)
    messages: list[LLMMessage] = [LLMMessage(LLMMessage.Role.USER, task)]
    try:
        # 子代理里的 ``read_image`` 与主对话走**同一条路**：识图槽转文字描述。
        # 不强推 inline —— 网关若没给模型声明图片输入，字节会被 provider 换成
        # 「图省略」占位，子代理看到的是 177 字符的路径文本、只能反复重读
        # （实测的死循环）。描述文本足够它推理，产出仍是文字。
        _, stop_reason = await runtime.run(
            provider,
            messages,
            session_id=f"{session_id}:sub:{subagent_id}",
            options=AgentRuntimeOptions(
                system_prompt=persona,
                max_turns=max(1, min(int(cfg.get("maxRounds", 6)), 12)),
            ),
        )
    finally:
        close = getattr(provider, "aclose", None)
        if callable(close):
            try:
                await close()
            except Exception:  # pragma: no cover
                pass

    parts: list[str] = []
    for msg in messages[1:]:
        if msg.role != LLMMessage.Role.ASSISTANT:
            continue
        for part in msg.content_parts:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                parts.append(text)
    body = "\n\n".join(parts).strip()
    if emit_events:
        await emit_event({**speaker, "type": "subagentEnd",
                          "ok": bool(body), "text": body,
                          "stopReason": stop_reason or ""})
    return body or f"(subagent 未产出文字，stop_reason={stop_reason})"


#: 技能名里带这些词 → 明确是「识图」技能，优先级最高。
_VISION_SKILL_HINTS = ("vision", "识图", "看图", "视觉", "ocr")
#: 弱信号：名字里有 image。注意 ``agnes-image`` 之类是**生图**技能，不是识图，
#: 所以只能当备选，不能抢在明确的识图技能前面。
_IMAGE_SKILL_HINTS = ("image", "图像", "图片")


def find_vision_subagent(store) -> str | None:
    """挑一个能「看图」的子代理 id，没有就返回 ``None``。

    判据（从严到宽）：
    1. 带 ``read_image`` 且技能名明确是识图类（vision / 识图 / 看图 / OCR）；
    2. 带 ``read_image`` 且技能名里有 image，或它绑的模型自带视觉能力。

    注意必须带 ``read_image`` —— 没有它，子代理也只是拿到一个路径，看不到像素。
    """
    try:
        from ..settings.model_capability import CAP_VISION
    except Exception:  # pragma: no cover
        CAP_VISION = "vision"

    strong: str | None = None
    weak: str | None = None
    for cfg in list_subagents(store):
        if "read_image" not in (cfg.get("tools") or []):
            continue
        sid = str(cfg.get("id") or "")
        if not sid:
            continue
        skills = [str(s).lower() for s in (cfg.get("skills") or [])]
        if any(h in s for s in skills for h in _VISION_SKILL_HINTS):
            if strong is None:
                strong = sid
            continue
        if weak is not None:
            continue
        if any(h in s for s in skills for h in _IMAGE_SKILL_HINTS):
            weak = sid
        elif _subagent_model_sees(store, cfg, CAP_VISION):
            weak = sid
    return strong or weak


#: 生图技能强信号：名字里明确就是「生图」，优先级最高。
_GENERATE_SKILL_STRONG_HINTS = ("生图", "图像生成", "imagegen", "modelscope", "魔搭")
#: 生图技能弱信号：可能是生图，也可能是看图（image 要往生图方向碰运气）。
_GENERATE_SKILL_WEAK_HINTS = ("image", "图像", "图片", "draw", "t2i")


def find_image_subagent(store) -> str | None:
    """挑一个能「生图」的子代理 id，没有就返回 ``None``。

    按分类匹配，**先窄后宽、逐级扩大**（搜不到才往下一级扩范围）：
    1. 窄 —带 ``image_gen`` 且技能名明确是生图类（生图 / 图像生成 / 魔搭）；
    2. 中 —带 ``image_gen`` 且技能名含 image / 图像 / 图片，或它绑定的模型
           自带生图能力；
    3. 宽 —只要带 ``image_gen`` 工具。
    前一级一个都搜不到才向下一级扩大；搜到即停。
    """
    try:
        from ..settings.model_capability import CAP_IMAGE
    except Exception:  # pragma: no cover
        CAP_IMAGE = "image"

    def _skills(cfg: dict[str, Any]) -> list[str]:
        return [str(s).lower() for s in (cfg.get("skills") or [])]

    def _has_gen_tool(cfg: dict[str, Any]) -> bool:
        return "image_gen" in (cfg.get("tools") or [])

    def _strong(cfg: dict[str, Any]) -> bool:
        return any(h in s for s in _skills(cfg) for h in _GENERATE_SKILL_STRONG_HINTS)

    def _weak(cfg: dict[str, Any]) -> bool:
        return any(h in s for s in _skills(cfg) for h in _GENERATE_SKILL_WEAK_HINTS)

    # 第 1 级（窄）：明确生图技能。
    for cfg in list_subagents(store):
        if _has_gen_tool(cfg) and _strong(cfg):
            return str(cfg.get("id") or "")
    # 第 2 级（中）：技能提示或模型自带生图能力。
    for cfg in list_subagents(store):
        if not _has_gen_tool(cfg):
            continue
        if _weak(cfg) or _subagent_model_sees(store, cfg, CAP_IMAGE):
            return str(cfg.get("id") or "")
    # 第 3 级（宽）：只要带 image_gen 工具。
    for cfg in list_subagents(store):
        if _has_gen_tool(cfg):
            return str(cfg.get("id") or "")
    return None


def _subagent_model_sees(store, cfg: dict[str, Any], cap_vision: str) -> bool:
    """子代理绑定的模型是否自带视觉能力。"""
    pid = str(cfg.get("providerId") or cfg.get("providerType") or "")
    conf = dict(store.load()["providers"].get(pid) or {})
    if not conf:
        conf = next(
            (dict(c) for c in store.provider_instances() if c.get("type") == pid),
            {},
        )
    if not conf:
        return False
    model = str(cfg.get("model") or conf.get("model") or "")
    if not model:
        return False
    try:
        return cap_vision in store.resolve_model_capabilities(
            str(conf.get("id") or pid), model
        )
    except Exception:  # pragma: no cover - settings unreadable
        return False
