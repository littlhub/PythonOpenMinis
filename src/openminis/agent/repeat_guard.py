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
import re
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

__all__ = [
    "RepeatRecord", "RepeatGuardConfig", "RepeatGuard",
    "looks_like_image_generation",
]

#: 技能脚本里声明张数的写法：``--count 3`` / ``-n 3`` / ``--num 3``。
_SHELL_IMAGE_COUNT_RE = re.compile(
    r"(?:--count|--num|--n|-n)\s*[= ]\s*(\d+)", re.IGNORECASE
)
#: ``image_gen`` 工具里可能出现的张数字段名。
_IMAGE_COUNT_KEYS = ("count", "n", "num_images", "number_of_images", "num")

#: 默认的「生图」判定参数（与 ``RepeatGuardConfig`` 的默认值一致）。
DEFAULT_IMAGE_GEN_TOOLS: tuple[str, ...] = ("image_gen",)
DEFAULT_IMAGE_COMMAND_MARKERS: tuple[str, ...] = (
    "agnes-image", "agnes_image", "image_generation", "imagegen",
)


def looks_like_image_generation(
    tool_name: str,
    params: Optional[dict[str, Any]] = None,
    *,
    image_gen_tools: tuple[str, ...] = DEFAULT_IMAGE_GEN_TOOLS,
    command_markers: tuple[str, ...] = DEFAULT_IMAGE_COMMAND_MARKERS,
) -> bool:
    """这次工具调用是不是「生成图片」。

    两条路都要认：原生 ``image_gen`` 工具；技能脚本形式 —— ``shell_execute``
    跑 agnes-image 的 ``image_generation*.py``（本项目实际在走的那条）。

    提到模块级是因为除了循环护栏，服务端还要用它决定「本轮生成了图 → 通知
    前端自动预览」（见 ``server/main.py`` 的 toolEnd 帧）。
    """
    if tool_name in image_gen_tools:
        return True
    if tool_name not in ("shell_execute", "bash", "terminal"):
        return False
    try:
        blob = json.dumps(params or {}, ensure_ascii=False).lower()
    except (TypeError, ValueError):  # pragma: no cover - 参数不可序列化
        blob = str(params).lower()
    return any(m in blob for m in command_markers)



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
    #: 生图家族 —— 张数由**模型自己按语义声明**（用户明确要求）。
    #:
    #: 用户实测：要一张图，模型却串行跑了 4 次 agnes-image 脚本（17:16 / 17:17
    #: / 17:18 各一张）；后来开着 kt 又连出 12 张。每次 prompt/种子都不同 → 同参
    #: 规则看不见；走 shell_execute → effect 兜底阈值 6/14 又太高。
    #:
    #: 现在的规则不是「硬限一张」，而是**消费模型声明的张数**：模型在调用里用
    #: ``count``（``image_gen`` 工具）或 ``--count N``（技能脚本）一次性声明本轮
    #: 要几张，护栏按这个预算放行 —— 说一张跑一次、说两张跑两次（同一次调用里
    #: 串行跑 N 次）。没声明就按 ``image_default_per_turn`` 张算。声明张数被封顶
    #: 在 ``max_images_per_turn``（防失控），成功数达到预算后同轮再调一律拦下，
    #: 且不允许把预算无限抬高。
    image_gen_tools: tuple[str, ...] = ("image_gen",)
    #: shell 命令里出现这些片段就认定是「生图」动作（技能脚本形式）。
    image_command_markers: tuple[str, ...] = (
        "agnes-image", "agnes_image", "image_generation", "imagegen",
    )
    #: 模型没在调用里声明张数时的默认预算。
    image_default_per_turn: int = 1
    #: 单轮生图张数硬上限（模型声明再多也不越过；防「12 张停不下来」回归）。
    max_images_per_turn: int = 8


