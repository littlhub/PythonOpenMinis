"""Appearance assets — the locally uploaded background image.

``appearance.background`` holds ``file:<name>`` for an upload; this router owns
the bytes. Kept out of the config layer on purpose: config values are small
scalars with schema validation, not binary blobs.

Storage is a single slot (``backgrounds/background.<ext>``). A background is a
single choice, so keeping old uploads would just accumulate megabytes in the
data directory every time the user tries a different picture.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..core.context import app_context
from ..core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/appearance", tags=["appearance"])

#: 8 MB — generous for a wallpaper, small enough that a stray raw photo or a
#: mis-picked video doesn't land in the data directory.
MAX_BYTES = 8 * 1024 * 1024

ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

#: Fixed stem — see the single-slot note above.
STEM = "background"


def _background_dir() -> Path:
    return app_context().data_dir / "backgrounds"


def _safe_name(name: str) -> str:
    """Reject anything that could escape the backgrounds directory.

    The name is server-generated, but this endpoint is reachable directly, so
    it must not be the caller's word that decides the path.
    """
    if not name or "/" in name or "\\" in name or ".." in name or name != Path(name).name:
        raise HTTPException(status_code=400, detail="非法文件名")
    if Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="不支持的图片格式")
    return name


@router.post("/background")
async def upload_background(file: UploadFile = File(...)) -> dict[str, str]:
    """Store the uploaded image and return the config spec that points at it."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的图片格式: {suffix or '未知'}（支持 png/jpg/webp/gif）",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(data) > MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"图片过大（{len(data) // 1024 // 1024}MB），上限 {MAX_BYTES // 1024 // 1024}MB",
        )

    directory = _background_dir()
    directory.mkdir(parents=True, exist_ok=True)
    # Drop the previous upload first: a .png→.jpg switch would otherwise leave
    # both files behind and nothing would ever clean them up.
    for old in directory.glob(f"{STEM}.*"):
        try:
            old.unlink()
        except OSError:  # pragma: no cover - best effort
            logger.debug("could not remove old background %s", old)

    name = f"{STEM}{suffix}"
    (directory / name).write_bytes(data)
    return {"spec": f"file:{name}", "url": f"/api/appearance/background/{name}"}


@router.get("/background/{name}")
async def get_background(name: str) -> FileResponse:
    safe = _safe_name(name)
    path = _background_dir() / safe
    if not path.is_file():
        raise HTTPException(status_code=404, detail="背景图不存在")
    return FileResponse(path)
