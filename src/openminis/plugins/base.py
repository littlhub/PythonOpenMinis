"""[T-channels-plugins] 通道适配器的统一接口。

一个「通道」= 一个 IM 平台（QQ / 微信 / 飞书 / Telegram…）。适配器只做平台那
一层的事：登录、收发消息、心跳重连、把平台事件归一化成 :class:`IncomingMessage`。
「会话怎么映射、命令怎么处理、怎么调引擎」全部在 :mod:`openminis.plugins.bridge`
里做一次 —— 这样再加平台只要写一个新适配器。

附件（图片与文件）走**两条对称的窄接口**，公共逻辑只写一遍：

* 入站：适配器实现 :meth:`ChannelAdapter.fetch_attachment`（把平台的附件 URL
  变成字节），「哪些要收 / 存哪 / 怎么命名 / 多大算过大」由
  :func:`save_attachments` 统一处理 —— 落进工作区的 ``uploads/``，因为引擎侧的
  附件解析（``settings/attachments.py``）只认工作区以内的路径。
* 出站：适配器实现 :meth:`ChannelAdapter.send_image` / :meth:`send_file`；不实现
  也不会静默丢，基类会退化成发一条「[图片] 路径」/「[附件] 路径」的文本。

设计借自 dsh-bridge 的 Platform 抽象（login/start/stop/sendText/getStatus），
但实现是 Python 侧重写的：它的内核绑在 DeepSeek Harness 上，接不到本引擎的
模型、工具、技能与记忆。
"""

from __future__ import annotations

import abc
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

#: 入站消息的处理函数（由 bridge 提供）。
MessageHandler = Callable[["IncomingMessage"], Awaitable[None]]

#: 状态机取值。
STATE_IDLE = "idle"
STATE_STARTING = "starting"
STATE_CONNECTED = "connected"
STATE_RECONNECTING = "reconnecting"
STATE_ERROR = "error"
STATE_STOPPED = "stopped"

#: 每个适配器保留多少条日志给界面看。
LOG_LINES = 120

#: 会被当作「图片」的扩展名 —— 与 ``server.media_scan`` 保持同一套。
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".avif"}

#: 扩展名 → MIME。QQ 富媒体只认 png/jpg，别处按需取用。
IMAGE_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".avif": "image/avif",
}

#: 一条入站消息最多落几个附件 —— 有人一口气丢 50 个也不该把工作区塞满。
MAX_INCOMING_ATTACHMENTS = 8

#: 单个入站附件的字节上限（收这一侧；**发**出去的上限由各平台自己定）。
MAX_ATTACHMENT_BYTES = 32 * 1024 * 1024

#: 一律不收的附件后缀（可执行文件）—— 与 ``server/upload_api`` 保持一致。
#: 有人把 .exe 丢进群里，agent 又恰好能跑 shell，那就是一条现成的执行链。
BLOCKED_SUFFIXES = {".exe", ".dll", ".msi", ".bat", ".cmd", ".com", ".scr", ".ps1"}

#: 附件被当成「通用二进制」的类型 —— 这时才回头用文件名/URL 后缀判断。
_VAGUE_MIMES = ("application/octet-stream", "binary/octet-stream", "")

#: 可安全写进工作区的后缀：字母数字、短。别的（含没有后缀）一律换成兜底后缀。
_SAFE_SUFFIX_RE = re.compile(r"^\.[A-Za-z0-9]{1,8}$")


def attachment_ext(att: dict[str, Any]) -> str:
    """从附件的文件名或 URL 猜后缀（小写，带点）；猜不出返回空串。"""
    for key in ("filename", "name", "file_name"):
        ext = Path(str(att.get(key) or "")).suffix.lower()
        if ext:
            return ext
    url = str(att.get("url") or att.get("file_url") or "")
    return Path(url.split("?")[0]).suffix.lower()


def attachment_name(att: dict[str, Any]) -> str:
    """附件的原始文件名；平台没给就从 URL 尾巴上取一个。"""
    for key in ("filename", "name", "file_name"):
        value = str(att.get(key) or "").strip()
        if value:
            return value
    url = str(att.get("url") or att.get("file_url") or "")
    if url:
        tail = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        if tail:
            return tail
    return ""


