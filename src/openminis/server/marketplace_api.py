"""技能广场 API — 市场源清单与 URL 一键安装."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from ..skills import marketplace
from ..skills.store import SkillError, SkillStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/marketplace", tags=["marketplace"])


class InstallUrlRequest(BaseModel):
    url: str
    force: bool = False


@router.get("")
def sources() -> dict[str, Any]:
    """Curated marketplace source cards (skills + MCP directories)."""
    return {"sources": marketplace.list_sources()}


@router.post("/install-url")
def install_url(req: InstallUrlRequest) -> dict[str, Any]:
    """Download a ``.zip`` skill bundle from *req.url* and install it."""
    store = SkillStore()
    try:
        result = marketplace.install_from_url(store, req.url, force=req.force)
    except SkillError as exc:
        logger.info("marketplace install failed: %s", exc)
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}
