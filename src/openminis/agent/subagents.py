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
import os
import re
import time
from typing import Any

from ..core.logging import get_logger
from ..data.model import LLMMessage, LLMStreamChunk

logger = get_logger(__name__)

__all__ = [
    "SubagentError",
    "DEFAULT_FIELDS",
    "EXTERNAL_OVERRIDABLE_FIELDS",
    "ZCODE_OVERRIDABLE_FIELDS",
    "slugify_name",
    "system_subagent_ids",
    "is_external_subagent",
    "list_subagents",
    "get_subagent",
    "upsert_subagent",
    "delete_subagent",
    "build_registry",
    "plan_subagent",
    "run_subagent",
    "find_vision_subagent",
    "group_block",
]

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

# ---------------------------------------------------------------------------
# 外部 agent 子代理 —— 描述符驱动
#
# 接入一个外部 agent（ZCode 内置、Codex、Claude Code、自研 CLI、远端服务…）
# **不需要改这个文件**：在 ``~/openminis/agents/`` 放一份 YAML/JSON 协议描述符，
# 下次加载时它会自动出现在子代理列表里，委派时按描述符调用并把回传带回对话。
#
# 外部 agent 与普通子代理的区别（用户给定的设计基线）：
#   - tool / mcp / skill / loop / prompt **都是它自己那一套**，不共享主智能体的；
#   - 因此它可以**独立运行**，``run_subagent`` 走描述符链路而不是 ``AgentRuntime``；
#   - 配置上只有「选模型、挂哪些工具技能、人设补充」可改（覆盖层，见下）。
#
# 协议字段全集见 ``openminis/agent/external_agents/PROTOCOL.md``。
# ---------------------------------------------------------------------------
def _external_descriptors() -> dict[str, Any]:
    """已注册的外部 agent 描述符（id → descriptor）。加载失败视为「一个都没有」。"""
    try:
        from .external_agents import load_descriptors

        return dict(load_descriptors())
    except Exception:  # pragma: no cover - 描述符层不可用不该拖垮子代理面
        logger.debug("external agent descriptors unavailable", exc_info=True)
        return {}


def _active_external_descriptors() -> dict[str, Any]:
    """启用的外部 agent（``enabled: false`` 的描述符不进列表、不可委派）。"""
    return {aid: d for aid, d in _external_descriptors().items() if d.enabled}


def system_subagent_ids() -> set[str]:
    """系统内置（外部 agent）子代理 id 集合 —— 由描述符目录决定，不是硬编码。"""
    return set(_active_external_descriptors())


def is_external_subagent(sid: str) -> bool:
    """该 id 是否由外部 agent 描述符提供。"""
    return str(sid) in _external_descriptors()


#: 允许用户覆盖的外部 agent 配置字段。**故意不含** ``loop`` / ``prompt``：
#: 它们是外部 agent 内核的一部分（外部 agent 的循环与系统提示不该由主智能体的配置面
#: 改写），``persona`` 只是追加在它系统提示之后的补充说明。
EXTERNAL_OVERRIDABLE_FIELDS: tuple[str, ...] = (
    "name",
    "emoji",
    "description",
    "persona",
    "providerId",
    "providerType",
    "model",
    "tools",
    "skills",
    "mcpServers",
    "maxRounds",
)

#: 兼容旧名：ZCode 是最早接入的外部 agent，历史调用点用的是这个常量名。
ZCODE_OVERRIDABLE_FIELDS = EXTERNAL_OVERRIDABLE_FIELDS

#: 这些字段由**后端**从描述符合成出来（``_external_builtin``），是给界面看的只读信息。
#: 界面「编辑」是把整条 ``SubagentInfo`` 塞进表单再回传的，所以它们一定会出现在
#: PUT 载荷里 —— 一律**静默忽略**，不能当成「试图修改不支持字段」而报错。
#: 真正的越界（``loop`` / ``prompt`` / ``entry``）不在这个名单里，仍会被拒。
EXTERNAL_DERIVED_FIELDS: frozenset[str] = frozenset({
    "id",
    "builtin",
    "configurable",
    "external",
    "transport",
    "selfManagedTools",
    "ownTools",
    "ownSkills",
    "borrowedTools",
    "borrowedSkills",
    "skillRoots",
    "toolRoots",
    "source",
    "systemPrompt",
    "borrowTools",
    "borrowSkills",
})

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


