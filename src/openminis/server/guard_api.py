"""沙箱守卫 API —— 拦截事件（异常删除 / 敏感信息）+ 控制台密码。

前端「沙箱」页读这里的接口：

* ``GET  /api/guard/live``              实时检测流水（放行/拦截都显示，内存态）
* ``GET  /api/guard/events``            拦截记录（异常输出 / 调用目录 / 状态）
* ``POST /api/guard/events/{id}/allow`` 手动放行（scope = once|session|always）
* ``POST /api/guard/events/{id}/deny``  维持拦截
* ``POST /api/guard/events/clear``      清空记录（不动白名单）
* ``GET  /api/guard/allowlist``         持久白名单
* ``POST /api/guard/allowlist/revoke``  撤销白名单
* ``GET  /api/guard/console/status``    控制台是否已设密码 / 是否锁定
* ``POST /api/guard/console/unlock``    用密码解锁控制台
* ``POST /api/guard/console/lock``      立即上锁
* ``POST /api/guard/console/password``  设置 / 修改 / 清除控制台密码
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from ..sandbox.console_auth import (
    ACCESS_COOKIE,
    ACCESS_TTL_SECONDS,
    UNLOCK_TTL_SECONDS,
    access_auth,
    console_auth,
)
from ..sandbox.guard import SCOPES, guard

router = APIRouter(prefix="/api/guard", tags=["guard"])

__all__ = ["router"]


class AllowRequest(BaseModel):
    scope: str = "once"


class PasswordRequest(BaseModel):
    password: str = ""
    current: str = ""


_FAMILY_LABELS = {"delete": "异常删除", "secret": "敏感信息", "escape": "目录越界"}


def _event_payload(event: Any) -> dict[str, Any]:
    data = event.as_dict()
    data["familyLabel"] = _FAMILY_LABELS.get(event.family, event.family)
    return data


@router.get("/events")
async def list_events(
    limit: int = Query(100, ge=1, le=300),
    family: str = Query("", description="escape | delete | secret；留空返回全部"),
) -> dict[str, Any]:
    events = guard.events(limit=limit, family=family)
    counts = {"escape": 0, "delete": 0, "secret": 0}
    for e in guard.events(limit=1000):
        counts[e.family] = counts.get(e.family, 0) + 1
    return {
        "events": [_event_payload(e) for e in events],
        "counts": counts,
        "blocked": sum(1 for e in events if e.status == "blocked"),
        "scopes": list(SCOPES),
    }


@router.get("/live")
async def live_feed(limit: int = Query(30, ge=1, le=50)) -> dict[str, Any]:
    """实时检测流水（内存态）：每条被扫描的命令一行，放行/拦截都显示。"""
    return {"items": guard.live(limit=limit)}


@router.post("/events/{event_id}/allow")
async def allow_event(event_id: str, body: AllowRequest) -> dict[str, Any]:
    if body.scope not in SCOPES:
        raise HTTPException(status_code=400, detail="bad_scope")
    event = guard.allow(event_id, body.scope)
    if event is None:
        raise HTTPException(status_code=404, detail="not_found")
    return {"ok": True, "event": _event_payload(event), "scope": body.scope}


@router.post("/events/{event_id}/deny")
async def deny_event(event_id: str) -> dict[str, Any]:
    event = guard.deny(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="not_found")
    return {"ok": True, "event": _event_payload(event)}


@router.post("/events/clear")
async def clear_events() -> dict[str, Any]:
    guard.clear()
    return {"ok": True}


@router.get("/allowlist")
async def get_allowlist() -> dict[str, Any]:
    return {"allowlist": guard.allowlist()}


@router.post("/allowlist/revoke")
async def revoke_allowlist(family: str = "", key: str = "") -> dict[str, Any]:
    return {"ok": True, "allowlist": guard.revoke(family, key)}


# ---------------------------------------------------------------------------
# 控制台密码
# ---------------------------------------------------------------------------
@router.get("/console/status")
async def console_status() -> dict[str, Any]:
    return console_auth.status()


@router.post("/console/unlock")
async def console_unlock(body: PasswordRequest) -> dict[str, Any]:
    if not console_auth.unlock(body.password):
        raise HTTPException(status_code=403, detail="wrong_password")
    return {"ok": True, "ttlSeconds": UNLOCK_TTL_SECONDS}


@router.post("/console/lock")
async def console_lock() -> dict[str, Any]:
    console_auth.lock()
    return {"ok": True}


@router.post("/console/password")
async def console_password(body: PasswordRequest) -> dict[str, Any]:
    ok, error = console_auth.set_password(body.current, body.password)
    if not ok:
        raise HTTPException(status_code=403, detail=error)
    return {"ok": True, **console_auth.status()}


# ---------------------------------------------------------------------------
# 页面访问密码（「用户」按钮里设置；未解锁时其它 API 返回 423）
# ---------------------------------------------------------------------------


@router.get("/access/status")
async def access_status() -> dict[str, Any]:
    return access_auth.status()


@router.post("/access/unlock")
async def access_unlock(body: PasswordRequest, response: Response) -> dict[str, Any]:
    token = access_auth.unlock(body.password)
    if token is None:
        raise HTTPException(status_code=403, detail="wrong_password")
    response.set_cookie(
        ACCESS_COOKIE,
        token,
        max_age=int(ACCESS_TTL_SECONDS),
        httponly=True,
        samesite="lax",
    )
    return {"ok": True, "ttlSeconds": ACCESS_TTL_SECONDS}


@router.post("/access/lock")
async def access_lock(response: Response) -> dict[str, Any]:
    access_auth.lock()
    response.delete_cookie(ACCESS_COOKIE)
    return {"ok": True}


@router.post("/access/password")
async def access_password(body: PasswordRequest, response: Response) -> dict[str, Any]:
    ok, error = access_auth.set_password(body.current, body.password)
    if not ok:
        raise HTTPException(status_code=403, detail=error)
    if not body.password:
        response.delete_cookie(ACCESS_COOKIE)
    return {"ok": True, **access_auth.status()}
