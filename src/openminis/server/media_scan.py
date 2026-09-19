"""生图产物扫描 —— 给前端「自动预览本轮生成的图」提供路径。

背景：生图走两条路（``image_gen`` 工具写 ``<workspace>/generated/``；
agnes-image 技能脚本写 ``<workspace>/image/``），而**脚本只打印文件名**，
模型也经常忘记在回复里写 ``![说明](绝对路径)`` → 用户「图生成了但看不到」。
与其指望模型，不如此处按 mtime 把**本次调用新产生**的图片找出来，随
``toolEnd`` 帧一起推给前端自动预览。

只看生图专用目录 + 只认最近生成的文件，避免 ``ls`` 之类把历史产物一股脑
推给前端。
"""

from __future__ import annotations

import time
from pathlib import Path

__all__ = [
    "IMAGE_EXTS",
    "GENERATED_SUBDIRS",
    "collect_recent_images",
    "append_image_refs",
]

#: 会被当作「图片」的扩展名。
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif"}

#: 生图产物目录（相对工作区）。image/ = agnes-image 技能脚本；generated/ = image_gen 工具。
GENERATED_SUBDIRS = ("image", "generated")

#: 没给 ``since`` 时的兜底窗口（秒）—— 只认这个时间之后落盘的图。
RECENT_WINDOW_SECONDS = 600.0


def collect_recent_images(
    since: float | None = None, limit: int = 8
) -> list[str]:
    """返回工作区生图目录里 ``since`` 之后新出现的图片（绝对路径，按时间升序）。

    ``since`` 是 Unix 时间戳（``time.time()`` 语义）。取不到工作区、目录不存在
    或读不动时一律返回空列表 —— 自动预览是锦上添花，绝不能拖垮一轮对话。
    """
    try:
        from ..core.context import app_context

        root = Path(app_context().external_files_dir)
    except Exception:  # pragma: no cover - 上下文未就绪
        return []

    cutoff = since if since is not None else time.time() - RECENT_WINDOW_SECONDS
    found: list[tuple[float, str]] = []
    for name in GENERATED_SUBDIRS:
        directory = root / name
        if not directory.is_dir():
            continue
        try:
            # **递归**：技能脚本会把图写进子目录（魔搭写 ``image/modelscope/``，
            # agnes 写 ``image/``），只扫一层就会漏掉 —— 用户现场就是
            # 「图生成了，但前端气泡下没出现、通道那边也没发」。
            entries = [p for p in directory.rglob("*") if p.is_file()]
        except OSError:  # pragma: no cover - 权限/IO
            continue
        for path in entries:
            if path.suffix.lower() not in IMAGE_EXTS:
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:  # pragma: no cover - 文件刚被删
                continue
            if mtime >= cutoff:
                found.append((mtime, str(path)))
    found.sort(key=lambda item: item[0])
    return [path for _mtime, path in found[:limit]]


def append_image_refs(text: str, images: list[str]) -> str:
    """把本轮自动生成的图片补成 ``![生成图](绝对路径)`` 追加到回复末尾。

    前端在 ``toolEnd`` 帧上已经做过一次即时自动预览；这里补的是**落库**的那份
    ——模型忘记写 ``![](路径)`` 时，刷新页面/切换会话后图片依然能渲染出来。
    正文里已经出现过的路径不重复追加。
    """
    if not images:
        return text
    missing = [p for p in images if p and p not in text]
    if not missing:
        return text
    lines = "\n".join(f"![生成图]({path})" for path in missing)
    return f"{text}\n\n{lines}" if text.strip() else lines