def _external_override(store, sid: str) -> dict[str, Any]:
    """用户对某个外部 agent 子代理的覆盖层（存在 ``subagents.<id>`` 里）。

    覆盖层只保存用户**真的改过**的字段，其余仍走描述符默认值 —— 这样描述符升级
    （新增工具、改默认人设）能自动惠及没动过那些字段的用户。
    """
    raw = _table(store).get(sid)
    return dict(raw) if isinstance(raw, dict) else {}


def _external_own_tools(descriptor) -> list[str]:
    """外部 agent **自带**的工具 id：描述符声明 + ``tools.roots`` 目录导入。

    目录导入让「把工具清单文件放进一个目录」就等于接入工具 —— 与技能根的用法
    完全一致，不用把每个工具手写进描述符。
    """
    try:
        from .external_agents import external_agent_tool_surface

        surface = external_agent_tool_surface(descriptor, os.getcwd())
        return [str(t["id"]) for t in surface["items"] if t.get("id")]
    except Exception:  # pragma: no cover - 目录扫描不可用时只剩声明式工具
        logger.debug("external tool surface unavailable", exc_info=True)
        return list(descriptor.default_tools)


def _external_own_skills(descriptor) -> list[str]:
    """外部 agent **自带**的技能名（描述符 ``skills.roots`` 里发现的那几个）。

    只扫它自己的根（``discover_skills_at_roots`` 不并宿主技能库），所以返回的就是
    「它自己这套技能」。这些技能在配置界面上是**锁定项**：与它自带工具一样，只增
    不减 —— 用户能往里加（借宿主技能），但不能把它们删掉。
    """
    if not getattr(descriptor, "skill_roots", None):
        return []
    try:
        from .zcode.integration import discover_skills_at_roots

        outcome = discover_skills_at_roots(
            list(descriptor.skill_roots), workspace=os.getcwd()
        )
        return [str(s.name) for s in outcome.skills if getattr(s, "name", "")]
    except Exception:  # pragma: no cover - 扫描不可用时视为「没有自带技能」
        logger.debug("external own skills unavailable", exc_info=True)
        return []


def _external_tool_ids(descriptor) -> list[str]:
    """外部 agent 默认可勾选的工具：只保留**工具目录里真实存在**的 id。

    描述符/工具目录里声明的是「能力面」；其中有依赖未移植端口的（如 WebSearch），
    注册出来必然是个一调就报错的壳 —— 所以这里按目录取交集，界面从此不会给出会
    失败的工具。（ZCode 的 10 个工具都已注册，所以它拿到完整工具链。）
    """
    try:
        from ..settings.catalog import known_tool_ids

        known = known_tool_ids()
    except Exception:  # pragma: no cover - 目录不可用时退回空（界面会提示无工具）
        logger.debug("tool catalog unavailable", exc_info=True)
        return []
    return [t for t in _external_own_tools(descriptor) if t in known]


def _backfill_provider(store, cfg: dict[str, Any]) -> None:
    """内置外部 agent 默认跟随「当前激活的模型服务实例」。"""
    data = store.load()
    active = str(data.get("activeProviderId") or "").strip()
    instances = list(store.provider_instances())
    conf = next((c for c in instances if str(c.get("id")) == active), None)
    if conf is None and instances:
        conf = instances[0]
    if conf is None:
        return
    cfg["providerId"] = str(conf.get("id") or active)
    ptype = str(conf.get("type") or "")
    if ptype:
        cfg["providerType"] = ptype
    cfg["model"] = str(cfg.get("model") or conf.get("model") or "")


