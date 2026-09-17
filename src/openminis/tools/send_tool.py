"""``send`` tool — 把本地产物（图片/文件）交付给用户。

为什么必须有这个工具（而不是让模型直接把路径写进正文）：

1. **硬停收尾依赖它**。``AgentRuntime`` 在连续多轮全被循环护栏拦住时，会切到
   「只允许 send」的收尾轮，先把产物投递出去再做文本总结。工具不存在的话
   ``tu.name in self.tools`` 永远为假 → 整条收尾链空转 → 用户既拿不到图，
   也拿不到总结（实测踩过：生图 4 次后直接断线，前端什么都没有）。
2. **前端要认得出**。图片只有以 ``![说明](路径)`` 的形式出现在消息正文里，
   聊天界面才会渲染；而路径必须是**绝对路径**（前端统一走 ``/api/fs/raw``，
   uploads 之外的图也拿得到）。

Android 侧没有对应工具（通知/分享走的是另一套 offload 通道），所以这里是
Python 移植的补充实现。

这两行引用同时也是**通道插件**（QQ/微信机器人）认的格式：``plugins.bridge``
会把它们从流式正文里摘出来、真的当成图片/文件发出去，所以机器人的会话里同样
能收到产物，而不只是网页端。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..core.logging import get_logger
from ..data.model.agent_tool_definition import AgentToolDefinition, AgentToolParam
from .tool_execution_result import ToolExecutionResult

logger = get_logger(__name__)

__all__ = ["SendTool"]

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico", ".avif"}
_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"}
_DOC_EXTS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md",
    ".csv", ".json", ".zip",
}

_WIN_ABS_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _kind_of(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _IMAGE_EXTS:
        return "图片"
    if ext in _VIDEO_EXTS:
        return "视频"
    if ext in _AUDIO_EXTS:
        return "音频"
    if ext in _DOC_EXTS:
        return "文档"
    return "文件"


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n / 1:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} GB"


def _resolve_any(path: str) -> Path | None:
    """绝对路径（工作区/技能根内）或相对工作区路径 → 真实路径，越界返回 None。"""
    from .path_utils import readonly_roots, resolve_workspace_path, workspace_root

    raw = (path or "").strip().strip('"').strip("'")
    if not raw:
        return None
    if Path(raw).is_absolute() or _WIN_ABS_RE.match(raw):
        resolved = Path(raw).resolve()
        roots = [workspace_root().resolve()]
        try:
            roots.extend(p.resolve() for p in readonly_roots())
        except Exception:  # pragma: no cover - 技能根读不到就不放行
            pass
        for r in roots:
            if resolved == r or r in resolved.parents:
                return resolved
        logger.warning("send rejected path outside roots: %s", path)
        return None
    return resolve_workspace_path(raw)


class SendTool:
    """Kotlin counterpart: none — Android 的产物投递走 offload/分享通道。"""

    NAME = "send"

    @staticmethod
    def definition() -> AgentToolDefinition:
        return AgentToolDefinition(
            name=SendTool.NAME,
            description=(
                "Deliver a LOCAL file you produced (generated image, chart, "
                "report, download) to the user's chat. Pass the file's path; the "
                "tool returns the exact one-line markdown引用 you must paste into "
                "your reply — that is what makes the file visible in the UI. "
                "Use it once per artefact, right after the file exists. Do NOT "
                "pass URLs (put those in the text directly)."
            ),
            parameters={
                "tool_title": AgentToolParam(
                    "string",
                    "A concise 5-10 word summary of what this tool call does, "
                    "shown to the user (e.g. 'Deliver generated poster'). Use "
                    "the same language as the user.",
                ),
                "path": AgentToolParam(
                    "string",
                    "Local file path of the artefact (absolute, or relative to "
                    "the workspace). Not a URL.",
                ),
                "message": AgentToolParam(
                    "string",
                    "Optional short caption to show with the file.",
                ),
            },
            required=["tool_title", "path"],
            property_ordering=["tool_title", "path", "message"],
        )

    @staticmethod
    async def execute(args_json: str, session_id: str) -> ToolExecutionResult:
        try:
            args = json.loads(args_json)
        except (ValueError, TypeError):
            return ToolExecutionResult("Error: invalid JSON args", False,
                                       tool_title=SendTool.NAME)
        tool_title = str(args.get("tool_title", SendTool.NAME))
        raw_path = str(args.get("path", "") or "")
        caption = str(args.get("message", "") or "").strip()

        if raw_path.lower().startswith(("http://", "https://")):
            return ToolExecutionResult(
                "Error: send 只收本地文件路径；URL 直接写进正文即可。",
                False, tool_title=tool_title,
            )

        host = _resolve_any(raw_path)
        if host is None:
            return ToolExecutionResult(
                f"Error: 无法解析路径（必须在工作区内）：{raw_path}",
                False, tool_title=tool_title,
            )
        if not host.is_file():
            return ToolExecutionResult(
                f"Error: 文件不存在：{host}", False, tool_title=tool_title
            )

        kind = _kind_of(host)
        try:
            size = _human_size(host.stat().st_size)
        except OSError:  # pragma: no cover
            size = "?"
        name = host.name
        posix = host.as_posix()

        # 图片用 ![]()；其它文件用 [附件: name]()，前端两种都认。
        ref = f"![{caption or name}]({posix})" if kind == "图片" else f"[附件: {name}]({posix})"
        output = (
            f"已交付给用户：{name}（{kind} · {size}）\n"
            f"请把下面这一行**原样**放进你给用户的回复里（单独成行），"
            f"用户界面上才会显示：\n{ref}"
        )
        return ToolExecutionResult(
            output,
            success=True,
            tool_title=tool_title,
            image_file_path=posix if kind == "图片" else None,
        )
