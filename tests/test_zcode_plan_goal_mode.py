"""回归测试：plan 模式闸门 + goal 状态机（对齐上游 ZCode）。

锁住的语义（全部来自上游源码，不是凭直觉设计的）：

1. ``resolveExecutionState``：``mode`` 与 ``planEnabled`` 是**两个独立字段** ——
   进 plan 模式只置 ``planEnabled``，**不改 mode**（``session-mode-port.ts`` 只传
   ``{ planEnabled: true }``）。把 mode 写成 ``"plan"`` 会让 ``ExecutionState``
   拒绝的取值混进模式集。
2. ``exitPlanMode``：未处于 plan 模式时抛错（不静默）。
3. Plan 与 Goal **不能同时 active**（``execution-state.ts`` 硬校验）。
4. plan 模式下写类工具被拦、读类放行 —— 且拦下时给的是**模型能自我纠正**的文本
   （改用 Read/Glob/Grep 或 ExitPlanMode），不是一句干巴巴的 denied。
5. goal 校验器坏 JSON 时 **fail-open**（``passed=True``）—— 裁判链路故障不能把
   已交付的目标卡进无限续跑。
"""

from __future__ import annotations

import asyncio

import pytest

from openminis.agent.zcode.contracts.session_mode import (
    PLAN_MODE_MAX_PLAN_CHARS,
)
from openminis.agent.zcode.integration.goal import (
    GOAL_COMPLETION_VERIFICATION_FALLBACK_REASON,
    GoalState,
    escape_goal_prompt_text,
    format_goal_continuation_prompt,
    format_goal_state_for_model,
    get_goal_store,
    normalize_goal_objective,
    parse_goal_completion_verification_text,
    reset_goal_store,
)
from openminis.agent.zcode.integration.permission_gate import (
    PLAN_MODE_DENIED_TEMPLATE,
    check_tool_gate,
)
from openminis.agent.zcode.integration.session_mode import (
    EXECUTION_PERMISSION_MODES,
    HostSessionModePort,
    SessionModeState,
    get_session_mode_state,
    resolve_execution_state,
    reset_session_mode_state,
    set_goal_activity_probe,
)
from openminis.agent.zcode.integration.toolchain import (
    build_zcode_runtime,
    zcode_tool_spec,
)
from openminis.agent.zcode.tool.handlers.goal_read import (
    format_goal_read_model_content,
    goal_read_handler,
)

SESSION = "test-plan-goal-host"
KERNEL_SESSION = f"{SESSION}:zcode"


def _spec(tool_id: str):
    spec = zcode_tool_spec(tool_id)
    assert spec is not None, f"工具未注册: {tool_id}"
    return spec


# ---------------------------------------------------------------------------
# 1) resolve_execution_state —— mode 与 planEnabled 独立
# ---------------------------------------------------------------------------


def test_enter_plan_does_not_change_mode() -> None:
    """上游只传 ``{planEnabled: true}``，mode 保持原值。"""
    nxt = resolve_execution_state({"plan_enabled": True}, SessionModeState("build", False))
    assert nxt.mode == "build"
    assert nxt.plan_enabled is True


def test_invalid_mode_keeps_current_value() -> None:
    """无效 mode 输入保持原值，不报错也不重置。"""
    nxt = resolve_execution_state({"mode": "plan"}, SessionModeState("edit", False))
    assert nxt.mode == "edit"
    assert nxt.plan_enabled is True  # "plan" 是旧格式兼容值 → planEnabled=True


def test_valid_mode_resets_plan_enabled() -> None:
    """显式给了合法 mode 而没给 planEnabled → planEnabled 归 False。"""
    nxt = resolve_execution_state({"mode": "build"}, SessionModeState("build", True))
    assert nxt.plan_enabled is False


def test_mode_set_matches_upstream() -> None:
    """合法模式集：build/edit/yolo/auto（plan 不在此列，auto 是内部权限）。"""
    assert EXECUTION_PERMISSION_MODES == ("build", "edit", "yolo", "auto")