def attachment_is_image(att: dict[str, Any]) -> bool:
    """这条入站附件是不是图片。

    优先信平台给的 ``content_type``；只给「通用二进制」或干脆没给时，才回头
    看文件名/URL 后缀 —— 有些平台（含 QQ 的部分场景）就是这么含糊。
    """
    ctype = str(att.get("content_type") or att.get("contentType") or "").strip().lower()
    # ``image/jpeg`` 是常见写法，但有的平台只给大类 ``image`` —— 都算图。
    if ctype.startswith("image/") or ctype == "image":
        return True
    if ctype not in _VAGUE_MIMES:
        return False
    return attachment_ext(att) in IMAGE_EXTS


def attachment_kind(att: dict[str, Any]) -> str:
    """``"image"`` / ``"file"``；既没名字也没 URL 的条目返回空串（跳过）。"""
    if attachment_is_image(att):
        return "image"
    return "file" if attachment_name(att) else ""


def sandbox_form(path: str | Path) -> str:
    """路径的「沙箱写法」—— 发给聊天对象时不该出现 ``C:\\Users\\<名字>\\…``。

    平台适配器要发的是文件本身；只有在发不出去、退化成发路径文本时才用得上它。
    """
    try:
        from ..tools.path_utils import to_sandbox_path

        return to_sandbox_path(path)
    except Exception:  # pragma: no cover - 路径工具不可用就原样给
        return str(path)


def channel_file_name(
    original: str, *, prefix: str = "im", fallback_suffix: str = ".bin"
) -> str:
    """给落盘的入站附件起个稳定、唯一、可读的名字。

    与 ``server/upload_api._store_name`` 同一套思路（时间戳 + 短随机 + 干净
    文件名），只是前缀换成了通道标记 —— 事后一眼能看出「这是从 IM 来的」，
    和用户自己上传的附件区分开。

    后缀只保留「字母数字且短」的那类；像 ``.tar.gz`` 的第二段、带空格的怪
    后缀一律换成 ``fallback_suffix``，别让文件名变成注入点。
    """
    stem = Path(original or "file").stem or "file"
    safe = "".join(ch for ch in stem if ch.isalnum() or ch in "-_")[:40] or "file"
    suffix = Path(original or "").suffix.lower()
    if not _SAFE_SUFFIX_RE.match(suffix) or suffix in BLOCKED_SUFFIXES:
        suffix = fallback_suffix
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{prefix}-{uuid.uuid4().hex[:8]}-{safe}{suffix}"


@dataclass
class IncomingMessage:
    """归一化后的入站消息 —— 各平台适配器都产出这个形状。"""

    #: ``c2c``（私聊）/ ``group``（群聊）。回复端点由它决定。
    scope: str
    #: 回复目标：私聊是用户 openid，群聊是群 openid。
    peer_id: str
    #: 发送者 id（群里是成员 openid）。
    sender_id: str
    text: str
    scope_name: str = ""
    message_id: str = ""
    event_id: str = ""
    #: 被动回复序号：同一条消息回复多条时递增（QQ 用它去重）。
    msg_seq: int | None = None
    #: 收到这条消息的时刻（``time.time()``）。
    #:
    #: 被动回复是有**有效期**的（QQ：单聊 60 分钟、群聊 5 分钟），超时后平台会
    #: 静默拒收带 ``msg_id`` 的回复。所以适配器要能算出「这条消息我还能被动回
    #: 多久」，超了就得改走主动消息 —— 否则用户看到的就是「它明明干活了，却什么
    #: 都没发回来」。
    received_at: float = 0.0
    attachments: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def conv_key(self) -> str:
        """会话映射键：同一个群/同一个私聊始终落到同一个会话。"""
        return f"{self.scope}:{self.peer_id}"

    @property
    def is_group(self) -> bool:
        return self.scope == "group"


@dataclass
class ChannelState:
    state: str = STATE_IDLE
    detail: str = ""
    account: str = ""
    error: str = ""
    since: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "detail": self.detail,
            "account": self.account,
            "error": self.error,
            "since": int(self.since * 1000),
        }


