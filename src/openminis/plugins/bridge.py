"""[T-plugins-manager] 通道桥：IM 会话 ↔ 引擎会话。

适配器只管平台那层；「这条消息归哪个会话、怎么跑一轮、回复怎么发回去」全在这里。
跑一轮**复用主对话链路**（``_run_chat``）：模型、技能、工具、子代理、记忆、压缩、
沙箱全都和网页端一模一样，机器人不是另一套缩水实现。

做法很直接：往连接管理器里挂一个「虚拟订阅者」，``_run_chat`` 的推流帧就会送到
这里来 —— 于是机器人也能用上流式输出（按平台上限分段发），不用另接一套。

附件（图片与文件）两头都在这里收口：

* **入站** —— 附件先落进工作区 ``uploads/``，再把路径按 ``![名字](路径)``（图片）
  或 ``[附件: 名字](路径)``（文件）贴到消息开头。引擎侧的附件解析
  （``settings/attachments.py``）认的就是这两种引用，于是 path-only 模式、
  ``read_image`` / ``read_file``、识图子代理、inline 模式全部白拿。
* **出站** —— ``toolEnd`` 帧带的 ``images``（生图工具/脚本新落盘的图）+ 正文里
  模型自己写的引用（图片 ``![](路径)``、文件 ``[附件: 名字](路径)``），都摘出来
  交给 ``adapter.send_image`` / ``adapter.send_file`` 单独发。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Awaitable

from ..core import context
from ..tools.path_utils import scrub_machine_paths
from . import options
from .base import ChannelAdapter, IncomingMessage, save_attachments

#: 累积到这么多字符就先发一段（长回答不必等到全部跑完）。
DEFAULT_CHUNK_CHARS = 900

#: 「最近的产物」观察窗口（秒）—— 只在「模型交付了旧文件」的兜底里用。
#:
#: 模型交付时用的路径是从会话历史里的 ``![生成图](…)`` 抄的，会话里攒了两张就会
#: 抄成上一轮那张。此时按「本轮落盘」扫是空的（那张图是上一轮生成的），所以放宽到
#: 这个窗口、把**最新的一张**补上。
RECENT_ARTIFACT_WINDOW = 1800.0

#: 收到消息后立刻回的这句「先响应」。
#:
#: 用户现场：群里 @ 机器人生图，一轮跑几分钟，屏幕上**什么都没有** —— 他以为
#: 消息根本没发出去（平台的「正在输入」只活几秒，在群里也不显眼）。先回一句
#: 回执照样重要：知道「它收到了、正在干」，比结果早到几秒有用得多。
#:
#: 文案可在插件配置里改（``ackText``），设成空字符串就关掉。
DEFAULT_ACK_TEXT = "收到，正在处理…"

#: 正文里形如 ``![说明](路径)`` 的图片引用。
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")

#: 正文里形如 ``[附件: 名字](路径)`` 的文件引用（与 ``settings/attachments.py``
#: 认的写法完全一致 —— 同一个 markdown，网页端渲染成文件芯片，IM 这边发成附件）。
_MD_FILE_RE = re.compile(r"\[附件[:：]\s*[^\]]*\]\(([^)\s]+)\)")

#: 流式文本的尾巴上最多压这么多字符，等一个可能被 chunk 切开的下半截引用。
_REF_TAIL_HOLD = 400

#: 连续 3 个以上换行 → 收敛成一段空行。
#:
#: 摘掉 ``![](路径)`` 之后原位置会留一串空行（模型习惯把引用单独成段），IM 里
#: 看就是一大片空白 —— 必须收拾一下，否则每条带图的回复下面都拖着一段空。
_BLANKS_RE = re.compile(r"\n[ \t]*\n(?:[ \t]*\n)+")


def _tidy(text: str) -> str:
    """把摘掉引用后留下的空行收敛掉，顺手统一换行符。"""
    if not text:
        return text
    out = text.replace("\r\n", "\n").replace("\r", "\n")
    return _BLANKS_RE.sub("\n\n", out)

#: 尾巴上「可能是一条半截引用」的形状。命中就把这一段压住，等下一块拼上来
#: 再判 —— 否则聊天里会漏出一串残废 markdown，或者把半截路径当图片去发。
_TAIL_REF_RE = re.compile(
    r"(?:"
    r"!\[[^\]]*\]\([^)\s]*"          # ![alt](path  —— 还没等到 ')'
    r"|!\[[^\]]*"                    # ![alt
    r"|!"
    r"|\[附件[:：][^\]]*\]\([^)\s]*"
    r"|\[附件[:：][^\]]*"
    r"|\[附?"                        # 半截开头：'[' 或 '[附'
    r")$"
)


def _hold_from(text: str) -> int:
    """返回需要留在缓冲里等下一块的起点；没有半截引用就返回 ``-1``。

    流式分块会把 ``![说明](C:/a.p`` + ``ng)`` 切开，这时既不能当正文发出去
    （聊天里会冒出一串残废 markdown），也不能提前当附件处理（路径还不全）。
    """
    floor = max(0, len(text) - _REF_TAIL_HOLD)
    m = _TAIL_REF_RE.search(text, floor)
    return m.start() if m else -1


class _AttachmentRefFilter:
    """把流式正文里的 ``![](本地路径)`` / ``[附件: 名字](本地路径)`` 摘出来。

    模型经常很自觉地写 ``![生成图](C:/.../a.png)`` —— 那是给网页端渲染用的。
    IM 里这串 markdown 只会显示成一行丑字，附件却发不出去。摘出来单独发，聊天
    里就只剩正文。

    ``feed`` 返回 ``(可以发出去的正文, [(kind, 路径)])``，``kind`` 是 ``image``
    或 ``file``；网络图 / ``data:`` 内联串原样留在正文里（它们不是本地文件，
    发不出去也不该被吞掉）。
    """

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, chunk: str = "") -> tuple[str, list[tuple[str, str]]]:
        self._buf += chunk
        found: list[tuple[str, str]] = []

        def _sub(kind: str):
            def _take(m: re.Match[str]) -> str:
                path = m.group(1)
                if path.startswith(("http://", "https://", "data:")):
                    return m.group(0)
                found.append((kind, path))
                return ""

            return _take

        text = _MD_IMAGE_RE.sub(_sub("image"), self._buf)
        text = _MD_FILE_RE.sub(_sub("file"), text)
        if found:
            # 只在真的摘掉过东西时才收敛空行 —— 平时不碰用户的排版
            text = _tidy(text)
        hold = _hold_from(text)
        if hold >= 0:
            self._buf = text[hold:]
            text = text[:hold]
        else:
            self._buf = ""
        return text, found

    def flush(self) -> tuple[str, list[tuple[str, str]]]:
        """收尾：把还压在缓冲里的半截引用当普通文本吐出来。

        这时候已经没机会再补齐了 —— 残废的 markdown 比丢字好，至少用户知道
        模型写了点什么。
        """
        body, self._buf = self._buf, ""
        return _tidy(body), []

HELP_TEXT = """我可以直接聊天，也认这几条命令：
/new      开一个新对话（之后的消息接着新对话走）
/list     看看这个会话有哪些对话
/resume N 切到第 N 个对话（N 见 /list）
/ping     看看我还在不在
/help     这条帮助

