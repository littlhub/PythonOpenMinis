"""REST surface for subagents: CRUD + live registry + LLM planner.

Subagents are worker profiles the main agent can delegate to (see
``openminis.tools.subagent_tool``); this router manages their configuration.
``POST /plan`` sends the live registry to the main agent's LLM and returns a
structured candidate the UI can show before the user saves it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..agent.subagents import (
    SubagentError,
    build_registry,
    delete_subagent,
    get_subagent,
    list_subagents,
    plan_subagent,
    upsert_subagent,
)
from ..core.logging import get_logger
from ..settings.store import SettingsError, SettingsStore

logger = get_logger(__name__)

router = APIRouter(prefix="/api/subagents", tags=["subagents"])


def _store() -> SettingsStore:
    return SettingsStore.get()


@router.get("")
async def subagents_list() -> dict[str, Any]:
    store = _store()
    return {"subagents": list_subagents(store), "registry": build_registry(store)}


@router.get("/registry")
async def subagents_registry() -> dict[str, Any]:
    return build_registry(_store())


@router.post("")
async def subagents_create(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return {"subagent": upsert_subagent(_store(), payload, sid=None)}
    except SubagentError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.put("/{sid}")
async def subagents_update(sid: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return {"subagent": upsert_subagent(_store(), payload, sid=sid)}
    except SubagentError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.delete("/{sid}")
async def subagents_delete(sid: str) -> dict[str, Any]:
    if not delete_subagent(_store(), sid):
        raise HTTPException(status_code=404, detail="not_found")
    return {"ok": True}


@router.post("/plan")
async def subagents_plan(payload: dict[str, Any]) -> dict[str, Any]:
    request = str(payload.get("request") or "").strip()
    auto_save = bool(payload.get("autoSave", False))
    if not request:
        raise HTTPException(status_code=400, detail="缺少 request：请描述你想要的 subagent")
    try:
        return await plan_subagent(_store(), request, auto_save=auto_save)
    except SubagentError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except SettingsError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
