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
- 图片 / 文件：**先传后发**。``POST /v2/{users|groups}/{id}/files``
  body ``{file_type: 1(图)|4(文件), file_data: <base64>, srv_send_msg: false}``
  → ``{file_info, ttl}``；再发一条 ``msg_type: 7`` 且 ``media: {file_info}`` 的
  消息，同样要带 ``msg_id`` + 递增的 ``msg_seq``。富媒体只在 ttl 内可发，所以
  每次都重新上传。**文件必须带 ``file_name``**，否则客户端里显示成「未命名」。
- 收附件：事件里的 ``attachments[]`` 带 ``url`` / ``content_type`` / ``filename``，
  下载后落进工作区的 ``uploads/``（引擎侧只认工作区以内的路径）。
- 被动回复有次数上限（单聊 4 次 / 群聊 5 次）；附件各占一次，逼近时会在日志里
  提前告警 —— 顶到之后平台只会静默失败，不说出来用户完全无从判断。

这里的实现**不依赖任何第三方框架**：用 httpx 发 HTTP、websockets 连网关，
断线自动重连（指数退避）、token 到期前自动刷新、消息 id 去重（QQ 会重推）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from pathlib import Path
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

#: ``content`` 里的 @ 占位符（``<@!openid>``）。
_MENTION_RE = re.compile(r"<@!?[0-9A-Za-z_\-]+>")

