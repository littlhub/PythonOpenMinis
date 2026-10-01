"""回归测试：工具参数「没送到」时不能退化成空参执行。

背景（用真实 provider 复现过）：写 20KB+ 单文件时，若单轮输出预算（``max_tokens``）
不够，上游会在半路截断 —— 工具调用只剩 id/name，``arguments`` 变成空串或半截 JSON。
旧行为在两处把它**静默**换成 ``{}``：

* ``OpenAIProvider._tail_chunks``：``except ValueError: args = {}``
* ``AnthropicProvider._safe_json``：解析失败 ``return {}``

于是 ``zcode_write`` 拿到空参 → ``resolve_workspace_path`` 在 ``len(None)`` 上抛
``TypeError: object of type 'NoneType' has no len()`` → 模型看不懂这句话，只能原地
重试到轮次耗尽（用户报的「ZCode 又卡了」）。

这里锁住三件事：
1. 参数没送到时，chunk 必须带上 ``args_parse_error`` / ``raw_args_len``；
2. 工具层据此回一句**模型能自我纠正**的失败说明，且**绝不能**去执行 handler；
3. 缺路径走可读的 ``CoreError``，而不是 ``len(None)`` 的 TypeError。
"""

from __future__ import annotations

import asyncio
import tempfile

import pytest

from openminis.agent.zcode.contracts.errors import CoreError
from openminis.agent.zcode.integration.agent import (
    ZCODE_DEFAULT_MAX_TOKENS,
    ZCODE_DEFAULT_MAX_TURNS,
    ZcodeRunOptions,
    _execute_tool_call,
)
from openminis.agent.zcode.tool.path_policy import (
    ToolWorkspacePathOptions,
    resolve_workspace_path,
)
from openminis.data.model import LLMStreamChunk
from openminis.provider.anthropic.anthropic_provider import AnthropicProvider
from openminis.provider.openai.openai_provider import (
    OpenAIProvider,
    _ToolCallAccumulator,
)

# ---------------------------------------------------------------------------
# 1) provider 侧：参数缺失要显式带出来，而不是静默变 {}
# ---------------------------------------------------------------------------


def _provider() -> OpenAIProvider:
    return OpenAIProvider(api_key="test-key", base_url="http://127.0.0.1:1/v1")


def _flush_one(acc: _ToolCallAccumulator) -> LLMStreamChunk.ToolCallComplete:
    chunks = asyncio.run(_provider()._tail_chunks({0: acc}, [], "tool_calls"))
    completes = [c for c in chunks if isinstance(c, LLMStreamChunk.ToolCallComplete)]
    assert len(completes) == 1
    return completes[0]


def test_openai_empty_arguments_is_reported() -> None:
    """``arguments`` 是空串（中转截断的典型形态）→ 必须标记出来。"""
    acc = _ToolCallAccumulator(id="c1", name="zcode_write", args=[], started=True)
    chunk = _flush_one(acc)
    assert chunk.args == {}
    assert chunk.args_parse_error, "空参数不能被当成「模型没传参」而不留痕"
    assert chunk.raw_args_len == 0


def test_openai_truncated_json_is_reported() -> None:
    """半截 JSON（``max_tokens`` 截断）→ 必须标记出来，并带上原始长度。"""
    acc = _ToolCallAccumulator(
        id="c2",
        name="zcode_write",
        args=['{"file_path": "a.html", "content": "<!doctype html>\\n<html>'],
        started=True,
    )
    chunk = _flush_one(acc)
    assert chunk.args == {}
    assert chunk.args_parse_error
    assert chunk.raw_args_len > 0


def test_openai_valid_arguments_unchanged() -> None:
    """正常参数照旧解析，且不带失败标记（不能误伤）。"""
    acc = _ToolCallAccumulator(
        id="c3",
        name="zcode_write",
        args=['{"file_path": "a.html", "content": "hi"}'],
        started=True,
    )
    chunk = _flush_one(acc)
    assert chunk.args == {"file_path": "a.html", "content": "hi"}
    assert chunk.args_parse_error is None
    assert chunk.raw_args_len > 0


def test_anthropic_safe_json_checked() -> None:
    assert AnthropicProvider._safe_json_checked("") == ({}, None)
    assert AnthropicProvider._safe_json_checked('{"a": 1}') == ({"a": 1}, None)
    args, err = AnthropicProvider._safe_json_checked('{"a": ')
    assert args == {} and err


# ---------------------------------------------------------------------------
# 2) 工具层：带 arg_issue 时不许执行 handler，要给可自我纠正的说明
# ---------------------------------------------------------------------------