def _external_builtin(store, descriptor) -> dict[str, Any]:
    """外部 agent 子代理的活体副本：描述符默认值 + 用户覆盖层。

    内置项**不必**落库，但用户改过的部分会以覆盖层形式留在 ``subagents.<id>``；
    这里负责把两层合成出「当前生效」的那一份。绑定的 provider 缺席（还没配 Key）
    时 providerId 留空，前端显示「未配模型」。

    工具与技能的合并规则是**只增不减**：外部 agent 自称的那套（``ownTools`` /
    ``ownSkills``）永远生效，覆盖层里存的只是用户额外借来的宿主工具/技能。这与它
    作为「独立 agent」的定位一致 —— 借东西可以，改它的本体不行。
    """
    own_tools = _external_own_tools(descriptor)
    own_skills = _external_own_skills(descriptor)
    cfg: dict[str, Any] = {
        "id": descriptor.id,
        "name": descriptor.name,
        "emoji": descriptor.emoji,
        "description": descriptor.description,
        "persona": descriptor.persona,
        "providerId": "",
        "providerType": "",
        "model": "",
        "tools": _external_tool_ids(descriptor),
        "skills": list(own_skills),
        "mcpServers": [],
        "maxRounds": 12,
        # ── 外部 agent 自己的东西（界面据此区分「它自带工具链」而不是「没工具」）──
        "builtin": bool(descriptor.builtin),
        "external": True,
        "transport": descriptor.transport,
        #: 它自己带的工具名（描述符声明 + tools.roots 目录导入；不一定在本应用的
        #: 工具目录里 —— CLI agent 的工具在自己进程里）
        "selfManagedTools": own_tools,
        #: 它自带的工具/技能（界面渲染成**锁定项**：只增不减）
        "ownTools": own_tools,
        "ownSkills": own_skills,
        #: 用户额外**借来**的宿主工具/技能（覆盖层净增量）
        "borrowedTools": [],
        "borrowedSkills": [],
        "skillRoots": list(descriptor.skill_roots),
        "toolRoots": list(descriptor.tool_roots),
        "systemPrompt": descriptor.resolved_system_prompt(),
        "source": descriptor.source_path,
        #: 与主智能体的互借开关（工具/技能）。
        "borrowTools": bool(descriptor.borrow_tools),
        "borrowSkills": bool(descriptor.borrow_skills),
    }
    _backfill_provider(store, cfg)

    # 用户覆盖层最后合并：改过什么就用什么。
    override = _external_override(store, descriptor.id)
    for key in EXTERNAL_OVERRIDABLE_FIELDS:
        if key not in override:
            continue
        if key in ("tools", "skills"):
            # 工具与技能是**只增不减**的：它自称的那套永远生效（界面上也删不掉），
            # 覆盖层里存的只是「额外借来的」——见下面两行合并。
            continue
        if key in ("mcpServers", "persona", "description"):
            # 空值是合法取值（用户主动清空人设/描述），不能当成「没改」。
            cfg[key] = override[key]
        elif override[key] not in (None, ""):
            cfg[key] = override[key]

    # 「借用」的净增量单独算出来：界面用它把「自带的」与「借来的」分开画，
    # 运行时也据此只把**勾中的那几个**宿主技能拿给外部 agent。
    borrowed_tools = [
        str(t) for t in (override.get("tools") or []) if str(t) not in cfg["tools"]
    ]
    borrowed_skills = [
        str(s) for s in (override.get("skills") or []) if str(s) not in cfg["skills"]
    ]
    cfg["tools"] = [*cfg["tools"], *borrowed_tools]
    cfg["skills"] = [*cfg["skills"], *borrowed_skills]
    cfg["borrowedTools"] = borrowed_tools
    cfg["borrowedSkills"] = borrowed_skills
    cfg["id"] = descriptor.id
    # 界面据此知道这张卡可以编辑（只是不可删除）。
    cfg["configurable"] = True
    return cfg


def list_subagents(store) -> list[dict[str, Any]]:
    descriptors = _active_external_descriptors()
    # 内置项（随包分发）排在前，其余按 id —— 顺序稳定，界面不会每次刷新跳来跳去。
    ordered = sorted(descriptors.values(), key=lambda d: (not d.builtin, d.id))
    out = [_external_builtin(store, d) for d in ordered]
    out.extend(
        _with_instance_id(store, cfg)
        for key, cfg in _table(store).items()
        if key not in descriptors
    )
    return out


def get_subagent(store, sid: str) -> dict[str, Any] | None:
    descriptor = _active_external_descriptors().get(str(sid))
    if descriptor is not None:
        # 必须走合成（覆盖层可能已经改过 name/tools/…），不能返回表里的原始行。
        return _external_builtin(store, descriptor)
    cfg = _table(store).get(sid)
    if cfg:
        return _with_instance_id(store, cfg)
    return None



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


