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


def _is_active(entry: Any, active: set[str]) -> bool:
    return entry.name in active or Path(entry.path).name in active


def _skill_dict(entry: Any, active: set[str] | None = None) -> dict[str, Any]:
    return {
        "name": entry.name,
        "description": entry.description,
        "source": entry.source,
        "generated": entry.generated,
        "scripts": list(entry.scripts),
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
