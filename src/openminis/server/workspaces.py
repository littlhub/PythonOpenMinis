"""Workspace (session folder) persistence.

A workspace is a named group ("folder") that sessions can belong to. The
sessions tab lets the user pick a workspace; new chats inherit the active
workspace until they are moved (PATCH /sessions/{id}) or deleted.

Mirrors the Kotlin/iOS ``folders`` table. SQLAlchemy treats ``folder_id``
as a non-FK index to preserve the "orphan = ungrouped" semantics the
originals ship with (see [T-android-session-grouping]).

Each workspace can optionally point at a real filesystem path (the
``workspace_paths`` JSON). When set, sessions filed under that workspace
boot their shell inside that directory; the file tree shown in the right
panel also follows it. A missing path falls back to the auto-generated
``external_files_dir/workspaces/<slug>/s-<id>`` sandbox.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.context import app_context
from ..data.db.chat_dao import ChatDao
from ..data.db.folder_entity import FolderEntity
from . import chat_store

__all__ = ["WorkspaceInfo", "list_workspaces", "create_workspace",
           "rename_workspace", "delete_workspace", "assign_session_workspace",
           "sandbox_dir_for_session", "set_workspace_path",
           "get_workspace_path", "router"]


@dataclass(frozen=True)
class WorkspaceInfo:
    id: str
    name: str
    description: str
    updatedAt: int
    pinned: bool
    sessionCount: int
    path: str = ""


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


async def _to_info(folder: FolderEntity, count: int) -> WorkspaceInfo:
    return WorkspaceInfo(
        id=folder.id,
        name=folder.name,
        description=(folder.description or ""),
        updatedAt=folder.updated_at,
        pinned=bool(folder.pinned_at),
        sessionCount=count,
        path=get_workspace_path(folder.id),
    )


async def list_workspaces() -> list[WorkspaceInfo]:
    await chat_store.ensure_db()
    async with chat_store._get_db().session() as s:  # noqa: SLF001 - private but stable
        dao = ChatDao(s)
        folders = await dao.list_folders()
        out: list[WorkspaceInfo] = []
        for f in folders:
            count = await dao.session_count_in_folder(f.id)
            out.append(await _to_info(f, count))
    return out


async def create_workspace(name: str, description: str = "") -> WorkspaceInfo:
    await chat_store.ensure_db()
    name = (name or "").strip() or "新工作空间"
    description = (description or "").strip()[: FolderEntity.DESC_MAX_CHARS]
    now = _now_ms()
    folder = FolderEntity(
        id=uuid.uuid4().hex,
        name=name,
        description=description or None,
        icon=None,
        color=None,
        origin=FolderEntity.ORIGIN_MANUAL,
        sort_index=0,
        pinned_at=None,
        created_at=now,
        updated_at=now,
    )
    async with chat_store._get_db().session() as s:  # noqa: SLF001
        await ChatDao(s).insert_folder(folder)
        return await _to_info(folder, 0)


async def rename_workspace(folder_id: str, name: str, description: str | None = None) -> WorkspaceInfo:
    await chat_store.ensure_db()
    name = (name or "").strip()
    if not name:
        raise ValueError("工作空间名不能为空")
    async with chat_store._get_db().session() as s:  # noqa: SLF001
        dao = ChatDao(s)
        folder = await dao.get_folder(folder_id)
        if folder is None:
            raise KeyError("workspace_not_found")
        await dao.rename_folder(
            folder_id,
            name,
            description,  # None keeps current; empty string clears it
            _now_ms(),
        )
        # ``rename_folder`` commits, which expires the instance; ``get_folder``
        # returns the cached (identity-mapped) object so attribute access would
        # try a sync reload. ``await s.refresh(folder)`` forces the async one
        # while the session is open.
        await s.refresh(folder)
        count = await dao.session_count_in_folder(folder_id)
        return await _to_info(folder, count)


# -----------------------------------------------------------------------
# Workspace → optional real filesystem path
# -----------------------------------------------------------------------

_PATHS_FILE = "workspace_paths.json"
_paths_lock = threading.Lock()
_paths_cache: dict[str, str] | None = None


def _paths_path() -> Path:
    return app_context().data_dir / _PATHS_FILE


def _load_paths() -> dict[str, str]:
    global _paths_cache
    if _paths_cache is not None:
        return _paths_cache
    p = _paths_path()
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            _paths_cache = {str(k): str(v) for k, v in data.items() if isinstance(v, str)}
        except Exception:
            _paths_cache = {}
    else:
        _paths_cache = {}
    return _paths_cache


def _flush_paths() -> None:
    p = _paths_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(_paths_cache or {}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(p)


def get_workspace_path(workspace_id: str) -> str:
    """Path the workspace is rooted at, or '' if none configured."""
    return _load_paths().get(workspace_id, "")


def set_workspace_path(workspace_id: str, raw: str) -> str:
    """Persist a path for a workspace.

    Returns the normalized path actually saved. Empty string clears it.
    An invalid path raises ``ValueError`` so the API can return HTTP 400.
    """
    global _paths_cache
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        with _paths_lock:
            _load_paths().pop(workspace_id, None)
            _flush_paths()
        return ""
    p = Path(text).expanduser()
    # We do NOT require it to exist on disk — many users point at a project
    # they haven't created yet, or a network share that's offline. Just reject
    # obvious garbage (relative paths, NUL, control chars).
    if not p.is_absolute():
        raise ValueError("路径必须是绝对路径(例如 D:\\projects\\foo)")
    if any(ord(c) < 0x20 for c in str(p)):
        raise ValueError("路径包含非法字符")
    with _paths_lock:
        _load_paths()[workspace_id] = str(p)
        _flush_paths()
    return str(p)


async def delete_workspace(folder_id: str) -> bool:
    """Delete a workspace; member sessions fall back to ``folder_id=NULL``
    (ungrouped) automatically by the CASCADE-free schema."""
    await chat_store.ensure_db()
    async with chat_store._get_db().session() as s:  # noqa: SLF001
        dao = ChatDao(s)
        if await dao.get_folder(folder_id) is None:
            return False
        # Unfile members first so they don't point at a missing folder.
        await dao.clear_folder_for_sessions(folder_id)
        await dao.delete_folder(folder_id)
    # Also drop any pinned path so the map doesn't accumulate orphans.
    global _paths_cache
    with _paths_lock:
        if _paths_cache is not None and folder_id in _paths_cache:
            _paths_cache.pop(folder_id, None)
            try:
                _flush_paths()
            except OSError:
                pass
    return True


async def assign_session_workspace(session_id: str, folder_id: str | None) -> bool:
    await chat_store.ensure_db()
    async with chat_store._get_db().session() as s:  # noqa: SLF001
        dao = ChatDao(s)
        if await dao.get_session(session_id) is None:
            return False
        if folder_id is not None and await dao.get_folder(folder_id) is None:
            raise ValueError("workspace_not_found")
        await dao.set_session_folder(session_id, folder_id)
    return True


# -----------------------------------------------------------------------
# Workspace → sandbox directory mapping
# -----------------------------------------------------------------------

_WORD_RE = re.compile(r"[^\w\u4e00-\u9fff-]+", re.UNICODE)


def _slug(name: str) -> str:
    """Turn a workspace name into a safe directory component."""
    slug = _WORD_RE.sub("-", (name or "").strip()).strip("-").lower()
    return (slug or "ws")[:48]


async def sandbox_dir_for_session(session_id: str) -> Path | None:
    """Directory a filed session's shell boots into, if any.

    Order of preference:
    1. The workspace's user-configured ``path`` (if it still resolves to an
       existing directory on the host).
    2. ``external_files_dir/workspaces/<slug(name)>/s-<session-prefix>``
       (created on demand).

    Ungrouped sessions return ``None`` and keep the global default
    (``external_files_dir/<session_id>``).
    """
    await chat_store.ensure_db()
    async with chat_store._get_db().session() as s:  # noqa: SLF001
        dao = ChatDao(s)
        sess = await dao.get_session(session_id)
        if sess is None or not sess.folder_id:
            return None
        folder = await dao.get_folder(sess.folder_id)
        if folder is None:
            return None
        name = folder.name or "工作空间"
        folder_id = folder.id
    user_path = get_workspace_path(folder_id)
    if user_path:
        candidate = Path(user_path)
        if candidate.is_dir():
            return candidate
    base = app_context().external_files_dir / "workspaces" / _slug(name)
    target = base / f"s-{session_id[:12]}"
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return target


# -----------------------------------------------------------------------
# FastAPI router
# -----------------------------------------------------------------------
from fastapi import APIRouter, HTTPException  # noqa: E402

router = APIRouter(prefix="/api/chats", tags=["chats"])


def _workspace_dict(info: WorkspaceInfo) -> dict[str, Any]:
    return {
        "id": info.id,
        "name": info.name,
        "description": info.description,
        "updatedAt": info.updatedAt,
        "pinned": info.pinned,
        "sessionCount": info.sessionCount,
        "path": info.path,
    }


@router.get("/workspaces")
async def list_workspaces_endpoint() -> dict[str, Any]:
    return {"workspaces": [_workspace_dict(w) for w in await list_workspaces()]}


@router.post("/workspaces")
async def create_workspace_endpoint(payload: dict[str, Any]) -> dict[str, Any]:
    info = await create_workspace(
        str(payload.get("name") or ""),
        str(payload.get("description") or ""),
    )
    raw_path = str(payload.get("path") or "")
    if raw_path.strip():
        try:
            normalized = set_workspace_path(info.id, raw_path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        info = WorkspaceInfo(
            id=info.id, name=info.name, description=info.description,
            updatedAt=info.updatedAt, pinned=info.pinned,
            sessionCount=info.sessionCount, path=normalized,
        )
    return _workspace_dict(info)


@router.patch("/workspaces/{folder_id}")
async def rename_workspace_endpoint(folder_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if "path" in payload:
        try:
            set_workspace_path(folder_id, str(payload.get("path") or ""))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    try:
        info = await rename_workspace(
            folder_id,
            str(payload.get("name") or ""),
            None if "description" not in payload else str(payload.get("description") or ""),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="工作空间不存在")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _workspace_dict(info)


@router.delete("/workspaces/{folder_id}")
async def delete_workspace_endpoint(folder_id: str) -> dict[str, Any]:
    if not await delete_workspace(folder_id):
        raise HTTPException(status_code=404, detail="工作空间不存在")
    return {"ok": True}


@router.patch("/sessions/{session_id}/workspace")
async def move_session_endpoint(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    folder_id = payload.get("folderId")
    if folder_id is not None and not isinstance(folder_id, str):
        raise HTTPException(status_code=400, detail="folderId 必须为字符串或 null")
    try:
        ok = await assign_session_workspace(session_id, folder_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="工作空间不存在")
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True}