def _upsert_external_override(
    store, descriptor, payload: dict[str, Any], sid: str
) -> dict[str, Any]:
    """保存外部 agent 子代理的**覆盖层**（而不是整条替换）。

    外部 agent 的关键区别：它跑的是自己的 loop / prompt / 工具链，所以能改的只有
    「选哪个模型、挂哪些工具与技能、人设补充」这几项；``loop``/``prompt`` 不在可改
    名单里 —— 让主智能体去改外部 agent 的循环与系统提示，只会造出一个四不像。

    与普通 subagent 的另一处不同：``providerId`` **允许留空**，表示继续跟随当前
    激活的模型服务实例（内置项默认就是这个语义）。
    """
    from ..settings.catalog import known_tool_ids

    data = store.load()
    sub = data.setdefault("subagents", {})
    existing = dict(sub.get(sid) or {})

    rejected = [
        key
        for key in payload
        if key not in EXTERNAL_OVERRIDABLE_FIELDS
        and key not in EXTERNAL_DERIVED_FIELDS
    ]
    if rejected:
        raise SubagentError(
            f"外部 agent 子代理 {sid} 不支持修改这些字段: {', '.join(sorted(rejected))}"
            f"（可改: {', '.join(EXTERNAL_OVERRIDABLE_FIELDS)}）"
        )

    cfg: dict[str, Any] = {**existing}
    for key, value in (payload or {}).items():
        if key in EXTERNAL_OVERRIDABLE_FIELDS:
            cfg[key] = value

    if "name" in cfg and not str(cfg.get("name") or "").strip():
        raise SubagentError("name 不能为空")
    # 工具/技能：界面把「自带的 + 借来的」整份回传（它就是照着当前生效那份画的），
    # 落库前**减掉自带的那一份**，只留净增的借用项。这样「删不掉自己的」是结构保证，
    # 而不是靠界面不给按钮。
    own_tools = set(_external_own_tools(descriptor))
    own_skills = set(_external_own_skills(descriptor))
    if "tools" in cfg:
        picked = _clean_tools(cfg.get("tools"), known_tool_ids(), sid)
        cfg["tools"] = [t for t in picked if t not in own_tools]
    if "skills" in cfg:
        picked = _clean_strings("skills", cfg.get("skills"), sid, "skills")
        cfg["skills"] = [s for s in picked if s not in own_skills]
    if "mcpServers" in cfg:
        cfg["mcpServers"] = _clean_strings("mcpServers", cfg.get("mcpServers"), sid, "mcpServers")
    if "maxRounds" in cfg:
        try:
            cfg["maxRounds"] = max(1, min(int(cfg.get("maxRounds", 12)), 12))
        except (TypeError, ValueError) as exc:
            raise SubagentError("maxRounds 必须是整数(1-12)") from exc
    if "emoji" in cfg:
        cfg["emoji"] = str(cfg.get("emoji") or "🤖")[:4]
    if "description" in cfg:
        cfg["description"] = str(cfg.get("description") or "")[:200]

    if "providerId" in payload or "providerType" in payload:
        pid = str(cfg.get("providerId") or cfg.get("providerType") or "").strip()
        if pid:
            instances = store.provider_instances()
            conf = next((c for c in instances if c.get("id") == pid), None)
            if conf is None:
                conf = next((c for c in instances if c.get("type") == pid), None)
            if conf is None or not str(conf.get("apiKey") or "").strip():
                raise SubagentError(f"模型服务未配置或未填 Key: {pid}")
            cfg["providerId"] = str(conf.get("id") or pid)
            if conf.get("type"):
                cfg["providerType"] = str(conf["type"])
        else:
            # 清空 → 回到「跟随当前激活实例」的内置语义，覆盖层里不留空值。
            cfg.pop("providerId", None)
            cfg.pop("providerType", None)

    cfg["id"] = sid
    cfg["builtin"] = bool(descriptor.builtin)
    sub[sid] = cfg
    store.save(data)
    return _external_builtin(store, descriptor)


