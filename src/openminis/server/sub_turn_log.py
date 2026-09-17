"""[T-subagent-log-persist] 子代理过程落库：把事件流折叠成「一段段过程」。

背景：子代理（``subagents.run_subagent``）的过程只以 WebSocket 帧推给前端
（``subagentStart`` / ``subagentDelta`` / ``subagentToolStart`` / ``subagentToolEnd``），
**不落库**。于是刷新页面、重连后重拉历史、重启后端之后，整段子代理过程连同它
调过的那一堆工具卡片一起消失 —— 用户回头只看到一句最终答复，无从回看「它到底
干了什么」。开着子代理时这个问题尤其明显（绝大部分工具调用其实发生在子代理里）。

这里只做一件事：把同一条会话里的子代理事件流，按「一次委派 = 一段过程」折叠，
产出可直接落库的结构。落库与读回在 :mod:`openminis.server.chat_store`
（``append_sub_turn`` / ``parts_to_sub``）。

**这些内容不进模型上下文**：正文写进 ``subtext`` part 而不是 ``text`` part，
而历史重建（``load_runtime_history`` → ``parts_to_text``）只认 text part。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

#: 单条子代理工具输出落库前的上限 —— 与主代理工具卡同档（8000 字符）。
SUB_OUTPUT_MAX_CHARS = 8000

#: 一段子代理过程最多保留多少条工具调用（防跑飞时把库撑爆）。
MAX_SUB_RUNS = 200


def _clip_output(text: Any) -> str:
    body = "" if text is None else str(text)
    if len(body) <= SUB_OUTPUT_MAX_CHARS:
        return body
    return (
        f"{body[:SUB_OUTPUT_MAX_CHARS]}\n"
        f"…(输出过长已截断：共 {len(body)} 字符，此处保留前 {SUB_OUTPUT_MAX_CHARS})"
    )


def _turn_key(ev: dict[str, Any]) -> str:
    """一次委派的唯一键：委派它的那次工具调用 id + 子代理 id。"""
    room = str(ev.get("id") or "")
    who = str(ev.get("subagentId") or "")
    return f"{room}:{who}"


def _speaker_of(ev: dict[str, Any]) -> dict[str, Any]:
    """子代理的身份（前端头像/名字/项目标签要用）。"""
    return {
        "id": str(ev.get("id") or ""),
        "subagentId": str(ev.get("subagentId") or ""),
        "name": str(ev.get("name") or ""),
        "emoji": str(ev.get("emoji") or "🤖"),
        "project": str(ev.get("project") or ""),
    }


@dataclass
class SubTurn:
    """一次子代理委派的全过程：它说了什么 + 它调了哪些工具。"""

    key: str
    room_id: str
    speaker: dict[str, Any]
    task: str = ""
    text: str = ""
    runs: list[dict[str, Any]] = field(default_factory=list)

    def as_payload(self) -> dict[str, Any]:
        """落库需要的字段（见 ``chat_store.append_sub_turn``）。"""
        return {
            "speaker": dict(self.speaker),
            "task": self.task,
            "roomId": self.room_id,
            "text": self.text,
            "runs": list(self.runs),
        }


class SubTurnRecorder:
    """把子代理事件流按顺序折叠成若干 :class:`SubTurn`。

    用法：把它挂在推流那条路上（``set_emitter`` 的回调里 ``on_event``），
    本轮结束后 ``turns()`` 拿结果落库。识别不了的帧直接忽略 —— 它只是旁路
    记录，绝不能让记录本身影响对话。
    """

    def __init__(self) -> None:
        self._order: list[str] = []
        self._turns: dict[str, SubTurn] = {}
        self._started: dict[tuple[str, str], float] = {}

    def on_event(self, ev: dict[str, Any] | None) -> None:
        if not isinstance(ev, dict):
            return
        kind = ev.get("type")
        if not isinstance(kind, str) or not kind.startswith("subagent"):
            return
        key = _turn_key(ev)
        if kind == "subagentStart":
            if key not in self._turns:
                self._order.append(key)
            self._turns[key] = SubTurn(
                key=key,
                room_id=str(ev.get("id") or ""),
                speaker=_speaker_of(ev),
                task=str(ev.get("task") or ""),
            )
            return

        turn = self._turns.get(key)
        if turn is None:
            # 没见过 Start 就先收到过程（理论上不会）：补一段，别丢过程。
            self._order.append(key)
            turn = SubTurn(
                key=key, room_id=str(ev.get("id") or ""), speaker=_speaker_of(ev)
            )
            self._turns[key] = turn

        if kind == "subagentDelta":
            turn.text += str(ev.get("text") or "")
        elif kind == "subagentToolStart":
            if len(turn.runs) >= MAX_SUB_RUNS:
                return
            call_id = str(ev.get("callId") or "")
            self._started[(key, call_id)] = time.time()
            turn.runs.append({
                "id": call_id,
                "name": str(ev.get("name") or ""),
                "input": ev.get("input") or {},
            })
        elif kind == "subagentToolEnd":
            call_id = str(ev.get("callId") or "")
            started = self._started.pop((key, call_id), None)
            for run in turn.runs:
                if run.get("id") != call_id:
                    continue
                run["ok"] = bool(ev.get("ok", True))
                run["output"] = _clip_output(ev.get("output"))
                if started is not None:
                    run["ms"] = int((time.time() - started) * 1000)
                images = ev.get("images")
                if isinstance(images, list) and images:
                    run["images"] = [str(p) for p in images]
                break

    def turns(self) -> list[SubTurn]:
        """按发生顺序返回所有折叠好的过程。"""
        return [self._turns[k] for k in self._order]


__all__ = ["SubTurn", "SubTurnRecorder", "SUB_OUTPUT_MAX_CHARS", "MAX_SUB_RUNS"]
