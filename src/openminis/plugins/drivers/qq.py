"""[T-channels-plugins] QQ 机器人驱动 —— 官方开放平台 Bot API v2。

协议（照官方文档与 dsh-bridge 的实现对齐）：

- 取 token：``POST https://bots.qq.com/app/getAppAccessToken``
  body ``{appId, clientSecret}`` → ``{access_token, expires_in}``
- 调用 API：``Authorization: QQBot <access_token>``，基址 ``https://api.bot.qq.com``
- 取网关：``GET /gateway/bot`` → ``{url}``，然后连 WebSocket
- 握手：收到 ``op 10``（hello）后发 ``op 2`` identify（或 ``op 6`` resume 快速恢复）；
  之后按 ``heartbeat_interval`` 发 ``op 1`` 心跳，``op 11`` 是 ACK
- 事件：``op 0`` + ``t=C2C_MESSAGE_CREATE``（私聊）/
  ``GROUP_AT_MESSAGE_CREATE``（群里 @机器人）
- 回复：``POST /v2/users/{openid}/messages`` 或 ``/v2/groups/{group_openid}/messages``
  body ``{content, msg_type: 0, msg_id, msg_seq}`` —— 带上被动回复的 ``msg_id``
  才不占用主动消息额度；同一条消息回多段时 ``msg_seq`` 递增。

这里的实现**不依赖任何第三方框架**：用 httpx 发 HTTP、websockets 连网关，
断线自动重连（指数退避）、token 到期前自动刷新、消息 id 去重（QQ 会重推）。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

try:  # websockets >= 14 的新入口；老版本回落到旧路径
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # pragma: no cover - 兼容老版本
    from websockets.client import connect as ws_connect  # type: ignore[no-redef]

from ..base import (
    STATE_CONNECTED,
    STATE_ERROR,
    STATE_IDLE,
    STATE_RECONNECTING,
    STATE_STARTING,
    STATE_STOPPED,
    ChannelAdapter,
    IncomingMessage,
)

#: 官方接口地址（2026-08 起域名统一到 api.bot.qq.com）。
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
API_BASE = "https://api.bot.qq.com"
DEFAULT_GATEWAY = "wss://api.bot.qq.com/websocket/"

#: 事件订阅位。C2C 与群 @ 同属 GROUP_AND_C2C_EVENT（1<<25）；
#: INTERACTION_CREATE（1<<26）是按钮/菜单回调。
INTENT_GROUP_AND_C2C = 1 << 25
INTENT_INTERACTION = 1 << 26

#: 官方单条消息上限。
MAX_MESSAGE_CHARS = 2000
#: 提前多久刷新 token（官方 TTL 7200s）。
TOKEN_MARGIN_SEC = 300
REQUEST_TIMEOUT_SEC = 15.0
RECONNECT_MIN_SEC = 3.0
RECONNECT_MAX_SEC = 30.0
#: 消息去重窗口（QQ 断线重连会重推事件）。
DEDUP_TTL_SEC = 300


class QQBotError(RuntimeError):
    """QQ 接口返回了错误（带上状态码，便于界面提示是凭证问题还是网络问题）。"""

    def __init__(self, message: str, *, status: int = 0, payload: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


class QQAdapter(ChannelAdapter):
    driver = "qq"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # 配置里的上限优先（用户设置）；没配就沿用基类那份（构造参数 / 默认 2000）。
        configured = self.config.get("maxMessageChars")
        if configured:
            self.max_message_chars = int(configured)
        self._task: asyncio.Task[Any] | None = None
        self._heartbeat: asyncio.Task[Any] | None = None
        self._ws: Any = None
        self._client: httpx.AsyncClient | None = None
        self._token = str(self.config.get("accessToken") or "")
        self._token_expire_at = float(self.config.get("accessTokenExpiresAt") or 0)
        self._sequence: int | None = None
        self._session_id: str | None = None
        self._unacked = 0
        self._stopping = False
        self._seen: dict[str, float] = {}
        #: ``msg_id -> (下一个可用的 msg_seq, 最后使用时间)``。
        self._seq_for_msg: dict[str, tuple[int, float]] = {}

    # ---- 配置快照 -------------------------------------------------------
    @property
    def app_id(self) -> str:
        return str(self.config.get("appId") or "").strip()

    @property
    def client_secret(self) -> str:
        return str(self.config.get("clientSecret") or "").strip()

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.client_secret)

    @property
    def allow_from(self) -> list[str]:
        raw = self.config.get("allowFrom")
        if isinstance(raw, list):
            return [str(v).strip() for v in raw if str(v).strip()]
        if isinstance(raw, str):
            return [v.strip() for v in raw.splitlines() if v.strip()]
        return []

    # ---- 生命周期 -------------------------------------------------------
    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        if not self.configured:
            self.set_state(STATE_IDLE, "还没填 AppID / ClientSecret")
            self.log("缺少 AppID 或 ClientSecret，未启动", "warn")
            return
        self._stopping = False
        self.set_state(STATE_STARTING, "正在连接 QQ 开放平台")
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stopping = True
        await self._close_ws()
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: B014 - 停就要停干净
                pass
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self.set_state(STATE_STOPPED, "已停止")
        self.log("通道已停止")

    async def _run_loop(self) -> None:
        backoff = RECONNECT_MIN_SEC
        while not self._stopping:
            try:
                token = await self._access_token()
                url = await self._gateway_url(token)
                await self._connect(url, token)
                backoff = RECONNECT_MIN_SEC
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._stopping:
                    break
                self.set_state(STATE_RECONNECTING, "", str(exc))
                self.log(f"连接断开：{exc}，{backoff:.0f}s 后重连", "warn")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, RECONNECT_MAX_SEC)
        if not self._stopping:
            self.set_state(STATE_IDLE, "")
        await self._close_ws()

    # ---- HTTP -----------------------------------------------------------
    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SEC)
        return self._client

    async def _access_token(self) -> str:
        if self._token and self._token_expire_at > time.time() + TOKEN_MARGIN_SEC:
            return self._token
        if not self.configured:
            raise QQBotError("缺少 AppID 或 ClientSecret")
        resp = await self._http().post(
            TOKEN_URL,
            json={"appId": self.app_id, "clientSecret": self.client_secret},
        )
        data = _json_or_text(resp)
        token = str((data or {}).get("access_token") or "")
        if not token:
            raise QQBotError(f"取 access_token 失败：{_brief(data, resp)}", status=resp.status_code)
        expires = max(60, int((data or {}).get("expires_in") or 7200))
        self._token = token
        self._token_expire_at = time.time() + expires
        self.log(f"已获取 access_token（{expires}s 有效）")
        return token

    async def _gateway_url(self, token: str) -> str:
        override = str(self.config.get("gatewayUrl") or "").strip()
        if override:
            return override
        resp = await self._http().get(
            f"{API_BASE}/gateway/bot", headers={"Authorization": f"QQBot {token}"}
        )
        data = _json_or_text(resp)
        url = str((data or {}).get("url") or "") if isinstance(data, dict) else ""
        return url or DEFAULT_GATEWAY

    async def _api(self, path: str, *, method: str = "POST", body: dict[str, Any] | None = None) -> Any:
        token = await self._access_token()
        resp = await self._http().request(
            method,
            f"{API_BASE}{path}",
            headers={
                "Authorization": f"QQBot {token}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        data = _json_or_text(resp)
        if resp.status_code >= 400:
            raise QQBotError(
                f"QQ 接口 {resp.status_code}：{_brief(data, resp)}",
                status=resp.status_code,
                payload=data,
            )
        return data

    # ---- WebSocket ------------------------------------------------------
    async def _connect(self, url: str, token: str) -> None:
        self.log(f"连接网关 {url}")
        async with ws_connect(url, ping_interval=None, max_size=16 * 1024 * 1024) as ws:
            self._ws = ws
            self.set_state(STATE_STARTING, "网关已连上，等待握手")
            async for raw in ws:
                if self._stopping:
                    break
                try:
                    payload = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                await self._handle_payload(payload, token, ws)
                if self._stopping:
                    break

    async def _handle_payload(self, payload: dict[str, Any], token: str, ws: Any) -> None:
        op = int(payload.get("op") or 0)
        if payload.get("s") is not None:
            self._sequence = int(payload["s"])

        if op == 10:  # hello
            interval = int((payload.get("d") or {}).get("heartbeat_interval") or 30000)
            self._start_heartbeat(ws, interval / 1000.0)
            if self._session_id and self._sequence is not None:
                self.log(f"尝试恢复会话（session={self._session_id[:8]}…）")
                await ws.send(json.dumps({
                    "op": 6,
                    "d": {
                        "token": f"QQBot {token}",
                        "session_id": self._session_id,
                        "seq": self._sequence,
                    },
                }))
            else:
                await ws.send(json.dumps({
                    "op": 2,
                    "d": {
                        "token": f"QQBot {token}",
                        "intents": INTENT_GROUP_AND_C2C | INTENT_INTERACTION,
                        "shard": [0, 1],
                        "properties": {"$os": "windows", "$browser": "openminis", "$device": "openminis"},
                    },
                }))
            return

        if op == 11:  # 心跳 ACK
            self._unacked = 0
            return

        if op == 7:  # 要求重连
            self.log("网关要求重连", "warn")
            await self._close_ws()
            raise ConnectionError("gateway requested reconnect")

        if op == 9:  # session 失效
            self._session_id = None
            self._sequence = None
            self.log("会话失效，下次重连走全量鉴权", "warn")
            await self._close_ws()
            raise ConnectionError("invalid session")

        if op != 0:
            return

        event = str(payload.get("t") or "")
        data = payload.get("d") or {}
        if event == "READY":
            self._session_id = str(data.get("session_id") or "") or None
            self.state.account = str((data.get("user") or {}).get("id") or "")
            self._unacked = 0
            self.set_state(STATE_CONNECTED, "已连接 QQ 开放平台")
            self.log("网关就绪（READY）")
            return
        if event == "RESUMED":
            self._unacked = 0
            self.set_state(STATE_CONNECTED, "已连接 QQ 开放平台（已恢复）")
            self.log("会话已恢复（RESUMED）")
            return

        msg = normalize_event(event, data)
        if msg is None:
            return
        if msg.message_id and not self._mark_seen(msg.message_id):
            return
        if not self._allowed(msg):
            self.log(f"忽略未在白名单里的私聊：{msg.sender_id[-6:]}", "warn")
            return
        self.log(f"收到{'群' if msg.is_group else '私聊'}消息：{msg.text[:40]}")
        await self._dispatch(msg)

    async def _close_ws(self) -> None:
        if self._heartbeat is not None:
            self._heartbeat.cancel()
            self._heartbeat = None
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # pragma: no cover - 关不掉就算了
                pass

    def _start_heartbeat(self, ws: Any, interval: float) -> None:
        if self._heartbeat is not None:
            self._heartbeat.cancel()
        self._unacked = 0

        async def beat() -> None:
            while not self._stopping:
                await asyncio.sleep(max(5.0, interval))
                if self._stopping:
                    return
                if self._unacked >= 2:
                    self.log("心跳连续两次没 ACK，判定链路已死，重连", "warn")
                    await self._close_ws()
                    return
                self._unacked += 1
                try:
                    await ws.send(json.dumps({"op": 1, "d": self._sequence}))
                except Exception:
                    return

        self._heartbeat = asyncio.create_task(beat())

    # ---- 出站 -----------------------------------------------------------
    def _endpoint(self, peer: str, scope: str) -> str:
        kind = "groups" if scope == "group" else "users"
        return f"/v2/{kind}/{peer}/messages"

    async def send_text(
        self, msg: IncomingMessage | None, text: str, **kwargs: Any
    ) -> None:
        scope = str(kwargs.get("scope") or (msg.scope if msg else "c2c"))
        peer = str(kwargs.get("peer") or (msg.peer_id if msg else ""))
        if not peer:
            return
        chunks = self.split_text(text)
        # 同一条入站消息可能被回好几条（流式分段、命令提示…），每条都要带
        # 互不相同的 msg_seq —— 官方按 (msg_id, msg_seq) 判重，重复会被丢弃。
        base_seq = self._reserve_seq(msg, len(chunks))
        for index, chunk in enumerate(chunks):
            body: dict[str, Any] = {"content": chunk, "msg_type": 0}
            if msg is not None and msg.message_id:
                body["msg_id"] = msg.message_id
                body["msg_seq"] = (base_seq or 0) + index
            try:
                await self._api(self._endpoint(peer, scope), body=body)
            except QQBotError as exc:
                self.log(f"发送失败：{exc}", "error")
                self.state.error = str(exc)
                return
            if index + 1 < len(chunks):
                await asyncio.sleep(float(self.config.get("sendChunkDelayMs") or 600) / 1000.0)

    async def send_typing(self, msg: IncomingMessage | None) -> None:
        if msg is None or not msg.message_id:
            return
        body = {
            "msg_type": 6,
            "input_notify": {"input_type": 1, "input_second": 5},
            "msg_id": msg.message_id,
            # 输入状态也算一条被动回复，一样要占一个 seq，否则跟正式回复撞号。
            "msg_seq": self._reserve_seq(msg, 1) or 1,
        }
        try:
            await self._api(self._endpoint(msg.peer_id, msg.scope), body=body)
        except QQBotError:
            pass  # 输入状态是锦上添花，失败不打扰用户

    # ---- 小工具 ---------------------------------------------------------
    def _reserve_seq(self, msg: IncomingMessage | None, count: int) -> int | None:
        """为同一条入站消息预留 ``count`` 个连续 ``msg_seq``，返回第一个。

        官方按 ``(msg_id, msg_seq)`` 判重，重复的会被静默丢弃；所以同一条消息
        的每一次出站（流式分段、输入状态、命令提示）都要拿一个没人用过的号。
        没有 ``msg_id`` 的主动消息不需要 seq，返回 ``None``。
        """
        if msg is None or not msg.message_id:
            return None
        now = time.time()
        expired = [k for k, (_, at) in self._seq_for_msg.items() if now - at > DEDUP_TTL_SEC]
        for key in expired:
            self._seq_for_msg.pop(key, None)
        start, _at = self._seq_for_msg.get(msg.message_id, (1, now))
        self._seq_for_msg[msg.message_id] = (start + max(1, int(count)), now)
        return start

    def _mark_seen(self, message_id: str) -> bool:
        """第一次见返回 True；重复推送返回 False。"""
        now = time.time()
        expired = [k for k, at in self._seen.items() if now - at > DEDUP_TTL_SEC]
        for key in expired:
            self._seen.pop(key, None)
        if message_id in self._seen:
            return False
        self._seen[message_id] = now
        return True

    def _allowed(self, msg: IncomingMessage) -> bool:
        """白名单只管私聊：群里能被 @ 到就算用户主动找上门了。"""
        if msg.is_group:
            return True
        allow = self.allow_from
        return not allow or msg.sender_id in allow

    def status(self) -> dict[str, Any]:
        out = super().status()
        out.update({
            "configured": self.configured,
            "allowFrom": self.allow_from,
            "appId": self.app_id,
        })
        return out


def normalize_event(event: str, data: dict[str, Any]) -> IncomingMessage | None:
    """把官方事件转成统一的入站消息。认不出来的事件返回 ``None``。"""
    if event == "C2C_MESSAGE_CREATE":
        openid = str((data.get("author") or {}).get("user_openid") or data.get("user_openid") or "")
        return IncomingMessage(
            scope="c2c",
            peer_id=openid,
            sender_id=openid,
            text=str(data.get("content") or "").strip(),
            message_id=str(data.get("id") or data.get("msg_id") or ""),
            msg_seq=_as_int(data.get("msg_seq")),
            attachments=_attachments(data),
            raw=data,
        )
    if event in ("GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"):
        author = data.get("author") or {}
        group = str(data.get("group_openid") or data.get("group_id") or "")
        return IncomingMessage(
            scope="group",
            peer_id=group,
            sender_id=str(author.get("member_openid") or author.get("id") or data.get("member_openid") or ""),
            text=str(data.get("content") or "").strip(),
            message_id=str(data.get("id") or data.get("msg_id") or ""),
            msg_seq=_as_int(data.get("msg_seq")),
            attachments=_attachments(data),
            raw=data,
        )
    return None


def _attachments(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("attachments")
    return [a for a in raw if isinstance(a, dict)] if isinstance(raw, list) else []


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _json_or_text(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return {"message": resp.text[:200]}


def _brief(data: Any, resp: httpx.Response) -> str:
    if isinstance(data, dict):
        for key in ("message", "msg", "error", "code"):
            if data.get(key):
                return str(data[key])[:200]
    return f"HTTP {resp.status_code}"


__all__ = [
    "QQAdapter",
    "QQBotError",
    "normalize_event",
    "TOKEN_URL",
    "API_BASE",
    "MAX_MESSAGE_CHARS",
]