def upsert_subagent(store, payload: dict[str, Any], sid: str | None = None) -> dict[str, Any]:
    """Create (sid None or new) or replace an existing subagent config.

    Note: 外部 agent 子代理（内置 ZCode、描述符注册的 Codex…）**可以被修改**，但改的
    是覆盖层而不是整条配置（见 ``_upsert_external_override``）；它们仍然**不可删除**。
    """
    descriptor = _active_external_descriptors().get(str(sid)) if sid is not None else None
    if descriptor is not None:
        return _upsert_external_override(store, descriptor, payload or {}, str(sid))
    if sid is not None and is_external_subagent(str(sid)):
        # 描述符存在但被 enabled: false 关掉了 —— 不要让它悄悄变成一条普通子代理。
        raise SubagentError(
            f"外部 agent '{sid}' 当前未启用（描述符 enabled: false），"
            "请先启用后再修改配置"
        )
    data = store.load()
    sub = data.setdefault("subagents", {})
    cfg = {
        **DEFAULT_FIELDS,
        **(payload or {}),
    }
    new_id = str(cfg.get("id") or sid or "").strip()
    if is_external_subagent(new_id):
        raise SubagentError(
            f"id '{new_id}' 已被外部 agent 描述符占用（工具/技能/循环由它自己提供），"
            "请换一个 id"
        )
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
    """Delete a subagent. 外部 agent 子代理不可删除。

    注意：不可删除 ≠ 不可配置 —— 改配置走 ``upsert_subagent`` 的覆盖层。
    想彻底移除一个描述符驱动的外部 agent，删掉它那份描述符文件即可（下面会把
    具体路径指出来）。
    """
    if is_external_subagent(str(sid)):
        descriptor = _external_descriptors().get(str(sid))
        shipped = bool(getattr(descriptor, "builtin", False))
        if shipped:
            raise SubagentError(
                f"子代理 '{sid}' 随应用分发，不可删除；但可以修改配置"
            )
        source = str(getattr(descriptor, "source_path", "") or "")
        where = f"删掉描述符文件 {source} 即可移除" if source else (
            "删掉 ~/openminis/agents/ 里对应的描述符文件即可移除"
        )
        raise SubagentError(f"子代理 '{sid}' 由外部 agent 描述符提供 —— {where}")
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
        # 技能库依赖 pyyaml；导不进来时注册表**照常返回**（技能那一栏空着），
        # 而不是让整个「可用资源注册表」连带子代理规划一起失败。
        from ..skills import SkillStore

        for entry in SkillStore().list():
            skills.append({"name": entry.name, "description": entry.description})
    except Exception:  # pragma: no cover - never break registry over skills
        logger.debug("skill list unavailable", exc_info=True)

    # 外部 agent 的技能来自**另一套技能根**（描述符里的 ``skills.roots``，如
    # ``.zcode/skills`` / ``.agents/skills``）。与上面主智能体的技能取并集展示：
    # 同名也不合并 —— 它们的正文与作用域可能不同，合并会让用户以为是一个技能，
    # 实则外部 agent 加载的是另一份。
    try:
        from .zcode.integration import discover_skills_at_roots

        workspace = os.getcwd()
        seen = {s["name"] for s in skills}
        for descriptor in _active_external_descriptors().values():
            if not descriptor.skill_roots:
                continue
            outcome = discover_skills_at_roots(
                list(descriptor.skill_roots), workspace=workspace
            )
            for skill in outcome.skills:
                skills.append({
                    "name": skill.name,
                    "description": skill.description,
                    "source": "external",
                    "agentId": descriptor.id,
                    "scope": skill.scope,
                    "path": skill.path,
                    "shadowed": skill.name in seen,
                })
    except Exception:  # pragma: no cover - 外部技能不可用不该拖垮注册表
        logger.debug("external skill discovery unavailable", exc_info=True)

    return {
        "providers": providers,
        "models": models,
        "tools": all_tool_catalog(),
        "skills": skills,
        "mcpServers": [],
        # 外部 agent（描述符驱动）：界面据此展示「已接入哪些外部 agent」及其协议来源。
        "externalAgents": _external_agent_views(),
        # 主智能体的工具对外部 agent 的**可借用**清单（互借的工具那一半）。
        "borrowedTools": _borrowed_tool_catalog(),
        # human note for the UI / planner about the MCP gap
        "mcpNote": "本引擎暂未移植 MCP 客户端，mcpServers 字段仅作预留",
    }


def _external_agent_views() -> list[dict[str, Any]]:
    """外部 agent 摘要（描述符层不可用时返回空列表）。"""
    try:
        from .external_agents import list_external_agent_views

        return list_external_agent_views()
    except Exception:  # pragma: no cover
        logger.debug("external agent views unavailable", exc_info=True)
        return []


def _borrowed_tool_catalog() -> list[dict[str, Any]]:
    """可被外部 agent 借用的宿主工具（ZCode 桥接层登记）。"""
    try:
        from .zcode.integration import borrowed_tool_catalog

        return borrowed_tool_catalog()
    except Exception:  # pragma: no cover
        logger.debug("borrowed tool catalog unavailable", exc_info=True)
        return []


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
# ZCode 外部 agent —— 独立运行链路
# ---------------------------------------------------------------------------
def _session_workspace(session_id: str) -> str:
    """会话的工作区根（与 ``file_*`` 工具同一套映射）。"""
    import os

    try:
        from ..core.context import app_context

        root = app_context().external_files_dir / str(session_id)
        root.mkdir(parents=True, exist_ok=True)
        return str(root)
    except Exception:  # pragma: no cover - 无 AppContext（CLI/测试）时退回 cwd
        logger.debug("session workspace unavailable, falling back to cwd", exc_info=True)
        return os.getcwd()


