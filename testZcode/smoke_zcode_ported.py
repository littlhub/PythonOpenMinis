"""Behavioral self-check for the ported ZCode core (coding-only port).

与两个**结构**体检脚本配套：

* ``verify_zcode_imports.py`` —— 每个模块能否 import（语法 / 导入错误）。
* ``scan_static_attrs.py``   —— 静态扫描「导入健康但运行时必崩」的属性 / kwarg 缺陷。
* ``smoke_zcode_ported.py``  —— **本脚本**：对已落地模块跑功能断言，确认行为符合上游语义。

用法：
    python smoke_zcode_ported.py        # 全部通过则打印每项 ✓ 并 exit 0
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

from _root import ensure_src_on_path  # noqa: E402

ensure_src_on_path()

from openminis.agent.zcode.contracts.execution import (  # noqa: E402
    ExecutionResult,
    ExecutionStreamResult,
)
from openminis.agent.zcode.embedded_search.capability import (  # noqa: E402
    ENABLE_EMBEDDED_SEARCH_BRANCH,
    EmbeddedSearchBranchCapabilityContext,
    resolve_embedded_search_branch_capability,
)
from openminis.agent.zcode.embedded_search.shell import (  # noqa: E402
    should_inject_embedded_search_bash_prelude,
    supports_embedded_search_shell_selection,
)
from openminis.agent.zcode.tool.bash_timeout_policy import (  # noqa: E402
    DEFAULT_BASH_TIMEOUT_POLICY,
    BashTimeoutPolicy,
    resolve_bash_timeout_ms,
)
from openminis.agent.zcode.tool.handlers import (  # noqa: E402
    EXCLUDED_COORDINATION_TOOL_NAMES,
    EXCLUDED_OFFICE_TOOL_NAMES,
    RegisterBuiltInToolsOptions,
    built_in_tools,
    register_built_in_tools,
    resolve_built_in_tool_entry_for_branch,
)
from openminis.agent.zcode.tool.handlers.bash import (  # noqa: E402
    command_category,
    create_execution_request,
    resolve_bash_permission_capability,
)
from openminis.agent.zcode.tool.handlers.bash_read_file_state import (  # noqa: E402
    BashReadFileStateEffectsInput,
    create_stale_read_file_state_hint,
    format_stale_read_file_state_hint,
    should_skip_bash_read_file_state_effects,
)
from openminis.agent.zcode.tool.handlers.bash_semantics import (  # noqa: E402
    is_bash_provider_error_status,
    BashProviderErrorStatusInput,
)
from openminis.agent.zcode.tool.handlers.ports import BashInput  # noqa: E402
from openminis.agent.zcode.tool.read_file_state import (  # noqa: E402
    create_read_file_state_key,
    find_latest_read_file_state,
)
from openminis.agent.zcode.tool.registry import ToolRegistryImpl  # noqa: E402
from openminis.agent.zcode.tool.tool_visibility import (  # noqa: E402
    create_tool_rule_name_set,
    normalize_tool_name_alias,
)
from openminis.agent.zcode.tool.types import (  # noqa: E402
    ReadFileStateEntry,
    ToolEntry,
    ToolMetadata,
)

_PASSED: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    if not condition:
        raise AssertionError(f"{label} FAILED {detail!r}")
    _PASSED.append(label)


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------
WS = "C:/ws"


class _Abort:
    aborted = False


class _Context:
    """Minimal stand-in for ``ToolExecutionContext`` (only the fields bash reads)."""

    def __init__(self, **overrides: object) -> None:
        self.working_directory = WS
        self.workspace_root = WS
        self.trace_id = "trace"
        self.span_id = "span"
        self.parent_span_id = None
        self.session_id = "session"
        self.turn_id = "turn"
        self.tool_call_id = "call"
        self.bash_shell_selection = {"dialect": "posix", "display": {"name": "bash"}}
        self.embedded_search = None
        self.abort_signal = _Abort()
        self.runtime_scope = "main"
        self.off_peak_turn = None
        self.execution_port = None
        self.telemetry = None
        self.file_system_port = None
        self.read_file_state = None
        self.emit_event = None
        for key, value in overrides.items():
            setattr(self, key, value)


def _stream(text: str, size: int) -> ExecutionStreamResult:
    return ExecutionStreamResult(text=text, bytes=size, truncated=False)


def _result(**kw: object) -> ExecutionResult:
    now = datetime.now(timezone.utc)
    return ExecutionResult(
        status=kw.get("status", "completed"),
        stdout=_stream(str(kw.get("stdout", "out\n")), 4),
        stderr=_stream(str(kw.get("stderr", "")), 0),
        duration_ms=int(kw.get("duration_ms", 7)),
        timed_out=bool(kw.get("timed_out", False)),
        cancelled=bool(kw.get("cancelled", False)),
        started_at=now,
        completed_at=now,
        exit_code=kw.get("exit_code", 0),
        resolved_cwd=kw.get("resolved_cwd", WS),
    )


def _bash_output(**kw: object) -> dict[str, object]:
    out: dict[str, object] = {
        "stdout": "",
        "stderr": "",
        "interrupted": False,
        "isImage": False,
        "status": "completed",
    }
    out.update(kw)
    return out


# ---------------------------------------------------------------------------
# 1. embedded-search 分支能力
# ---------------------------------------------------------------------------
def check_embedded_search() -> None:
    check("embedded search branch default on", ENABLE_EMBEDDED_SEARCH_BRANCH is True)

    supported = resolve_embedded_search_branch_capability(
        EmbeddedSearchBranchCapabilityContext(bash_available=True)
    )
    check("bash available -> supported", supported.reason == "supported")
    check("...and branch used", supported.use_embedded_search_branch is True)

    no_bash = resolve_embedded_search_branch_capability(
        EmbeddedSearchBranchCapabilityContext(bash_available=False)
    )
    check("no bash -> bash_unavailable", no_bash.reason == "bash_unavailable")

    # 显式 False 必须被尊重（``??`` 不是 ``||``）
    disabled = resolve_embedded_search_branch_capability(
        EmbeddedSearchBranchCapabilityContext(
            bash_available=True, embedded_search_branch_enabled=False
        )
    )
    check("explicit false disables", disabled.reason == "disabled_by_global_flag")

    # 容忍宿主注入的 dict 形态
    dict_form = resolve_embedded_search_branch_capability({"bash_available": True})  # type: ignore[arg-type]
    check("dict input tolerated", dict_form.use_embedded_search_branch is True)

    check("prelude default on", should_inject_embedded_search_bash_prelude() is True)
    check("posix supports prelude", supports_embedded_search_shell_selection({"dialect": "posix"}))
    check("git-bash supports prelude", supports_embedded_search_shell_selection({"dialect": "git-bash"}))
    check("cmd rejects prelude", not supports_embedded_search_shell_selection({"dialect": "cmd"}))
    check("missing selection rejects prelude", not supports_embedded_search_shell_selection(None))


# ---------------------------------------------------------------------------
# 2. timeout 解析
# ---------------------------------------------------------------------------
def check_timeout_policy() -> None:
    policy = BashTimeoutPolicy(default_timeout_ms=120_000, max_timeout_ms=600_000)
    check("default used when omitted", resolve_bash_timeout_ms(None, policy) == 120_000)
    # ``timeout: 0`` 是「未提供」而不是「1ms」（上游旧 watchdog 的 bug 就出在这里）
    check("zero falls back to default", resolve_bash_timeout_ms(0, policy) == 120_000)
    check("value honored", resolve_bash_timeout_ms(45_000, policy) == 45_000)
    check("clamped to max", resolve_bash_timeout_ms(10**9, policy) == 600_000)


# ---------------------------------------------------------------------------
# 3. bash handler：请求构造 / 拒绝分支 / 遥测
# ---------------------------------------------------------------------------
def check_bash_handler() -> None:
    from openminis.agent.zcode.tool.handlers.bash import bash_handler

    entry = next(e for e in built_in_tools if e.metadata.name == "Bash")
    check("bash metadata is dataclass", isinstance(entry.metadata, ToolMetadata))
    check("bash needs approval", entry.metadata.needs_approval is True)
    check("bash permission kind", entry.permission.permission == "bash")
    check("bash trace required", entry.trace.required is True)
    check("bash timeout has cleanup grace", entry.timeout.cleanup_grace_ms == 6_000)
    check("bash budget preview tails", entry.result_budget.preview.direction == "tail")

    # 默认策略下 resolve_timeout_budget_ms 与 handler 共用解析（0 → default）
    check("budget resolver uses default for 0",
          entry.resolve_timeout_budget_ms({"timeout": 0}) == 120_000)
    check("budget resolver parses strings",
          entry.resolve_timeout_budget_ms({"timeout": "30000"}) == 30_000)

    ctx = _Context()
    request = create_execution_request(BashInput(command="ls"), ctx, DEFAULT_BASH_TIMEOUT_POLICY)
    check("request is shell mode", request.command.mode == "shell")
    check("request uses posix-bash profile", request.command.shell_profile == "posix-bash")
    check("request captures cwd", request.capture_cwd_after_success is True)
    check("request persists on truncate", request.output_limit.persist_output == "on_truncate")
    check("sandbox on by default", request.sandbox.enabled is True)
    check("request carries toolCallId attr",
          request.trace.attributes["toolCallId"] == "call")

    bg = create_execution_request(
        BashInput(command="sleep 5", run_in_background=True), ctx, DEFAULT_BASH_TIMEOUT_POLICY
    )
    check("background skips cwd capture", bg.capture_cwd_after_success is None)
    check("background persists always", bg.output_limit.persist_output == "always")

    unsafe = create_execution_request(
        BashInput(command="ls", dangerously_disable_sandbox=True), ctx, DEFAULT_BASH_TIMEOUT_POLICY
    )
    check("sandbox opt-out honored", unsafe.sandbox.enabled is False)

    # embedded prelude 只在四个条件同时成立时下发
    class _Embedded:
        enabled = True
        backend = {"command": "rg"}
        find_and_grep_enabled = False

    prelude_ctx = _Context(embedded_search=_Embedded())
    with_prelude = create_execution_request(
        BashInput(command="ls"), prelude_ctx, DEFAULT_BASH_TIMEOUT_POLICY
    )
    check("prelude injected", with_prelude.bash_prelude is not None)
    check("prelude carries find/grep opt-out",
          with_prelude.bash_prelude.find_and_grep_enabled is False)
    check("prelude omitted without backend", create_execution_request(
        BashInput(command="ls"), _Context(), DEFAULT_BASH_TIMEOUT_POLICY
    ).bash_prelude is None)

    # command category 映射
    check("git category", command_category("git status") == "git")
    check("package category", command_category("npm install") == "package_manager")
    check("network category", command_category("curl https://example.com") == "network")

    # 权限能力：只读命令才降级（依赖 unbash 解析端口，未配置时恒不降级）
    check("destructive command not downgraded",
          resolve_bash_permission_capability({"command": "rm -rf /"}) is None)
    check("missing command not downgraded", resolve_bash_permission_capability({}) is None)

    class _Port:
        def __init__(self) -> None:
            self.calls: list[object] = []

        async def run(self, request: object, options: object = None) -> ExecutionResult:
            self.calls.append(request)
            return _result()

    class _LifecyclePort:
        def __init__(self) -> None:
            self.calls: list[object] = []

        async def run_bash_with_background_lifecycle(
            self, request: object, lifecycle: object, options: object = None
        ) -> object:
            self.calls.append((request, lifecycle))
            return {
                "kind": "backgrounded",
                "task": _background_task(),
            }

    async def run() -> None:
        port = _Port()
        out = await bash_handler({"command": "echo hi"}, _Context(execution_port=port))
        check("handler returns mapping", isinstance(out, dict))
        check("handler stdout passthrough", out["stdout"] == "out\n")
        check("handler status", out["status"] == "completed")
        check("handler attaches perf", isinstance(out.get("perf"), dict))
        check("port called once", len(port.calls) == 1)

        # 空白命令短路：不触达 execution port
        blank_port = _Port()
        blank = await bash_handler({"command": "   "}, _Context(execution_port=blank_port))
        check("blank command short-circuits", len(blank_port.calls) == 0)
        check("blank command marks no-output", blank["noOutputExpected"] is False)
        check("blank command is completed", blank["status"] == "completed")

        # 缺 ExecutionPort → 配置错误
        try:
            await bash_handler({"command": "ls"}, _Context())
        except Exception as exc:  # noqa: BLE001
            check("missing execution port raises", type(exc).__name__ == "CoreError")
        else:
            raise AssertionError("missing execution port should raise")

        # run_in_background 但端口不支持生命周期 → 配置错误
        try:
            await bash_handler(
                {"command": "sleep 1", "run_in_background": True},
                _Context(execution_port=_Port()),
            )
        except Exception as exc:  # noqa: BLE001
            check("background without lifecycle raises", type(exc).__name__ == "CoreError")
        else:
            raise AssertionError("background without lifecycle should raise")

        # 闲时 turn 拒绝显式后台
        try:
            await bash_handler(
                {"command": "sleep 1", "run_in_background": True},
                _Context(execution_port=_Port(), off_peak_turn=True),
            )
        except Exception as exc:  # noqa: BLE001
            check("off-peak rejects background", type(exc).__name__ == "CoreError")
        else:
            raise AssertionError("off-peak background should raise")

        # 支持生命周期的端口 → 返回 backgrounded 形态
        lifecycle = _LifecyclePort()
        bg_out = await bash_handler(
            {"command": "sleep 1", "run_in_background": True},
            _Context(execution_port=lifecycle),
        )
        check("backgrounded status", bg_out["status"] == "backgrounded")
        check("backgrounded carries task id", bg_out["backgroundTaskId"] == "task-1")
        check("backgrounded lifecycle mode explicit",
              lifecycle.calls[0][1] == {"mode": "explicit"})

        # 字符串布尔 / 字符串数字的语义化归一（zod preprocess 等价物）
        coerced = await bash_handler(
            {"command": "echo hi", "run_in_background": "false", "timeout": "1000"},
            _Context(execution_port=_Port()),
        )
        check("string 'false' coerces to False", coerced["status"] == "completed")

    asyncio.run(run())


def _background_task() -> object:
    from openminis.agent.zcode.contracts.execution import BackgroundExecutionStartResult

    return BackgroundExecutionStartResult(
        task_id="task-1",
        started_at=datetime.now(timezone.utc),
        output_path="/tmp/out.log",
    )


# ---------------------------------------------------------------------------
# 4. bash read-file-state 副作用
# ---------------------------------------------------------------------------
def check_bash_read_file_state() -> None:
    check("hint singular",
          format_stale_read_file_state_hint([os.path.join(WS, "a.ts")], WS).startswith(
              "[This command modified 1 file you've previously read:"))
    many = [os.path.join(WS, f"f{i}.ts") for i in range(7)]
    hint = format_stale_read_file_state_hint(many, WS)
    check("hint caps display paths", hint.count(",") == 4)
    check("hint reports hidden remainder", "and 2 more" in hint)
    check("hint plural", "modified 7 files" in hint)

    # 自身路径 → JS ``relative(a, a) === ""`` 为假值，回落原路径
    self_path = format_stale_read_file_state_hint([WS], WS)
    check("same-path falls back to raw path", WS in self_path)

    # provider 错误 / 后台 / 图片 / 中断 → 全部跳过
    check("plain output not skipped",
          should_skip_bash_read_file_state_effects(_bash_output()) is False)
    check("backgrounded skipped",
          should_skip_bash_read_file_state_effects(
              _bash_output(backgroundTaskId="t")) is True)
    check("image skipped",
          should_skip_bash_read_file_state_effects(_bash_output(isImage=True)) is True)
    check("interrupted skipped",
          should_skip_bash_read_file_state_effects(_bash_output(interrupted=True)) is True)
    check("provider error skipped",
          should_skip_bash_read_file_state_effects(
              _bash_output(
                  status="failed",
                  exitCode=2,
                  returnCodeInterpretation="Command exited with code 2",
              )) is True)

    # ``status: "failed"`` 本身不足以判定 provider error：语义化非错误退出码（grep 未匹配、
    # test 判假…）带的是 ``returnCodeInterpretation``，那才是「命令事实」而非执行框架失败。
    check("failed + semantic interpretation is not provider error",
          is_bash_provider_error_status(
              BashProviderErrorStatusInput(
                  status="failed",
                  exit_code=1,
                  return_code_interpretation="No matches found",
              )) is False)
    check("failed + no interpretation IS provider error",
          is_bash_provider_error_status(
              BashProviderErrorStatusInput(
                  status="failed", exit_code=1, return_code_interpretation=None)) is True)
    check("completed is never a provider error",
          is_bash_provider_error_status(
              BashProviderErrorStatusInput(
                  status="completed", exit_code=0, return_code_interpretation=None)) is False)
    check("failed with exit 0 is not a provider error",
          is_bash_provider_error_status(
              BashProviderErrorStatusInput(
                  status="failed", exit_code=0, return_code_interpretation=None)) is False)

    class _Port:
        def __init__(self, current_mtime_ms: int) -> None:
            self.current_mtime_ms = current_mtime_ms
            self.stat_calls: list[str] = []

        async def stat(self, request: dict, options: object = None) -> dict:
            self.stat_calls.append(request["path"])
            path = request["path"]
            return {
                "path": path,
                "kind": "file",
                "sizeBytes": 10,
                "mtimeMs": self.current_mtime_ms,
            }

    old_read_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    old_mtime_ms = int(old_read_at.timestamp() * 1000)
    read_state = {
        create_read_file_state_key(os.path.join(WS, "a.ts"), None, None): ReadFileStateEntry(
            path=os.path.join(WS, "a.ts"),
            content="old",
            is_partial_view=False,
            read_at=old_read_at,
            mtime_ms=old_mtime_ms,
        )
    }

    async def run() -> None:
        # 命令不含 write marker → 不产生提示，也不 stat
        port = _Port(old_mtime_ms)
        ctx = _Context(file_system_port=port, read_file_state=read_state)
        quiet = await create_stale_read_file_state_hint(
            BashReadFileStateEffectsInput(
                command="ls -la", context=ctx, output=_bash_output(), result=_result()
            )
        )
        check("no write marker -> no hint", quiet is None)
        check("no write marker -> no stat", port.stat_calls == [])

        # 含 write marker 且文件 mtime 已前进 → 产生提示
        future_ms = int((datetime.now(timezone.utc) + timedelta(seconds=30)).timestamp() * 1000)
        port = _Port(future_ms)
        ctx = _Context(file_system_port=port, read_file_state=read_state)
        loud = await create_stale_read_file_state_hint(
            BashReadFileStateEffectsInput(
                command="prettier --write src",
                context=ctx,
                output=_bash_output(),
                result=_result(),
            )
        )
        check("write marker -> hint", loud is not None and "Call Read before editing" in loud)
        check("write marker -> stat once", len(port.stat_calls) == 1)

        # 含 write marker 但 mtime 未变 → 不产生提示
        port = _Port(old_mtime_ms)
        ctx = _Context(file_system_port=port, read_file_state=read_state)
        unchanged = await create_stale_read_file_state_hint(
            BashReadFileStateEffectsInput(
                command="prettier --write src",
                context=ctx,
                output=_bash_output(),
                result=_result(),
            )
        )
        check("unchanged mtime -> no hint", unchanged is None)

        # stdout 截断时禁止回填 read-state
        port2 = _Port(future_ms)
        ctx2 = _Context(file_system_port=port2, read_file_state=read_state)
        from openminis.agent.zcode.tool.handlers.bash_read_file_state import (
            backfill_read_file_state_from_bash,
        )

        before = len(read_state)
        await backfill_read_file_state_from_bash(
            BashReadFileStateEffectsInput(
                command="cat a.ts",
                context=ctx2,
                output=_bash_output(stdoutTruncated=True),
                result=_result(),
            )
        )
        check("truncated stdout blocks backfill", len(read_state) == before)

        # find_latest_read_file_state 能按路径命中既有条目
        found = find_latest_read_file_state(read_state, os.path.join(WS, "a.ts"))
        check("read state lookup by path", found is not None and found.content == "old")

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 5. 工具装配
# ---------------------------------------------------------------------------
def check_tool_registration() -> None:
    names = [entry.metadata.name for entry in built_in_tools]
    check("18 coding tools declared", len(names) == 18, names)
    check("declared metadata normalized",
          all(isinstance(entry.metadata, ToolMetadata) for entry in built_in_tools))
    check("no office tool leaks into surface",
          not (set(names) & EXCLUDED_OFFICE_TOOL_NAMES))
    check("no coordination tool leaks into surface",
          not (set(names) & EXCLUDED_COORDINATION_TOOL_NAMES))
    check("agent/task declared", "Agent" in names and "Task" in names)

    default = ToolRegistryImpl()
    register_built_in_tools(default)
    registered = set(default._tools.keys())
    check("default registration drops Agent/Task",
          "Agent" not in registered and "Task" not in registered)
    check("default registration keeps Skill", "Skill" in registered)

    with_agent = ToolRegistryImpl()
    register_built_in_tools(
        with_agent, RegisterBuiltInToolsOptions(include_agent=True)
    )
    check("include_agent registers Agent/Task",
          {"Agent", "Task"} <= set(with_agent._tools.keys()))

    embedded = ToolRegistryImpl()
    register_built_in_tools(
        embedded, RegisterBuiltInToolsOptions(embedded_search_enabled=True)
    )
    check("embedded branch hides Glob/Grep",
          "Glob" not in embedded._tools and "Grep" not in embedded._tools)
    check("embedded branch keeps Bash", "Bash" in embedded._tools)

    filtered = ToolRegistryImpl()
    register_built_in_tools(
        filtered,
        RegisterBuiltInToolsOptions(
            allowed_tools=["Read", "Write", "Skill", "Bash"],
            disallowed_tools=["Read"],
            include_skill=False,
            silent_duplicate_warnings=True,
        ),
    )
    check("allowlist + denylist + include_skill compose",
          set(filtered._tools.keys()) == {"Bash", "Write"})

    # 分支重建：Bash 的超时策略与 schema 描述同步更新，且不改动模块级基线
    base_bash = next(e for e in built_in_tools if e.metadata.name == "Bash")
    custom = resolve_built_in_tool_entry_for_branch(
        base_bash,
        RegisterBuiltInToolsOptions(
            bash_timeout_policy=BashTimeoutPolicy(default_timeout_ms=5_000, max_timeout_ms=9_000)
        ),
    )
    check("branch rebuild updates timeout",
          (custom.timeout.default_ms, custom.timeout.max_ms) == (5_000, 9_000))
    check("branch rebuild updates schema description",
          "max 9000" in custom.input_schema["properties"]["timeout"]["description"])
    check("branch rebuild is a copy", base_bash.timeout.default_ms == 120_000)

    # EnterPlanMode 描述随 embedded-search 分支切换
    enter = next(e for e in built_in_tools if e.metadata.name == "EnterPlanMode")
    embed_desc = resolve_built_in_tool_entry_for_branch(
        enter, RegisterBuiltInToolsOptions(embedded_search_enabled=True)
    ).metadata.description
    plain_desc = resolve_built_in_tool_entry_for_branch(
        enter, RegisterBuiltInToolsOptions(embedded_search_enabled=False)
    ).metadata.description
    check("plan-mode description honors branch", "`find`/Glob" in embed_desc)
    check("plan-mode plain description drops find", "`find`/Glob" not in plain_desc)
    check("plan-mode description returns ToolMetadata",
          isinstance(enter, ToolEntry))

    # Agent 描述不再指向被裁掉的动态工作流工具
    agent = next(e for e in built_in_tools if e.metadata.name == "Agent")
    agent_desc = resolve_built_in_tool_entry_for_branch(
        agent, RegisterBuiltInToolsOptions(include_agent=True)
    ).metadata.description
    check("agent description has no CreateWorkflow pointer",
          isinstance(agent_desc, str) and "CreateWorkflow" not in agent_desc)
    check("task metadata is ToolMetadata", isinstance(
        next(e for e in built_in_tools if e.metadata.name == "Task").metadata, ToolMetadata))


# ---------------------------------------------------------------------------
# 6. tool-visibility
# ---------------------------------------------------------------------------
def check_tool_visibility() -> None:
    check("alias normalized", normalize_tool_name_alias("web_search") == "WebSearch")
    check("alias idempotent", normalize_tool_name_alias("WebSearch") == "WebSearch")
    check("empty rules -> None", create_tool_rule_name_set([]) is None)
    check("missing rules -> None", create_tool_rule_name_set(None) is None)
    # ``Bash(git status:*)`` 取括号前的前缀（上游 ``parenIndex > 0``）
    check("rule with paren strips args",
          create_tool_rule_name_set(["Bash(git status:*)", "Read"]) == {"Bash", "Read"})
    check("rule starting with paren keeps full text",
          create_tool_rule_name_set(["(weird)"]) == {"(weird)"})

    from openminis.agent.zcode.tool.tool_visibility import (
        filter_disallowed_tool_names,
        is_tool_name_disallowed,
    )

    check("alias denylist matches canonical name",
          is_tool_name_disallowed("WebSearch", ["web_search"]) is True)
    check("filter drops denied names",
          filter_disallowed_tool_names(["Read", "Bash", "WebSearch"], ["Bash"])
          == ["Read", "WebSearch"])
    check("filter is a no-op without rules",
          filter_disallowed_tool_names(["Read"], None) == ["Read"])


def main() -> int:
    sections = [
        ("embedded-search", check_embedded_search),
        ("bash timeout policy", check_timeout_policy),
        ("bash handler", check_bash_handler),
        ("bash read-file state", check_bash_read_file_state),
        ("tool registration", check_tool_registration),
        ("tool visibility", check_tool_visibility),
    ]
    print("ZCode coding-kernel behavioral self-check")
    print(f"{'=' * 60}")
    for title, fn in sections:
        before = len(_PASSED)
        fn()
        added = len(_PASSED) - before
        print(f"  [ok] {title:<26} {added:>3} assertions")
    print(f"{'=' * 60}")
    print(f"PASSED = {len(_PASSED)}   FAILED = 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
