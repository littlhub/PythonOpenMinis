"""聊天附件上传 —— 把文件落到工作区，只在消息里留一个**路径**。

为什么要有这个接口（而不是让前端把图读成 base64 塞进消息）：

* 一张手机照片 5MB，base64 之后 ~6.8MB，进输入框会撑爆受控 ``<textarea>``
  的排版（浏览器主线程直接卡死「页面无响应」），进上下文则按 token 计费，
  一次就把预算烧光；
* 项目本身的约定是 **path-only**（见 ``agent.imageContextMode``）：图片只在
  上下文里留路径，「看懂」交给 ``read_image`` 工具 / 识图槽。

所以上传只做两件事：存字节 + 回一个可以被 ``read_image`` / shell 解析的路径。

存储位置是 ``<workspace>/uploads/`` —— 必须在工作区内，因为工具层
（``file_read_tool._resolve_session_host_path``）只认工作区以内的路径；
放到 ``data_dir`` 会被路径围栏拒掉。
"""

from __future__ import annotations

import mimetypes
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from ..core.context import app_context
from ..core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/upload", tags=["upload"])

#: 32 MB —— 够放原图/PDF/短视频，又不至于让一次误选把工作区塞满。
MAX_BYTES = 32 * 1024 * 1024

#: 图片后缀（决定 ``kind``，前端据此决定是否渲染缩略图）。
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}

#: 明显不该走这个口子的类型（可执行文件）。
_BLOCKED_SUFFIXES = {".exe", ".dll", ".msi", ".bat", ".cmd", ".com", ".scr", ".ps1"}


def uploads_dir() -> Path:
    """``<workspace>/uploads`` —— 工作区内，工具层能解析到。"""
    d = app_context().external_files_dir / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_name(name: str) -> str:
    """只允许 ``uploads`` 目录下的裸文件名 —— 防目录穿越。"""
    if not name or "/" in name or "\\" in name or ".." in name or name != Path(name).name:
        raise HTTPException(status_code=400, detail="非法文件名")
    return name


def _store_name(original: str) -> str:
    """生成一个稳定、唯一、可读的文件名：``20260912-084519-ab12cd34-原文件名``。

    保留原始文件名（用户和模型都更容易认出「这是什么」），前缀时间戳 + 短
    随机串避免同秒覆盖。
    """
    stem = Path(original or "file").stem or "file"
    # 只留安全字符，别让中文/空格/引号进入路径（shell 引用会变麻烦）。
    safe_stem = "".join(ch for ch in stem if ch.isalnum() or ch in "-_")[:40] or "file"
    suffix = Path(original or "").suffix.lower()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:8]}-{safe_stem}{suffix}"


@router.post("")
async def upload(file: UploadFile = File(...)) -> dict[str, object]:
    """存一个附件，回它的绝对路径 / 预览 URL / 元信息。

    把 ``file`` 字段发给本接口即可（multipart/form-data）。
    """
    original = file.filename or "file"
    suffix = Path(original).suffix.lower()
    if suffix in _BLOCKED_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"不允许上传该类型: {suffix}")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(data) > MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"文件过大（{len(data) / 1024 / 1024:.1f}MB），"
                f"上限 {MAX_BYTES // 1024 // 1024}MB"
            ),
        )

    name = _store_name(original)
    target = uploads_dir() / name
    target.write_bytes(data)

    mime = file.content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
    kind = "image" if suffix in IMAGE_SUFFIXES else "file"
    logger.info("upload stored: %s (%s bytes, %s)", target, len(data), kind)
    return {
        "name": original,
        "storedName": name,
        # 正斜杠：进 JSON 不用转义，进 markdown 引用不会被当成转义符，
        # Windows API 与 shell 也都认这种写法。
        "path": target.resolve().as_posix(),
        "url": f"/api/upload/raw?name={name}",
        "mime": mime,
        "size": len(data),
        "kind": kind,
    }


@router.get("/raw")
async def raw(name: str = Query(...)) -> FileResponse:
    """按存储名回读附件字节（前端缩略图 / 预览用）。"""
    path = uploads_dir() / _safe_name(name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="附件不存在")
    return FileResponse(path)
