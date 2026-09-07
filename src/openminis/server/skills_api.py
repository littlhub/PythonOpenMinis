"""REST endpoints for the skills pane (Web 技能).

Reads the on-disk ``<data_dir>/skills`` tree: one bundle per skill, each with a
``SKILL.md``. Bundled skills and the generated builtin-tool manifest live in
the same tree, so what the UI lists is exactly what the agent can load.

Install accepts a local directory or a ``.zip`` archive — remote marketplaces
are out of scope for the port, and an on-device agent shouldn't silently fetch
code anyway.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core.logging import get_logger
from ..skills import SkillStore
from ..skills.store import SkillError

logger = get_logger(__name__)

router = APIRouter(prefix="/api/skills", tags=["skills"])


class InstallRequest(BaseModel):
    source: str
    force: bool = False


def _skill_dict(entry: Any) -> dict[str, Any]:
    return {
        "name": entry.name,
        "description": entry.description,
        "source": entry.source,
        "generated": entry.generated,
        "scripts": list(entry.scripts),
        "path": entry.path,
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
    return {
        "skills": [_skill_dict(e) for e in store.list()],
        "tools": [_tool_dict(t) for t in SkillStore.builtin_tools()],
        "dir": str(store.root),
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
    return {**_skill_dict(entry), "content": entry.body}


@router.delete("/{name}")
async def skill_delete(name: str) -> dict[str, Any]:
    try:
        removed = SkillStore().uninstall(name)
    except SkillError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    if not removed:
        raise HTTPException(status_code=404, detail=f"unknown_skill: {name}")
    return {"ok": True, "name": name}
