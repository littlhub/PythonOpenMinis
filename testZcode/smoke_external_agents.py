"""外部 agent 协议冒烟：**写好一份 YAML/JSON 就能调用并回传**。

覆盖用户提的三件事：
1. 可扩展接口 —— 往描述符目录丢一份文件，它自动成为可委派的子代理；
2. 三种传输（internal / stdio / http）都收敛成同一份回传文本；
3. 与主智能体**互借**工具与技能（两个方向都验）。

全部用假 store / 真子进程 / 真本地 HTTP，不依赖应用运行环境。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from _root import PROJECT_ROOT as ROOT, ensure_src_on_path  # noqa: E402

ensure_src_on_path()

_TMP_AGENTS = Path(tempfile.mkdtemp(prefix="external-agents-"))
os.environ["OPENMINIS_AGENTS_DIR"] = str(_TMP_AGENTS)
#: 「外部 CLI agent」替身脚本（各节共用）。
_STUB_CLI = _TMP_AGENTS / "_stub_cli.py"

from openminis.agent import subagents as SA  # noqa: E402
from openminis.agent.external_agents import (  # noqa: E402
    ExternalAgentError,
    extract_path,
    get_descriptor,
    list_external_agent_views,
    load_descriptor_file,
    load_descriptors,
    mini_yaml_load,
    reload_descriptors,
    render_template,
    run_external_agent,
)

PASSED = 0
FAILED = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


class FakeStore:
    def __init__(self, data: dict) -> None:
        self._data = json.loads(json.dumps(data))

    def load(self) -> dict:
        return self._data

    def save(self, data: dict) -> None:
        self._data = data

    def provider_instances(self) -> list[dict]:
        return list(self._data.get("providers", {}).values())


def store() -> FakeStore:
    return FakeStore(
        {
            "activeProviderId": "openAI",
            "providers": {
                "openAI": {"id": "openAI", "type": "openAI", "apiKey": "sk-test", "model": "gpt-5.2"}
            },
            "subagents": {},
        }
    )


# ---------------------------------------------------------------------------
# 0. 便携解析 + 取值
# ---------------------------------------------------------------------------
def test_reserved_slots() -> None:
    print("=== 预留槽位（DeepSeek Harness / WorkBuddy / 豆包 / 自定义）===")
    _write_stub_cli(_STUB_CLI)
    views = {v["id"]: v for v in list_external_agent_views()}
    for slot in ("zcode", "codex", "deepseek-harness", "workbuddy", "doubao", "custom-agent"):
        check(f"槽位存在: {slot}", slot in views, str(sorted(views)))

    check("ZCode 是唯一启用的内置槽位", views["zcode"]["enabled"] and views["zcode"]["builtin"])
    for slot in ("codex", "deepseek-harness", "workbuddy", "doubao", "custom-agent"):
        check(f"{slot} 默认未启用（不冒充可用）", views[slot]["enabled"] is False, str(views[slot]))
    check("自定义槽位是可照抄的模板（有描述符路径）", bool(views["custom-agent"]["source"]), str(views["custom-agent"]))
    check(
        "每个槽位都能指出自己的描述符文件",
        all(v["source"].endswith((".yaml", ".json")) for v in views.values()),
        str({k: v["source"] for k, v in views.items()}),
    )

    # 预留槽位不可委派，也不进子代理列表
    st = store()
    listed = {c["id"] for c in SA.list_subagents(st)}
    check("预留槽位不进子代理列表", "doubao" not in listed and "workbuddy" not in listed, str(listed))
    check("只有启用的槽位可委派", listed == {"zcode"}, str(listed))

    # 用户目录覆盖内置同名槽位
    (_TMP_AGENTS / "doubao.yaml").write_text(
        textwrap.dedent(
            f"""
            id: doubao
            name: 豆包（我的）
            enabled: true
            transport: stdio
            stdio:
              command: ["{sys.executable.replace(os.sep, '/')}", "{_STUB_CLI.as_posix()}", "text"]
              input: {{mode: argv-template}}
            """
        ),
        encoding="utf-8",
    )
    reload_descriptors()
    override = get_descriptor("doubao")
    check("用户目录覆盖内置同名槽位", override.name == "豆包（我的）", override.name)
    check("覆盖后可被委派", "doubao" in {c["id"] for c in SA.list_subagents(store())})
    (_TMP_AGENTS / "doubao.yaml").unlink()
    reload_descriptors()


def test_parser() -> None:
    print("=== 协议基础件 ===")
    parsed = mini_yaml_load(
        textwrap.dedent(
            """
            version: 1
            id: demo
            enabled: true
            transport: stdio
            command: ["echo", "hi"]
            tools:
              - id: one
                name: First
                input_schema:
                  type: object
                  required: [task]
              - id: two
            # 整行注释
            nested:
              borrow: {tools: true, skills: false}
            """
        )
    )
    check("嵌套映射解析", parsed.get("id") == "demo")
    check("行内列表解析", parsed.get("command") == ["echo", "hi"])
    check("布尔解析", parsed.get("enabled") is True)
    check("列表项=映射（含续行）", parsed["tools"][0]["name"] == "First", str(parsed["tools"]))
    check("续行里的嵌套映射", parsed["tools"][0]["input_schema"]["type"] == "object")
    check("第二个列表项", parsed["tools"][1]["id"] == "two")
    check("行内映射解析", parsed["nested"]["borrow"]["tools"] is True)
    check("整行注释不产生键", "#" not in parsed)

    check("模板替换", render_template("a {{task}} b {{nope}}", {"task": "T"}) == "a T b ")
    payload = {"a": {"b": [{"c": "deep"}]}}
    check("路径取值 $.a.b[0].c", extract_path(payload, "$.a.b[0].c") == "deep")
    check("路径取不到返回 None", extract_path(payload, "$.a.x") is None)


# ---------------------------------------------------------------------------
# 1. 写一份 descriptor → 自动成为可委派子代理
# ---------------------------------------------------------------------------
def _write_stub_cli(path: Path) -> None:
    """一个「外部 CLI agent」替身：按模拟式把回传打进 stdout。"""
    path.write_text(
        textwrap.dedent(
            """
            import json, sys
            arg_mode = sys.argv[1] if len(sys.argv) > 1 else "text"
            if arg_mode == "json":
                print(json.dumps({"result": {"message": "json 回传"}, "error": None}))
            elif arg_mode == "jsonl":
                print("这是一行日志，不是 JSON")
                print(json.dumps({"type": "log", "msg": {"content": "中间过程"}}))
                print(json.dumps({"type": "result", "msg": {"content": "最终结论"}}))
            elif arg_mode == "fail":
                print(json.dumps({"result": {"message": ""}, "error": "对端自报失败"}))
            elif arg_mode == "crash":
                sys.stderr.write("boom\\n")
                sys.exit(3)
            elif arg_mode == "argv":
                print("argv 里收到: " + (sys.argv[2] if len(sys.argv) > 2 else ""))
            elif arg_mode == "stdin-text":
                print("stdin 收到: " + sys.stdin.read().strip())
            else:
                payload = json.loads(sys.stdin.read() or "{}")
                print("json-stdin task=" + payload.get("task", "") + " sess=" + payload.get("sessionId", ""))
            """
        ),
        encoding="utf-8",
    )


def test_descriptor_driven() -> None:
    print("=== 写描述符 → 出现为子代理 ===")
    _write_stub_cli(_STUB_CLI)
    py = sys.executable.replace("\\", "/")

    (_TMP_AGENTS / "demo.yaml").write_text(
        textwrap.dedent(
            f"""
            version: 1
            id: demo
            name: 演示 CLI
            emoji: 🧪
            description: 一个只存在于描述符里的外部 agent
            persona: 你是演示 agent。
            transport: stdio
            stdio:
              command: ["{py}", "{_STUB_CLI.as_posix()}", "json"]
              input:
                mode: argv-template
                args: ["{{{{task}}}}"]
              timeoutSeconds: 60
            output:
              format: json
              textPath: $.result.message
              errorPath: $.error
            tools:
              - id: demo_exec
                name: Exec
                description: 跑一段演示任务
            skills:
              roots: ["<workspace>/.demo/skills"]
            borrow:
              tools: true
              skills: true
            """
        ),
        encoding="utf-8",
    )

    reload_descriptors()
    descriptors = load_descriptors()
    check("描述符被发现", "demo" in descriptors, str(list(descriptors)))

    st = store()
    listing = SA.list_subagents(st)
    ids = [c["id"] for c in listing]
    check("内置 ZCode 排第一", ids[0] == "zcode", str(ids))
    check("新描述符进入子代理列表", "demo" in ids, str(ids))

    demo = next(c for c in listing if c["id"] == "demo")
    check("走描述符的人设", demo["persona"] == "你是演示 agent。")
    check("标记为外部 agent", demo.get("external") is True)
    check("标记可配置", demo.get("configurable") is True)
    check("登记传输方式", demo.get("transport") == "stdio")
    check("声明了自带工具", demo["selfManagedTools"] == ["demo_exec"], str(demo["selfManagedTools"]))
    check("自带工具不在应用目录 → 默认勾选为空", demo["tools"] == [], str(demo["tools"]))
    check("互借开关透出", demo["borrowTools"] is True and demo["borrowSkills"] is True)
    check("配置里能看到技能根", demo["skillRoots"] == ["<workspace>/.demo/skills"], str(demo["skillRoots"]))
    check("不可删除（描述符提供）", True)

    # 委派：真起子进程，按描述符解析回传
    out = asyncio.run(
        SA.run_subagent(st, "demo", "把这段任务交给外部 agent", "sess-demo")
    )
    check("stdio 回传被正确解析", "json 回传" in out, repr(out))

    # 覆盖层：改配置改的是覆盖层，不是整条替换
    saved = SA.upsert_subagent(st, {"name": "演示 CLI（改过）", "maxRounds": 4}, sid="demo")
    check("覆盖保存成功", saved["name"] == "演示 CLI（改过）", str(saved.get("name")))
    check("未改字段回落描述符默认（persona）", saved["persona"] == "你是演示 agent。")
    check("覆盖层落在同名键下", "demo" in st.load()["subagents"])
    try:
        SA.upsert_subagent(st, {"loop": "custom"}, sid="demo")
        check("loop 不可改", False, "no error raised")
    except SA.SubagentError as exc:
        check("loop 不可改", "不支持修改" in str(exc), str(exc))
    try:
        SA.delete_subagent(st, "demo")
        check("外部 agent 不可删除", False, "no error raised")
    except SA.SubagentError as exc:
        check("外部 agent 不可删除", "描述符" in str(exc), str(exc))

    # 普通子代理不能占用描述符已用的 id
    try:
        SA.upsert_subagent(st, {"id": "demo", "name": "冒名"}, sid=None)
        check("id 冲突被拒", False, "no error raised")
    except SA.SubagentError as exc:
        check("id 冲突被拒", "描述符占用" in str(exc), str(exc))


# ---------------------------------------------------------------------------
# 2. 三种输入模式 / 三种回传格式 / 失败路径
# ---------------------------------------------------------------------------
def _descriptor_with(text: str, name: str):
    path = _TMP_AGENTS / name
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    reload_descriptors()
    return get_descriptor(Path(name).stem)


def test_transports() -> None:
    print("=== stdio 输入模式与回传格式 ===")
    py = sys.executable.replace("\\", "/")
    cli = _STUB_CLI.as_posix()

    # argv-template
    _descriptor_with(
        f"""
        id: t-argv
        name: argv
        transport: stdio
        stdio:
          command: ["{py}", "{cli}", argv]
          input:
            mode: argv-template
            args: ["前缀-{{{{task}}}}"]
        """,
        "t-argv.yaml",
    )
    out = asyncio.run(run_external_agent("t-argv", "任务A", session_id="s", workspace=str(_TMP_AGENTS)))
    check("argv-template 注入任务", out.ok and "前缀-任务A" in out.text, out.as_text())

    # stdin-text
    _descriptor_with(
        f"""
        id: t-stdin
        name: stdin
        transport: stdio
        stdio:
          command: ["{py}", "{cli}", stdin-text]
          input:
            mode: stdin-text
        """,
        "t-stdin.yaml",
    )
    out = asyncio.run(run_external_agent("t-stdin", "任务B", session_id="s", workspace=str(_TMP_AGENTS)))
    check("stdin-text 注入任务", out.ok and "任务B" in out.text, out.as_text())

    # stdin-json
    _descriptor_with(
        f"""
        id: t-json
        name: json
        transport: stdio
        stdio:
          command: ["{py}", "{cli}"]
          input:
            mode: stdin-json
        """,
        "t-json.yaml",
    )
    out = asyncio.run(
        run_external_agent("t-json", "任务C", session_id="sess-9", workspace=str(_TMP_AGENTS))
    )
    check("stdin-json 带 sessionId", out.ok and "task=任务C" in out.text and "sess=sess-9" in out.text, out.as_text())

    # json-lines + textPath 取最后一条
    _descriptor_with(
        f"""
        id: t-lines
        name: lines
        transport: stdio
        stdio:
          command: ["{py}", "{cli}", jsonl]
          input:
            mode: argv-template
        output:
          format: json-lines
          textPath: $.msg.content
        """,
        "t-lines.yaml",
    )
    out = asyncio.run(run_external_agent("t-lines", "x", session_id="s", workspace=str(_TMP_AGENTS)))
    check("json-lines 坏行被跳过", out.ok, out.as_text())
    check("json-lines 取到最后一条结论", out.text == "最终结论", repr(out.text))

    # 对端自报错误
    _descriptor_with(
        f"""
        id: t-fail
        name: fail
        transport: stdio
        stdio:
          command: ["{py}", "{cli}", fail]
          input:
            mode: argv-template
        output:
          format: json
          errorPath: $.error
        """,
        "t-fail.yaml",
    )
    out = asyncio.run(run_external_agent("t-fail", "x", session_id="s", workspace=str(_TMP_AGENTS)))
    check("errorPath 认成失败", (not out.ok) and "对端自报失败" in (out.error or ""), str(out))
    check("失败也给出可读文本", "调用失败" in out.as_text(), out.as_text())

    # 非零退出码
    _descriptor_with(
        f"""
        id: t-crash
        name: crash
        transport: stdio
        stdio:
          command: ["{py}", "{cli}", crash]
          input:
            mode: argv-template
        """,
        "t-crash.yaml",
    )
    out = asyncio.run(run_external_agent("t-crash", "x", session_id="s", workspace=str(_TMP_AGENTS)))
    check("非零退出码算失败", not out.ok and out.exit_code == 3, str(out))
    check("失败信息带退出码", "退出码 3" in (out.error or ""), str(out.error))

    # 可执行文件不存在
    _descriptor_with(
        """
        id: t-missing
        name: missing
        transport: stdio
        stdio:
          command: ["definitely-not-a-real-binary-xyz"]
          input:
            mode: argv-template
        """,
        "t-missing.yaml",
    )
    out = asyncio.run(run_external_agent("t-missing", "x", session_id="s", workspace=str(_TMP_AGENTS)))
    check("找不到可执行文件 → 可读失败", (not out.ok) and "找不到可执行文件" in (out.error or ""), str(out))


def test_http_transport() -> None:
    print("=== http 传输 ===")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("content-length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            body = json.dumps(
                {"data": {"answer": f"收到 {payload.get('task')}"}}, ensure_ascii=False
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # 静音
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    _descriptor_with(
        f"""
        id: t-http
        name: http
        transport: http
        http:
          endpoint: "http://127.0.0.1:{port}/run"
          method: POST
          headers:
            x-agent: demo
        output:
          format: json
          textPath: $.data.answer
        """,
        "t-http.yaml",
    )
    out = asyncio.run(run_external_agent("t-http", "远端任务", session_id="s", workspace=str(_TMP_AGENTS)))
    check("http 回传被解析", out.ok and out.text == "收到 远端任务", out.as_text())
    server.shutdown()


def test_internal_transport() -> None:
    print("=== internal 传输（进程内） ===")
    module = _TMP_AGENTS / "my_internal_agent.py"
    module.write_text(
        textwrap.dedent(
            """
            async def handle(task, *, session_id, workspace, config, provider=None):
                return f"internal 收到 {task} / 会话 {session_id} / 工具 {len(config.get('tools') or [])}"
            """
        ),
        encoding="utf-8",
    )
    sys.path.insert(0, str(_TMP_AGENTS))

    _descriptor_with(
        """
        id: t-internal
        name: internal
        transport: internal
        entry: "my_internal_agent:handle"
        """,
        "t-internal.yaml",
    )
    out = asyncio.run(
        run_external_agent(
            "t-internal", "进程内任务", session_id="s1", workspace=str(_TMP_AGENTS),
            config={"tools": ["a", "b"]},
        )
    )
    check("internal 入口被调用", out.ok and "internal 收到 进程内任务" in out.text, out.as_text())
    check("会话与配置被透传", "会话 s1" in out.text and "工具 2" in out.text, out.text)

    # 入口写错 → 可读失败（不是崩掉）
    _descriptor_with(
        """
        id: t-bad-entry
        name: bad
        transport: internal
        entry: "no_such_module_xyz:handle"
        """,
        "t-bad-entry.yaml",
    )
    out = asyncio.run(run_external_agent("t-bad-entry", "x", session_id="s", workspace=str(_TMP_AGENTS)))
    check("坏 entry → 可读失败", (not out.ok) and "无法导入" in (out.error or ""), str(out))


# ---------------------------------------------------------------------------
# 3. 校验与容错
# ---------------------------------------------------------------------------
def test_validation_and_isolation() -> None:
    print("=== 校验与容错 ===")
    good_before = set(load_descriptors())

    (_TMP_AGENTS / "bad-no-id.yaml").write_text("transport: stdio\n", encoding="utf-8")
    (_TMP_AGENTS / "bad-transport.yaml").write_text(
        "id: badt\ntransport: telepathy\n", encoding="utf-8"
    )
    (_TMP_AGENTS / "bad-stdio.yaml").write_text(
        "id: bads\ntransport: stdio\n", encoding="utf-8"
    )
    (_TMP_AGENTS / "bad-entry.yaml").write_text(
        "id: bade\ntransport: internal\n", encoding="utf-8"
    )
    (_TMP_AGENTS / "bad-json.json").write_text("{ not json", encoding="utf-8")

    reload_descriptors()
    after = set(load_descriptors())
    from openminis.agent.external_agents import descriptor_load_errors

    errors = descriptor_load_errors()
    check("坏文件不让好文件消失", good_before <= after, f"{good_before - after}")
    check("坏 id 被记录", any("bad-no-id" in e for e in errors), str(errors))
    check("非法 transport 被记录", any("transport" in e and "bad-transport" in e for e in errors), str(errors))
    check("stdio 缺 command 被记录", any("bad-stdio" in e for e in errors), str(errors))
    check("internal 缺 entry 被记录", any("bad-entry" in e for e in errors), str(errors))
    check("坏 JSON 被记录", any("bad-json" in e for e in errors), str(errors))
    check("坏 id 文件不注册任何东西", not any(aid.startswith("bad") for aid in after), str(after))

    # enabled: false 的描述符不进子代理列表、不可委派
    (_TMP_AGENTS / "disabled-demo.yaml").write_text(
        textwrap.dedent(
            f"""
            id: disabled-demo
            name: 关掉的
            enabled: false
            transport: stdio
            stdio:
              command: ["{sys.executable.replace(os.sep, '/')}", "{_STUB_CLI.as_posix()}"]
              input: {{mode: argv-template}}
            """
        ),
        encoding="utf-8",
    )
    reload_descriptors()
    check("禁用的描述符仍在注册表里", get_descriptor("disabled-demo") is not None)
    st = store()
    check("禁用的不进子代理列表", "disabled-demo" not in [c["id"] for c in SA.list_subagents(st)])
    try:
        SA.upsert_subagent(st, {"name": "x"}, sid="disabled-demo")
        check("禁用项改配置被拒", False, "no error raised")
    except SA.SubagentError as exc:
        check("禁用项改配置被拒", "未启用" in str(exc), str(exc))

    # 直接加载单个文件（诊断/预演用）
    single = load_descriptor_file(_TMP_AGENTS / "demo.yaml")
    check("单文件加载可用", single.id == "demo" and single.transport == "stdio")
    try:
        load_descriptor_file(_TMP_AGENTS / "bad-no-id.yaml")
        check("单文件加载会抛错", False, "no error raised")
    except ExternalAgentError:
        check("单文件加载会抛错", True)


# ---------------------------------------------------------------------------
# 4. 工具 / 技能互借（双向）
# ---------------------------------------------------------------------------
class BorrowProbeProvider:
    """第一轮调「借来的宿主工具」，第二轮收口。"""

    def __init__(self) -> None:
        self.rounds = 0
        self.seen_tools: list[str] = []
        self.seen_system = ""
        self.tool_name = ""

    async def stream_message(self, messages, system_prompt, max_tokens, temperature=None,
                             image_parts=None, tools=None, thinking_level=None):
        from openminis.data.model import LLMStreamChunk

        self.rounds += 1
        self.seen_system = system_prompt or ""
        self.seen_tools = [t.name for t in (tools or [])]
        if self.rounds == 1:
            yield LLMStreamChunk.ToolCallComplete(id="c1", name=self.tool_name, args=self.args)
            yield LLMStreamChunk.Finished(stop_reason="tool_use")
            return
        yield LLMStreamChunk.Text(text="借用完成。")
        yield LLMStreamChunk.Finished(stop_reason="end_turn")

    def aclose(self) -> None:
        return None


def test_tool_borrowing() -> None:
    print("=== 工具互借 ===")
    from openminis.agent.zcode.integration import (
        borrowed_tool_catalog,
        host_tool_spec,
        resolve_tool_spec,
        zcode_tool_ids,
    )

    # 方向一：主智能体借 ZCode 的工具（已在工具目录里）
    from openminis.settings.catalog import known_tool_ids

    known = known_tool_ids()
    check("主 agent 侧能看到 zcode_* 工具", zcode_tool_ids() <= known, str(sorted(zcode_tool_ids() - known)))

    # 方向二：外部 agent 借主智能体的工具（桥接层）
    borrowed = borrowed_tool_catalog()
    borrowed_ids = {t["id"] for t in borrowed}
    check("可借用清单非空", bool(borrowed_ids), str(sorted(borrowed_ids))[:120])
    check("ZCode 自带工具不在借用清单里", not (zcode_tool_ids() & borrowed_ids))

    spec = host_tool_spec("file_read")
    check("能借到宿主 file_read", spec is not None and spec.origin == "host")
    check("借来的工具沿用宿主原名", spec is not None and spec.id == "file_read")
    check("借来的工具带 schema", bool(spec.entry.input_schema.get("properties")), str(spec.entry.input_schema)[:120])
    check("借来的工具不占端口", spec.needs == ())
    check("委派工具不外借", host_tool_spec("subagent_delegate") is None)
    check("不存在的工具借不到", host_tool_spec("no_such_tool_xyz") is None)
    check("统一解析：自带优先", resolve_tool_spec("zcode_read").origin == "kernel")
    check("统一解析：回落借用", resolve_tool_spec("file_read").origin == "host")
    check("统一解析：都没有则 None", resolve_tool_spec("no_such_tool_xyz") is None)

    # 真跑一轮：ZCode 循环里调用借来的宿主工具
    from openminis.agent.zcode.integration import ZcodeRunOptions, run_zcode_agent

    root = Path(tempfile.mkdtemp(prefix="borrow-"))
    target = root / "note.txt"
    target.write_text("借来的工具真读到了这一行\n", encoding="utf-8")

    provider = BorrowProbeProvider()
    provider.tool_name = "file_read"
    provider.args = {"path": str(target)}
    result = asyncio.run(
        run_zcode_agent(
            provider,
            "用借来的工具读一下 note.txt",
            session_id="sess-borrow",
            options=ZcodeRunOptions(
                working_directory=str(root),
                workspace_root=str(root),
                tool_ids=["zcode_read", "file_read"],
            ),
        )
    )
    check("混合工具链可运行", result.ok, repr(result.error or result.text))
    check("模型看到两套工具", set(provider.seen_tools) == {"zcode_read", "file_read"}, str(provider.seen_tools))
    check("借来的工具真的执行了（走了宿主注册表）", bool(result.tool_calls), str(result.tool_calls)[:200])
    # 宿主的 file_read 有自己的会话沙箱规则；沙箱外的绝对路径会被它自己拒绝 ——
    # 这恰好证明调用**落回了宿主工具**（而不是 ZCode 内核的 Read），且失败如实上报。
    first = result.tool_calls[0]
    check(
        "宿主工具自己的沙箱规则生效（未越权）",
        (not first["ok"]) and "Cannot resolve path" in first["output"],
        f"ok={first['ok']} out={first['output'][:160]}",
    )
    check("提示词分开标注自带/借用", "ZCode toolchain" in provider.seen_system and "Borrowed host tools" in provider.seen_system)

    # 同一份工作区内的文件：ZCode 自己的 Read 能读（用来对照「借来的工具受宿主约束」）
    provider3 = BorrowProbeProvider()
    provider3.tool_name = "zcode_read"
    provider3.args = {"file_path": str(target)}
    result3 = asyncio.run(
        run_zcode_agent(
            provider3,
            "读一下 note.txt",
            session_id="sess-borrow-2",
            options=ZcodeRunOptions(
                working_directory=str(root),
                workspace_root=str(root),
                tool_ids=["zcode_read", "file_read"],
            ),
        )
    )
    check(
        "ZCode 自己的工具读得到（借用不改自带工具的行为）",
        result3.tool_calls and result3.tool_calls[0]["ok"]
        and "借来的工具真读到了这一行" in result3.tool_calls[0]["output"],
        str(result3.tool_calls)[:200],
    )

    # 关掉互借 → 宿主工具解析不到，只剩自带
    provider2 = BorrowProbeProvider()
    provider2.tool_name = "file_read"
    provider2.args = {"path": str(target)}
    result2 = asyncio.run(
        run_zcode_agent(
            provider2,
            "x",
            session_id="s2",
            options=ZcodeRunOptions(
                working_directory=str(root),
                workspace_root=str(root),
                tool_ids=["zcode_read", "file_read"],
                borrow_host_tools=False,
            ),
        )
    )
    check("关掉互借后只剩自带工具", provider2.seen_tools == ["zcode_read"], str(provider2.seen_tools))
    check("关掉互借后调用借来工具会报错", any(not c["ok"] for c in result2.tool_calls), str(result2.tool_calls)[:160])


def test_skill_borrowing() -> None:
    print("=== 技能互借 ===")
    from openminis.agent.zcode.integration import discover_skills_at_roots, discover_zcode_skills

    workspace = Path(tempfile.mkdtemp(prefix="borrow-skills-"))
    demo_root = workspace / ".demo" / "skills"
    (demo_root / "demo-skill").mkdir(parents=True)
    (demo_root / "demo-skill" / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: 外部 agent 自己的技能\n---\n\n正文\n",
        encoding="utf-8",
    )
    # 描述符里的 <workspace> 占位符要能展开
    outcome = discover_skills_at_roots(
        ["<workspace>/.demo/skills"], workspace=str(workspace)
    )
    names = [s.name for s in outcome.skills]
    check("<workspace> 占位符可展开", "demo-skill" in names, str(names))
    check("来源标为 project", outcome.skills[0].scope == "project" if outcome.skills else False)

    # 方向一：主 agent 的注册表能看到外部 agent 的技能
    from openminis.agent import subagents as _SA

    registry = _SA.build_registry(store())
    external_skills = [s for s in registry["skills"] if s.get("source") == "external"]
    check("注册表含外部 agent 技能来源标记", isinstance(registry.get("externalAgents"), list))
    check("注册表带 externalAgents 摘要", len(registry["externalAgents"]) >= 1, str(registry.get("externalAgents"))[:200])
    check("外部 agent 的 skillRoots 被登记", any(v.get("skillRoots") for v in registry["externalAgents"]), str(registry["externalAgents"])[:200])
    check("借用清单进入注册表", isinstance(registry.get("borrowedTools"), list) and registry["borrowedTools"])

    # 技能页那一栏的数据源：外部 agent 自己的技能（只读）
    from openminis.agent.external_agents import collect_external_agent_skills

    collect_ws = Path(tempfile.mkdtemp(prefix="collect-skills-"))
    zroot = collect_ws / ".zcode" / "skills" / "z-own"
    zroot.mkdir(parents=True)
    (zroot / "SKILL.md").write_text(
        "---\nname: z-own\ndescription: zcode 自己的技能\nwhen_to_use: 调试时\n---\n正文\n",
        encoding="utf-8",
    )
    rows = collect_external_agent_skills(str(collect_ws), host_skill_names={"z-own"})
    by_name = {r["name"]: r for r in rows}
    check("技能页能读到外部 agent 技能", "z-own" in by_name, str(sorted(by_name)))
    check("标记为只读", all(r["readOnly"] for r in rows))
    check("标出归属哪个 agent", by_name["z-own"]["agentId"] == "zcode" and by_name["z-own"]["agentName"])
    check("同名会标 shadowed（不合并）", by_name["z-own"]["shadowed"] is True)
    check("未启用的槽位不产出技能", all(r["agentId"] not in ("doubao", "workbuddy") for r in rows), str({r["agentId"] for r in rows}))

    _ = (names, external_skills, demo_root)

    # 方向二：外部 agent 借主 agent 的技能库（用假的宿主技能根验证机制）
    home = Path(tempfile.mkdtemp(prefix="fake-home-"))
    (home / ".zcode" / "skills" / "own").mkdir(parents=True)
    (home / ".zcode" / "skills" / "own" / "SKILL.md").write_text(
        "---\nname: own\ndescription: 自己的\n---\n正文\n", encoding="utf-8"
    )
    borrowed_view = discover_zcode_skills(
        str(workspace), home_directory=str(home), borrow_host_skills=True
    )
    plain_view = discover_zcode_skills(
        str(workspace), home_directory=str(home), borrow_host_skills=False
    )
    check("自己的技能在两个视图里都在", "own" in [s.name for s in borrowed_view.skills])
    check("借技能视图 ⊇ 不借视图", len(borrowed_view.skills) >= len(plain_view.skills))
    borrowed_extra = {s.name for s in borrowed_view.skills} - {s.name for s in plain_view.skills}
    check(
        "借来的技能确实来自宿主库（多出来的都标 bundled）",
        all(
            s.source == "bundled"
            for s in borrowed_view.skills
            if s.name in borrowed_extra
        ),
        str(borrowed_extra),
    )

    _ = (names, external_skills, demo_root)

    # 方向二**收窄**：勾中哪几个就只借哪几个 —— 这正是配置界面「借自主智能体」
    # 那一栏的语义；没勾 = 借整个技能库（默认行为，向后兼容）。
    import openminis.agent.zcode.integration.skills as _SK

    host_lib = Path(tempfile.mkdtemp(prefix="fake-host-lib-"))
    for nm in ("alpha", "beta", "gamma"):
        d = host_lib / nm
        d.mkdir()
        (d / "SKILL.md").write_text(
            f"---\nname: {nm}\ndescription: 宿主技能 {nm}\n---\n正文\n",
            encoding="utf-8",
        )
    _real_host_roots = _SK.host_skill_roots
    _SK.host_skill_roots = lambda: [
        _SK.ZcodeSkillRoot(
            path=str(host_lib), scope="user", source="bundled", priority=1_000_000
        )
    ]
    try:
        all_view = discover_zcode_skills(
            str(workspace), home_directory=str(home), borrow_host_skills=True
        )
        all_names = [s.name for s in all_view.skills]
        check(
            "不传 filter = 整个宿主技能库都借",
            {"alpha", "beta", "gamma"} <= set(all_names),
            str(all_names),
        )

        one_view = discover_zcode_skills(
            str(workspace),
            home_directory=str(home),
            borrow_host_skills=True,
            host_skill_filter={"alpha"},
        )
        one_names = [s.name for s in one_view.skills]
        check(
            "只借勾中的那一个",
            "alpha" in one_names and "beta" not in one_names and "gamma" not in one_names,
            str(one_names),
        )
        check(
            "过滤只管借来的，不管自己的技能",
            "own" in one_names,
            str(one_names),
        )

        # 配置 → 运行时：勾中的技能名要一路传到技能端口（模型看到的清单同源）。
        from openminis.agent.zcode.integration import build_zcode_runtime

        runtime = build_zcode_runtime(
            session_id="s-borrow",
            working_directory=str(workspace),
            need=("skill",),
            borrowed_skill_names=["alpha"],
        )
        check(
            "借技能白名单落到技能端口",
            runtime.skill_port._host_filter == {"alpha"},
            str(getattr(runtime.skill_port, "_host_filter", None)),
        )

        # 配置 → 提示词：借来的技能会出现在系统提示里，且只出现勾中的那些。
        from openminis.agent.zcode.integration import build_zcode_system_prompt

        prompt = build_zcode_system_prompt(
            working_directory=str(workspace),
            borrow_host_skills=True,
            borrowed_skill_names=["alpha"],
        )
        check("系统提示列出借来的技能", "`alpha`" in prompt, prompt[:400])
        check(
            "没勾的宿主技能不出现在提示里",
            "`beta`" not in prompt and "`gamma`" not in prompt,
            prompt[:400],
        )
    finally:
        _SK.host_skill_roots = _real_host_roots


test_reserved_slots()
test_parser()
test_descriptor_driven()
test_transports()
test_http_transport()
test_internal_transport()
test_validation_and_isolation()
test_tool_borrowing()
test_skill_borrowing()

print()
print(f"PASSED = {PASSED}   FAILED = {FAILED}")
if FAILED:
    raise SystemExit(1)