群里需要 @我；私聊直接说就行。

⚠ 群里我只能收到**@我的那一条**消息：别人发的、你没 @ 我时发的（哪怕带图）
我都收不到。所以图要和 @我 放在同一条消息里发，
或者把图转发/引用后再 @我 一次并说清要我做什么。"""


class _Sink:
    """伪 WebSocket：只要有 ``send_json`` 就能挂进连接管理器接同一条推流。"""

    def __init__(self, handler: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        self._handler = handler

    async def send_json(self, payload: dict[str, Any]) -> None:
        await self._handler(payload)


class ConversationBridge:
    """把一个适配器接到引擎的会话上。"""

    def __init__(
        self,
        *,
        plugin_id: str,
        adapter: ChannelAdapter,
        chunk_chars: int = DEFAULT_CHUNK_CHARS,
        label: str = "",
    ) -> None:
        self.plugin_id = plugin_id
        self.adapter = adapter
        self.label = label or plugin_id
        self.chunk_chars = max(0, int(chunk_chars or 0))
        self._locks: dict[str, asyncio.Lock] = {}
        self._map_lock = asyncio.Lock()

    # -- 映射文件 ---------------------------------------------------------
    @property
    def map_path(self) -> Path:
        return context.app_context().data_dir / "plugins" / self.plugin_id / "sessions.json"

    def _load_map(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self.map_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _save_map(self, data: dict[str, dict[str, Any]]) -> None:
        self.map_path.parent.mkdir(parents=True, exist_ok=True)
        self.map_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def active_session(self, conv_key: str) -> str | None:
        entry = self._load_map().get(conv_key) or {}
        value = entry.get("active")
        return str(value) if value else None

    # -- 入站 -------------------------------------------------------------
    async def handle(self, msg: IncomingMessage) -> None:
        """适配器收到消息后调这里。"""
        # 先确认「有活可干」：纯空消息（既没文字也没附件）不值得回执 —— 否则用户
        # 会收到一句「收到，正在处理…」然后什么也没有。
        if not (msg.text or "").strip() and not msg.attachments:
            return
        # **先响应再干活**。下面这两步都可能很慢：收附件要下载（图片几 MB）、
        # 跑一轮动辄几分钟（生图更久）。期间用户在 IM 里什么都看不到，只会认为
        # 「消息发丢了 / 卡住了」—— 所以第一件事就是给一句回执。
        await self._ack(msg)
        text = (msg.text or "").strip()
        text = await self._attach_incoming_attachments(msg, text)
        blocked = [str(x) for x in (msg.raw.get("_blockedAttachments") or [])]
        if blocked:
            # 有人把 .exe 这类文件丢进来，我们一律不收 —— 但必须说清楚，否则
            # 用户以为发出去了、模型却什么也没看到，双方都莫名其妙。
            await self._send(
                msg, "（这些文件类型不收，已跳过：" + "、".join(blocked) + "）"
            )
        if not text:
            return
        if text.startswith("/"):
            try:
                if await self._command(msg, text):
                    return
            except Exception as exc:  # pragma: no cover - 命令出错不该断线
                self.adapter.log(f"命令处理失败：{exc}", "error")
                return
        key = msg.conv_key
        lock = self._locks.setdefault(key, asyncio.Lock())
        if lock.locked():
            # 同一个人连着发：排队跑，别把两轮对话搅在一起
            await self._send(msg, "（上一条还在处理，这条排在后面）")
        async with lock:
            await self._run_turn(msg, text)

    async def _ack(self, msg: IncomingMessage) -> None:
        """收到消息立刻回一句（「先响应，再干活」）。

        跳过两类：斜杠命令（``/new`` 之类本来就秒回，再加一句回执纯属噪音）、
        以及配置里把 ``ackText`` 设成空字符串的情况。

        额度问题交给适配器自己判断（见 ``ChannelAdapter.send_ack``）：QQ 的被动
        回复次数有限（群聊 5 次），额度不够时**宁可少这一句**，也不能把真正的
        结果挤掉。
        """
        raw = self.adapter.config.get("ackText")
        text = DEFAULT_ACK_TEXT if raw is None else str(raw)
        text = text.strip()
        if not text:
            return
        if (msg.text or "").strip().startswith("/"):
            return
        try:
            sent = await self.adapter.send_ack(msg, text)
        except Exception as exc:  # pragma: no cover - 回执失败绝不能影响正事
            self.adapter.log(f"回执发送失败：{exc}", "warn")
            return
        if sent:
            self.adapter.log(f"已回执：{text}")

    async def _attach_incoming_attachments(
        self, msg: IncomingMessage, text: str
    ) -> str:
        """把入站附件落进工作区，并按 markdown 引用贴到消息开头。

        引用格式与网页端上传附件完全一致（图片 ``![名字](绝对路径)`` / 文件
        ``[附件: 名字](绝对路径)``），所以引擎那边的附件解析、path-only 约定、
        识图子代理、inline 模式全部不用改一行。

        失败（下载不了、写不进去）只记日志，原来那句话照样往下走 —— 收附件是加分
        项，不该因为它把一条正常消息吞掉。收附件成功但用户一个字没说时，只把附件
        送进去也是合理的（「这个文件讲了什么」本来就是完整的提问）。
        """
        if not msg.attachments:
            return text
        try:
            dest = context.app_context().external_files_dir / "uploads"
            saved = await save_attachments(self.adapter, msg, dest)
        except Exception as exc:  # pragma: no cover - 兜底，别拖垮这一轮
            self.adapter.log(f"处理入站附件失败：{exc}", "warn")
            return text
        if not saved:
            return text
        lines = []
        for kind, path in saved:
            posix = path.as_posix()
            lines.append(
                f"![{path.name}]({posix})" if kind == "image"
                else f"[附件: {path.name}]({posix})"
            )
        images = sum(1 for kind, _ in saved if kind == "image")
        files = len(saved) - images
        self.adapter.log(
            f"收到 {len(saved)} 个附件（图 {images} / 文件 {files}），已存入工作区"
        )
        refs = "\n".join(lines)
        return f"{refs}\n\n{text}" if text else refs

    async def _send(self, msg: IncomingMessage, text: str) -> None:
        # 一律 strip：分块发送时常有一段以空行开头/结尾，IM 里就是一片空白，
        # 看起来像「机器人发了条空消息」。
        body = _tidy(str(text or "")).strip()
        if not body:
            return
        # 发到 IM 的正文也不带盘符：对方看到的应该是
        # ``/var/minis/workspace/uploads/x.jpg`` 而不是 ``C:\\Users\\<名字>\\…``。
        # 一是别把本机目录结构甩给聊天对象，二是和模型看到的是同一套写法，
        # 出问题时对着排查不用做心算换算。
        body = scrub_machine_paths(body)
        try:
            await self.adapter.send_text(msg, body)
        except Exception as exc:  # pragma: no cover - 发不出去只能记日志
            self.adapter.log(f"回复失败：{exc}", "error")

    async def _deliver_new_images(
        self, msg: IncomingMessage, since: float, sent: set[str]
    ) -> None:
        """把本轮新生成的图片补发给用户（去重后）。

        技能脚本生图（shell 里跑）不会带 ``toolEnd`` 的 images 元数据，模型又常
        忘记调 ``send`` —— 用户那边只看到「画好了」却收不到图。这里按落盘时间扫
        一遍工作区的生图目录兜底；正文引用与 toolEnd 已经发过的路径不会重复发。

        还有第二种漏法（用户现场「发过来 → 收到一张昨天的图」）：模型交付的路径
        是**从会话历史里抄的** ``![生成图](…)``，会话里攒了两张就抄错，把上一轮那张
        发了出去；而本轮压根没有新落盘的图，按 ``since`` 扫自然是空的。此时模型已经
        表露了「要交付图片」的意图（``sent`` 非空），就把**最近的那张产物**也兜上
        —— 用户先收到错的那张、紧接着收到对的那张，总好过只收到错的。
        """
        try:
            from ..server.media_scan import collect_recent_images

            images = collect_recent_images(since=since)
            if not images and sent:
                recent = collect_recent_images(since=time.time() - RECENT_ARTIFACT_WINDOW)
                # 只补**最新那一张**：放宽窗口会把一堆旧图也捞进来，那才是真的刷屏。
                # 显式按 mtime 取最大，别依赖返回顺序（同一秒内落盘的图顺序不稳）。
                if recent:
                    newest = max(
                        recent, key=lambda p: Path(p).stat().st_mtime, default=None
                    )
                    if newest is not None and newest not in sent:
                        images = [newest]
        except Exception as exc:  # pragma: no cover - 兜底失败不影响这一轮
            # 带上异常文本：这里曾经把 ``NameError`` 一起吞掉，表现为「兜底悄无声息
            # 地不生效」，排障时全靠猜。
            self.adapter.log(f"本轮新图兜底扫描失败：{exc}", "warn")
            return
        fresh = [p for p in images if p not in sent]
        if not fresh:
            return
        await self._send_attachments(
            msg, [("image", p) for p in fresh], sent
        )

    def _start_typing_heartbeat(self, msg: IncomingMessage) -> asyncio.Task[Any] | None:
        """跑一轮期间持续刷新「正在输入」。

        平台的输入状态只活几秒，而带工具的回合经常跑几分钟（识图尤其慢）——
        只在开头打一次，用户 5 秒后就看不出机器人还在不在干活，体验就是「发出去
        石沉大海，几分钟后突然蹦出一大段」。这个心跳让它一直显示「正在输入」。

        用输入状态而不是发消息，是因为它**不占被动回复的次数额度**（QQ 群聊只有
        5 次、单聊 4 次），刷新多少次都不影响正经回复。
        """
        interval = max(1.0, float(self.adapter.config.get("typingIntervalSec") or 4.0))

        async def beat() -> None:
            while True:
                await asyncio.sleep(interval)
                try:
                    await self.adapter.send_typing(msg)
                except Exception:  # pragma: no cover - 心跳失败不值得中断对话
                    return

        try:
            return asyncio.create_task(beat())
        except RuntimeError:  # pragma: no cover - 没有事件循环（同步测试里）
            return None

    async def _send_attachments(
        self, msg: IncomingMessage, refs: list[tuple[str, str]], sent: set[str]
    ) -> None:
        """把一批本地附件发给通道（同一个文件只发一次）。

        ``sent`` 由调用方持有、跨整个回合共用 —— 生图工具、``toolEnd`` 帧、正文里
        的 markdown 引用可能重复指向同一个文件，用户不该收到三遍。
        """
        for kind, path in refs:
            key = str(path or "").strip()
            if not key or key in sent:
                continue
            sent.add(key)
            handler = self.adapter.send_file if kind == "file" else self.adapter.send_image
            # 适配器有没有实现富媒体？（基类默认实现是「把路径当文本发」并返回 False）
            base = ChannelAdapter.send_file if kind == "file" else ChannelAdapter.send_image
            unsupported = getattr(type(self.adapter), base.__name__, None) is base
            try:
                ok = await handler(msg, key)
            except Exception as exc:  # pragma: no cover - 平台侧各种意外
                self.adapter.log(f"发附件失败：{exc}", "error")
                continue
            if ok is False and unsupported:
                self.adapter.log("这个通道不支持直接发附件，已退回把路径当文本发", "warn")

    # -- 跑一轮 -----------------------------------------------------------
    async def _run_turn(self, msg: IncomingMessage, text: str) -> None:
        from ..server import main as server_main

        sid = await self._ensure_session(msg)
        if sid is None:
            await self._send(msg, "会话创建失败，稍后再试。")
            return

        kind, target = options.parse_target(str(self.adapter.config.get("agentId") or ""))
        if kind == "subagent" and target:
            await self._run_subagent_turn(msg, sid, text, target)
            return

        client_id = f"bot:{self.plugin_id}:{msg.conv_key}"
        buffer: list[str] = []
        seen = {"chars": 0, "error": ""}
        refs = _AttachmentRefFilter()
        #: 这个回合已经发过的附件 —— 生图工具 / toolEnd 帧 / 正文引用会重复指向
        #: 同一个文件，用户不该收到三遍。
        sent_atts: set[str] = set()

        async def flush_text() -> None:
            payload = "".join(buffer)
            buffer.clear()
            await self._send(msg, payload)

        async def on_frame(frame: dict[str, Any]) -> None:
            kind_ = frame.get("type")
            if kind_ == "delta":
                chunk = str(frame.get("text") or "")
                if not chunk:
                    return
                seen["chars"] += len(chunk)
                text_out, found = refs.feed(chunk)
                if text_out:
                    buffer.append(text_out)
                if found:
                    # 先把已经攒着的正文发出去，再发附件 —— 顺序别倒过来
                    await flush_text()
                    await self._send_attachments(msg, found, sent_atts)
                if self.chunk_chars and sum(len(p) for p in buffer) >= self.chunk_chars:
                    await flush_text()
            elif kind_ == "toolEnd":
                # 生图工具/脚本本轮新落盘的图，引擎已经扫好放在帧里了
                images = [
                    ("image", str(p))
                    for p in (frame.get("images") or [])
                    if str(p or "")
                ]
                if images:
                    await flush_text()
                    await self._send_attachments(msg, images, sent_atts)
            elif kind_ == "error":
                seen["error"] = str(frame.get("error") or "")

        server_main.manager.attach(client_id, _Sink(on_frame))
        typing: asyncio.Task[Any] | None = None
        turn_started = time.time()
        try:
            await self.adapter.send_typing(msg)
            typing = self._start_typing_heartbeat(msg)
            payload: dict[str, Any] = {"text": text, "sessionId": sid}
            if kind == "identity" and target:
                # 「由哪个 agent 接待」= 固定人设 + 工具集，不跟着网页端当前身份变
                payload["identityId"] = target
            await server_main._run_chat(client_id, payload)
            # 收尾兜底：本轮新落盘的图，凡是还没发过的都补发。
            #
            # 为什么需要：技能脚本（shell 里跑生图）产出的图**不带** toolEnd 的
            # images 元数据，模型又常忘记调 send、也不一定写 markdown 引用 ——
            # 用户在 QQ 那边只看到「画好了」却什么也没收到。这里按落盘时间兜一次，
            # 与正文引用/toolEnd 已发过的路径去重，不会重复发。
            await self._deliver_new_images(msg, turn_started, sent_atts)
        except Exception as exc:
            self.adapter.log(f"跑一轮失败：{exc}", "error")
            await self._send(msg, f"出错了：{exc}")
            return
        finally:
            if typing is not None:
                typing.cancel()
            server_main.manager.disconnect(client_id)

        tail, found = refs.flush()
        if tail:
            buffer.append(tail)
        if found:
            await self._send_attachments(msg, found, sent_atts)
        payload = "".join(buffer)
        if payload.strip():
            await self._send(msg, payload)
        elif seen["error"]:
            await self._send(msg, f"出错了：{seen['error']}")
        elif seen["chars"] == 0 and not sent_atts:
            await self._send(msg, "（这轮没有产生内容）")
        self.adapter.log(
            f"回复完成（{seen['chars']} 字"
            + (f"，{len(sent_atts)} 个附件" if sent_atts else "")
            + "）"
        )

    async def _run_subagent_turn(
        self, msg: IncomingMessage, session_id: str, text: str, subagent_id: str
    ) -> None:
        """这一段对话交给子代理跑。

        子代理不走 ``_run_chat``（那是主 agent 的循环），但它的内层循环会把过程
        发到**子代理事件总线**上 —— 我们把那个总线接过来，于是 QQ 这边照样是
        流式回复，而不是干等一大段。子代理失败/不存在时如实告诉用户。
        """
        from ..agent import subagent_events
        from ..agent.subagents import SubagentError, run_subagent
        from ..settings.store import SettingsStore

        buffer: list[str] = []
        streamed = {"n": 0}
        refs = _AttachmentRefFilter()
        sent_atts: set[str] = set()

        async def flush_text() -> None:
            payload = "".join(buffer)
            buffer.clear()
            if payload.strip():
                streamed["n"] += len(payload)
                await self._send(msg, payload)

        async def on_event(event: dict[str, Any]) -> None:
            if event.get("type") != "subagentDelta":
                return
            chunk = str(event.get("text") or "")
            if not chunk:
                return
            text_out, found = refs.feed(chunk)
            if text_out:
                buffer.append(text_out)
            if found:
                await flush_text()
                await self._send_attachments(msg, found, sent_atts)
            if self.chunk_chars and sum(len(p) for p in buffer) >= self.chunk_chars:
                await flush_text()

        token = subagent_events.set_emitter(on_event)
        typing: asyncio.Task[Any] | None = None
        try:
            await self.adapter.send_typing(msg)
            typing = self._start_typing_heartbeat(msg)
            store = SettingsStore.get()
            answer = await run_subagent(store, subagent_id, text, session_id)
        except SubagentError as exc:
            await self._send(msg, f"这个通道指定的子代理用不了：{exc}")
            return
        except Exception as exc:  # pragma: no cover - 兜底，别让连接断掉
            self.adapter.log(f"子代理跑失败：{exc}", "error")
            await self._send(msg, f"出错了：{exc}")
            return
        finally:
            if typing is not None:
                typing.cancel()
            subagent_events.reset_emitter(token)

        tail, found = refs.flush()
        if tail:
            buffer.append(tail)
        if found:
            await self._send_attachments(msg, found, sent_atts)
        payload = "".join(buffer)
        if payload.strip():
            # 已经流出去一部分，剩下的尾巴补一条
            if streamed["n"] == 0:
                streamed["n"] = len(payload)
            await self._send(msg, payload)
        elif streamed["n"] == 0 and not sent_atts:
            # 一个字都没流过（子代理没走事件总线 / 内容太短）→ 整段发出去
            await self._send(msg, answer or "（这轮没有产生内容）")
        self.adapter.log(f"回复完成（子代理 {subagent_id}）")

    # -- 会话 -------------------------------------------------------------
    async def _ensure_session(self, msg: IncomingMessage) -> str | None:
        """这个 IM 会话对应的引擎会话：没有就建一个并记下来。"""
        from ..server import chat_store

        async with self._map_lock:
            data = self._load_map()
            entry = data.get(msg.conv_key) or {}
            sid = str(entry.get("active") or "")
            if sid and await chat_store.get_session(sid) is not None:
                return sid
            created = await chat_store.create_session()
            known = [str(x) for x in (entry.get("known") or [])]
            if sid and sid not in known:
                known.append(sid)  # 旧会话失效了也留在列表里（可能被删了）
            known.append(created.id)
            data[msg.conv_key] = {
                "active": created.id,
                "known": known[-20:],
                "label": msg.scope_name or msg.scope,
                "updatedAt": int(time.time() * 1000),
            }
            self._save_map(data)
            self.adapter.log(f"新建对话 {created.id[:8]}（{msg.conv_key}）")
            return created.id

    async def new_session(self, conv_key: str) -> str:
        """开新对话（/new）。"""
        from ..server import chat_store

        async with self._map_lock:
            data = self._load_map()
            entry = data.get(conv_key) or {}
            created = await chat_store.create_session()
            known = [str(x) for x in (entry.get("known") or [])]
            known.append(created.id)
            data[conv_key] = {
                "active": created.id,
                "known": known[-20:],
                "updatedAt": int(time.time() * 1000),
            }
            self._save_map(data)
            return created.id

    async def conversation_list(self, conv_key: str) -> list[dict[str, Any]]:
        from ..server import chat_store

        data = self._load_map()
        entry = data.get(conv_key) or {}
        active = str(entry.get("active") or "")
        out: list[dict[str, Any]] = []
        for sid in reversed([str(x) for x in (entry.get("known") or [])]):
            info = await chat_store.get_session(sid)
            if info is None:
                continue
            out.append({
                "id": sid,
                "title": info.title,
                "updatedAt": info.updatedAt,
                "active": sid == active,
            })
        return out

    # -- 命令 -------------------------------------------------------------
    async def _command(self, msg: IncomingMessage, text: str) -> bool:
        head, _, arg = text[1:].partition(" ")
        name = head.strip().lower()
        arg = arg.strip()

        if name in ("help", "?", "h", "帮助"):
            await self._send(msg, HELP_TEXT)
            return True
        if name in ("new", "reset", "新"):
            sid = await self.new_session(msg.conv_key)
            await self._send(msg, f"好，新开了一个对话（{sid[:8]}）。说吧。")
            return True
        if name in ("list", "sessions", "ls", "列表"):
            await self._send(msg, await self._list_text(msg))
            return True
        if name in ("resume", "use", "switch", "切"):
            await self._send(msg, await self._resume_text(msg, arg))
            return True
        if name in ("ping", "在吗"):
            await self._send(msg, "pong")
            return True
        return False

    async def _list_text(self, msg: IncomingMessage) -> str:
        rows = await self.conversation_list(msg.conv_key)
        if not rows:
            return "还没有对话，直接说句话就开一个。"
        lines = ["这个会话的对话（/resume N 切换）："]
        for index, row in enumerate(rows[:10], start=1):
            mark = " ← 当前" if row["active"] else ""
            when = time.strftime("%m-%d %H:%M", time.localtime(row["updatedAt"] / 1000))
            lines.append(f"{index}. {row['title']}（{when}）{mark}")
        return "\n".join(lines)

    async def _resume_text(self, msg: IncomingMessage, arg: str) -> str:
        rows = await self.conversation_list(msg.conv_key)
        if not rows:
            return "还没有可切换的对话。"
        target: dict[str, Any] | None = None
        if arg.isdigit():
            index = int(arg)
            if 1 <= index <= len(rows):
                target = rows[index - 1]
        else:
            for row in rows:
                if row["id"].startswith(arg):
                    target = row
                    break
        if target is None:
            return "没找到这个对话，用 /list 看看编号。"
        async with self._map_lock:
            data = self._load_map()
            entry = data.get(msg.conv_key) or {}
            entry["active"] = target["id"]
            data[msg.conv_key] = entry
            self._save_map(data)
        return f"切到「{target['title']}」了，接着说。"


__all__ = ["ConversationBridge", "HELP_TEXT", "DEFAULT_CHUNK_CHARS"]
