"""The retrieval-family runaway guard (``agent/repeat_guard.py``).

memory_get / web_search / web_fetch / browser_use / read_image called many
times in a row must trip even when every call has different arguments — that
is the "整理知识 → 反复 memory_get" / "同一张图反复识图" spiral identical-args
detection never catches.

This guard is deliberately NOT part of the ported KT ``ToolLoopDetector``
(which stays at four generic strategies); it lives beside it as a runtime-level
companion strategy, so the tests below target ``RepeatGuard``.
"""

from __future__ import annotations

from openminis.agent.repeat_guard import RepeatGuard
from openminis.agent.tool_loop_detector import LoopLevel


def _record_get(guard: RepeatGuard, i: int) -> None:
    guard.record("memory_get", {"scope": "all", "keywords": f"keyword-{i}"},
                 result=f"chunk-{i}", tool_call_id=f"t{i}")


def test_memory_get_runaway_blocks_after_ten():
    g = RepeatGuard()
    for i in range(10):  # ten consecutive memory_get, DIFFERENT keywords
        _record_get(g, i)
    r = g.check("memory_get", {"scope": "all", "keywords": "another"})
    assert r.level == LoopLevel.CRITICAL
    assert "in a row" in (r.message or "")


def test_memory_get_warns_from_five():
    g = RepeatGuard()
    for i in range(5):
        _record_get(g, i)
    r = g.check("memory_get", {"scope": "all", "keywords": "x"})
    assert r.level == LoopLevel.WARNING


def test_other_tool_in_between_resets_streak():
    g = RepeatGuard()
    for i in range(4):
        _record_get(g, i)
    # a different tool (real progress) breaks the consecutive streak
    g.record("memory_write", {"scope": "wiki", "topic": "t", "content": "c"},
             result="saved", tool_call_id="w1")
    for i in range(4, 8):
        _record_get(g, i)
    r = g.check("memory_get", {"scope": "all", "keywords": "later"})
    assert r.level != LoopLevel.CRITICAL  # streak restarted at 4


def test_normal_tool_not_guarded():
    """Non-poll / non-query / non-effect tools with changing args stay
    unguarded by the family rule. (shell_execute is an *effect* tool with its
    own run-away backstop — covered in test_loop_effect_guard.)"""
    g = RepeatGuard()
    for i in range(15):
        g.record("memory_write", {"scope": "wiki", "topic": f"t{i}", "content": f"c{i}"},
                 result=f"saved-{i}", tool_call_id=f"w{i}")
    r = g.check("memory_write", {"scope": "wiki", "topic": "next", "content": "c"})
    assert r.level == LoopLevel.NONE


def test_read_image_in_same_family():
    """反复识图（每次换 prompt 或换图）也属检索家族，连续调用要能识别。"""
    g = RepeatGuard()
    for i in range(10):
        g.record("read_image", {"path": f"img-{i}.png"},
                 result=f"desc-{i}", tool_call_id=f"r{i}")
    r = g.check("read_image", {"path": "img-next.png"})
    assert r.level == LoopLevel.CRITICAL


def test_identical_web_fetch_blocks_on_first_repeat():
    """用户在截图里看到的问题：同一个 URL 连拉 9 次才被拦住。
    「第一次重复就停」——同一个工具紧接着用完全相同的参数再调一次，直接拦。"""
    g = RepeatGuard()
    g.record("web_fetch", {"url": "https://www.toutiao.com"}, result="今日头条",
             tool_call_id="f1")
    r = g.check("web_fetch", {"url": "https://www.toutiao.com"})
    assert r.level == LoopLevel.CRITICAL
    assert "完全相同的参数" in (r.message or "")


def test_identical_repeat_with_different_args_still_allowed():
    """换 URL 继续抓取是正常调研，不因「同参重复」规则被拦。"""
    g = RepeatGuard()
    g.record("web_fetch", {"url": "https://a.example"}, result="A", tool_call_id="f1")
    r = g.check("web_fetch", {"url": "https://b.example"})
    assert r.level == LoopLevel.NONE


def test_identical_repeat_after_failure_allowed():
    """上次失败 → 同参重试是合理修复动作，不得拦。"""
    g = RepeatGuard()
    g.record("web_fetch", {"url": "https://a.example"}, result="timeout",
             error_message="timeout", tool_call_id="f1")
    r = g.check("web_fetch", {"url": "https://a.example"})
    assert r.level == LoopLevel.NONE


# --------------------------------------------------------------------------
# browse-family merge: web_fetch / browser_use alternate during a news roundup
# --------------------------------------------------------------------------

def test_browser_use_runaway_blocks_after_ten():
    """browser_use is part of the retrieval/browse family — a long single-tool
    run must trip the same guard as web_fetch."""
    g = RepeatGuard()
    for i in range(10):
        g.record("browser_use", {"url": f"https://x/{i}"},
                 result=f"page-{i}", tool_call_id=f"b{i}")
    r = g.check("browser_use", {"url": "https://x/next"})
    assert r.level == LoopLevel.CRITICAL


def test_web_fetch_browser_use_alternating_still_trips():
    """The news spiral: web_fetch a listing, browser_use an article, web_fetch
    the next listing… Different tools AND different args on every call, so
    identical-args detection never fires. The family merge must still trip."""
    g = RepeatGuard()
    for i in range(10):
        if i % 2 == 0:
            g.record("web_fetch", {"url": f"https://news.example/list?p={i}"},
                     result=f"list-{i}", tool_call_id=f"f{i}")
        else:
            g.record("browser_use", {"url": f"https://news.example/a{i}"},
                     result=f"article-{i}", tool_call_id=f"b{i}")
    r = g.check("web_fetch", {"url": "https://news.example/list?p=99"})
    assert r.level == LoopLevel.CRITICAL


def test_non_query_tool_between_fetches_resets_streak():
    """A genuine non-browse action (e.g. writing a file) is progress and must
    reset the browsing streak."""
    g = RepeatGuard()
    for i in range(6):
        g.record("web_fetch", {"url": f"https://x/{i}"},
                 result=f"p-{i}", tool_call_id=f"f{i}")
    g.record("memory_write", {"scope": "wiki", "topic": "t", "content": "c"},
             result="saved", tool_call_id="s1")
    for i in range(3):
        g.record("web_fetch", {"url": f"https://y/{i}"},
                 result=f"q-{i}", tool_call_id=f"g{i}")
    r = g.check("web_fetch", {"url": "https://y/next"})
    assert r.level != LoopLevel.CRITICAL  # streak restarted at 3