#: 官方单条消息上限。
MAX_MESSAGE_CHARS = 2000
#: 富媒体 ``file_type``：1=图片 2=视频 3=语音 4=文件。这里只发图片与文件。
FILE_TYPE_IMAGE = 1
FILE_TYPE_FILE = 4
#: 各类富媒体的字节上限（官方不写死，按实测/社区实现取保守值）。
MAX_IMAGE_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_FILE_UPLOAD_BYTES = 20 * 1024 * 1024
#: 被动回复次数上限：单聊每个周期 4 次、群聊 5 次。发图/发文件各算一次，
#: 所以「带图的长回答」很容易顶到，逼近时提前告警。
PASSIVE_REPLY_LIMIT = {"c2c": 4, "group": 5}
#: 被动回复的**有效期**（官方：单聊 60 分钟、群聊 5 分钟）。
#:
#: 超时后带 ``msg_id`` 的回复会被平台静默拒收 —— 表现就是「它干活了但什么都没
#: 发回来」。识图这类慢活儿（一次识图几十秒，模型还会反复调）很容易把群聊这 5
#: 分钟耗尽，所以到点了要改走**主动消息**（不带 msg_id/msg_seq）。
PASSIVE_REPLY_TTL = {"c2c": 3600.0, "group": 300.0}
#: 提前这么多秒就判定被动窗口不可靠（留出上传富媒体的时间）。
#:
#: 原来是 20 秒 —— 实测太保守：现场那张图在用户消息后 4 分 36 秒落盘，距 5 分钟
#: 窗口还剩 24 秒，却被判成「已过期」，于是转走主动消息、撞上「无权限」，图就丢了。
#: 富媒体上传确实要留余量，但 10 秒足够（本机实测上传 + 发送 < 3 秒）。
PASSIVE_REPLY_MARGIN = 10.0
#: 被判「没有主动消息权限」之后，多久之内不再尝试主动消息（秒）。
PROACTIVE_DENIED_TTL = 600.0
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
        #: ``msg_id -> 已经回了几条``（被动回复有次数上限，见 PASSIVE_REPLY_LIMIT）。
        self._replies: dict[str, tuple[int, float]] = {}
        #: ``(scope, peer) -> 上次被平台拒「无权限」的时间``。
        #:
        #: 群聊被动窗口只有 5 分钟，超了就只能走**主动消息**；而主动消息要在开放
        #: 平台单独申请，多数机器人并没有。用户现场：一轮跑了 6 分 23 秒，窗口过期
        #: 后 3 个附件 + 一段正文全撞「主动消息失败, 无权限」，日志里三连错误、
        #: 用户那边一片空白。拒过一次就记下来，同一批发送不再重复撞墙，并且把真实
        #: 原因写成人话（而不是让人以为是代码坏了）。
        self._proactive_denied: dict[tuple[str, str], float] = {}

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
        self.log(
            f"收到{'群' if msg.is_group else '私聊'}消息：{msg.text[:40]}"
            f"［{describe_inbound(data)}］"
        )
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
    def _passive_expired(self, msg: IncomingMessage | None) -> bool:
        """这条消息的被动回复窗口还开着吗。

        人不在电脑前时的典型场景：群里 @ 一张图 → 识图要几十秒、模型还反复调几次
        → 等要发结果已经过了 5 分钟 → 平台静默拒收，用户只看到机器人「装死」。
        到点就改走主动消息，宁可少一次被动额度、也要把结果送出去。
        """
        if msg is None or not msg.message_id:
            return True  # 没有 msg_id 本来就是主动消息
        if not msg.received_at:
            # 不知道收到多久了 → 按正常被动回复走。宁可试一次带 msg_id 的，
            # 也别在不知情时退化成主动消息（用户关掉「允许主动发送」的话，
            # 那就真的发不出去了）。
            return False
        ttl = PASSIVE_REPLY_TTL.get(msg.scope, 300.0)
        return (time.time() - msg.received_at) >= (ttl - PASSIVE_REPLY_MARGIN)

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
        # 按上限切分会切出一堆以空行开头/结尾的碎片（按 ``\n`` 断句时尤其明显），
        # 原样发出去 QQ 里就是「一条全是空白的消息」。先收拾干净再发。
        chunks = [c.strip() for c in chunks if c.strip()]
        if not chunks:
            return
        passive = not self._passive_expired(msg)
        if not passive and self._proactive_blocked(scope, peer):
            # 已经确认这个机器人没有主动消息权限（刚被平台拒过）。再试也只是把
            # 一模一样的 400 刷三遍 —— 如实说明原因，让用户再发一句重开窗口。
            self.log(
                "这条内容超出被动回复窗口，且该机器人没有主动消息权限，发不出去；"
                "让用户再发一句即可重开窗口。",
                "warn",
            )
            return
        if not passive and msg is not None and msg.message_id:
            self.log(
                f"这条消息的被动回复窗口已过（{msg.scope} 限 "
                f"{PASSIVE_REPLY_TTL.get(msg.scope, 300.0) / 60:.0f} 分钟），"
                "改走主动消息发出",
                "warn",
            )
        # 同一条入站消息可能被回好几条（流式分段、命令提示…），每条都要带
        # 互不相同的 msg_seq —— 官方按 (msg_id, msg_seq) 判重，重复会被丢弃。
        base_seq = self._reserve_seq(msg, len(chunks)) if passive else None
        for index, chunk in enumerate(chunks):
            body: dict[str, Any] = {"content": chunk, "msg_type": 0}
            if passive and msg is not None and msg.message_id:
                body["msg_id"] = msg.message_id
                body["msg_seq"] = (base_seq or 0) + index
            try:
                await self._api(self._endpoint(peer, scope), body=body)
            except QQBotError as exc:
                if "msg_id" in body and await self._resend_proactive(peer, scope, body):
                    continue
                if _is_permission_error(exc):
                    # 本来就是主动消息（窗口已过），被拒就是**没有主动消息权限**。
                    self._note_proactive_denied(peer, scope, exc)
                else:
                    self.log(f"发送失败：{exc}", "error")
                self.state.error = str(exc)
                return
            if index + 1 < len(chunks):
                await asyncio.sleep(float(self.config.get("sendChunkDelayMs") or 600) / 1000.0)

    async def _resend_proactive(
        self, peer: str, scope: str, body: dict[str, Any]
    ) -> bool:
        """带 ``msg_id`` 的被动回复失败了 → 去掉它，当主动消息再发一次。

        兜住「窗口刚好在发送前一刻关闭 / 次数用尽」这类失败：官方对超时只会回
        一个错误码，用户那边完全无感 —— 与其让答案烂在日志里，不如换条路送出去。
        """
        retry = {k: v for k, v in body.items() if k not in ("msg_id", "msg_seq")}
        try:
            await self._api(self._endpoint(peer, scope), body=retry)
        except QQBotError as exc:
            if _is_permission_error(exc):
                self._note_proactive_denied(peer, scope, exc)
            else:
                self.log(f"主动消息补发也失败（{exc}）", "warn")
            return False
        self.log("被动回复失败，已用主动消息补发成功", "warn")
        return True

    def _proactive_blocked(self, scope: str, peer: str) -> bool:
        """刚被平台拒过「无权限」吗（TTL 内不再重复撞墙）。"""
        at = self._proactive_denied.get((scope, peer))
        return bool(at) and (time.time() - at) < PROACTIVE_DENIED_TTL

    def _note_proactive_denied(self, peer: str, scope: str, exc: Exception) -> None:
        """主动消息被平台拒了 —— 记一次，并把真实原因说成人话。

        这不是代码坏了：QQ 开放平台的**主动消息**要单独申请权限，多数机器人没有。
        群聊被动回复窗口只有 5 分钟，超了就只能主动消息 ⇒ 长任务（生图几分钟）
        一旦拖过窗口，结果就必然送不出去。日志里说清楚，排障时不用再猜。
        """
        self._proactive_denied[(scope, peer)] = time.time()
        ttl_min = PASSIVE_REPLY_TTL.get(scope, 300.0) / 60
        self.log(
            f"主动消息被平台拒绝（{exc}）—— 这个机器人没有主动消息权限。"
            f"{'群聊' if scope == 'group' else '单聊'}被动回复窗口只有 "
            f"{ttl_min:.0f} 分钟，超出之后这条内容就发不出去了；"
            "让用户再发一句（会重新开一个新窗口）即可。",
            "error",
        )

    async def send_ack(self, msg: IncomingMessage | None, text: str) -> bool:
        """回执也要占一次被动回复额度，所以额度不足时**主动放弃**这一句。

        群聊只有 5 次（正文分段 + 每张图各占一次）。现场一次回答很容易用掉 1–2
        次；为了「收到」把结果挤掉是本末倒置。留 3 次余量：不足就跳过，用户看到
        的顶多是晚几秒的结果，而不是「只收到一句收到」。
        """
        if msg is None or not msg.message_id:
            return False
        if self._passive_expired(msg) or self._proactive_blocked(msg.scope, msg.peer_id):
            return False
        limit = PASSIVE_REPLY_LIMIT.get(msg.scope, 4)
        used = self._replies.get(msg.message_id, (0, 0.0))[0]
        if used > limit - 3:
            self.log("跳过「收到」回执：被动回复额度要留给结果", "info")
            return False
        try:
            await self.send_text(msg, text)
        except Exception as exc:  # pragma: no cover - 回执失败不该打断正事
            self.log(f"回执发送失败：{exc}", "warn")
            return False
        return True

    async def send_typing(self, msg: IncomingMessage | None) -> None:
        if msg is None or not msg.message_id:
            return
        if self._passive_expired(msg):
            return  # 窗口已过，「正在输入」也发不出去，别白占一个 seq
        body = {
            "msg_type": 6,
            "input_notify": {"input_type": 1, "input_second": 5},
            "msg_id": msg.message_id,
            # 输入状态也算一条被动回复，一样要占一个 seq，否则跟正式回复撞号。
            "msg_seq": self._reserve_seq(msg, 1, counts_as_reply=False) or 1,
        }
        try:
            await self._api(self._endpoint(msg.peer_id, msg.scope), body=body)
        except QQBotError:
            pass  # 输入状态是锦上添花，失败不打扰用户

    # ---- 出站附件（富媒体）----------------------------------------------
    async def send_image(
        self, msg: IncomingMessage | None, path: str | Path, **kwargs: Any
    ) -> bool:
        """发一张本地图片：先传成富媒体拿 ``file_info``，再发一条 ``msg_type 7``。"""
        return await self._send_media(msg, path, file_type=FILE_TYPE_IMAGE, **kwargs)

    async def send_file(
        self, msg: IncomingMessage | None, path: str | Path, **kwargs: Any
    ) -> bool:
        """发一个本地文件（``file_type 4``）。

        **必须带 ``file_name``** —— 官方不传这个字段也能上传成功，但客户端里会
        显示成「未命名」，收的人根本不知道是什么。
        """
        return await self._send_media(msg, path, file_type=FILE_TYPE_FILE, **kwargs)

    async def _send_media(
        self,
        msg: IncomingMessage | None,
        path: str | Path,
        *,
        file_type: int,
        **kwargs: Any,
    ) -> bool:
        """上传 + 发送富媒体。图片和文件走同一条路，只有 ``file_type`` 与上限不同。

        官方规定富媒体只在 ttl 内有效，所以**每次都重新上传**，不做缓存 ——
        与其赌缓存里那份还没过期，不如多一次请求换确定性。
        """
        scope = str(kwargs.get("scope") or (msg.scope if msg else "c2c"))
        peer = str(kwargs.get("peer") or (msg.peer_id if msg else ""))
        if not peer:
            return False
        target = _media_path(path)
        if not target.is_file():
            self.log(f"要发的{'图片' if file_type == FILE_TYPE_IMAGE else '文件'}"
                     f"不存在：{target}", "warn")
            return False
        is_image = file_type == FILE_TYPE_IMAGE
        passive = msg is not None and bool(msg.message_id) and not self._passive_expired(msg)
        if not passive and self._proactive_blocked(scope, peer):
            # 已确认没有主动消息权限，就别再白上传一遍（上传本身就十几秒）。
            self.log(
                f"{'图片' if is_image else '文件'} {target.name} 超出被动回复窗口，"
                "且该机器人没有主动消息权限，发不出去；让用户再发一句可重开窗口。",
                "warn",
            )
            return False
        try:
            file_info = await self._upload_media(
                scope, peer, target, file_type=file_type, is_image=is_image
            )
        except QQBotError as exc:
            self.log(f"上传{'图片' if is_image else '文件'}失败：{exc}", "error")
            self.state.error = str(exc)
            return False

        body: dict[str, Any] = {
            "msg_type": 7,  # 7 = 富媒体
            "media": {"file_info": file_info},
            "content": "",
        }
        if passive and msg is not None:
            body["msg_id"] = msg.message_id
            # 附件也算一条出站，一样要占 seq，否则和文字那条撞号被官方丢掉。
            body["msg_seq"] = self._reserve_seq(msg, 1) or 1
        try:
            await self._api(self._endpoint(peer, scope), body=body)
        except QQBotError as exc:
            if "msg_id" in body and await self._resend_proactive(peer, scope, body):
                self.log(f"已发送{'图片' if is_image else '文件'}：{target.name}")
                return True
            if _is_permission_error(exc):
                self._note_proactive_denied(peer, scope, exc)
            else:
                self.log(f"发送{'图片' if is_image else '文件'}失败：{exc}", "error")
            self.state.error = str(exc)
            return False
        self.log(f"已发送{'图片' if is_image else '文件'}：{target.name}")
        return True

    async def _upload_media(
        self,
        scope: str,
        peer: str,
        path: Path,
        *,
        file_type: int,
        is_image: bool,
    ) -> str:
        """把本地文件传成富媒体，返回 ``file_info``（ttl 内可用来发消息）。"""
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise QQBotError(f"读不了文件：{exc}") from exc
        if not data:
            raise QQBotError("文件是空的")
        if is_image:
            data = _to_supported_image(data, path.suffix.lower())
        cap = MAX_IMAGE_UPLOAD_BYTES if is_image else MAX_FILE_UPLOAD_BYTES
        if len(data) > cap:
            raise QQBotError(
                f"{'图片' if is_image else '文件'} {len(data) / 1024 / 1024:.1f}MB "
                f"超过平台上限 {cap // 1024 // 1024}MB"
            )
        body: dict[str, Any] = {
            "file_type": file_type,
            # 官方只认 base64 本体字符串（不带 data: 前缀）。
            "file_data": base64.b64encode(data).decode("ascii"),
            # false = 只上传不立刻发；发送由后面那条 msg_type 7 完成，
            # 这样附件才是「跟着这一轮回复」走的，而不是凭空冒出来。
            "srv_send_msg": False,
        }
        if not is_image:
            # 文件不传 file_name，客户端里会显示成「未命名」。
            body["file_name"] = path.name
        kind = "groups" if scope == "group" else "users"
        resp = await self._api(f"/v2/{kind}/{peer}/files", body=body)
        info = ""
        if isinstance(resp, dict):
            info = str(resp.get("file_info") or "")
        if not info:
            raise QQBotError("上传没拿到 file_info（可能是格式或尺寸不合规）")
        return info

    # ---- 入站附件 -------------------------------------------------------
    async def fetch_attachment(
        self, att: dict[str, Any]
    ) -> tuple[bytes, str] | None:
        """下载一条入站附件（图片或文件）。官方把直链放在 ``attachments[].url``。"""
        url = str(att.get("url") or "").strip()
        if not url:
            return None
        name = str(att.get("filename") or att.get("name") or "qq-file.bin")
        try:
            token = await self._access_token()
            resp = await self._http().get(
                url, headers={"Authorization": f"QQBot {token}"}
            )
            if resp.status_code >= 400:
                # CDN 直链本身是签过名的，偶尔不带鉴权头反而更顺；再裸试一次。
                resp = await self._http().get(url)
        except Exception as exc:
            self.log(f"下载附件失败：{exc}", "warn")
            return None
        if resp.status_code >= 400 or not resp.content:
            self.log(f"下载附件失败：HTTP {resp.status_code}", "warn")
            return None
        return resp.content, name

    # ---- 小工具 ---------------------------------------------------------
    def _reserve_seq(
        self, msg: IncomingMessage | None, count: int, *, counts_as_reply: bool = True
    ) -> int | None:
        """为同一条入站消息预留 ``count`` 个连续 ``msg_seq``，返回第一个。

        官方按 ``(msg_id, msg_seq)`` 判重，重复的会被静默丢弃；所以同一条消息
        的每一次出站（流式分段、输入状态、命令提示、附件）都要拿一个没人用过的号。
        没有 ``msg_id`` 的主动消息不需要 seq，返回 ``None``。

        ``counts_as_reply`` 只有输入状态会传 False —— 它占 ``msg_seq`` 但通常不算
        一次被动回复，别让「正在输入」把次数额度也吃掉、又反过来误报超额。
        """
        if msg is None or not msg.message_id:
            return None
        now = time.time()
        expired = [k for k, (_, at) in self._seq_for_msg.items() if now - at > DEDUP_TTL_SEC]
        for key in expired:
            self._seq_for_msg.pop(key, None)
        start, _at = self._seq_for_msg.get(msg.message_id, (1, now))
        self._seq_for_msg[msg.message_id] = (start + max(1, int(count)), now)
        if counts_as_reply:
            self._note_replies(msg, max(1, int(count)))
        return start

    def _note_replies(self, msg: IncomingMessage, count: int) -> None:
        """记账「这条入站消息已经回了几条」，逼近官方上限时告警一次。

        官方对被动回复有次数限制（单聊 4 次 / 群聊 5 次）。**每张图、每个文件
        都各占一次**，所以一次「带三张图的回答」很容易顶到天花板。顶到之后平台
        只会静默失败 —— 与其让用户对着「怎么不回我了」发呆，不如提前说出来。
        """
        if not msg.message_id:
            return
        now = time.time()
        expired = [k for k, (_, at) in self._replies.items() if now - at > DEDUP_TTL_SEC]
        for key in expired:
            self._replies.pop(key, None)
        used, _at = self._replies.get(msg.message_id, (0, now))
        used += count
        self._replies[msg.message_id] = (used, now)
        limit = PASSIVE_REPLY_LIMIT.get(msg.scope, 4)
        if used == limit:
            self.log(
                f"这条消息已经回了 {used} 条（官方上限 {limit}），"
                "再往后平台会静默失败 —— 附件/长回答要拆开发",
                "warn",
            )

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
            text=_clean_content(str(data.get("content") or "")),
            message_id=str(data.get("id") or data.get("msg_id") or ""),
            msg_seq=_as_int(data.get("msg_seq")),
            received_at=time.time(),
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
            text=_clean_content(str(data.get("content") or "")),
            message_id=str(data.get("id") or data.get("msg_id") or ""),
            msg_seq=_as_int(data.get("msg_seq")),
            received_at=time.time(),
            attachments=_attachments(data),
            raw=data,
        )
    return None