class RepeatGuard:
    """Independent sliding-window guard for the two project-specific spirals.

    Same two-hook shape as ``ToolLoopDetector``: ``check()`` before execution
    (CRITICAL → caller must skip the tool), ``record()`` after execution.
    """

    def __init__(self, config: RepeatGuardConfig = RepeatGuardConfig()) -> None:
        self.config = config
        self._history: deque[RepeatRecord] = deque()
        #: 本轮（一条用户消息 = 一个 run()）内**已成功生成**的图片数。
        #: 生图预算是按轮计的：用户下一句再要一张图时不该被上一轮历史拦住。
        self._turn_image_success = 0
        #: 本轮的生图预算（模型声明的张数）。``None`` = 还没声明过，用默认值。
        self._turn_image_budget: Optional[int] = None

    def reset(self) -> None:
        self._history.clear()
        self._turn_image_success = 0
        self._turn_image_budget = None

    def begin_turn(self) -> None:
        """新的一轮用户消息开始 —— 按轮计数的护栏在这里归零。

        由 ``AgentRuntime.run()`` 调用。生图预算只在本轮内生效，跨轮保留会让
        用户第二次要图时莫名被拦。
        """
        self._turn_image_success = 0
        self._turn_image_budget = None

    def set_image_budget(self, count: Optional[int]) -> None:
        """显式设定本轮生图预算（张数）。

        供运行时在**执行前**注入一个已知张数（例如从模型声明的 JSON 配置里
        解析出来）。``None`` 表示回到「按调用里声明的张数自适应」。
        """
        if count is None:
            self._turn_image_budget = None
            return
        try:
            n = int(count)
        except (TypeError, ValueError):
            return
        if n > 0:
            self._turn_image_budget = min(n, max(1, self.config.max_images_per_turn))

    # ─── 生图配额（两种循环模式都生效）───────────────────────────────────────
    def check_image_budget(
        self, tool_name: str, params: dict[str, Any]
    ) -> LoopCheckResult:
        """按**模型声明的张数**放行本轮生图。

        说一张跑一次、说两张跑两次：模型在调用里声明 ``count``（``image_gen``
        工具）或 ``--count N``（技能脚本），这里按声明的张数建预算并放行；
        没声明就按 ``image_default_per_turn``（默认 1）张算。

        这是**产物规则**而不是循环启发式，所以 ``kt`` 模式（只用 KT 原版检测器）
        下也照样生效：用户实测开着 kt 时连出 12 张图（每张 prompt 都不同，
        KT 的「同参+同结果」策略永远看不见）。同轮并发给全 —— check 发生在
        执行前、计数还是 0。

        预算可以被**更大的声明**抬高（模型改主意要多几张），但封顶在
        ``max_images_per_turn``；成功数达到预算后同轮再调一律拦下，所以
        「每轮都声明白张」也堆不出失控（超过上限的声明会被裁到上限）。
        """
        if not self._is_image_generation(tool_name, params):
            return LoopCheckResult.none()

        declared = self._declared_image_count(tool_name, params)
        cap = max(1, self.config.max_images_per_turn)
        if declared is not None:
            declared = min(declared, cap)
            if self._turn_image_budget is None or declared > self._turn_image_budget:
                self._turn_image_budget = declared

        budget = (
            self._turn_image_budget
            if self._turn_image_budget is not None
            else max(1, self.config.image_default_per_turn)
        )
        if self._turn_image_success < budget:
            return LoopCheckResult.none()

        msg = (
            "[LOOP BLOCKED] CRITICAL: 本轮生图预算已用完"
            f"（已生成 {self._turn_image_success} 张 / 预算 {budget} 张），"
            "**不要再次生成**。需要多张图的正确做法是：在**一次**调用里把张数"
            "声明清楚 —— `image_gen` 用 `count` 参数、技能脚本用 `--count N`"
            "（例如 `--count 2`），而不是反复串行重跑同一个命令。"
            "现在立刻停止生成，把已经拿到的图片用 "
            "`![简短说明](图片绝对路径)` 写进你的回复交出产物并总结收尾。"
            # 实测（通道场景）：模型被拦后容易一直重试生成、不肯收尾，结果用户
            # 一张图都收不到。明说「已出的图会自动交出去」能明显减少这种空转。
            "（本轮已经产出的图片**会自动交给用户**，不必反复重跑；"
            "你只需要用文字总结即可。）"
        )
        logger.warning("CRITICAL image_budget_exhausted tool=%s done=%s budget=%s",
                       tool_name, self._turn_image_success, budget)
        # ``warning_key`` 让运行时能认出「这是生图预算被拦」而不是普通循环拦截：
        # 预算用完后的每一次生图尝试都是纯空转，哪怕同一轮里还有别的工具成功，
        # 也要计入硬停（见 agent_runtime 的 image_blocked_rounds）。
        return LoopCheckResult(LoopLevel.CRITICAL, msg, "imagebudget")

    # ─── before-execution hook ──────────────────────────────────────────────
    def check(self, tool_name: str, params: dict[str, Any]) -> LoopCheckResult:
        args_hash = args_hash_for(tool_name, params)

        # 0. 生图配额 —— 「一张就停」。放在最前面：本轮已出过图，再跑只会多一张。
        blocked = self.check_image_budget(tool_name, params)
        if blocked.is_blocking:
            return blocked

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
        # 生图成功 → 记本轮已出图（按**声明的张数**累加，同样封顶），供上面的
        # 生图预算用。
        if error_message is None and self._is_image_generation(tool_name, params):
            self._turn_image_success += min(
                self._declared_image_count(tool_name, params) or 1,
                max(1, self.config.max_images_per_turn),
            )
        while len(self._history) > self.config.history_size:
            self._history.popleft()
        return LoopCheckResult.none()

    # ─── strategy helpers ───────────────────────────────────────────────────
    def _is_image_generation(self, tool_name: str, params: dict[str, Any]) -> bool:
        """这一次调用是不是「生成图片」（详见模块级同名函数）。"""
        return looks_like_image_generation(
            tool_name, params,
            image_gen_tools=self.config.image_gen_tools,
            command_markers=self.config.image_command_markers,
        )

    def _declared_image_count(
        self, tool_name: str, params: dict[str, Any]
    ) -> Optional[int]:
        """模型在本次调用里声明的张数（JSON 配置里的语义决策）。

        * ``image_gen`` 工具：读 ``count`` / ``n`` / ``num_images`` … 参数；
        * 技能脚本（shell）：从命令行里读 ``--count 2`` / ``-n 2``。

        读不到就返回 ``None``（调用方按 ``image_default_per_turn`` 处理）。
        """
        if tool_name in self.config.image_gen_tools:
            for key in _IMAGE_COUNT_KEYS:
                if key not in params:
                    continue
                try:
                    n = int(params[key])
                except (TypeError, ValueError):
                    continue
                if n > 0:
                    return n
            return None
        if tool_name not in ("shell_execute", "bash", "terminal"):
            return None
        try:
            blob = json.dumps(params, ensure_ascii=False)
        except (TypeError, ValueError):  # pragma: no cover - 参数不可序列化
            blob = str(params)
        match = _SHELL_IMAGE_COUNT_RE.search(blob)
        if not match:
            return None
        try:
            n = int(match.group(1))
        except (TypeError, ValueError):  # pragma: no cover - 正则已保证是数字
            return None
        return n if n > 0 else None

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
