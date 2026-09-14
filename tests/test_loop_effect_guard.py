"""Effect-tool guards (``agent/repeat_guard.py``): shell_execute / send produce
real-world output, so a repeat of the *same successful command* only generates
duplicate artefacts (the "生图成功了还在重复生成重复图" case). Success-then-repeat
blocks on the very next identical run (user's 「一张就停」); retry-after-failure
stays allowed.

Two layers guard image generation specifically, because the user hit both:

* **identical args** — the same script call re-run verbatim;
* **per-turn image budget** — the script re-run with *different* prompts inside the
  same user turn (实测：只要一张图，却串行跑了 4 次 agnes-image 脚本，
  17:16 / 17:17 / 17:18 各出一张 —— 同参规则看不见，effect 兜底阈值又太高).
  张数不是硬编码一张，而是**由模型按语义声明**：``image_gen`` 的 ``count``
  参数 / 脚本的 ``--count N`` —— 说一张跑一次、说两张跑两次（见下面 count 用例）。

These rules are a runtime-level companion to the ported KT ToolLoopDetector —
the detector itself deliberately stays at KT's four generic strategies.
"""

from __future__ import annotations

from openminis.agent.repeat_guard import RepeatGuard, RepeatGuardConfig
from openminis.agent.tool_loop_detector import LoopLevel

#: 生图（技能脚本形式）：命令里带 image_generation → 命中 image_gen 家族。
GEN_ARGS = {"command": 'python scripts/image_generation.py "queen" --size 1024x1536'}
#: 普通产出型命令：不含生图特征，用来单独验证「同参重复 / 连续兜底」两条规则。
PLAIN_ARGS = {"command": 'python render_chart.py --out chart.png'}


# ---------------------------------------------------------------------------
# identical_repeat（与生图家族无关的通用规则）
# ---------------------------------------------------------------------------
def test_second_identical_success_blocks():
    """「一张就停」：成功后第 2 次原样重跑直接拦截。"""
    g = RepeatGuard()
    g.record("shell_execute", PLAIN_ARGS, result="saved chart.png",
             tool_call_id="g1")
    r = g.check("shell_execute", PLAIN_ARGS)
    assert r.level == LoopLevel.CRITICAL
    assert "LOOP BLOCKED" in (r.message or "")


def test_third_identical_success_blocks():
    g = RepeatGuard()
    for i in ("g1", "g2"):
        g.record("shell_execute", PLAIN_ARGS, result=f"saved {i}.png",
                 tool_call_id=i)
    r = g.check("shell_execute", PLAIN_ARGS)
    assert r.level == LoopLevel.CRITICAL
    assert "LOOP BLOCKED" in (r.message or "")


def test_failure_then_retry_allowed():
    """A failed run resets the streak — retrying the same command is legit."""
    g = RepeatGuard()
    g.record("shell_execute", PLAIN_ARGS, result="boom", error_message="boom",
             tool_call_id="g1")
    r = g.check("shell_execute", PLAIN_ARGS)
    assert r.level == LoopLevel.NONE
    # …and after a successful retry, the next identical repeat blocks.
    g.record("shell_execute", PLAIN_ARGS, result="ok this time",
             tool_call_id="g2")
    r = g.check("shell_execute", PLAIN_ARGS)
    assert r.level == LoopLevel.CRITICAL


def test_other_tool_in_between_resets_effect_streak():
    """effect 兜底只数**紧邻**的同工具记录：中间夹了别的工具就断。

    生图走的是另一条更严的规则（按轮计数，见下面的 image_gen 用例）。
    """
    g = RepeatGuard()
    g.record("shell_execute", PLAIN_ARGS, result="saved chart.png",
             tool_call_id="g1")
    g.record("read_image", {"path": "chart.png"}, result="a chart",
             tool_call_id="r1")
    r = g.check("shell_execute", PLAIN_ARGS)
    assert r.level == LoopLevel.NONE


# ---------------------------------------------------------------------------
# image_gen 家族 —— 「一张就停」（按轮）
# ---------------------------------------------------------------------------
def test_image_generation_stops_after_one_per_turn():
    """本轮出过图后，再跑生图脚本直接拦 —— 换提示词、中间看过程图也没用。

    用户实测：只要一张图，模型却串行跑了 4 次脚本（每次 prompt/种子不同）。
    """
    g = RepeatGuard()
    g.begin_turn()
    g.record("shell_execute", GEN_ARGS, result="saved a.png", tool_call_id="g1")
    g.record("read_image", {"path": "a.png"}, result="a queen",
             tool_call_id="r1")
    r = g.check("shell_execute",
                {"command": 'python scripts/image_generation.py "king"'})
    assert r.level == LoopLevel.CRITICAL
    assert "不要再次生成" in (r.message or "")


def test_image_gen_native_tool_counts_too():
    """原生 image_gen 工具同属生图家族（参数不同也算重复）。"""
    g = RepeatGuard()
    g.begin_turn()
    g.record("image_gen", {"prompt": "a cat"}, result="saved cat.png",
             tool_call_id="i1")
    r = g.check("image_gen", {"prompt": "a dog"})
    assert r.level == LoopLevel.CRITICAL