def _resolve_provider_for(store, cfg: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """解析子代理绑定的模型服务实例；空则跟随当前激活实例。"""
    pid = str(cfg.get("providerId") or cfg.get("providerType") or "")
    data = store.load()
    conf = dict(data["providers"].get(pid) or {})
    if not conf:
        conf = next(
            (dict(c) for c in store.provider_instances() if c.get("type") == pid),
            {},
        )
    if not conf:
        active = str(data.get("activeProviderId") or "")
        conf = dict(data["providers"].get(active) or {})
        if not conf:
            conf = next((dict(c) for c in store.provider_instances()), {})
        pid = str(conf.get("id") or active)
    if not conf:
        raise SubagentError("没有可用的模型服务：请先在 设置 → 模型服务 配好 API Key")
    conf["model"] = cfg.get("model") or conf.get("model", "")
    return pid, conf


async def _run_external_subagent(
    store, descriptor, cfg: dict[str, Any], task: str, session_id: str
) -> str:
    """用外部 agent **自己的 loop / prompt / 工具链 / 技能**跑一轮（不经过 ``AgentRuntime``）。

    这是「外部 agent 可以独立运行」的落点：
    - **loop**：由描述符指向的实现提供（内置 ZCode 走 ``integration.agent``）；
    - **prompt**：外部 agent 自己拼装，``cfg.persona`` 只作追加说明；
    - **tools**：它自己的工具链 ∪（打开互借时）勾中的主智能体工具；
    - **skills**：它自己的技能根 ∪（打开互借时）主智能体的技能库。

    描述符一律从这里分发：``internal`` 走进程内入口，``stdio`` 起子进程，``http``
    发请求 —— 三者都收敛成同一份回传文本（见 ``external_agents.runner``）。
    """
    from .external_agents import run_external_agent
    from .subagent_events import project_for

    # 只有进程内实现才需要宿主模型；CLI/HTTP agent 自带执行环境，不需要我们的 Key。
    # （provider 层依赖 httpx 等运行环境库，能不起就不起 —— 一个纯 CLI 委派不该
    #  因为聊天服务装不起来而失败。）
    provider = None
    if descriptor.transport == "internal":
        from ..settings.chat_service import build_provider

        pid, conf = _resolve_provider_for(store, cfg)
        provider = build_provider(pid, conf)

    cwd = _session_workspace(session_id)
    project_dir = project_for(cfg.get("id") or descriptor.id)
    if project_dir:
        cwd = str(project_dir)

    # 让外部 agent 的 shell 与应用自己的 shell 各自落在自己的键上（互不干扰），
    # 但目录一致 —— 借来的宿主工具也因此落在同一个工作区。
    try:
        from pathlib import Path

        from ..tools.shell_execute_tool import get_coordinator

        get_coordinator().set_session_cwd(f"{session_id}:{descriptor.id}", Path(cwd))
    except Exception:  # pragma: no cover - 目录不可用不该打断运行
        logger.debug("external agent session cwd failed", exc_info=True)

    try:
        result = await run_external_agent(
            descriptor.id,
            task,
            session_id=session_id,
            workspace=cwd,
            config=cfg,
            provider=provider,
        )
    finally:
        close = getattr(provider, "aclose", None)
        if callable(close):
            try:
                await close()
            except Exception:  # pragma: no cover
                pass

    return result.as_text()


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
    cfg = get_subagent(store, subagent_id)
    if cfg is None:
        raise SubagentError(f"subagent 不存在: {subagent_id}")

    # 外部 agent（内置 ZCode、描述符注册的 Codex…）是**独立 agent**：tool / mcp /
    # skill / loop / prompt 都是它自己那一套，走描述符链路。这里必须早于下面的通用
    # 分支返回，否则它会被当普通子代理塞进 ``AgentRuntime`` + 主智能体工具链，等于
    # 把外部 agent 降级成普通人格。
    #
    # 注意：通用分支那几个 import（provider 层 / AgentRuntime）刻意放在这个早返回
    # **之后** —— 一个纯 CLI 外部 agent 不该因为宿主聊天服务装不起来而委派失败。
    descriptor = _active_external_descriptors().get(str(subagent_id))
    if descriptor is not None:
        return await _run_external_subagent(store, descriptor, cfg, task, session_id)

    from ..settings.catalog import build_tool_registry
    from ..settings.chat_service import build_provider
    from ..tools.subagent_tool import SubagentDelegateTool
    from .agent_runtime import AgentRuntime, AgentRuntimeOptions

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