def _clean_content(text: str) -> str:
    """去掉 ``content`` 里的 @ 占位符（官方塞的是 ``<@!openid>`` 这种）。

    不清掉的话正文会变成 ``<@!E4F4AE…> 识图``，一路带进上下文 —— 模型看见一串
    无意义的 id，用户在 IM 里也会看到它出现在回复里。
    """
    cleaned = _MENTION_RE.sub("", text or "").strip()
    return re.sub(r"[ \t]{2,}", " ", cleaned) if cleaned else ""


def _attachments(data: dict[str, Any]) -> list[dict[str, Any]]:
    """入站附件 —— ``attachments`` 和 ``msg_elements`` 两处都收。

    正常消息的图就在 ``attachments``；但**引用回复 / 图文混排**场景下官方会把
    消息拆成 ``msg_elements``，附件挂在每个元素里。少收一处，用户看到的就是
    「我明明发了图，机器人说没看到」。
    """
    out: list[dict[str, Any]] = []
    raw = data.get("attachments")
    if isinstance(raw, list):
        out.extend(a for a in raw if isinstance(a, dict))
    elements = data.get("msg_elements")
    if isinstance(elements, list):
        for element in elements:
            if not isinstance(element, dict):
                continue
            nested = element.get("attachments")
            if isinstance(nested, list):
                out.extend(a for a in nested if isinstance(a, dict))
    return out