def test_image_generation_recovers_next_turn():
    """下一轮用户再要一张图时，不该被上一轮的计数拦住（换个提示词即可）。"""
    g = RepeatGuard()
    g.begin_turn()
    g.record("shell_execute", GEN_ARGS, result="saved a.png", tool_call_id="g1")
    g.begin_turn()
    r = g.check("shell_execute",
                {"command": 'python scripts/image_generation.py "king"'})
    assert r.level == LoopLevel.NONE


def test_failed_generation_does_not_count():
    """生成失败不算「出过图」，允许重试。"""
    g = RepeatGuard()
    g.begin_turn()
    g.record("image_gen", {"prompt": "x"}, error_message="boom",
             tool_call_id="i1")
    assert g.check("image_gen", {"prompt": "x"}).level == LoopLevel.NONE


# ---------------------------------------------------------------------------
# image_gen 家族 —— 张数由**模型按语义声明**（说一张跑一次、说两张跑两次）
# ---------------------------------------------------------------------------
def test_declared_count_runs_that_many_times():
    """脚本用 ``--count 2`` 声明两张 → 本轮放行到 2 张才拦。"""
    g = RepeatGuard()
    g.begin_turn()
    first = {"command": 'python scripts/image_generation.py "a" --count 2'}
    assert g.check("shell_execute", first).level == LoopLevel.NONE
    g.record("shell_execute", first, result="saved 2", tool_call_id="g1")
    r = g.check("shell_execute",
                {"command": 'python scripts/image_generation.py "b" --count 2'})
    assert r.level == LoopLevel.CRITICAL
    assert "预算" in (r.message or "")


def test_native_image_gen_count_param():
    """原生工具用 ``count`` 声明两张：一次调用给全，第二次才拦。"""
    g = RepeatGuard()
    g.begin_turn()
    assert g.check("image_gen", {"prompt": "x", "count": 2}).level == LoopLevel.NONE
    g.record("image_gen", {"prompt": "x", "count": 2}, result="2 imgs",
             tool_call_id="i1")
    assert g.check("image_gen", {"prompt": "y"}).level == LoopLevel.CRITICAL


def test_budget_escalation_is_capped():
    """模型改主意要多几张可以抬预算，但封顶；到顶后再声明也不放行。"""
    g = RepeatGuard(config=RepeatGuardConfig(max_images_per_turn=3))
    g.begin_turn()
    g.record("image_gen", {"prompt": "a", "count": 1}, result="1",
             tool_call_id="i1")
    # 已出 1 张，声明 3 → 预算抬到 3，还放行
    assert g.check("image_gen", {"prompt": "b", "count": 3}).level == LoopLevel.NONE
    g.record("image_gen", {"prompt": "b", "count": 2}, result="2",
             tool_call_id="i2")                       # 共 3 张 = 上限
    # 声明再大也封顶在 3，而已经出了 3 张 → 拦死（「12 张停不下来」不回归）
    assert g.check("image_gen", {"prompt": "c", "count": 99}).level == LoopLevel.CRITICAL


def test_set_image_budget_overrides_declaration():
    """运行时可以预先注入一个张数预算（例如从模型声明的 JSON 配置里解析）。"""
    g = RepeatGuard()
    g.begin_turn()
    g.set_image_budget(2)
    g.record("shell_execute",
             {"command": 'python scripts/image_generation.py "a"'},
             result="saved a", tool_call_id="g1")
    assert g.check("shell_execute",
                   {"command": 'python scripts/image_generation.py "b"'}
                   ).level == LoopLevel.NONE
    g.record("shell_execute",
             {"command": 'python scripts/image_generation.py "b"'},
             result="saved b", tool_call_id="g2")
    assert g.check("shell_execute",
                   {"command": 'python scripts/image_generation.py "c"'}
                   ).level == LoopLevel.CRITICAL


# ---------------------------------------------------------------------------
# 连续执行兜底
# ---------------------------------------------------------------------------
def test_effect_runaway_backstop():
    """Near-identical commands (args differ by a timestamp digit) dodge the
    exact-hash rule; the any-args consecutive backstop still trips — but only
    ABOVE the auto-wrap-up horizon (≤12 rounds) so pure tool-no-text loops
    get the summary path first."""
    g = RepeatGuard()
    for i in range(14):
        g.record("shell_execute",
                 {"command": f'python gen.py "p" --out out-{i}.png'},
                 result=f"saved {i}", tool_call_id=f"g{i}")
    r = g.check("shell_execute", {"command": 'python gen.py "p" --out out-99.png'})
    assert r.level == LoopLevel.CRITICAL
    # 6..13 consecutive runs only warn (wrap-up guard owns the earlier rounds).
    g2 = RepeatGuard()
    for i in range(13):
        g2.record("shell_execute",
                  {"command": f'python gen.py "p" --out out-{i}.png'},
                  result=f"saved {i}", tool_call_id=f"g{i}")
    assert g2.check("shell_execute",
                    {"command": 'x'}).level == LoopLevel.WARNING


def test_send_tool_guarded_too():
    g = RepeatGuard()
    g.record("send", {"path": "a.png", "text": "图"}, result="sent",
             tool_call_id="s1")
    r = g.check("send", {"path": "a.png", "text": "图"})
    assert r.level == LoopLevel.CRITICAL
