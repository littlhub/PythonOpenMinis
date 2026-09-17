"""[T-plugins-manager] 通道桥：IM 会话 ↔ 引擎会话。

适配器只管平台那层；「这条消息归哪个会话、怎么跑一轮、回复怎么发回去」全在这里。
跑一轮**复用主对话链路**（``_run_chat``）：模型、技能、工具、子代理、记忆、压缩、
沙箱全都和网页端一模一样，机器人不是另一套缩水实现。

做法很直接：往连接管理器里挂一个「虚拟订阅者」，``_run_chat`` 的推流帧就会送到
这里来 —— 于是机器人也能用上流式输出（按平台上限分段发），不用另接一套。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable, Awaitable

from ..core import context
from . import options
from .base import ChannelAdapter, IncomingMessage

#: 累积到这么多字符就先发一段（长回答不必等到全部跑完）。
DEFAULT_CHUNK_CHARS = 900

HELP_TEXT = """我可以直接聊天，也认这几条命令：
/new      开一个新对话（之后的消息接着新对话走）
/list     看看这个会话有哪些对话
/resume N 切到第 N 个对话（N 见 /list）
/ping     看看我还在不在
/help     这条帮助

群里需要 @我；私聊直接说就行。"""


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
        text = (msg.text or "").strip()
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

    async def _send(self, msg: IncomingMessage, text: str) -> None:
        if not str(text or "").strip():
            return
        try:
            await self.adapter.send_text(msg, text)
        except Exception as exc:  # pragma: no cover - 发不出去只能记日志
            self.adapter.log(f"回复失败：{exc}", "error")

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

        async def on_frame(frame: dict[str, Any]) -> None:
            kind_ = frame.get("type")
            if kind_ == "delta":
                chunk = str(frame.get("text") or "")
                if not chunk:
                    return
                buffer.append(chunk)
                seen["chars"] += len(chunk)
                pending = sum(len(p) for p in buffer)
                if self.chunk_chars and pending >= self.chunk_chars:
                    payload = "".join(buffer)
                    buffer.clear()
                    await self.adapter.send_text(msg, payload)
            elif kind_ == "error":
                seen["error"] = str(frame.get("error") or "")

        server_main.manager.attach(client_id, _Sink(on_frame))
        try:
            await self.adapter.send_typing(msg)
            payload: dict[str, Any] = {"text": text, "sessionId": sid}
            if kind == "identity" and target:
                # 「由哪个 agent 接待」= 固定人设 + 工具集，不跟着网页端当前身份变
                payload["identityId"] = target
            await server_main._run_chat(client_id, payload)
        except Exception as exc:
            self.adapter.log(f"跑一轮失败：{exc}", "error")
            await self._send(msg, f"出错了：{exc}")
            return
        finally:
            server_main.manager.disconnect(client_id)

        tail = "".join(buffer)
        if tail.strip():
            await self._send(msg, tail)
        elif seen["error"]:
            await self._send(msg, f"出错了：{seen['error']}")
        elif seen["chars"] == 0:
            await self._send(msg, "（这轮没有产生内容）")
        self.adapter.log(f"回复完成（{seen['chars']} 字）")

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

        async def on_event(event: dict[str, Any]) -> None:
            if event.get("type") != "subagentDelta":
                return
            chunk = str(event.get("text") or "")
            if not chunk:
                return
            buffer.append(chunk)
            if self.chunk_chars and sum(len(p) for p in buffer) >= self.chunk_chars:
                payload = "".join(buffer)
                buffer.clear()
                streamed["n"] += len(payload)
                await self.adapter.send_text(msg, payload)

        token = subagent_events.set_emitter(on_event)
        try:
            await self.adapter.send_typing(msg)
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
            subagent_events.reset_emitter(token)

        tail = "".join(buffer)
        if tail.strip():
            # 已经流出去一部分，剩下的尾巴补一条
            await self._send(msg, tail)
        elif streamed["n"] == 0:
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
