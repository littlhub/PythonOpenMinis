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
    from ..settings.catalog import ENGINE_READY, PROVIDER_TYPES, VALID_TOOLS
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

    cfg["tools"] = _clean_tools(cfg.get("tools"), VALID_TOOLS, sid)
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
    from ..settings.catalog import ENGINE_READY, MODEL_GROUPS, PROVIDER_TYPES, TOOL_CATALOG
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
        "tools": [dict(t) for t in TOOL_CATALOG],
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
    from ..settings.catalog import build_tool_registry, image_caller_scope
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

    runtime = AgentRuntime(tools=inner_tools)  # 内层循环静默，不往外推流
    messages: list[LLMMessage] = [LLMMessage(LLMMessage.Role.USER, task)]
    try:
        # 子代理内部一律按 inline 处理图片：带 read_image 的子代理（识图类）
        # 必须真的拿到像素，否则它也只是拿到一个路径、什么也说不出。它的
        # 产出是**文字**，回到主 agent 上下文时依然只有文字，不会带图。
        with image_caller_scope("inline"):
            _, stop_reason = await runtime.run(
                provider,
                messages,
                session_id=f"{session_id}:sub:{subagent_id}",
                options=AgentRuntimeOptions(
                    system_prompt=persona,
                    max_turns=max(1, min(int(cfg.get("maxRounds", 6)), 12)),
                    image_context_mode="inline",
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
