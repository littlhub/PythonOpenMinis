"""子代理活动 → 前端「群聊」事件的总线。

主代理委派子代理时，子代理的内层循环默认是**静默**的
（``AgentRuntime(tools=…)`` 不带 ``chunk_sink``）：用户只看到一个
``subagent_delegate`` 工具卡，然后干等结果 —— 子代理说了什么、调了哪些工具，
一概看不见。

这里用 contextvar 把内层循环接到外层推流上：``server.main._run_chat`` 在跑
agent 之前 ``set_emitter(...)``，``asyncio.gather`` 派生出的工具任务会继承
这份上下文，于是 ``agent.subagents.run_subagent`` **不用改签名**就能把过程推给
前端，前端把它们渲染成群聊气泡（每个子代理一个「发言人」）。

没有听众时 :func:`emit` 是空操作 —— 子代理照旧静默跑（子代理内部的识图链路、
单元测试、CLI 都走这条），行为与从前完全一致。
"""

from __future__ import annotations

import contextvars
from typing import Any, Awaitable, Callable, Optional

__all__ = [
    "EventSink",
    "set_emitter",
    "reset_emitter",
    "emitter",
    "active",
    "emit",
    "push_tool_use",
    "reset_tool_use",
    "current_tool_use",
    "set_projects",
    "project_for",
]

#: 事件出口：拿到一个 JSON 可序列化的 dict，推给前端（WS 帧直接用它）。
EventSink = Callable[[dict[str, Any]], Awaitable[None]]

_EMITTER: contextvars.ContextVar[Optional[EventSink]] = contextvars.ContextVar(
    "openminis.subagent_emitter", default=None
)
#: 当前正在执行的**外层**工具调用（id + name）。子代理要知道自己是挂在哪个
#: `subagent_delegate` 调用下面的 —— 前端据此把子代理气泡归到那张工具卡。
_TOOL_USE: contextvars.ContextVar[Optional[dict[str, str]]] = contextvars.ContextVar(
    "openminis.current_tool_use", default=None
)


def set_emitter(sink: Optional[EventSink]) -> contextvars.Token:
    """装上事件出口，返回 token（必须用 :func:`reset_emitter` 还原）。"""
    return _EMITTER.set(sink)


def reset_emitter(token: contextvars.Token) -> None:
    try:
        _EMITTER.reset(token)
    except ValueError:  # pragma: no cover - token 跨上下文时才会发生
        _EMITTER.set(None)


def emitter() -> Optional[EventSink]:
    return _EMITTER.get()


def active() -> bool:
    """有没有人在听（子代理据此决定是否要多做一层事件包装）。"""
    return _EMITTER.get() is not None


async def emit(event: dict[str, Any]) -> None:
    """推一条事件。没装出口就什么也不做；出口自己抛错也不该影响子代理。"""
    sink = _EMITTER.get()
    if sink is None:
        return
    try:
        await sink(event)
    except Exception:  # pragma: no cover - 推送失败不能打断子代理
        from ..core.logging import get_logger

        get_logger(__name__).debug("subagent event send failed", exc_info=True)


def push_tool_use(tool_id: str, name: str) -> contextvars.Token:
    return _TOOL_USE.set({"id": str(tool_id or ""), "name": str(name or "")})


def reset_tool_use(token: contextvars.Token) -> None:
    try:
        _TOOL_USE.reset(token)
    except ValueError:  # pragma: no cover
        _TOOL_USE.set(None)


def current_tool_use() -> dict[str, str]:
    return dict(_TOOL_USE.get() or {})


#: 「分配项目」：子代理 id → 绝对目录。用户在群聊里给某个成员指定了项目，
#: 该子代理的 shell 就 root 到那个目录（而不是会话默认的工作空间）。
_PROJECTS: contextvars.ContextVar[Optional[dict[str, str]]] = contextvars.ContextVar(
    "openminis.subagent_projects", default=None
)


def set_projects(mapping: Optional[dict[str, str]]) -> None:
    """装上本轮「成员 → 项目目录」的分配表（``None`` 清空）。"""
    _PROJECTS.set(dict(mapping) if mapping else None)


def project_for(subagent_id: str) -> Optional[str]:
    """这个子代理本轮被分配到的项目目录；没分配返回 ``None``。"""
    mapping = _PROJECTS.get() or {}
    value = mapping.get(str(subagent_id))
    return str(value) if value else None