# ---------------------------------------------------------------------------
# 2) HostSessionModePort —— 进/出 plan 模式
# ---------------------------------------------------------------------------


def _port() -> HostSessionModePort:
    return HostSessionModePort(SESSION)


def test_port_enter_and_exit_plan_mode() -> None:
    reset_session_mode_state(SESSION)
    port = _port()
    result = asyncio.run(port.enter_plan_mode())
    assert result.mode == "build"
    assert result.previous_mode == "build"
    assert result.plan_enabled is True
    assert result.previous_plan_enabled is False

    st = get_session_mode_state(SESSION)
    assert st.mode == "build" and st.plan_enabled is True

    exited = asyncio.run(port.exit_plan_mode())
    assert exited.mode == "build"
    assert exited.plan_enabled is False
    # 上游 exitPlanMode 硬编码 previousPlanEnabled=True
    assert exited.previous_plan_enabled is True


def test_port_exit_without_plan_raises() -> None:
    reset_session_mode_state(SESSION)
    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(_port().exit_plan_mode())
    message = str(excinfo.value)
    assert "not in plan mode" in message


def test_plan_and_goal_mutual_exclusion() -> None:
    """有 active goal 时进 plan 模式必须被拒（上游 execution-state.ts 硬校验）。"""
    reset_session_mode_state(SESSION)
    reset_goal_store()
    get_goal_store().set_goal(SESSION, "把项目跑通")
    set_goal_activity_probe(lambda sid: get_goal_store().has_active(sid))
    try:
        with pytest.raises(RuntimeError, match="Plan and Goal cannot be active"):
            asyncio.run(_port().enter_plan_mode())
        # 失败后状态不得被污染
        assert get_session_mode_state(SESSION).plan_enabled is False
        # goal 非 active（paused）时不拦
        get_goal_store().update_status(SESSION, "paused")
        result = asyncio.run(_port().enter_plan_mode())
        assert result.plan_enabled is True
    finally:
        set_goal_activity_probe(None)
        reset_goal_store()


# ---------------------------------------------------------------------------
# 3) plan 模式闸门 —— 写类拦、读类放行
# ---------------------------------------------------------------------------


def _gate(spec, *, plan_enabled: bool, mode: str = "build", tool_input=None):
    from openminis.agent.zcode.integration.permission_gate import (
        build_permission_capability,
    )

    return check_tool_gate(
        tool_name=spec.name,
        tool_input=tool_input if tool_input is not None else {},
        capability=build_permission_capability(spec),
        mode=mode,
        plan_enabled=plan_enabled,
    )


def test_gate_allows_everything_when_plan_disabled() -> None:
    reset_session_mode_state(SESSION)
    for tool_id in ("zcode_write", "zcode_bash", "zcode_read"):
        assert _gate(_spec(tool_id), plan_enabled=False) is None


def test_gate_blocks_write_and_bash_in_plan_mode() -> None:
    reset_session_mode_state(SESSION)
    for tool_id in ("zcode_write", "zcode_bash"):
        denied = _gate(_spec(tool_id), plan_enabled=True)
        assert denied is not None, f"{tool_id} 必须被 plan 模式拦下"
        assert denied == PLAN_MODE_DENIED_TEMPLATE.format(
            tool=_spec(tool_id).name,
            reason="Plan mode only allows read-only, non-destructive tools",
        )
        # 拦下文本必须指路，否则模型会把失败当瞬时故障原地重试
        assert "ExitPlanMode" in denied
        assert "Read / Glob / Grep" in denied


def test_gate_allows_read_only_tools_in_plan_mode() -> None:
    reset_session_mode_state(SESSION)
    for tool_id in ("zcode_read", "zcode_glob", "zcode_grep"):
        spec = _spec(tool_id)
        assert _gate(spec, plan_enabled=True) is None, f"{spec.name} 应放行"