def _media_path(path: str | Path) -> Path:
    """要发出去的附件路径 → 本机路径。

    模型在回复里回填的常常是**沙箱写法**（``/var/minis/workspace/generated/x.png``
    —— 那是引擎出站时自己换的形态），而 Windows 上没有这个目录。不还原就会
    「文件找不到、只剩一句文本发出去」。还原不了（本来就是本机路径）就原样用。
    """
    raw = str(path or "")
    try:
        from ...tools.path_utils import to_host_path

        host = to_host_path(raw)
        if host is not None:
            return host
    except Exception:  # pragma: no cover - 路径工具不可用不该拦住发图
        pass
    return Path(raw)


def describe_inbound(data: dict[str, Any]) -> str:
    """一行说清这条事件的形状 —— 排障用。

    「用户发了图，机器人没看到」这类问题，光看正文猜不出来是平台没推、还是推了
    我们没认。把附件条数与类型、mentions/msg_elements/message_scene 的存在情况
    记进日志，下次一眼能定位。
    """
    atts = _attachments(data)
    kinds = ",".join(str(a.get("content_type") or "?") for a in atts) or "无"
    scene = data.get("message_scene")
    ext = scene.get("ext") if isinstance(scene, dict) else None
    parts = [
        f"附件 {len(atts)}({kinds})",
        f"mentions {len(data.get('mentions') or [])}",
        f"msg_elements {len(data.get('msg_elements') or [])}",
    ]
    if ext:
        parts.append(f"scene {ext}")
    return " · ".join(parts)