class ChannelAdapter(abc.ABC):
    """平台适配器基类。子类至少要实现 ``start`` / ``stop`` / ``send_text``。"""

    #: 与清单里的 ``driver`` 对应。
    driver: str = ""

    def __init__(
        self,
        *,
        plugin_id: str,
        name: str = "",
        config: dict[str, Any] | None = None,
        on_message: MessageHandler | None = None,
        max_message_chars: int = 2000,
    ) -> None:
        self.plugin_id = plugin_id
        self.name = name or plugin_id
        self.config: dict[str, Any] = dict(config or {})
        self.max_message_chars = int(max_message_chars or 2000)
        self._on_message = on_message
        self.state = ChannelState()
        self._logs: deque[dict[str, Any]] = deque(maxlen=LOG_LINES)

    # -- 日志（界面要能看到「为什么没连上」）--------------------------------
    def log(self, message: str, level: str = "info") -> None:
        self._logs.append({
            "ts": int(time.time() * 1000),
            "level": level,
            "text": str(message),
        })

    def logs(self, limit: int = 60) -> list[dict[str, Any]]:
        items = list(self._logs)
        return items[-max(1, int(limit)):]

    def set_state(self, state: str, detail: str = "", error: str = "") -> None:
        self.state.state = state
        self.state.detail = detail
        self.state.error = error
        self.state.since = time.time()

    # -- 生命周期 ---------------------------------------------------------
    @abc.abstractmethod
    async def start(self) -> None:
        """起连接（幂等：已在跑就什么也不做）。"""

    @abc.abstractmethod
    async def stop(self) -> None:
        """停连接（保留配置与状态文件）。"""

    # -- 出站 -------------------------------------------------------------
    @abc.abstractmethod
    async def send_text(
        self, msg: IncomingMessage | None, text: str, **kwargs: Any
    ) -> None:
        """发文本。``msg`` 是触发这条回复的入站消息（被动回复要带它的 id）。"""

    async def send_typing(self, msg: IncomingMessage | None) -> None:
        """「正在输入」。平台不支持时什么都不做。"""
        return None

    async def send_ack(self, msg: IncomingMessage | None, text: str) -> bool:
        """收到消息先回一句「收到，正在处理…」，返回「真的发出去了吗」。

        为什么要有这一步：收附件（下载几 MB）+ 跑一轮（动辄几分钟，生图更久）
        期间，用户在 IM 里看不到任何东西 —— 体感就是「消息发丢了」。先给回执，
        比结果早到几秒更重要。

        默认就是发一条普通文本。**有额度限制的平台要覆盖它**（QQ 的被动回复
        群聊只有 5 次）：额度不足时宁可返回 False 不发这一句，也不能把真正的
        结果挤掉。
        """
        try:
            await self.send_text(msg, text)
        except Exception:  # pragma: no cover - 回执失败不该打断正事
            return False
        return True

    async def send_image(
        self, msg: IncomingMessage | None, path: str | Path, **kwargs: Any
    ) -> bool:
        """发一张本地图片，返回「平台真的收下了吗」。

        默认实现不支持富媒体 —— 退化成把路径当文本发出去。用户至少知道图生成
        在哪、能自己去拿，比静默丢掉强得多。支持图片的平台覆盖这个方法。
        """
        await self.send_text(msg, f"[图片] {sandbox_form(path)}", **kwargs)
        return False

    async def send_file(
        self, msg: IncomingMessage | None, path: str | Path, **kwargs: Any
    ) -> bool:
        """发一个本地文件（PDF / 表格 / 压缩包…）。语义与 :meth:`send_image` 一致。

        图片与文件分开是因为平台侧本来就是两套限制（QQ 的 ``file_type`` 不同、
        大小上限也不同），合成一个「发媒体」反而要在实现里再判一次类型。
        """
        await self.send_text(msg, f"[附件] {sandbox_form(path)}", **kwargs)
        return False

    async def fetch_attachment(
        self, att: dict[str, Any]
    ) -> tuple[bytes, str] | None:
        """下载一条入站附件的字节，返回 ``(字节, 建议文件名)``。

        默认不实现（返回 ``None``）—— 桥那边会跳过它并记一条日志，不会因为这
        个平台收不了附件就把整条消息丢掉。
        """
        return None

    # -- 状态 -------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "driver": self.driver,
            "state": self.state.state,
            "detail": self.state.detail,
            "account": self.state.account,
            "error": self.state.error,
            "since": int(self.state.since * 1000),
            "maxMessageChars": self.max_message_chars,
        }

    @property
    def running(self) -> bool:
        return self.state.state in (STATE_STARTING, STATE_CONNECTED, STATE_RECONNECTING)

    # -- 给子类用的小工具 --------------------------------------------------
    async def _dispatch(self, msg: IncomingMessage) -> None:
        """把入站消息交给 bridge。适配器不关心它被怎么处理。"""
        if self._on_message is None:
            self.log("收到消息但没有处理器，忽略", "warn")
            return
        try:
            await self._on_message(msg)
        except Exception as exc:  # pragma: no cover - 处理失败不能拖垮连接
            self.log(f"处理消息失败：{exc}", "error")

    def split_text(self, text: str) -> list[str]:
        """按平台单条上限切分：优先在换行 / 句读处断开，避免切碎中英混排。"""
        body = str(text or "")
        limit = max(80, self.max_message_chars)
        if len(body) <= limit:
            return [body] if body else []
        chunks: list[str] = []
        rest = body
        while len(rest) > limit:
            window = rest[:limit]
            cut = max(
                window.rfind("\n"),
                window.rfind("。"),
                window.rfind("！"),
                window.rfind("？"),
                window.rfind(". "),
            )
            if cut < limit // 2:
                cut = limit
            else:
                cut += 1
            chunks.append(rest[:cut])
            rest = rest[cut:]
        if rest:
            chunks.append(rest)
        return chunks


