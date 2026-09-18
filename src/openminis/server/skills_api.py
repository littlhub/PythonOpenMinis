"""REST endpoints for the skills pane (Web 技能).

Reads the on-disk ``<data_dir>/skills`` tree: one bundle per skill, each with a
``SKILL.md``. Bundled skills and the generated builtin-tool manifest live in
the same tree, so what the UI lists is exactly what the agent can load.

Activation is separate from installation: ``settings.json`` holds the
``activeSkills`` list, and only activated skills enter the agent's system
prompt / can be loaded by ``skill_use``. That list *is* the main agent's
"技能调用范围".

Install accepts a local directory or a ``.zip`` archive — remote marketplaces
are out of scope for the port, and an on-device agent shouldn't silently fetch
code anyway.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core.logging import get_logger
from ..settings.store import SettingsStore
from ..skills import SkillStore
from ..skills.store import SkillError

logger = get_logger(__name__)

router = APIRouter(prefix="/api/skills", tags=["skills"])


class InstallRequest(BaseModel):
    source: str
    force: bool = False


class EnvRequest(BaseModel):
    values: dict[str, str] = {}


def _is_active(entry: Any, active: set[str]) -> bool:
    return entry.name in active or Path(entry.path).name in active


def _skill_dict(entry: Any, active: set[str] | None = None) -> dict[str, Any]:
    return {
        "name": entry.name,
        "description": entry.description,
        "source": entry.source,
        "generated": entry.generated,
        "scripts": list(entry.scripts),
        # SKILL.md 里声明的环境变量名（``metadata.requires.env`` …）
        "env": list(getattr(entry, "env", ()) or ()),
        "path": entry.path,
        "active": bool(active) and _is_active(entry, active or set()),
    }


def _tool_dict(tool: Any) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
        "required": list(tool.required),
    }


@router.get("")
async def skills_list() -> dict[str, Any]:
    store = SkillStore()
    active = set(SettingsStore.get().active_skills())
    return {
        "skills": [_skill_dict(e, active) for e in store.list()],
        "tools": [_tool_dict(t) for t in SkillStore.builtin_tools()],
        "dir": str(store.root),
        "active": sorted(active),
    }


# -- 环境变量 ---------------------------------------------------------------
# 技能会在 SKILL.md 里声明「我需要这些环境变量」（``metadata.requires.env``，
# 如 modelscope-image 的 MODELSCOPE_API_KEY）。引擎不会自己变出密钥，但可以把
# 「哪些技能需要什么、现在缺哪些」摆到界面上，用户填一次就够。
#
# 值存的是**同一份** ``sandbox.envExtra`` —— 设置页那个「环境变量」编辑器读写的
# 就是它，沙箱 shell 每次执行前会整份注入（``shell_execute_tool._load_env_extra``）。
# 所以在这里填完，技能脚本里的 ``os.getenv`` / ``$VAR`` 立刻就能拿到，不用重启。
def _env_values() -> dict[str, str]:
    from ..core.prefs import get_prefs

    raw = get_prefs().get_string("sandbox.envExtra") or ""
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("sandbox.envExtra 不是合法 JSON，按空处理")
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _env_values_save(values: dict[str, str]) -> None:
    from ..core.prefs import get_prefs

    get_prefs().set_now(
        "sandbox.envExtra", json.dumps(values, ensure_ascii=False, indent=2)
    )


@router.get("/env")
async def skills_env() -> dict[str, Any]:
    """技能声明的环境变量 + 当前值 + 有没有配。"""
    values = _env_values()
    needs: dict[str, list[str]] = {}
    for entry in SkillStore().list():
        if entry.generated:
            continue
        for name in getattr(entry, "env", ()) or ():
            needs.setdefault(name, []).append(entry.name)
    return {
        "required": [
            {"name": name, "skills": sorted(skills), "set": bool(values.get(name))}
            for name, skills in sorted(needs.items())
        ],
        "values": values,
        "count": len(values),
    }


@router.put("/env")
async def skills_env_save(req: EnvRequest) -> dict[str, Any]:
    """整份覆盖环境变量（与设置页那份是同一处存储）。空值 = 删掉该项。"""
    clean = {
        str(k).strip(): str(v)
        for k, v in (req.values or {}).items()
        if str(k).strip() and str(v or "").strip()
    }
    _env_values_save(clean)
    logger.info("skill env updated: %d 项", len(clean))
    return {"ok": True, **await skills_env()}


@router.post("/install")
async def skills_install(req: InstallRequest) -> dict[str, Any]:
    store = SkillStore()
    try:
        entry = store.install(req.source, force=req.force)
    except SkillError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    logger.info("skill installed: %s <- %s", entry.name, req.source)
    return {"ok": True, "skill": _skill_dict(entry)}


@router.get("/{name}")
async def skill_get(name: str) -> dict[str, Any]:
    entry = SkillStore().get(name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"unknown_skill: {name}")
    active = set(SettingsStore.get().active_skills())
    return {**_skill_dict(entry, active), "content": entry.body}


@router.post("/{name}/activate")
async def skill_activate(name: str) -> dict[str, Any]:
    """Put a skill in scope for the main agent (可被调用)."""
    entry = SkillStore().get(name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"unknown_skill: {name}")
    store = SettingsStore.get()
    active = store.set_skill_active(entry.name, True)
    return {"ok": True, "name": entry.name, "active": active}


@router.post("/{name}/deactivate")
async def skill_deactivate(name: str) -> dict[str, Any]:
    entry = SkillStore().get(name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"unknown_skill: {name}")
    store = SettingsStore.get()
    active = store.set_skill_active(entry.name, False)
    return {"ok": True, "name": entry.name, "active": active}


@router.delete("/{name}")
async def skill_delete(name: str) -> dict[str, Any]:
    try:
        removed = SkillStore().uninstall(name)
    except SkillError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    if not removed:
        raise HTTPException(status_code=404, detail=f"unknown_skill: {name}")
    # don't leave a dangling entry in the active scope
    try:
        SettingsStore.get().set_skill_active(name, False)
    except Exception:  # pragma: no cover - best effort cleanup
        logger.debug("could not clear active flag for %s", name, exc_info=True)
    return {"ok": True, "name": name}
