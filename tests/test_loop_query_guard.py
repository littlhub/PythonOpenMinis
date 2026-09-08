"""The query-tool runaway guard: memory_get / web_search / web_fetch called
many times in a row must trip even when every call has different arguments
(that is the "整理知识 → 反复 memory_get" spiral identical-args detection
never catches)."""

from __future__ import annotations

from openminis.agent.tool_loop_detector import (
    LoopLevel,
    ToolLoopDetector,
)


def _record_get(detector, i: int) -> None:
    detector.record("memory_get", {"scope": "all", "keywords": f"keyword-{i}"},
                    result=f"chunk-{i}", tool_call_id=f"t{i}")


def test_memory_get_runaway_blocks_after_ten():
    d = ToolLoopDetector()
    for i in range(10):  # ten consecutive memory_get, DIFFERENT keywords
        _record_get(d, i)
    r = d.check("memory_get", {"scope": "all", "keywords": "another"})
    assert r.level == LoopLevel.CRITICAL
    assert "in a row" in (r.message or "")


def test_memory_get_warns_from_five():
    d = ToolLoopDetector()
    for i in range(5):
        _record_get(d, i)
    r = d.check("memory_get", {"scope": "all", "keywords": "x"})
    assert r.level == LoopLevel.WARNING


def test_other_tool_in_between_resets_streak():
    d = ToolLoopDetector()
    for i in range(4):
        _record_get(d, i)
    # a different tool (real progress) breaks the consecutive streak
    d.record("memory_write", {"scope": "wiki", "topic": "t", "content": "c"},
             result="saved", tool_call_id="w1")
    for i in range(4, 8):
        _record_get(d, i)
    r = d.check("memory_get", {"scope": "all", "keywords": "later"})
    assert r.level != LoopLevel.CRITICAL  # streak restarted at 4


def test_normal_tool_not_guarded():
    d = ToolLoopDetector()
    for i in range(15):
        d.record("shell_execute", {"command": f"echo {i}"},
                 result=f"out-{i}", tool_call_id=f"s{i}")
    r = d.check("shell_execute", {"command": "echo done"})
    assert r.level == LoopLevel.NONE
