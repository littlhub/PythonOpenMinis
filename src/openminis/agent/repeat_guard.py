"""Runtime-level loop guards that are NOT part of the Kotlin detector.

``tool_loop_detector.py`` is a verbatim port of KT's ``ToolLoopDetector`` — four
generic strategies keyed on *identical arguments + identical results*. The two
spirals this project actually hit in practice are structurally invisible to
them, because every call looks brand new:

* **identical repeat** — the same tool called *back to back with identical
  arguments* after a successful run. Nothing can change between the two calls,
  so the 2nd one is blocked immediately ("第一次重复就停"): this is what the user
  hit with ``web_fetch`` pulling the same URL nine times.
* **retrieval-family runaway** — ``memory_get`` / ``web_search`` / ``web_fetch``
  / ``browser_use`` / ``read_image`` called back to back with *different*
  arguments ("整理知识 → 反复 memory_get", the news roundup spiral of
  ``web_fetch`` a listing → ``browser_use`` an article → ``web_fetch`` another
  listing, and "同一张图反复识图"). The family is merged into one streak so the
  model cannot dodge the guard by alternating tools inside the family.
* **effect-tool repeat** — ``shell_execute`` / ``send`` change the outside world
  (生图 / 发消息 / 写产物). Every successful run produces a *different* artefact
  name (timestamp), so result-based rules never see "no progress" either;
  re-running the identical successful command only yields duplicates. The user
  asked for 「一张就停」: the 2nd identical run is blocked outright.

Keeping them here (instead of inside the detector) means the ported detector
stays byte-for-byte comparable with Kotlin, while these heuristics can evolve
freely as runtime policy. ``AgentRuntime`` consults both and blocks if either
returns CRITICAL.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from typing import Any, Optional

from ..core.logging import get_logger
from .tool_loop_detector import (
    LoopCheckResult,
    LoopLevel,
    args_hash_for,
)

logger = get_logger("agent.repeat_guard")

__all__ = ["RepeatRecord", "RepeatGuardConfig", "RepeatGuard"]


@dataclass(frozen=True)
class RepeatRecord:
    """One finished tool call as seen by the guard (no result text needed)."""

    tool_name: str
    args_hash: str
    #: True=执行成功。effect 规则要区分「成功后再跑一遍」与「失败后合理重试」，
    #: 失败会打断连续计数。
    ok: Optional[bool] = None


@dataclass(frozen=True)
class RepeatGuardConfig:
    """Thresholds for the project-specific guards."""

    history_size: int = 30
    #: 「第一次重复就停」：同一个工具**紧接着**用完全相同的参数再调一次，且上次
    #: 是成功的 —— 结果不可能变，直接拦。用户明确要求（截图里 web_fetch 同一个
    #: URL 连拉 9 次才被拦住）。失败后的重试不受影响（ok=False 打断计数）。
    identical_repeat_critical: int = 1
    #: 检索/浏览家族：同一家族内接力调用合并计数（换成家族内另一个工具也不重置）。
    query_tools: tuple[str, ...] = (
        "memory_get", "web_search", "web_fetch", "browser_use", "read_image",
    )
    query_warning_threshold: int = 5
    query_critical_threshold: int = 10
    #: 产出型工具：执行成功会改变外部世界（生图/发消息/写产物）。同参重复由上
    #: 面的「第一次重复就停」兜住；这里只保留「换着参数反复跑」的兜底。
    effect_tools: tuple[str, ...] = ("shell_execute", "send")
    #: 任意参数连续执行兜底：连发多张不同图属正常（4 张内不打扰）。硬拦截阈值
    #: 必须高于 auto wrap-up（≤12 轮）——纯「只有工具没有文本」的空转优先走带
    #: 总结的收尾路径，这个兜底只负责「有文本但仍在反复跑命令」的残余场景。
    effect_run_warning: int = 6
    effect_run_critical: int = 14
    #: 生图家族 —— 「一张就停」（技能脚本版）。用户实测：只要一张图，模型却
    #: 串行跑了 4 次 agnes-image 脚本（17:16 / 17:17 / 17:18 各一张）。每次
    #: prompt/种子不同 → 同参规则看不见；走 shell_execute → effect 兜底阈值
    #: 6/14 又太高，4 次根本没到。生图是「一次就够」的动作，**同一轮内**
    #: 第二次调用直接拦（同轮并发给全 = 用户明确要多张：check 发生在执行前、
    #: 计数还是 0，不会被误伤）。
    image_gen_tools: tuple[str, ...] = ("image_gen",)
    #: shell 命令里出现这些片段就认定是「生图」动作（技能脚本形式）。
    image_command_markers: tuple[str, ...] = (
        "agnes-image", "agnes_image", "image_generation", "imagegen",
    )
    image_repeat_critical: int = 1


class RepeatGuard:
    """Independent sliding-window guard for the two project-specific spirals.

    Same two-hook shape as ``ToolLoopDetector``: ``check()`` before execution
    (CRITICAL → caller must skip the tool), ``record()`` after execution.
    """

    def __init__(self, config: RepeatGuardConfig = RepeatGuardConfig()) -> None:
        self.config = config
        self._history: deque[RepeatRecord] = deque()
        #: 本轮（一条用户消息 = 一个 run()）内**已成功生成**的图片数。
        #: 「一张就停」是按轮计的：用户下一句再要一张图时不该被上一轮历史拦住。
        self._turn_image_success = 0

    def reset(self) -> None:
        self._history.clear()
        self._turn_image_success = 0

    def begin_turn(self) -> None:
        """新的一轮用户消息开始 —— 按轮计数的护栏在这里归零。

        由 ``AgentRuntime.run()`` 调用。生图「一张就停」只在本轮内生效，
        跨轮保留会让用户第二次要图时莫名被拦。
        """
        self._turn_image_success = 0

    # ─── before-execution hook ──────────────────────────────────────────────
    def check(self, tool_name: str, params: dict[str, Any]) -> LoopCheckResult:
        args_hash = args_hash_for(tool_name, params)

        # 0. image_gen_repeat — 「一张就停」（含技能脚本形式）。放在最前面：
        #    本轮已经出过图了，再跑一次只会多一张重复图。用户实测「生成一张图
        #    却连续生成了 4 张」，根因就是这条缺口。
        if self._is_image_generation(tool_name, params):
            if self._turn_image_success >= self.config.image_repeat_critical:
                msg = (
                    "[LOOP BLOCKED] CRITICAL: 本轮已经成功生成过图片"
                    f"（{self._turn_image_success} 张），**不要再次生成**。"
                    "生成图片是「一次就够」的动作：用户没有明确要求多张时只出一张；"
                    "确需多张也不是反复串行重跑，而是在**同一轮**里一次性给出多个调用。"
                    "现在立刻停止生成，把已经拿到的图片用 "
                    "`![简短说明](图片绝对路径)` 写进你的回复交出产物并总结收尾。"
                )
                logger.warning("CRITICAL image_gen_repeat tool=%s done=%s",
                               tool_name, self._turn_image_success)
                return LoopCheckResult(LoopLevel.CRITICAL, msg)

        # 1. identical_repeat — 「第一次重复就停」：紧接着用完全相同的参数再调
        #    一次（上次成功）本身就是空转。不看结果内容（生图/抓网页的结果每次
        #    都可能不一样，看结果永远判不出重复），只看工具+参数+上次是否成功。
        streak = self._identical_success_streak(tool_name, args_hash)
        if streak >= self.config.identical_repeat_critical:
            msg = (
                f"[LOOP BLOCKED] CRITICAL: {tool_name} 刚刚已经用**完全相同的参数**"
                f"成功调用过 {streak} 次，结果不会改变，本次调用已被拦截。"
                "不要重复调用同一个工具 —— 直接引用上一次的结果；"
                "如果目标已经达成，请立刻用文本向用户总结收尾，"
                "确需重跑请修改参数并说明原因。"
            )
            logger.warning("CRITICAL identical_repeat tool=%s streak=%s",
                           tool_name, streak)
            return LoopCheckResult(LoopLevel.CRITICAL, msg)

        # 2. effect_tool_repeat — 同一条产出型命令换着参数反复跑（文件名/时间戳
        #    每次都不同，所以同参规则看不见），连续次数过高时兜底拦截。
        if tool_name in self.config.effect_tools:
            run_streak = self._consecutive_run_streak(tool_name)
            if run_streak >= self.config.effect_run_critical:
                msg = (
                    f"[LOOP BLOCKED] CRITICAL: {tool_name} 已连续执行 {run_streak} 次。"
                    "任务大概率已完成，停止执行，对照已有产出总结收尾。"
                )
                logger.warning("CRITICAL effect_tool_runaway tool=%s streak=%s",
                               tool_name, run_streak)
                return LoopCheckResult(LoopLevel.CRITICAL, msg)
            if run_streak >= self.config.effect_run_warning:
                msg = (
                    f"[LOOP WARNING] {tool_name} 已连续执行 {run_streak} 次。"
                    "先自检：目标是否已全部完成？完成就立即总结收尾，"
                    "不要继续执行同类命令。"
                )
                logger.debug("WARNING effect_tool_runaway tool=%s streak=%s",
                             tool_name, run_streak)
                return LoopCheckResult(LoopLevel.WARNING, msg,
                                       "effectrun:" + tool_name)

        # 3. retrieval-family runaway — 家族内接力调用合并计数，参数每次都不同
        #    也能识别（同参数检测永远看不到这种「每次换关键词再搜一遍」）。
        if tool_name in self.config.query_tools:
            streak = self._family_run_streak(tool_name)
            if streak >= self.config.query_critical_threshold:
                msg = (
                    f"[LOOP BLOCKED] CRITICAL: retrieval/browsing tools "
                    f"({tool_name} and similar like web_fetch/browser_use) have "
                    f"now been called {streak} times in a row with no other tool "
                    "in between. Re-searching does not add information. Stop now "
                    "and answer with what you already have, or tell the user the "
                    "task needs more input."
                )
                logger.warning("CRITICAL query_tool_runaway tool=%s streak=%s",
                               tool_name, streak)
                return LoopCheckResult(LoopLevel.CRITICAL, msg)
            if streak >= self.config.query_warning_threshold:
                msg = (
                    f"[LOOP WARNING] retrieval/browsing tools (incl. "
                    f"{tool_name}) have now been called {streak} times in a row. "
                    "If the last calls did not find new information, stop "
                    "searching and proceed with what you have."
                )
                logger.debug("WARNING query_tool_runaway tool=%s streak=%s",
                             tool_name, streak)
                return LoopCheckResult(LoopLevel.WARNING, msg,
                                       f"queryrun:{tool_name}")

        return LoopCheckResult.none()

    # ─── after-execution hook ───────────────────────────────────────────────
    def record(
        self,
        tool_name: str,
        params: dict[str, Any],
        result: Optional[str] = None,
        error_message: Optional[str] = None,
        tool_call_id: Optional[str] = None,
    ) -> LoopCheckResult:
        """Append the finished call. Warnings are emitted by ``check()``."""
        self._history.append(RepeatRecord(
            tool_name=tool_name,
            args_hash=args_hash_for(tool_name, params),
            ok=error_message is None,
        ))
        # 生图成功 → 记本轮已出图，供上面的「一张就停」用。
        if error_message is None and self._is_image_generation(tool_name, params):
            self._turn_image_success += 1
        while len(self._history) > self.config.history_size:
            self._history.popleft()
        return LoopCheckResult.none()

    # ─── strategy helpers ───────────────────────────────────────────────────
    def _is_image_generation(self, tool_name: str, params: dict[str, Any]) -> bool:
        """这一次调用是不是「生成图片」。

        两条路都要认：
        * 原生 ``image_gen`` 工具；
        * 技能脚本形式 —— ``shell_execute`` 跑 agnes-image 的
          ``image_generation*.py``（本项目实际在走的那条）。
        """
        if tool_name in self.config.image_gen_tools:
            return True
        if tool_name not in ("shell_execute", "bash", "terminal"):
            return False
        try:
            blob = json.dumps(params, ensure_ascii=False).lower()
        except (TypeError, ValueError):  # pragma: no cover - 参数不可序列化
            blob = str(params).lower()
        return any(m in blob for m in self.config.image_command_markers)
    def _identical_success_streak(self, tool_name: str, args_hash: str) -> int:
        """Trailing *successful* records with identical (tool, args).

        A failed run (ok=False) resets the count — retrying after failure is
        legitimate. Records of other tools also reset it: the rule only targets
        back-to-back duplication of the exact same call.
        """
        streak = 0
        for rec in reversed(self._history):
            if rec.tool_name != tool_name or rec.args_hash != args_hash:
                break
            if rec.ok is False:
                break
            streak += 1
        return streak

    def _consecutive_run_streak(self, tool_name: str) -> int:
        """Trailing records of the same effect tool, *any* args.

        Backstop for near-identical commands whose args differ by a digit
        (e.g. a timestamp), which exact-hash matching never sees.
        """
        streak = 0
        for rec in reversed(self._history):
            if rec.tool_name == tool_name:
                streak += 1
            else:
                break
        return streak

    def _family_run_streak(self, tool_name: str) -> int:
        """Count trailing records belonging to the retrieval/browse family."""
        streak = 0
        for rec in reversed(self._history):
            if rec.tool_name in self.config.query_tools:
                streak += 1
            else:
                break
        return streak