def test_execute_tool_call_refuses_empty_args() -> None:
    called = {"handler": False}

    class _Port:  # pragma: no cover - 不应被触达
        pass

    async def _run() -> tuple[str, bool]:
        # ``spec`` 只用于 ``is None`` 判定；带 arg_issue 时应当在触碰 spec.needs
        # 之前就返回，所以这里给一个最小占位对象。
        return await _execute_tool_call(
            spec=object(),  # type: ignore[arg-type]
            call_id="call_x",
            name="zcode_write",
            args={},
            runtime=_Port(),  # type: ignore[arg-type]
            turn_id="t1",
            chunk_sink=None,
            arg_issue=("arguments 为空串（上游未返回工具参数）", 0),
        )

    text, is_error = asyncio.run(_run())
    assert is_error is True
    assert "zcode_write" in text
    assert "不是" in text and "截断" in text
    # 必须告诉模型「怎么改」，否则它只会重试同一件事
    assert "edit" in text or "追加" in text
    assert called["handler"] is False


def test_execute_tool_call_without_issue_still_reports_unknown_tool() -> None:
    async def _run() -> tuple[str, bool]:
        return await _execute_tool_call(
            spec=None,
            call_id="c",
            name="nope",
            args={},
            runtime=None,  # type: ignore[arg-type]
            turn_id=None,
            chunk_sink=None,
        )

    text, is_error = asyncio.run(_run())
    assert is_error is True
    assert "Unknown ZCode tool" in text


# ---------------------------------------------------------------------------
# 3) 路径策略：缺路径给可读错误，不再 ``len(None)``
# ---------------------------------------------------------------------------


def test_resolve_workspace_path_none_path_is_readable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(CoreError) as excinfo:
            resolve_workspace_path(
                ToolWorkspacePathOptions(
                    input_path=None,  # type: ignore[arg-type]
                    working_directory=tmp,
                    workspace_root=tmp,
                    operation="write",
                )
            )
    message = str(excinfo.value)
    assert "must not be empty" in message
    assert "has no len()" not in message


# ---------------------------------------------------------------------------
# 4) 输出预算：ZCode 循环必须和宿主同量级
# ---------------------------------------------------------------------------


def test_zcode_default_max_tokens_matches_host_budget() -> None:
    """8192 写不下 20KB+ 的单文件（实测要 ~10.2k tokens），必须 ≥ 宿主的一半以上。

    注：宿主 ``AgentRuntimeOptions.max_tokens`` 默认 16384，实测一份 36KB 的
    HTML5 文件需要 10206 个输出 token。
    """
    assert ZCODE_DEFAULT_MAX_TOKENS >= 16_384
    assert ZcodeRunOptions(working_directory=".").max_tokens == ZCODE_DEFAULT_MAX_TOKENS


def test_zcode_mode_takes_turn_budget_from_host() -> None:
    """轮次预算沿用宿主注入的值 —— 这是与上游对齐的做法。

    上游 ZCode 的 ``maxTurns`` 本来就来自 ``runtimeConfig.maxTurns``（宿主注入，
    见 ``script-workflow-child-runtime.ts``），内核没有硬编码默认。实测一个改
    飞机的任务真会用到 20 轮，所以**不能**在这里写死成内核默认值。
    """
    import openminis.agent.zcode.integration.agent as zagent
    from openminis.agent.agent_runtime import AgentRuntime, AgentRuntimeOptions
    from openminis.data.model import LLMMessage

    captured: dict[str, object] = {}
    real_run = zagent.run_zcode_agent

    async def _fake(provider, task, session_id=None, options=None):  # noqa: ANN001
        captured["max_turns"] = options.max_turns
        captured["max_tokens"] = options.max_tokens
        return zagent.ZcodeRunResult(text="(stub)", stop_reason="end_turn")

    zagent.run_zcode_agent = _fake  # type: ignore[assignment]
    try:
        asyncio.run(
            AgentRuntime()._run_zcode_mode(
                object(),  # type: ignore[arg-type]
                [LLMMessage(LLMMessage.Role.USER, "写个游戏")],
                "test-turn-budget-session",
                AgentRuntimeOptions(max_turns=20, loop_mode="zcode"),
            )
        )
    finally:
        zagent.run_zcode_agent = real_run  # type: ignore[assignment]

    assert captured["max_turns"] == 20
    # 输出预算必须原样下传（这才是原先断掉的那根线）
    assert captured["max_tokens"] == AgentRuntimeOptions().max_tokens
    assert captured["max_tokens"] == ZCODE_DEFAULT_MAX_TOKENS
