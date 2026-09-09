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
import threading
from pathlib import Path
from typing import Any

from ..core.context import app_context
from ..core.logging import get_logger
from .catalog import BUILTIN_IDENTITIES, Identity, PROVIDER_TYPES

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
        #: type -> {type, apiKey, baseUrl, model}
        "providers": {},
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
        #: Agent 对话运行参数（对应 UI「模型设置」里的 Agent 参数项）。
        #: maxContextTokens=超过该预算时智能压缩; maxMemoryRounds=几轮问答后
        #: 压缩; maxToolSteps=单次对话工具调用上限; deepThinking=是否深度思考。
        "agent": {
            "maxContextTokens": 511998,
            "maxMemoryRounds": 30,
            "maxToolSteps": 40,
            "deepThinking": False,
        },
    }


def _valid_type(provider_type: str) -> bool:
    return any(p.type == provider_type for p in PROVIDER_TYPES)


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

        # providers: full replacement list of {type, apiKey, baseUrl, model}
        if "providers" in payload:
            new_providers: dict[str, dict] = {}
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
                entry = {"type": ptype}
                api_key = conf.get("apiKey")
                if isinstance(api_key, str) and api_key:
                    entry["apiKey"] = api_key
                elif ptype in data["providers"]:
                    # blank key on update = keep existing secret
                    entry["apiKey"] = data["providers"][ptype].get("apiKey", "")
                base_url = conf.get("baseUrl")
                entry["baseUrl"] = (base_url if isinstance(base_url, str) else "").strip()
                model = conf.get("model")
                entry["model"] = (model if isinstance(model, str) else "").strip()
                # remote model hints are owned by fetch-models; a plain save
                # must not wipe them
                if ptype in data["providers"]:
                    hints = data["providers"][ptype].get("modelHints")
                    if isinstance(hints, list) and all(
                        isinstance(h, str) for h in hints
                    ):
                        entry["modelHints"] = hints
                new_providers[ptype] = entry
            data["providers"] = new_providers

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
                if not _valid_type(aid):
                    errors.append(f"未知厂商: {aid}")
                elif aid not in data["providers"]:
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
                }
                for key, (lo, hi) in ints.items():
                    if key in raw:
                        try:
                            agent[key] = max(lo, min(hi, int(raw[key])))
                        except (TypeError, ValueError):
                            errors.append(f"agent.{key} 必须是整数")
                if "deepThinking" in raw:
                    dt = raw["deepThinking"]
                    agent["deepThinking"] = (
                        dt if isinstance(dt, bool) else str(dt).lower() in ("1", "true", "on")
                    )
                data["agent"] = agent

        if errors:
            raise SettingsError("; ".join(errors))
        self.save(data)
        return data

    # -- read helpers ------------------------------------------------------
    def set_model_hints(self, provider_type: str, hints: list[str]) -> None:
        """Persist remote model ids fetched from a provider's Base URL.

        Hints extend (not replace) the static catalogued models in the UI.
        No-op when the provider has no stored config yet.
        """
        data = self.load()
        conf = data["providers"].get(provider_type)
        if conf is None:
            return
        conf["modelHints"] = list(dict.fromkeys(h for h in hints if isinstance(h, str)))
        self.save(data)

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
