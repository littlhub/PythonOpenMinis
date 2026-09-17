"""[T-channels-plugins] 通道适配器的统一接口。

一个「通道」= 一个 IM 平台（QQ / 微信 / 飞书 / Telegram…）。适配器只做平台那
一层的事：登录、收发消息、心跳重连、把平台事件归一化成 :class:`IncomingMessage`。
「会话怎么映射、命令怎么处理、怎么调引擎」全部在 :mod:`openminis.plugins.bridge`
里做一次 —— 这样再加平台只要写一个新适配器。

设计借自 dsh-bridge 的 Platform 抽象（login/start/stop/sendText/getStatus），
但实现是 Python 侧重写的：它的内核绑在 DeepSeek Harness 上，接不到本引擎的
模型、工具、技能与记忆。
"""

from __future__ import annotations

import abc
import time
from collections import deque
from dataclasses import dataclass, field
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
]