def test_gate_allows_exit_plan_mode_despite_ask() -> None:
    """宿主没有交互通道，ExitPlanMode 判定为 ask 时必须放行，否则计划永远批不了。"""
    reset_session_mode_state(SESSION)
    spec = _spec("zcode_exit_plan_mode")
    denied = _gate(
        spec,
        plan_enabled=True,
        tool_input={"plan": "1. 写代码\n2. 跑测试"},
    )
    assert denied is None, f"ExitPlanMode 被挂起: {denied}"


# ---------------------------------------------------------------------------
# 4) goal 状态机与解析
# ---------------------------------------------------------------------------


def test_normalize_goal_objective() -> None:
    assert normalize_goal_objective("  目标  ") == "目标"
    with pytest.raises(ValueError):
        normalize_goal_objective("   ")
    with pytest.raises(ValueError, match="at most"):
        normalize_goal_objective("x" * (PLAN_MODE_MAX_PLAN_CHARS if False else 4001))


def test_escape_goal_prompt_text_only_escapes_markup() -> None:
    assert escape_goal_prompt_text("a<b>&c") == "a&lt;b&gt;&amp;c"
    # 引号不转义 —— 只防伪造闭合标签，不做全文 HTML 转义
    assert escape_goal_prompt_text('say "hi"') == 'say "hi"'


def test_format_goal_state_for_model_wraps_objective() -> None:
    goal = GoalState(session_id="s", objective="跑通项目")
    text = format_goal_state_for_model(goal)
    assert "Status: active" in text
    assert "<untrusted_objective>" in text and "</untrusted_objective>" in text
    assert format_goal_state_for_model(None) == ""


def test_parse_verification_valid_and_fenced() -> None:
    ok = parse_goal_completion_verification_text(
        '{"passed": true, "reason": "done", "nextAction": ""}'
    )
    assert ok.passed is True and ok.reason == "done"

    fenced = parse_goal_completion_verification_text(
        '```json\n{"passed": false, "reason": "缺测试", "nextAction": "补测试"}\n```'
    )
    assert fenced.passed is False
    assert fenced.reason == "缺测试"
    assert fenced.next_action == "补测试"


def test_parse_verification_bad_json_fails_open() -> None:
    """坏 JSON → fail-open（passed=True），裁判故障不卡死目标。"""
    out = parse_goal_completion_verification_text("模型输出了半截 {broken")
    assert out.passed is True
    assert "did not return valid JSON" in out.reason
    assert out.next_action is None


def test_parse_verification_passed_requires_explicit_true() -> None:
    """passed 只有显式 true 才算过 —— 字符串 "true"/缺失都不算。"""
    for payload in ('{"passed": "true", "reason": "r"}', '{"reason": "r"}'):
        out = parse_goal_completion_verification_text(payload)
        assert out.passed is False
        assert out.reason == GOAL_COMPLETION_VERIFICATION_FALLBACK_REASON or out.reason


def test_goal_store_set_replaces_and_tracks_usage() -> None:
    reset_goal_store()
    store = get_goal_store()
    first = store.set_goal(SESSION, "目标 A")
    again = store.set_goal(SESSION, "目标 B", token_budget=10)
    assert again.target_id == first.target_id  # 替换不换 id
    assert store.read(SESSION).objective == "目标 B"

    store.record_usage(SESSION, tokens=9)
    assert store.read(SESSION).status == "active"
    store.record_usage(SESSION, tokens=5)  # 10+5 >= budget → budget_limited
    assert store.read(SESSION).status == "budget_limited"
    # 非 active 状态不再累计触发
    store.record_usage(SESSION, tokens=100)
    assert store.read(SESSION).status == "budget_limited"

    with pytest.raises(ValueError):
        store.set_goal(SESSION, "x", token_budget=0)
    with pytest.raises(ValueError, match="unknown goal status"):
        store.update_status(SESSION, "nope")
    assert store.update_status("missing-session", "complete") is None


