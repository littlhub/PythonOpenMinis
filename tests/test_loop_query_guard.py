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


# --------------------------------------------------------------------------
# browse-family merge: web_fetch / browser_use alternate during a news roundup
# --------------------------------------------------------------------------

def test_browser_use_runaway_blocks_after_ten():
    """browser_use is part of the retrieval/browse family — a long single-tool
    run must trip the same guard as web_fetch."""
    d = ToolLoopDetector()
    for i in range(10):
        d.record("browser_use", {"url": f"https://x/{i}"},
                 result=f"page-{i}", tool_call_id=f"b{i}")
    r = d.check("browser_use", {"url": "https://x/next"})
    assert r.level == LoopLevel.CRITICAL


def test_web_fetch_browser_use_alternating_still_trips():
    """The news spiral: web_fetch a listing, browser_use an article, web_fetch
    the next listing… Different tools AND different args on every call, so
    identical-args detection never fires. The family merge must still trip."""
    d = ToolLoopDetector()
    for i in range(10):
        if i % 2 == 0:
            d.record("web_fetch", {"url": f"https://news.example/list?p={i}"},
                     result=f"list-{i}", tool_call_id=f"f{i}")
        else:
            d.record("browser_use", {"url": f"https://news.example/a{i}"},
                     result=f"article-{i}", tool_call_id=f"b{i}")
    r = d.check("web_fetch", {"url": "https://news.example/list?p=99"})
    assert r.level == LoopLevel.CRITICAL


def test_non_query_tool_between_fetches_resets_streak():
    """A genuine non-browse action (e.g. writing a file) is progress and must
    reset the browsing streak."""
    d = ToolLoopDetector()
    for i in range(6):
        d.record("web_fetch", {"url": f"https://x/{i}"},
                 result=f"p-{i}", tool_call_id=f"f{i}")
    d.record("shell_execute", {"command": "echo hi"}, result="hi",
             tool_call_id="s1")
    for i in range(3):
        d.record("web_fetch", {"url": f"https://y/{i}"},
                 result=f"q-{i}", tool_call_id=f"g{i}")
    r = d.check("web_fetch", {"url": "https://y/next"})
    assert r.level != LoopLevel.CRITICAL  # streak restarted at 3
