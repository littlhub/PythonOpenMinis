"""LLM 兜底模型链：主模型限流/超时自动切下一个。

覆盖三件事：
1. 切换策略 —— 哪些错误该换人（限流/API Key 失效/厂商错误/网络超时），
   哪些不该（解码错误），以及**已经吐出内容之后一律不换**（撤不回来）。
2. 顺序 —— 主模型 → 第一个兜底 → 第二个兜底 …… 谁先成功就用谁。
3. 配置解析 —— 无效项、已删除的厂商实例、与主模型重复的条目都要跳过。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from openminis.data.model import (
    LLMError,
    LLMMessage,
    LLMModel,
    LLMStreamChunk,
    ThinkingLevel,
)
from openminis.provider.fallback import (
    FallbackCandidate,
    FallbackChainProvider,
    describe_error,
)
from openminis.server.main import app
from openminis.settings import chat_service
from openminis.settings.chat_service import _fallback_candidates
from openminis.settings.store import SettingsStore


@pytest.fixture()
def store(tmp_path, monkeypatch):
    s = SettingsStore(path=tmp_path / "settings.json")
    monkeypatch.setattr(SettingsStore, "get", classmethod(lambda cls: s))
    return s


class FakeProvider:
    """够用的假 provider：要么吐字，要么在吐字前/后报指定的错。"""

    def __init__(
        self,
        model_id: str,
        *,
        text: str = "",
        error: Exception | None = None,
        error_after_text: bool = False,
    ) -> None:
        self.model = LLMModel(
            id=model_id, display_name=model_id.upper(), provider="test"
        )
        self.name = model_id
        self._text = text
        self._error = error
        self._error_after_text = error_after_text
        self.calls = 0

    @property
    def default_max_output_tokens(self) -> int:
        return 1024

    async def send_message_clamped(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError

    async def stream_message_clamped(
        self,
        messages,
        system_prompt,
        max_tokens,
        temperature,
        image_parts,
        tools,
        thinking_level,
    ):
        self.calls += 1
        if self._error is not None and not self._error_after_text:
            raise self._error
        if self._text:
            yield LLMStreamChunk.Text(self._text)
        if self._error is not None and self._error_after_text:
            raise self._error


def _collect(provider: FallbackChainProvider) -> list[str]:
    async def run() -> list[str]:
        out: list[str] = []
        async for chunk in provider.stream_message_clamped(
            [], None, 128, None, [], [], ThinkingLevel.OFF
        ):
            if isinstance(chunk, LLMStreamChunk.Text):
                out.append(chunk.text)
        return out

    return asyncio.run(run())


def _chain(
    primary: FakeProvider,
    candidates: list[tuple[str, FakeProvider]],
    *,
    on_switch=None,
) -> FallbackChainProvider:
    by_id = {cand_id: prov for cand_id, prov in candidates}

    def build(c: FallbackCandidate):  # noqa: ANN202
        return by_id[c.instance_id]

    return FallbackChainProvider(
        primary,
        [FallbackCandidate(i, "m", i) for i, _ in candidates],
        build,
        on_switch=on_switch,
    )


# ---------------------------------------------------------------------------
# 切换策略
# ---------------------------------------------------------------------------
def test_rate_limited_switches_to_first_fallback():
    p0 = FakeProvider("p0", error=LLMError.RateLimited())
    p1 = FakeProvider("p1", text="hello")
    seen: list[tuple[str, str, str]] = []
    chain = _chain(p0, [("p1", p1)], on_switch=lambda a, b, r: seen.append((a, b, r)))

    assert _collect(chain) == ["hello"]
    assert p0.calls == 1 and p1.calls == 1
    # 主模型那一侧报的是「用户看得见的名字」（display_name），兜底报配置里的标签
    assert seen == [("P0", "p1", "Rate limited")]
    assert chain.active is p1
    assert chain.active_label == "P1"


def test_walks_chain_in_order_until_one_works():
    p0 = FakeProvider("p0", error=LLMError.RateLimited())
    p1 = FakeProvider("p1", error=LLMError.RateLimited())
    p2 = FakeProvider("p2", text="ok")
    chain = _chain(p0, [("p1", p1), ("p2", p2)])

    assert _collect(chain) == ["ok"]
    assert [p0.calls, p1.calls, p2.calls] == [1, 1, 1]
    assert len(chain.switch_trail) == 2


def test_network_error_is_fallbackable():
    """超时/断网映射来的 NetworkError 也要换人 —— 网关抖动比模型本身更常见。"""
    p0 = FakeProvider("p0", error=LLMError.NetworkError(TimeoutError("read")))
    p1 = FakeProvider("p1", text="recovered")
    chain = _chain(p0, [("p1", p1)])

    assert _collect(chain) == ["recovered"]
    assert chain.active is p1


def test_non_fallbackable_error_raises_without_switching():
    """解码错误不是"换个人就能好"的问题 —— 直接抛，别白烧一次兜底。"""
    p0 = FakeProvider("p0", error=LLMError.DecodingError(ValueError("bad json")))
    p1 = FakeProvider("p1", text="should not run")
    chain = _chain(p0, [("p1", p1)])

    with pytest.raises(LLMError):
        _collect(chain)
    assert p1.calls == 0
    assert chain.switch_trail == []


def test_error_after_content_does_not_switch():
    """已经吐出去的文字撤不回来 —— 这时报错只能如实抛，不能换模型重来。"""
    p0 = FakeProvider(
        "p0", text="half an answer", error=LLMError.RateLimited(),
        error_after_text=True,
    )
    p1 = FakeProvider("p1", text="restart")
    chain = _chain(p0, [("p1", p1)])

    with pytest.raises(LLMError):
        _collect(chain)
    assert p1.calls == 0
    assert chain.switch_trail == []


def test_transient_error_retries_same_provider_before_switching():
    """瞬时错误（5xx）先在原地重试；重试完还不行才换人。"""
    p0 = FakeProvider("p0", error=LLMError.TransientError("boom"))
    p1 = FakeProvider("p1", text="fallback")
    chain = _chain(p0, [("p1", p1)])

    assert _collect(chain) == ["fallback"]
    # 主模型被试了 1 + _SAME_PROVIDER_RETRIES 次，之后才轮到兜底
    assert p0.calls == 3
    assert p1.calls == 1


def test_chain_exhausted_raises_last_error():
    p0 = FakeProvider("p0", error=LLMError.RateLimited())
    p1 = FakeProvider("p1", error=LLMError.RateLimited())
    chain = _chain(p0, [("p1", p1)])

    with pytest.raises(LLMError):
        _collect(chain)
    assert len(chain.switch_trail) == 1


def test_broken_candidate_is_skipped_not_fatal():
    """某个兜底的配置坏了（实例被删等）—— 跳过它，别把整轮拖死。"""
    p0 = FakeProvider("p0", error=LLMError.RateLimited())
    p2 = FakeProvider("p2", text="survived")

    def build(c: FallbackCandidate):  # noqa: ANN202
        if c.instance_id == "bad":
            raise RuntimeError("no such instance")
        return p2

    chain = FallbackChainProvider(
        p0,
        [FallbackCandidate("bad", "m", "bad"), FallbackCandidate("good", "m", "good")],
        build,
    )
    assert _collect(chain) == ["survived"]
    assert chain.active is p2


def test_describe_error_is_user_facing():
    assert describe_error(LLMError.RateLimited()) == "Rate limited"
    assert describe_error(ValueError("x")) == "ValueError"


# ---------------------------------------------------------------------------
# 配置解析
# ---------------------------------------------------------------------------
def _put(client: TestClient, **payload) -> dict:
    r = client.put("/api/settings", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def test_default_agent_config_has_empty_fallback_chain(store):
    assert store.agent_config().get("fallbackModels") == []


def test_settings_cleans_malformed_fallback_entries(store):
    with TestClient(app) as c:
        _put(c, providers=[
            {"id": "gw-a", "type": "openAI", "apiKey": "sk-a", "model": "m1"},
            {"id": "gw-b", "type": "openAI", "apiKey": "sk-b", "model": "m2"},
        ])
        _put(c, agent={"fallbackModels": [
            {"instance": "gw-b", "model": "m2"},
            {"instance": "", "model": "m2"},        # 缺实例 → 丢
            {"instance": "gw-a", "model": ""},      # 缺模型 → 丢
            "not-a-dict",                           # 类型不对 → 丢
            {"instance": " gw-a ", "model": " m1 "},  # 前后空格要 trim
        ]})
    assert store.agent_config()["fallbackModels"] == [
        {"instance": "gw-b", "model": "m2"},
        {"instance": "gw-a", "model": "m1"},
    ]


def test_settings_rejects_non_list_fallback_models(store):
    with TestClient(app) as c:
        r = c.put("/api/settings", json={"agent": {"fallbackModels": "gw-a/m1"}})
    assert r.status_code == 400
    assert "fallbackModels" in r.text


def test_fallback_candidates_skips_missing_and_duplicate(store):
    with TestClient(app) as c:
        _put(c, providers=[
            {"id": "gw-a", "type": "openAI", "apiKey": "sk-a", "model": "primary"},
            {"id": "gw-b", "type": "openAI", "apiKey": "sk-b", "model": "m2"},
        ])

    cfg = {
        "fallbackModels": [
            {"instance": "gw-a", "model": "primary"},  # 与主模型一样 → 跳过
            {"instance": "ghost", "model": "m9"},      # 实例不存在 → 跳过
            {"instance": "gw-b", "model": "m2"},
            {"instance": "gw-b", "model": "m2"},       # 重复 → 只留一个
        ]
    }
    cands = _fallback_candidates(store, cfg, "gw-a")
    assert [(c.instance_id, c.model_id) for c in cands] == [("gw-b", "m2")]


def test_chat_setup_wraps_provider_only_when_chain_configured(store):
    """没配兜底 → 原样返回 provider；配了 → 包成链。"""
    with TestClient(app) as c:
        _put(c, providers=[
            {"id": "gw-a", "type": "openAI", "apiKey": "sk-a", "model": "m1"},
            {"id": "gw-b", "type": "openAI", "apiKey": "sk-b", "model": "m2"},
        ], activeProviderId="gw-a")

    provider, _runtime, _options, _identity, _conf = chat_service.build_chat_setup(store)
    assert not isinstance(provider, FallbackChainProvider)

    with TestClient(app) as c:
        _put(c, agent={"fallbackModels": [{"instance": "gw-b", "model": "m2"}]})
    provider, _runtime, _options, _identity, _conf = chat_service.build_chat_setup(store)
    assert isinstance(provider, FallbackChainProvider)
    assert provider.active_label  # 主模型名先顶上


# ---------------------------------------------------------------------------
# 上游把 SSE 流中途掐断（RemoteProtocolError）—— 两层都曾出错（2026-09-27 线上实测）
#
# 现场：agnes 网关在流中途关掉连接 → httpx 抛 RemoteProtocolError →
#   (1) ``_map_error`` 只认 5 个具体异常类，它落到 ``Unknown`` —— 既不可重试也
#       不可切换，明明配了兜底模型也永远不生效；
#   (2) ``Unknown.__init__`` 照抄 Kotlin 的 ``cause.message``，Python 异常没这个
#       属性 → **错误映射器自己崩**，用户只看到
#       "对话出错: 'RemoteProtocolError' object has no attribute 'message'"。
# ---------------------------------------------------------------------------
def _truncation() -> Exception:
    import httpx

    return httpx.RemoteProtocolError(
        "peer closed connection without sending complete message body "
        "(incomplete chunked read)"
    )


def test_unknown_error_does_not_crash_on_bare_exception():
    """错误映射器**绝不能自己崩** —— 否则真正的上游故障被整套埋掉。"""
    cause = _truncation()
    err = LLMError.Unknown(cause)  # 修前：AttributeError: no attribute 'message'
    assert "peer closed connection" in str(err)
    # ``LLMError`` 不自定义 ``__init__``，cause 走 ``Exception.args[1]``
    # （不是 ``__cause__`` —— 那要 ``raise ... from ...`` 才会设）。
    assert err.args[1] is cause
    # 没有 cause 也不能崩
    assert "None" in str(LLMError.Unknown(None))
    # 有 ``message`` 属性的异常仍沿用原语义
    class WithMessage(Exception):
        message = "自定义消息"

    assert "自定义消息" in str(LLMError.Unknown(WithMessage()))


@pytest.mark.parametrize(
    "module_name",
    ["openai.openai_provider", "anthropic.anthropic_provider"],
)
def test_truncated_stream_maps_to_retryable_network_error(module_name):
    """掐流必须是**可重试的网络错误**，兜底链才会接手。"""
    import importlib

    import httpx

    module = importlib.import_module(f"openminis.provider.{module_name}")
    provider_cls = next(
        obj
        for obj in vars(module).values()
        if isinstance(obj, type) and hasattr(obj, "_map_error")
        and obj.__module__ == module.__name__
    )
    mapped = provider_cls._map_error(_truncation())
    assert isinstance(mapped, LLMError)
    assert isinstance(mapped, LLMError) and mapped.is_retryable, (
        "掐流不可重试 → 兜底链永远不会换人（修前的症状）"
    )
    assert "peer closed connection" in str(mapped)
    # 原有覆盖不能丢：连接/读超时仍归网络错误
    for exc in (httpx.ConnectError("x"), httpx.ReadTimeout("x"), httpx.ReadError("x")):
        assert provider_cls._map_error(exc).is_retryable
    # LLMError 原样透传、不二次包装
    already = LLMError.RateLimited()
    assert provider_cls._map_error(already) is already


def test_truncated_stream_actually_switches_to_fallback():
    """端到端：主模型掐流 → 真的切到兜底模型并拿到答案（不只是"分类对了"）。"""
    import httpx

    mapped = LLMError.NetworkError(_truncation())
    p0 = FakeProvider("p0", error=mapped)
    p1 = FakeProvider("p1", text="recovered")
    seen: list[tuple[str, str, str]] = []
    chain = _chain(p0, [("p1", p1)], on_switch=lambda a, b, r: seen.append((a, b, r)))

    assert _collect(chain) == ["recovered"]
    assert chain.active is p1
    # NetworkError 先原地重试（_SAME_PROVIDER_RETRIES=2 次），失败后才换人
    assert p0.calls == 3 and p1.calls == 1
    assert seen and seen[0][2].startswith("Network error")


def test_truncation_after_partial_content_does_not_merge_two_answers():
    """已经吐出正文之后再掐流 —— 不能重来（会把两段回复接在一起）。"""
    p0 = FakeProvider("p0", text="半截…", error=_truncation(), error_after_text=True)
    p1 = FakeProvider("p1", text="不该出现")
    chain = _chain(p0, [("p1", p1)])

    with pytest.raises(Exception) as ei:
        _collect(chain)
    assert "peer closed connection" in str(ei.value)
    assert p1.calls == 0