async def save_attachments(
    adapter: "ChannelAdapter",
    msg: IncomingMessage,
    dest_dir: Path,
    *,
    limit: int = MAX_INCOMING_ATTACHMENTS,
    allow_files: bool = True,
) -> list[tuple[str, Path]]:
    """把入站消息里的附件落到 ``dest_dir``，返回 ``[(kind, 路径)]``（按到达顺序）。

    ``kind`` 是 ``"image"`` 或 ``"file"`` —— 桥据此决定贴成 ``![](路径)`` 还是
    ``[附件: 名字](路径)``（引擎侧的附件解析就认这两种写法）。

    平台适配器只负责 ``fetch_attachment``；「哪些要收、存哪、怎么命名、多大算
    过大」这套判断在这里做一次，新增平台不用重写一遍。

    三条安全线：可执行后缀一律不收（agent 手里有 shell，收个 .exe 就等于把一条
    执行链递过去）、单条消息有条数上限、单个文件有字节上限。任何一步失败只记日志
    然后跳过那一个 —— 收附件是加分项，绝不能把一条本来能正常回答的消息整条丢掉。
    """
    saved: list[tuple[str, Path]] = []
    skipped_blocked: list[str] = []
    for att in msg.attachments:
        if len(saved) >= limit:
            adapter.log(f"入站附件超过 {limit} 个，其余的忽略", "warn")
            break
        kind = attachment_kind(att)
        if not kind:
            continue
        if kind == "file" and not allow_files:
            continue
        original = attachment_name(att)
        if Path(original).suffix.lower() in BLOCKED_SUFFIXES:
            skipped_blocked.append(original)
            adapter.log(f"拒绝接收可执行文件：{original}", "warn")
            continue
        try:
            got = await adapter.fetch_attachment(att)
        except Exception as exc:  # pragma: no cover - 网络异常各平台自己会抛
            adapter.log(f"下载入站附件失败（{original}）：{exc}", "warn")
            continue
        if not got:
            continue
        data, name = got
        if not data:
            continue
        if len(data) > MAX_ATTACHMENT_BYTES:
            adapter.log(
                f"入站附件过大（{len(data) / 1024 / 1024:.1f}MB），已忽略：{name}",
                "warn",
            )
            continue
        fallback = ".jpg" if kind == "image" else ".bin"
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            target = dest_dir / channel_file_name(
                name or original, prefix=adapter.driver or "im",
                fallback_suffix=fallback,
            )
            target.write_bytes(data)
        except OSError as exc:
            adapter.log(f"写入入站附件失败：{exc}", "warn")
            continue
        saved.append((kind, target))
    if skipped_blocked:
        # 别让「用户以为发出去了、模型啥也没看到」这种哑巴亏发生 —— 让调用方
        # 有机会把这件事说回给用户。
        msg.raw["_blockedAttachments"] = skipped_blocked
    return saved


__all__ = [
    "ChannelAdapter",
    "ChannelState",
    "IncomingMessage",
    "MessageHandler",
    "STATE_CONNECTED",
    "STATE_ERROR",
    "STATE_IDLE",
    "STATE_RECONNECTING",
    "STATE_STARTING",
    "STATE_STOPPED",
    "LOG_LINES",
    "IMAGE_EXTS",
    "IMAGE_MIME_BY_EXT",
    "BLOCKED_SUFFIXES",
    "MAX_INCOMING_ATTACHMENTS",
    "MAX_ATTACHMENT_BYTES",
    "attachment_ext",
    "attachment_name",
    "attachment_is_image",
    "attachment_kind",
    "channel_file_name",
    "save_attachments",
    "sandbox_form",
]