def test_goal_continuation_prompt_lists_completion_audit() -> None:
    goal = GoalState(session_id="s", objective="跑通项目", token_budget=100, tokens_used=40)
    prompt = format_goal_continuation_prompt(goal)
    assert "<untrusted_objective>" in prompt
    assert "Tokens remaining: 60" in prompt
    assert "Do not mark the goal complete yourself" in prompt
    # 带校验结果时要把 reason / next action 一起带回
    with_result = format_goal_continuation_prompt(
        goal, {"reason": "缺测试", "next_action": "补测试"}
    )
    assert "Completion verifier result:" in with_result
    assert "Next action: 补测试" in with_result


# ---------------------------------------------------------------------------
# 5) GoalRead handler —— 模型可见文本
# ---------------------------------------------------------------------------


def test_goal_read_handler_and_model_content() -> None:
    reset_goal_store()
    runtime = build_zcode_runtime(
        session_id=KERNEL_SESSION,
        host_session_id=SESSION,
        working_directory=".",
        need=("mode", "goal"),
    )
    ctx = runtime.make_context(tool_call_id="g0")

    out = asyncio.run(goal_read_handler({}, ctx))
    assert out == {"goal": None}
    assert format_goal_read_model_content(out) == "No goal is set for this session."

    goal = get_goal_store().set_goal(SESSION, "把 <项目> 跑通")
    out = asyncio.run(goal_read_handler({}, ctx))
    assert out["goal"]["objective"] == "把 <项目> 跑通"
    assert out["goal"]["sessionID"] == SESSION
    text = format_goal_read_model_content(out)
    # 模型可见文本里 objective 必须被转义，防止伪造闭合标签
    assert "把 &lt;项目&gt; 跑通" in text
    assert "Status: active" in text


def test_goal_read_via_runtime_port() -> None:
    reset_goal_store()
    runtime = build_zcode_runtime(
        session_id=KERNEL_SESSION,
        host_session_id=SESSION,
        working_directory=".",
        need=("goal",),
    )
    assert runtime.goal_port.read_goal() is None
    get_goal_store().set_goal(SESSION, "目标 X")
    assert runtime.goal_port.read_goal().objective == "目标 X"


# ---------------------------------------------------------------------------
# 6) 端到端：EnterPlanMode → 拦写 → ExitPlanMode → 放行
# ---------------------------------------------------------------------------


def _gate_for_runtime(runtime, tool_id: str, tool_input=None):
    from openminis.agent.zcode.integration.permission_gate import (
        build_permission_capability,
    )

    spec = _spec(tool_id)
    return check_tool_gate(
        tool_name=spec.name,
        tool_input=tool_input or {},
        capability=build_permission_capability(spec),
        mode=runtime.session_mode_port.get_mode(),
        plan_enabled=runtime.session_mode_port.is_plan_enabled(),
    )


def test_end_to_end_plan_gate_with_runtime_ports() -> None:
    reset_session_mode_state(SESSION)
    reset_goal_store()
    runtime = build_zcode_runtime(
        session_id=KERNEL_SESSION,
        host_session_id=SESSION,
        working_directory=".",
        need=("mode",),
    )
    assert runtime.session_mode_port.get_mode() == "build"
    assert runtime.session_mode_port.is_plan_enabled() is False

    asyncio.run(runtime.session_mode_port.enter_plan_mode())
    # 端口绑在**宿主**会话 id 上 —— 宿主与内核必须看到同一份状态
    assert get_session_mode_state(SESSION).plan_enabled is True

    assert _gate_for_runtime(runtime, "zcode_write") is not None
    assert _gate_for_runtime(runtime, "zcode_read") is None

    asyncio.run(runtime.session_mode_port.exit_plan_mode())
    assert _gate_for_runtime(runtime, "zcode_write") is None
