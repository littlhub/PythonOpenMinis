"""REST API for chat sessions — list/create/load/delete.

Drives the Web chat sidebar (会话): persisted turns let the user come back to
any conversation and start a fresh one without losing history.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from . import chat_store

router = APIRouter(prefix="/api/chats", tags=["chats"])


def _session_dict(info: chat_store.ChatSessionInfo) -> dict[str, Any]:
    return {
        "id": info.id,
        "title": info.title,
        "updatedAt": info.updatedAt,
        "lastMessage": info.lastMessage,
        "folderId": info.folderId,
    }


@router.get("/sessions")
async def list_sessions(
    workspace: str | None = Query(
        default=None,
        description=(
            "Optional workspace filter: pass a folder id, or the sentinel "
            "'unfiled' to list sessions that have no workspace. Omit for all."
        ),
    ),
) -> dict[str, Any]:
    if workspace == "unfiled":
        sessions = await chat_store.list_sessions(folder_id=chat_store.UNFILED)
    elif workspace is not None:
        sessions = await chat_store.list_sessions(folder_id=workspace)
    else:
        sessions = await chat_store.list_sessions()
    return {"sessions": [_session_dict(s) for s in sessions]}


@router.post("/sessions")
async def create_session(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    folder_id = payload.get("folderId")
    if folder_id is not None and not isinstance(folder_id, str):
        raise HTTPException(status_code=400, detail="folderId 必须为字符串或 null")
    info = await chat_store.create_session(folder_id=folder_id)
    return _session_dict(info)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str) -> dict[str, Any]:
    if not await chat_store.delete_session(session_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True}


@router.get("/sessions/{session_id}/messages")
async def session_messages(session_id: str) -> dict[str, Any]:
    if await chat_store.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    rows = await chat_store.load_messages(session_id)
    return {
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "text": m.text,
                "createdAt": m.createdAt,
            }
            for m in rows
        ]
    }