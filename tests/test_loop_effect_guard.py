"""Effect-tool guards: shell_execute / send produce real-world output, so a
repeat of the *same successful command* only generates duplicate artefacts
(the "生图成功了还在重复生成重复图" case). Success-then-repeat must warn fast
and block on the third identical run; retry-after-failure stays allowed."""

from __future__ import annotations

from openminis.agent.tool_loop_detector import (
    LoopLevel,
    ToolLoopDetector,
)

GEN_ARGS = {"command": 'python scripts/image_generation.py "queen" --size 1024x1536'}


def test_second_identical_success_blocks():
    """「一张就停」：成功后第 2 次原样重跑直接拦截。"""
    d = ToolLoopDetector()
    d.record("shell_execute", GEN_ARGS, result="saved 20260912_x.png",
             tool_call_id="g1")
    r = d.check("shell_execute", GEN_ARGS)
    assert r.level == LoopLevel.CRITICAL
    assert "LOOP BLOCKED" in (r.message or "")


def test_third_identical_success_blocks():
    d = ToolLoopDetector()
    for i in ("g1", "g2"):
        d.record("shell_execute", GEN_ARGS, result=f"saved {i}.png",
                 tool_call_id=i)
    r = d.check("shell_execute", GEN_ARGS)
    assert r.level == LoopLevel.CRITICAL
    assert "LOOP BLOCKED" in (r.message or "")


def test_failure_then_retry_allowed():
    """A failed run resets the streak — retrying the same command is legit."""
    d = ToolLoopDetector()
    d.record("shell_execute", GEN_ARGS, result="boom", error_message="boom",
             tool_call_id="g1")
    r = d.check("shell_execute", GEN_ARGS)
    assert r.level == LoopLevel.NONE
    # …and after a successful retry, one more repeat warns (not blocks).
    d.record("shell_execute", GEN_ARGS, result="ok this time",
             tool_call_id="g2")
    d.record("shell_execute", GEN_ARGS, result="ok again", tool_call_id="g3")
    r = d.check("shell_execute", GEN_ARGS)
    assert r.level == LoopLevel.CRITICAL


def test_different_args_not_affected():
    """连发多张不同图（不同 prompt/size）是正常工作流，不得拦截。"""
    d = ToolLoopDetector()
    for i in range(4):
        d.record("shell_execute", {"command": f'python gen.py "prompt-{i}"'},
                 result=f"saved-{i}.png", tool_call_id=f"g{i}")
    r = d.check("shell_execute", {"command": 'python gen.py "prompt-4"'})
    assert r.level == LoopLevel.NONE


def test_other_tool_in_between_resets_effect_streak():
    d = ToolLoopDetector()
    d.record("shell_execute", GEN_ARGS, result="saved a.png", tool_call_id="g1")
    d.record("read_image", {"path": "a.png"}, result="a queen", tool_call_id="r1")
    r = d.check("shell_execute", GEN_ARGS)
    assert r.level == LoopLevel.NONE


def test_effect_runaway_backstop():
    """Near-identical commands (args differ by a timestamp digit) dodge the
    exact-hash rule; the any-args consecutive backstop still trips — but only
    ABOVE the auto-wrap-up horizon (≤12 rounds) so pure tool-no-text loops
    get the summary path first."""
    d = ToolLoopDetector()
    for i in range(14):
        d.record("shell_execute",
                 {"command": f'python gen.py "p" --out out-{i}.png'},
                 result=f"saved {i}", tool_call_id=f"g{i}")
    r = d.check("shell_execute", {"command": 'python gen.py "p" --out out-99.png'})
    assert r.level == LoopLevel.CRITICAL
    # 6..13 consecutive runs only warn (wrap-up guard owns the earlier rounds).
    d2 = ToolLoopDetector()
    for i in range(13):
        d2.record("shell_execute",
                  {"command": f'python gen.py "p" --out out-{i}.png'},
                  result=f"saved {i}", tool_call_id=f"g{i}")
    assert d2.check("shell_execute",
                    {"command": 'x'}).level == LoopLevel.WARNING


def test_send_tool_guarded_too():
    d = ToolLoopDetector()
    d.record("send", {"path": "a.png", "text": "图"}, result="sent",
             tool_call_id="s1")
    r = d.check("send", {"path": "a.png", "text": "图"})
    assert r.level == LoopLevel.CRITICAL
