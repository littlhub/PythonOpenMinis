"""Persistent storage for the settings the Web UI edits: model provider
credentials/config, the active provider, the active identity and per-identity
tool overrides.

Backed by a single JSON file in the app data dir (``data_dir/settings.json``).
Keys are stored as plain text — this is a local single-user desktop service;
the README notes the security caveat. Kotlin persists provider configs in a
Room database (``ProviderInstanceEntity`` etc.) plus Keychain for secrets.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Iterable

from ..core.context import app_context
from ..core.logging import get_logger
from .catalog import BUILTIN_IDENTITIES, Identity, PROVIDER_TYPES
from .model_capability import (
    SLOT_ORDER,
    normalize_capabilities,
    resolve_capabilities,
)

logger = get_logger(__name__)

__all__ = ["SettingsStore", "SettingsError", "provider_type_label"]

_FILE_NAME = "settings.json"
_VERSION = 1


class SettingsError(Exception):
    """Raised for invalid settings payloads (surfaced as HTTP 400)."""


def provider_type_label(provider_type: str) -> str:
    for p in PROVIDER_TYPES:
        if p.type == provider_type:
            return p.label
    return provider_type


def _defaults() -> dict[str, Any]:
    return {
        "version": _VERSION,
        "activeProviderId": None,
        "activeIdentityId": "assistant",
        #: instance_id -> {id, type, label?, apiKey, baseUrl, model, modelHints?,
        #: modelTypes?}  ``modelTypes`` = {model_id: capability} 用户对模型
        #: 类型(LLM/多模态/识图/生图)的手动覆盖;缺省走 id 自动推断。
        #: Multiple instances may share one provider *type* (e.g. two OpenAI-
        #: compatible gateways); ``activeProviderId`` points at an instance id.
        "providers": {},
        #: 用途槽位 —— 把「对话/识图/生图」各绑到一个 (实例, 模型)。
        #: 对话槽复用 activeProviderId + 该实例的 model, 这里只存另外两个:
        #: {"vision": {"instanceId":…, "model":…}, "image": {…}}
        "modelSlots": {},
        #: identity id -> {"enabledTools": [...]}  (built-in overrides only)
        "identityOverrides": {},
        #: id -> full Identity dict (user-created identities)
        "customIdentities": {},
        #: subagent id -> config dict (model / skills / tools / mcp / persona).
        #: Subagents are delegatable worker agents the main agent can call.
        "subagents": {},
        #: skill names the main agent may load (技能调用范围,空=不启用).
        #: Activation is per-skill and edited from the 技能 page.
        "activeSkills": [],
        #: 用户自定义的模型用途类型:[{id,label,type,url,json}]。除八类内置
        #: 用途外,用户可自建类型(自带 JSON 配置与 URL),并在模型旁勾选。
        "customModelTypes": [],
        #: Agent 对话运行参数（对应 UI「模型设置」里的 Agent 参数项）。
        #: maxContextTokens=超过该预算时智能压缩; maxMemoryRounds=几轮问答后
        #: 压缩; maxToolSteps=单次对话工具调用上限; deepThinking=是否深度思考;
        #: imageContextMode=图片如何进上下文 —— "path"(默认)只把图片**路径**
        #: 记进上下文，理解交给识图槽/子代理，避免 base64 把上下文撑爆；
        #: "inline" 则把图片字节附给多模态主模型（原项目行为，吃上下文）。
        #: imageMaxEdge=读图/送图前把长边缩到这个像素上限（默认 2000）——
        #: 图片的 token/上下文开销随像素增长，调小它可直接压低开销。
        "agent": {
            "maxContextTokens": 511998,
            "maxMemoryRounds": 30,
            "maxToolSteps": 40,
            "deepThinking": False,
            # 是否允许主 Agent 委派子代理助理(subagent_delegate)。关闭后该
            # 工具既不出现在 schema 里,也无法执行 —— 主模型直接自己干活。
            "subagentEnabled": True,
            "imageContextMode": "path",
            "imageMaxEdge": 2000,
        },
    }


def _valid_type(provider_type: str) -> bool:
    return any(p.type == provider_type for p in PROVIDER_TYPES)


#: provider instance id charset — ids are user-visible slugs ("openAI-2").
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _normalize_providers(providers: Any) -> dict[str, dict[str, Any]]:
    """Canonicalise the stored ``providers`` map into ``{instance_id: conf}``.

    Older files keyed providers by **provider type** and stored no ``id`` on
    the entry — the id then equals the type, which keeps ``activeProviderId``
    (previously the type) resolving to the same record.
    """
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(providers, dict):
        return out
    for key, conf in providers.items():
        if not isinstance(conf, dict):
            continue
        cid = str(conf.get("id") or key or "").strip()
        if not cid:
            continue
        entry = dict(conf)
        entry["id"] = cid
        # legacy shape stored the type as both map key and conf["type"]
        entry.setdefault("type", str(key))
        out[cid] = entry
    return out


def _provider_display_label(conf: dict[str, Any]) -> str:
    """User-facing name of a stored provider instance."""
    label = str(conf.get("label") or "").strip()
    if label:
        return label
    return provider_type_label(str(conf.get("type") or ""))


def _normalize_model_types(
    raw: Any, extra_ids: Iterable[str] = ()
) -> dict[str, list[str]] | None:
    """Coerce a ``{model_id: [capability, …]}`` map; drop unknown labels.

    Tolerates legacy single-string values (``{model: "llm"}``) and the old
    ``"multimodal"`` label (expands to ``["llm", "vision"]``). ``extra_ids``
    are user-defined custom type ids that pass through verbatim. Returns
    ``None`` when the input is not a map at all (caller keeps the previously
    stored value), ``{}`` when it is an empty map (caller clears).
    """
    if not isinstance(raw, dict):
        return None
    out: dict[str, list[str]] = {}
    for mid, caps in raw.items():
        if not isinstance(mid, str) or not mid.strip():
            continue
        norm = normalize_capabilities(caps, extra_ids)
        if norm:  # skip empty / unknown-only entries
            out[mid.strip()] = norm
    return out


#: 自定义模型类型的 id 字符集(与 provider 实例 id 同风格)。
_TYPE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$")


def _normalize_custom_types(raw: Any) -> list[dict[str, Any]]:
    """Validate a ``[{id,label,type,url,json}]`` list.

    ``json`` is a free-form JSON **string** (kept as text so the UI can show
    it back verbatim); invalid JSON is rejected. Unknown keys are dropped.
    Raises :class:`SettingsError` on structural problems.
    """
    if not isinstance(raw, list):
        raise SettingsError("customModelTypes 必须是列表")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise SettingsError(f"customModelTypes[{i}] 必须是对象")
        cid = str(item.get("id") or "").strip()
        if not _TYPE_ID_RE.match(cid):
            raise SettingsError(
                f"customModelTypes[{i}].id 非法(字母数字开头,可含 . _ -,≤48 字)"
            )
        if cid in seen:
            raise SettingsError(f"customModelTypes 中 id 重复: {cid}")
        seen.add(cid)
        label = str(item.get("label") or "").strip() or cid
        ctype = str(item.get("type") or "").strip()
        url = str(item.get("url") or "").strip()
        raw_json = item.get("json")
        if raw_json in (None, ""):
            js = ""
        elif isinstance(raw_json, str):
            try:
                json.loads(raw_json)
            except (ValueError, TypeError):
                raise SettingsError(f"customModelTypes[{i}].json 不是合法 JSON")
            js = raw_json
        else:
            # accept an object/array and store its serialised form
            try:
                js = json.dumps(raw_json, ensure_ascii=False)
            except (TypeError, ValueError):
                raise SettingsError(f"customModelTypes[{i}].json 无法序列化")
        out.append({"id": cid, "label": label, "type": ctype, "url": url, "json": js})
    return out


class SettingsStore:
    """Thread-safe JSON-backed settings store (module singleton by default)."""

    _instance: "SettingsStore | None" = None
    _lock = threading.Lock()

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (app_context().data_dir / _FILE_NAME)
        self._io_lock = threading.Lock()
        self._data: dict[str, Any] | None = None

    # -- construction ------------------------------------------------------
    @classmethod
    def get(cls) -> "SettingsStore":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Drop the singleton (used by tests)."""
        with cls._lock:
            cls._instance = None

    # -- load / save -------------------------------------------------------
    def load(self) -> dict[str, Any]:
        with self._io_lock:
            if self._data is None:
                self._data = self._read_disk()
            return self._data

    def _read_disk(self) -> dict[str, Any]:
        if not self.path.exists():
            return _defaults()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("settings.json unreadable — starting fresh")
            return _defaults()
        data = _defaults()
        if isinstance(raw, dict):
            for k in data:
                if k in raw:
                    data[k] = raw[k]
        # one-time migration: providers keyed by type → instance map (id=type)
        data["providers"] = _normalize_providers(data["providers"])
        return data

    def save(self, data: dict[str, Any]) -> None:
        with self._io_lock:
            self._data = data
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(self.path)

    # -- public mutations (all validated) ----------------------------------
    def apply_full(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate + apply the full settings payload from the REST API.

        Returns the resulting stored snapshot.
        """
        data = self.load()
        errors: list[str] = []

        # 自定义模型类型 —— 先处理,好让 providers 里的 modelTypes 覆盖能识别
        # 用户自建的 id(否则勾了自定义类型保存会被当成非法标签丢掉)。
        if "customModelTypes" in payload:
            data["customModelTypes"] = _normalize_custom_types(
                payload["customModelTypes"]
            )
        extra_type_ids: set[str] = set()
        for ct in data.get("customModelTypes") or []:
            if isinstance(ct, dict) and ct.get("id"):
                extra_type_ids.add(str(ct["id"]))

        # providers: full replacement list of
        #   {id?, type, label?, apiKey, baseUrl, model}
        # id 为空时(旧客户端/旧测试)按 type upsert 该类型的现有实例;新实例
        # 由前端分配 id,后端校验唯一性与格式。
        if "providers" in payload:
            existing = _normalize_providers(data["providers"])
            new_map: dict[str, dict[str, Any]] = {}
            seen: set[str] = set()
            raw_providers = payload["providers"]
            if not isinstance(raw_providers, list):
                raise SettingsError("providers 必须是列表")
            for conf in raw_providers:
                if not isinstance(conf, dict) or not conf.get("type"):
                    errors.append("provider 缺少 type")
                    continue
                ptype = str(conf["type"])
                if not _valid_type(ptype):
                    errors.append(f"未知厂商: {ptype}")
                    continue
                cid = str(conf.get("id") or "").strip()
                if not cid:
                    # legacy no-id entry → keep the existing instance of this
                    # type (or take the type itself as the first instance id)
                    legacy = next(
                        (i for i in existing.values() if i["type"] == ptype),
                        None,
                    )
                    cid = str(legacy["id"]) if legacy else ptype
                if not _ID_RE.match(cid):
                    errors.append(f"非法的厂商实例 id: {cid or '(空)'}")
                    continue
                if cid in seen:
                    errors.append(f"重复的厂商实例: {cid}")
                    continue
                seen.add(cid)
                entry: dict[str, Any] = {"id": cid, "type": ptype}
                label = conf.get("label")
                if isinstance(label, str) and label.strip():
                    entry["label"] = label.strip()
                api_key = conf.get("apiKey")
                if isinstance(api_key, str) and api_key:
                    entry["apiKey"] = api_key
                elif cid in existing and existing[cid].get("apiKey"):
                    # blank key on update = keep existing secret
                    entry["apiKey"] = existing[cid].get("apiKey", "")
                base_url = conf.get("baseUrl")
                entry["baseUrl"] = (base_url if isinstance(base_url, str) else "").strip()
                model = conf.get("model")
                entry["model"] = (model if isinstance(model, str) else "").strip()
                # remote model hints are owned by fetch-models; a plain save
                # must not wipe them
                if cid in existing:
                    hints = existing[cid].get("modelHints")
                    if isinstance(hints, list) and all(
                        isinstance(h, str) for h in hints
                    ):
                        entry["modelHints"] = hints
                    # 用户对模型类型的手动覆盖:{model_id: capability}。
                    # 显式传了(哪怕是空 map)就以本次为准;没传则保留旧值。
                    kept = existing[cid].get("modelTypes")
                    if isinstance(kept, dict) and kept:
                        entry["modelTypes"] = kept
                # 本次请求带来的覆盖优先(含清空)
                incoming = _normalize_model_types(
                    conf.get("modelTypes"), extra_type_ids
                )
                if incoming is not None:
                    if incoming:
                        entry["modelTypes"] = incoming
                    else:
                        entry.pop("modelTypes", None)
                new_map[cid] = entry
            data["providers"] = new_map

        if "identityEdits" in payload:
            edits = payload["identityEdits"]
            if not isinstance(edits, list):
                raise SettingsError("identityEdits 必须是列表")
            builtin_ids = {i.id for i in BUILTIN_IDENTITIES}
            for edit in edits:
                if not isinstance(edit, dict) or not edit.get("id"):
                    continue
                eid = str(edit["id"])
                tools = edit.get("enabledTools")
                if not isinstance(tools, list):
                    errors.append(f"身份 {eid} 的 enabledTools 必须是列表")
                    continue
                from .catalog import VALID_TOOLS  # local import, small cycle risk

                clean = [t for t in tools if t in VALID_TOOLS]
                if eid in builtin_ids:
                    ov = dict(data["identityOverrides"].get(eid, {}))
                    ov["enabledTools"] = clean
                    data["identityOverrides"][eid] = ov
                else:
                    # custom identity: upsert only the tools (identity text is
                    # managed through customIdentities)
                    if eid not in data["customIdentities"]:
                        errors.append(f"未知身份: {eid}")
                    else:
                        data["customIdentities"][eid]["enabledTools"] = clean

        if "customIdentities" in payload:
            customs = payload["customIdentities"]
            if not isinstance(customs, list):
                raise SettingsError("customIdentities 必须是列表")
            new_customs: dict[str, dict] = {}
            for c in customs:
                if not isinstance(c, dict) or not c.get("id"):
                    continue
                cid = str(c["id"])
                if cid in {i.id for i in BUILTIN_IDENTITIES}:
                    errors.append(f"身份 id 与内置冲突: {cid}")
                    continue
                persona = str(c.get("persona") or "")
                if not persona.strip():
                    errors.append(f"身份 {cid} 缺少 persona")
                    continue
                from .catalog import VALID_TOOLS

                tools = [t for t in (c.get("enabledTools") or []) if t in VALID_TOOLS]
                new_customs[cid] = {
                    "id": cid,
                    "name": str(c.get("name") or cid),
                    "emoji": str(c.get("emoji") or "🧩"),
                    "description": str(c.get("description") or ""),
                    "persona": persona,
                    "recommendedTools": tools,
                    "enabledTools": tools,
                    "builtin": False,
                }
            data["customIdentities"] = new_customs
            if data["activeIdentityId"] not in {i.id for i in BUILTIN_IDENTITIES} | set(
                new_customs
            ):
                data["activeIdentityId"] = "assistant"

        # activate providers / identities LAST — after provider+identity
        # payloads above have been validated and stored
        if "activeProviderId" in payload:
            aid = payload["activeProviderId"]
            if aid is not None:
                aid = str(aid)
                if aid not in data["providers"]:
                    errors.append(f"厂商 {aid} 尚未配置,无法激活")
                else:
                    data["activeProviderId"] = aid
            else:
                data["activeProviderId"] = None

        if "activeIdentityId" in payload:
            iid = str(payload["activeIdentityId"])
            known = [i.id for i in BUILTIN_IDENTITIES] + list(
                data["customIdentities"]
            )
            if iid in known:
                data["activeIdentityId"] = iid
            else:
                errors.append(f"未知身份: {iid}")

        # 用途槽位 —— 把「识图 / 生图」绑到 (实例, 模型)。对话槽不在 storage 里
        # 另存:传入 chat 会被翻译成 activeProviderId + 该实例的 model,保持单一
        # 事实来源(否则两个地方各存一份,必然打架)。
        if "modelSlots" in payload:
            raw_slots = payload["modelSlots"]
            if not isinstance(raw_slots, dict):
                errors.append("modelSlots 必须是对象")
            else:
                stored = dict(data.get("modelSlots") or {})
                for slot in SLOT_ORDER:
                    if slot not in raw_slots:
                        continue
                    val = raw_slots[slot]
                    if val is None:
                        stored.pop(slot, None)
                        continue
                    if not isinstance(val, dict):
                        errors.append(f"modelSlots.{slot} 必须是对象或 null")
                        continue
                    iid = str(val.get("instanceId") or "").strip()
                    mid = str(val.get("model") or "").strip()
                    if not iid and not mid:
                        stored.pop(slot, None)
                        continue
                    if iid not in data["providers"]:
                        errors.append(f"modelSlots.{slot} 指向未配置的厂商: {iid or '(空)'}")
                        continue
                    if not mid:
                        errors.append(f"modelSlots.{slot} 缺少模型 id")
                        continue
                    if slot == "chat":
                        # 翻译进 activeProviderId + provider.model
                        data["activeProviderId"] = iid
                        data["providers"][iid]["model"] = mid
                    else:
                        stored[slot] = {"instanceId": iid, "model": mid}
                data["modelSlots"] = stored

        # Agent 对话参数 — 只合并传入的键,越界值钳制到合理区间而不是拒绝,
        # 这样一次误填不会让整个设置保存失败。
        if "agent" in payload:
            raw = payload["agent"]
            if not isinstance(raw, dict):
                errors.append("agent 必须是对象")
            else:
                agent = dict(data.get("agent") or _defaults()["agent"])
                ints = {
                    "maxContextTokens": (1, 100_000_000),
                    "maxMemoryRounds": (1, 1000),
                    "maxToolSteps": (1, 1000),
                    # 长边上限：过小会糊到看不清，过大则吃上下文
                    "imageMaxEdge": (128, 8192),
                }
                for key, (lo, hi) in ints.items():
                    if key in raw:
                        try:
                            agent[key] = max(lo, min(hi, int(raw[key])))
                        except (TypeError, ValueError):
                            errors.append(f"agent.{key} 必须是整数")
                # 布尔开关(深度思考 / 子代理助理)—— 宽松解析,非布尔按真假字符串判。
                for bkey in ("deepThinking", "subagentEnabled"):
                    if bkey in raw:
                        bv = raw[bkey]
                        agent[bkey] = (
                            bv
                            if isinstance(bv, bool)
                            else str(bv).lower() in ("1", "true", "on")
                        )
                if "imageContextMode" in raw:
                    mode = str(raw["imageContextMode"] or "").strip().lower()
                    if mode in ("path", "inline"):
                        agent["imageContextMode"] = mode
                    else:
                        errors.append("agent.imageContextMode 只能是 path 或 inline")
                data["agent"] = agent

        if errors:
            raise SettingsError("; ".join(errors))
        self.save(data)
        return data

    # -- read helpers ------------------------------------------------------
    def set_model_hints(self, provider_id: str, hints: list[str]) -> None:
        """Persist remote model ids fetched from a provider instance's Base URL.

        Hints extend (not replace) the static catalogued models in the UI.
        No-op when the instance has no stored config yet.
        """
        data = self.load()
        conf = data["providers"].get(provider_id)
        if conf is None:
            return
        conf["modelHints"] = list(dict.fromkeys(h for h in hints if isinstance(h, str)))
        self.save(data)

    def provider_conf(self, provider_id: str) -> dict[str, Any] | None:
        """Stored config of one provider instance (by instance id)."""
        return self.load()["providers"].get(str(provider_id or ""))

    def provider_instances(self) -> list[dict[str, Any]]:
        """Every configured provider instance, insertion-stable.

        The stored map is id-keyed; entries carry their own ``id`` + ``type``,
        so the caller renders instances as first-class rows (multiple rows may
        share a type = several OpenAI-compatible gateways).
        """
        return list(_normalize_providers(self.load()["providers"]).values())

    # -- model capability / purpose slots ---------------------------------
    def custom_model_types(self) -> list[dict[str, Any]]:
        """User-defined model types (``[{id,label,type,url,json}]``)."""
        raw = self.load().get("customModelTypes")
        try:
            return _normalize_custom_types(raw) if raw else []
        except SettingsError:  # pragma: no cover - stored file already validated
            return []

    def _custom_type_ids(self) -> set[str]:
        return {str(ct["id"]) for ct in self.custom_model_types()}

    def model_types(self, provider_id: str) -> dict[str, list[str]]:
        """User overrides ``{model_id: [capability, …]}`` for one instance."""
        conf = self.provider_conf(provider_id) or {}
        return _normalize_model_types(
            conf.get("modelTypes"), self._custom_type_ids()
        ) or {}

    def model_slots(self) -> dict[str, dict[str, str]]:
        """Stored purpose slots (``vision`` / ``image`` / ``3D`` / 音视频 …).

        The ``chat`` slot is intentionally absent — it is derived from
        ``activeProviderId`` + that instance's ``model`` so there is only one
        source of truth. Use :meth:`slot_binding("chat")` to read it.
        """
        raw = self.load().get("modelSlots") or {}
        out: dict[str, dict[str, str]] = {}
        for slot in SLOT_ORDER:
            if slot == "chat":
                continue
            v = raw.get(slot)
            if not isinstance(v, dict):
                continue
            iid = str(v.get("instanceId") or "").strip()
            mid = str(v.get("model") or "").strip()
            if iid and mid:
                out[slot] = {"instanceId": iid, "model": mid}
        return out

    def slot_binding(self, slot: str) -> tuple[dict[str, Any], str] | None:
        """Resolve a purpose slot to ``(provider_conf, model_id)``.

        Returns ``None`` when the slot has no usable binding (nothing stored,
        or the referenced instance was deleted). ``chat`` derives from the
        active provider; every other slot reads ``modelSlots``.
        """
        data = self.load()
        if slot == "chat":
            pid = str(data.get("activeProviderId") or "")
            conf = data["providers"].get(pid) if pid else None
            if not conf:
                return None
            model = str(conf.get("model") or "").strip()
            return (conf, model) if model else None
        binding = self.model_slots().get(slot)
        if not binding:
            return None
        conf = data["providers"].get(binding["instanceId"])
        if not conf:
            return None
        return conf, binding["model"]

    def slot_capability(self, slot: str) -> list[str] | None:
        """Capability tags of the model currently bound to ``slot``."""
        hit = self.slot_binding(slot)
        if hit is None:
            return None
        conf, model = hit
        return self.resolve_model_capabilities(str(conf.get("id") or ""), model)

    def resolve_model_capabilities(
        self, provider_id: str, model: str
    ) -> list[str]:
        """Resolved capability tags for one model (override- + custom-aware)."""
        return resolve_capabilities(
            model, self.model_types(provider_id), self._custom_type_ids()
        )[0]

    def agent_config(self) -> dict[str, Any]:
        """Agent 对话参数 with defaults applied (safe even on old files)."""
        defaults = _defaults()["agent"]
        stored = self.load().get("agent") or {}
        return {**defaults, **{k: stored[k] for k in defaults if k in stored}}

    # -- active skills (主 agent 的技能调用范围) -------------------------
    def active_skills(self) -> list[str]:
        """Names of the skills the main agent is allowed to load.

        Empty list = 未启用任何技能(默认)。技能本体仍在磁盘上,只是不进入
        system prompt、也不能被 ``skill_use`` 加载 —— 这就是「技能范围」。
        """
        data = self.load()
        raw = data.get("activeSkills")
        if not isinstance(raw, list):
            return []
        return [str(s) for s in raw if isinstance(s, str) and s.strip()]

    def set_skill_active(self, name: str, active: bool) -> list[str]:
        """Activate / deactivate one skill; returns the new active list."""
        name = str(name or "").strip()
        if not name:
            raise SettingsError("技能名不能为空")
        data = self.load()
        cur = self.active_skills()
        if active:
            if name not in cur:
                cur.append(name)
        else:
            cur = [s for s in cur if s != name]
        data["activeSkills"] = cur
        self.save(data)
        return cur

    def identity(self, identity_id: str) -> Identity | None:
        """Resolve an identity (built-in merged with its tool overrides, or a
        custom one) with its effective enabled tools."""
        data = self.load()
        for bi in BUILTIN_IDENTITIES:
            if bi.id == identity_id:
                ov = data["identityOverrides"].get(identity_id) or {}
                tools = ov.get("enabledTools")
                return Identity(
                    id=bi.id, name=bi.name, emoji=bi.emoji,
                    description=bi.description, persona=bi.persona,
                    recommended_tools=list(bi.recommended_tools),
                    builtin=True,
                    enabled_tools=(list(tools) if isinstance(tools, list) else None),
                )
        custom = data["customIdentities"].get(identity_id)
        if custom:
            return Identity(
                id=custom["id"], name=custom["name"], emoji=custom["emoji"],
                description=custom.get("description", ""),
                persona=custom["persona"],
                recommended_tools=list(custom.get("recommendedTools", [])),
                builtin=False,
                enabled_tools=list(custom.get("enabledTools") or []),
            )
        return None

    def identities_all(self) -> list[Identity]:
        """Every identity (built-ins merged with overrides, then customs)."""
        out = [self.identity(bi.id) for bi in BUILTIN_IDENTITIES]
        for cid in self.load()["customIdentities"]:
            ident = self.identity(cid)
            if ident is not None:
                out.append(ident)
        return [i for i in out if i is not None]

    def active_provider(self) -> dict[str, Any] | None:
        data = self.load()
        pid = data.get("activeProviderId")
        if not pid:
            return None
        conf = data["providers"].get(pid)
        if not conf or not conf.get("apiKey"):
            return None
        return conf

    def active_identity(self) -> Identity:
        data = self.load()
        ident = self.identity(data.get("activeIdentityId") or "assistant")
        return ident or BUILTIN_IDENTITIES[0]