def _to_supported_image(data: bytes, suffix: str) -> bytes:
    """把平台不收的图片格式转成 PNG —— QQ 富媒体只认 png / jpg。

    生图产物可能是 webp / bmp / avif，直接扔过去会被平台拒掉，而报错信息只有
    「上传失败」，用户完全看不出是格式问题。所以在这里先转一道：PNG 保留透明
    通道，比统一转 JPEG 更安全。

    Pillow 缺失或解码失败就原样返回 —— 让平台自己报错，至少错误是真实的，而
    不是被我们吞掉换成一句含糊的「转换失败」。
    """
    if suffix in (".png", ".jpg", ".jpeg"):
        return data
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            im.load()
            buf = io.BytesIO()
            im.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:  # pragma: no cover - Pillow 缺失 / 文件损坏
        return data


def _is_permission_error(exc: Exception) -> bool:
    """这个失败是「没有主动消息权限」吗。

    官方对主动消息无权限回的是 ``400 主动消息失败, 无权限``（也会出现 ``no
    permission`` / ``forbidden`` 之类的英文写法）。这是**平台权限**问题，不是
    代码或网络问题 —— 认出来才能给出可执行的提示，也才能不再重复撞墙。
    """
    text = str(exc)
    if "无权限" in text or "没有权限" in text:
        return True
    low = text.lower()
    return "no permission" in low or "forbidden" in low


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
    "MAX_IMAGE_UPLOAD_BYTES",
    "MAX_FILE_UPLOAD_BYTES",
    "FILE_TYPE_IMAGE",
    "FILE_TYPE_FILE",
]
