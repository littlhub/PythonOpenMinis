"""聊天消息里的附件解析 —— 决定**字节进不进上下文**。

前端把上传后的附件写成两种 markdown 引用（路径都在工作区的 ``uploads/`` 里）：

* ``![名字](路径)``   —— 图片附件
* ``[附件: 名字](路径)`` —— 非图片附件

这个模块在真正发给模型前按**原 Kotlin/iOS 的做法**（``PreparedAttachments`` /
``AIChatViewModel+Attachments.swift``）把它还原成三部分：

1. **``<user-attached-files>`` XML 清单** —— 每个附件一条 ``<file>``，只有元数据
   （路径/大小/时间）。模型拿到的是「有什么文件、在哪」，需要内容时自己用
   ``read_image`` / ``read_file`` / shell 去取。**这是默认行为**：图片字节不进
   上下文，开销只有一个路径字符串。
2. **``image_parts``** —— 仅当 ``agent.imageContextMode == "inline"`` **且**主对话
   模型有原生视觉时才附上（按 ``agent.imageMaxEdge`` 缩边）。等价于原版的
   ``inlineBudget`` 内联路径。
3. **``[attached image: <路径>]`` 文本标记** —— 附在对应图片的前面，保持「哪张图
   对应哪个路径」的对应关系（原版同样这么贴）。

另外这里会把历史遗留的 ``data:image/...;base64,`` 直接**剥掉**：那种消息会把受控
输入框的排版拖死（用户看到的「页面无响应」就是这么来的），而且 base64 进上下文
等于按图片体积烧 token。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.context import app_context
from ..core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "AttachmentRef",
    "AttachmentBundle",
    "extract_attachments",
    "build_attachment_context",
]

#: ``![alt](src)`` —— src 里不允许空白，避免把后面正文一起吃掉。
_MD_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)\)")

#: ``[附件: 名字](src)`` —— 非图片附件（文件芯片）。
_MD_FILE_RE = re.compile(r"\[附件[:：]\s*(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)\)")

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}

#: 一个回合里最多内联几张图（inline 模式下），与原版的 inlineBudget 同义。
_INLINE_BUDGET = 4


@dataclass(slots=True)
class AttachmentRef:
    """一条附件引用。``kind``: ``image`` / ``file`` / ``url`` / ``missing``。"""

    alt: str
    src: str
    kind: str
    host_path: Path | None = None
    mime: str = "image/png"
    size: int = 0

    @property
    def is_image(self) -> bool:
        return self.kind == "image"


@dataclass(slots=True)
class AttachmentBundle:
    """一次附件解析的结果。

    * ``text`` —— 剥掉内联 base64 之后的**干净消息**，用来落库/展示（保持简短）；
    * ``prompt`` —— 真正发给模型的内容（``text`` + XML 清单 / 图片说明）；
    * ``image_parts`` —— inline 模式下要随消息附上的图片字节；
    * ``dropped`` —— 被丢弃的内联 base64 张数。
    """

    text: str
    prompt: str
    image_parts: list[Any]
    dropped: int = 0
    refs: list[AttachmentRef] = field(default_factory=list)


def _workspace() -> Path:
    return app_context().external_files_dir


def _resolve_local(src: str) -> Path | None:
    """把引用里的 src 解析成本机路径 —— 只认工作区以内的。

    与 ``file_read_tool._resolve_session_host_path`` 同一套围栏：绝对路径必须
    落在 workspace 里，``/var/minis/...`` 前缀按工作区相对路径处理。
    """
    raw = (src or "").strip()
    if not raw or raw.startswith(("data:", "http://", "https://")):
        return None
    workspace = _workspace().resolve()
    for prefix in ("/var/minis/workspace", "/workspace", "/var/minis"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    candidate = Path(raw)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (workspace / raw.lstrip("/")).resolve()
    if resolved == workspace or workspace in resolved.parents:
        return resolved
    logger.warning("attachment rejected (outside workspace): %s", src)
    return None


def _guess_mime(path: Path) -> str:
    return {
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".tiff": "image/tiff",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".json": "application/json",
    }.get(path.suffix.lower(), "image/jpeg")


def _collect(text: str, regex: re.Pattern[str], is_image: bool):
    """按 ``regex`` 收集引用；``data:`` 内联内容就地替换成一句人话并计数。"""
    refs: list[AttachmentRef] = []
    dropped = 0

    def _sub(m: re.Match[str]) -> str:
        nonlocal dropped
        alt = m.group("alt") or ("图片" if is_image else "附件")
        src = m.group("src")
        if src.startswith("data:"):
            dropped += 1
            return f"[{'图片' if is_image else '附件'} {alt}：内联 base64 已丢弃，请用 📎/🖼️ 按钮重新上传]"
        host = _resolve_local(src)
        if host is None:
            refs.append(AttachmentRef(alt=alt, src=src, kind="url"))
            return m.group(0)
        if not host.is_file():
            refs.append(AttachmentRef(alt=alt, src=src, kind="missing"))
            return m.group(0)
        if is_image and host.suffix.lower() not in _IMAGE_SUFFIXES:
            refs.append(AttachmentRef(alt=alt, src=src, kind="missing"))
            return m.group(0)
        try:
            size = host.stat().st_size
        except OSError:  # pragma: no cover
            size = 0
        refs.append(
            AttachmentRef(
                alt=alt,
                src=src,
                kind="image" if is_image else "file",
                host_path=host,
                mime=_guess_mime(host),
                size=size,
            )
        )
        return m.group(0)

    return regex.sub(_sub, text), refs, dropped


def extract_attachments(text: str) -> tuple[str, list[AttachmentRef], int]:
    """把消息拆成 ``(干净文本, 附件引用, 被丢弃的 base64 条数)``。

    ``data:`` URL 一律剥掉并留一句人话说明 —— 用户能看见「附件没进来」，
    而不是对着一个几 MB 的乱码串发懵。
    """
    cleaned, image_refs, d1 = _collect(text or "", _MD_IMAGE_RE, True)
    cleaned, file_refs, d2 = _collect(cleaned, _MD_FILE_RE, False)
    return cleaned, image_refs + file_refs, d1 + d2


def _attached_files_xml(refs: list[AttachmentRef]) -> str:
    """``<user-attached-files>`` 清单 —— 与原 Kotlin/iOS 同格式。

    只给元数据：模型知道「有这些文件、在哪」，要看内容自己调工具。这是
    path-only 的落地方式，也是避免上下文被图片字节吃满的关键。
    """
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    lines = ["<user-attached-files>"]
    for r in refs:
        if r.host_path is None:
            continue
        lines.append(
            f'  <file name="{r.alt}" path="{r.src}" size="{r.size}" '
            f'modified="{stamp}" />'
        )
    lines.append("</user-attached-files>")
    return "\n".join(lines)


def _attachment_hint(
    store: Any,
    images: int,
    files: int,
    inlined: int,
) -> str:
    """附件提示 —— 只负责「告诉主 agent 图在哪、该找谁看」。

    路子由「识图走子代理」开关（``agent.imageVisionSubagent``）决定：

    * 关（默认）—— 与其它工具同一条路：调 ``read_image``，识图槽转文字描述；
    * 开 —— 主 agent 先规划，再把看图这件事用 ``subagent_delegate`` 委派给
      识图子代理（用子代理自己的模型）；没配识图子代理时退回 read_image。
    """
    parts = []
    if images:
        parts.append(f"{images} 张图片")
    if files:
        parts.append(f"{files} 个文件")
    what = "、".join(parts) or "附件"
    bits = [f"（本轮 {what}：只给了你路径，图片内容不在你的上下文里。"]

    via_subagent = False
    vision_subagent: str | None = None
    if images and not inlined:
        try:
            via_subagent = bool(
                store.agent_config().get("imageVisionSubagent") or False
            )
        except Exception:  # pragma: no cover - settings unreadable
            via_subagent = False
        if via_subagent:
            try:
                from ..agent.subagents import find_vision_subagent

                vision_subagent = find_vision_subagent(store)
            except Exception:  # pragma: no cover
                vision_subagent = None

    if images:
        if inlined:
            bits.append(f"{inlined} 张已作为多模态输入随本条消息附上。")
        elif via_subagent and vision_subagent:
            bits.append(
                f"要看图请先规划这一步，再用 subagent_delegate 委派给"
                f"「{vision_subagent}」子代理（用它的模型看图），task 里把这些"
                "路径原样带上。不要凭路径猜测图片内容。"
            )
        else:
            bits.append(
                "要看图请调用 read_image，path 填上面的路径"
                "（识图模型会转成文字描述返回）。不要凭路径猜测图片内容。"
            )
    if files:
        bits.append("文件内容请用 read_file 或 shell 按路径读取。")
    return "".join(bits) + "）"


def _downscaled(host: Path, max_edge: int) -> tuple[bytes, str] | None:
    """按长边上限缩放并重编码为 JPEG；Pillow 缺失/解码失败就返回 ``None``。"""
    try:
        import io

        from PIL import Image

        with Image.open(host) as im:
            im.load()
            w, h = im.size
            if max(w, h) > max_edge:
                scale = max_edge / max(w, h)
                im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            # PNG/GIF 保留原格式（可能有透明/动图），其余转 JPEG。
            if host.suffix.lower() in (".png", ".gif", ".webp"):
                buf = io.BytesIO()
                im.save(buf, format="PNG")
                return buf.getvalue(), "image/png"
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=85)
            return buf.getvalue(), "image/jpeg"
    except Exception:  # pragma: no cover - Pillow missing / broken file
        logger.warning("attachment downscale failed for %s", host, exc_info=True)
        return None


def _max_edge(store: Any) -> int:
    try:
        val = int(store.agent_config().get("imageMaxEdge") or 2000)
    except Exception:  # pragma: no cover
        return 2000
    return max(128, min(8192, val))


async def build_attachment_context(
    store: Any, text: str, *, extra_note: str = ""
) -> AttachmentBundle:
    """给一条用户消息补上附件信息。

    规则（默认 path 模式）：**上下文里只有路径**。图片字节不进消息，后端也
    不替主 agent 读图 —— 主 agent 自己决定把识图交给谁（识图**子代理**，
    子代理带着自己的识图技能/模型；或退回 ``read_image`` 走识图槽）。

    任何失败都退化成「只给路径」，绝不因为附件把整个回合打挂。
    """
    cleaned, refs, dropped = extract_attachments(text)
    images = [r for r in refs if r.is_image]
    files = [r for r in refs if r.kind == "file"]
    notes: list[str] = []
    parts: list[Any] = []

    if refs:
        mode = "path"
        try:
            mode = str(store.agent_config().get("imageContextMode") or "path").lower()
        except Exception:  # pragma: no cover
            pass

        # 路径清单：模型据此知道「有哪些附件、在哪」，需要时自己取。
        notes.append(_attached_files_xml(refs))

        if mode == "inline" and images:
            parts = _inline_image_parts(store, images)

        notes.append(
            _attachment_hint(store, images=len(images), files=len(files), inlined=len(parts))
        )

    if dropped:
        notes.append(f"（{dropped} 条内联 base64 附件已被丢弃，未发送给模型。）")

    note = extra_note.strip()
    if note:
        notes.append(note)
    prompt = cleaned.rstrip() + ("\n\n" + "\n".join(notes) if notes else "")
    return AttachmentBundle(
        text=cleaned, prompt=prompt, image_parts=parts, dropped=dropped, refs=refs
    )


def _inline_image_parts(store: Any, images: list[AttachmentRef]) -> list[Any]:
    """inline 模式：把（缩边后的）图片字节作为 provider 级 image part 附上。

    注意这仍然不是「把 base64 写进消息文本」—— 文本里只有路径，字节走多模态
    通道。但按项目默认约定，这张口是关着的（``imageContextMode="path"``）。
    """
    from ..data.model import LLMMessage

    out: list[Any] = []
    max_edge = _max_edge(store)
    for ref in images[: _INLINE_BUDGET]:
        if ref.host_path is None:
            continue
        scaled = _downscaled(ref.host_path, max_edge)
        if scaled is None:
            continue
        data, mime = scaled
        out.append(
            LLMMessage.ImagePart(
                data=data, mime_type=mime, linux_path=str(ref.host_path)
            )
        )
    return out
