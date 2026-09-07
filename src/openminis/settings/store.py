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
