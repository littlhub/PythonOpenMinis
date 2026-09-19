"""[T-plugins-manager] 插件体系：安装 / 导入 / 推断 / 配置 / 起停 / 通道桥。

用户诉求：「完善插件，在通道处装入插件，加入桥接插件，完善 QQ 机器人」+
「加插件管理可以导入 dsh 等插件」。这组测试钉住四件事：

1. 插件清单与配置读写（含密钥脱敏、密钥留空不改）；
2. **导入现成项目**：没有 plugin.json 也能装 —— 按 package.json / 入口文件推断，
   认不出入口就只登记（不硬启动）；
3. 生命周期：引擎内驱动（假驱动）能起停，外部程序能起子进程并收到日志；
4. 通道桥：IM 会话 ↔ 引擎会话的映射、命令、以及「跑一轮把回复发回去」。
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import zipfile
from pathlib import Path

import pytest

from openminis.plugins import store
from openminis.plugins.base import (
    STATE_CONNECTED,
    ChannelAdapter,
    IncomingMessage,
    attachment_is_image,
)
from openminis.plugins.bridge import ConversationBridge
from openminis.plugins.manifest import (
    RUNTIME_MANUAL,
    RUNTIME_PROCESS,
    ChannelManifest,
    ManifestError,
)
from openminis.plugins.registry import PluginRuntime
from openminis.plugins.drivers.qq import QQAdapter, QQBotError, normalize_event


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    from openminis.core import context

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MINIS_HOME", str(home))
    context.set_app_context(context.AppContext(data_dir=home, cache_dir=home))
    return home


# ---------------------------------------------------------------------------
# 清单
# ---------------------------------------------------------------------------
def test_manifest_requires_id_and_driver():
    with pytest.raises(ManifestError):
        ChannelManifest.from_dict({"name": "x"})
    with pytest.raises(ManifestError):
        ChannelManifest.from_dict({"id": "x"})          # 缺 driver
    with pytest.raises(ManifestError):
        ChannelManifest.from_dict({"id": "x", "runtime": "weird"})


def test_manifest_process_needs_command():
    with pytest.raises(ManifestError):
        ChannelManifest.from_dict({"id": "x", "runtime": "process"})
    m = ChannelManifest.from_dict({
        "id": "dsh", "runtime": "process",
        "process": {"command": "node", "args": ["index.js"]},
    })
    assert m.is_process
    assert m.command_line() == ("node", ["index.js"], ".")


def test_manifest_id_is_sanitized():
    with pytest.raises(ManifestError):
        ChannelManifest.from_dict({"id": "../evil", "driver": "qq"})


# ---------------------------------------------------------------------------
# 安装 / 配置
# ---------------------------------------------------------------------------
def test_install_builtin_and_config_roundtrip(data_dir):
    manifest = store.install_builtin("qq-bot")
    assert store.is_installed("qq-bot")
    assert manifest.driver == "qq"
    assert "AppID" in store.missing_required("qq-bot")

    store.write_config("qq-bot", {"appId": "102xx", "clientSecret": "s3cret",
                                  "allowFrom": "u1\n\nu2\n"})
    cfg = store.read_config("qq-bot")
    assert cfg["allowFrom"] == ["u1", "u2"]
    assert store.missing_required("qq-bot") == []

    # 密钥留空 = 沿用旧值（界面从来拿不到明文，不能一保存就抹掉）
    store.write_config("qq-bot", {"clientSecret": ""})
    assert store.read_config("qq-bot")["clientSecret"] == "s3cret"

    public = store.public_config("qq-bot")
    assert public["clientSecret"] == ""
    assert public["clientSecret__set"] is True
    assert public["appId"] == "102xx"


def test_install_builtin_missing(data_dir):
    with pytest.raises(ManifestError):
        store.install_builtin("nope")


def test_remove_refuses_path_escape(data_dir):
    store.install_builtin("qq-bot")
    assert store.remove("qq-bot") is True
    assert not store.is_installed("qq-bot")
    with pytest.raises(ManifestError):
        store.remove("../qq-bot")


# ---------------------------------------------------------------------------
# 导入：现成的项目（dsh 这类）
# ---------------------------------------------------------------------------
def _make_node_project(root: Path, name: str = "@wenbin_wb/dsh-bridge") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(json.dumps({
        "name": name,
        "version": "2.8.5",
        "description": "QQ / 微信 / 飞书 / Telegram 桥接",
        "main": "index.js",
    }, ensure_ascii=False), encoding="utf-8")
    (root / "index.js").write_text("console.log('hi')\n", encoding="utf-8")
    return root


def test_import_dir_without_manifest_infers(data_dir, tmp_path):
    src = _make_node_project(tmp_path / "dsh-bridge-main")
    manifest = store.import_plugin(src)
    assert manifest.id == "dsh-bridge"
    assert manifest.runtime == RUNTIME_PROCESS
    assert manifest.category == "channel"          # 名字里有 bridge
    assert manifest.process["command"] == "node"
    assert manifest.process["args"] == ["index.js"]
    # 推断出来的清单要落盘，用户能改
    assert (store.plugins_dir() / "dsh-bridge" / "plugin.json").is_file()


def test_import_zip_with_nested_dir(data_dir, tmp_path):
    src = _make_node_project(tmp_path / "build" / "dsh-bridge-main")
    archive = tmp_path / "dsh-bridge.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for file in src.rglob("*"):
            if file.is_file():
                zf.write(file, file.relative_to(src.parent))
    manifest = store.import_plugin(archive)
    assert manifest.id == "dsh-bridge"
    assert store.is_installed("dsh-bridge")


def test_import_zip_rejects_path_escape(data_dir, tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escape.json", "{}")
        zf.writestr("plugin.json", "{}")
    with pytest.raises(ManifestError):
        store.import_plugin(archive)


def test_import_unknown_project_is_registered_only(data_dir, tmp_path):
    src = tmp_path / "mystery"
    src.mkdir()
    (src / "notes.txt").write_text("nothing runnable", encoding="utf-8")
    manifest = store.import_plugin(src)
    assert manifest.runtime == RUNTIME_MANUAL       # 不硬启动
    assert store.is_installed(manifest.id)


def test_import_python_project(data_dir, tmp_path):
    src = tmp_path / "pybot"
    src.mkdir()
    (src / "main.py").write_text("print('hi')\n", encoding="utf-8")
    manifest = store.import_plugin(src)
    assert manifest.runtime == RUNTIME_PROCESS
    assert manifest.process["args"] == ["main.py"]


def test_install_builtin_then_import_same_id_replaces(data_dir, tmp_path):
    store.install_builtin("qq-bot")
    src = tmp_path / "qq-bot"
    src.mkdir()
    (src / "plugin.json").write_text(json.dumps({
        "id": "qq-bot", "name": "自定义 QQ", "driver": "qq",
    }, ensure_ascii=False), encoding="utf-8")
    manifest = store.import_plugin(src)
    assert manifest.name == "自定义 QQ"


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------
class FakeAdapter(ChannelAdapter):
    driver = "fake"

    def __init__(self, **kw):
        super().__init__(**kw)
        self.sent: list[str] = []
        #: 发出去的图片 / 文件路径（按调用顺序）。
        self.images: list[str] = []
        self.files: list[str] = []
        #: 让测试注入「附件 URL → 字节 / 文件名」，模拟下载。
        self.attachment_bytes: dict[str, bytes] = {}
        self.attachment_names: dict[str, str] = {}

    async def start(self):
        self.set_state(STATE_CONNECTED, "ok")

    async def stop(self):
        from openminis.plugins.base import STATE_STOPPED

        self.set_state(STATE_STOPPED)

    async def send_text(self, msg, text, **kw):
        self.sent.append(text)

    async def send_image(self, msg, path, **kw):
        self.images.append(str(path))
        return True

    async def send_file(self, msg, path, **kw):
        self.files.append(str(path))
        return True

    async def fetch_attachment(self, att):
        url = str(att.get("url") or "")
        data = self.attachment_bytes.get(url)
        if data is None:
            return None
        return data, self.attachment_names.get(url, "pic.jpg")


def _install_fake(monkeypatch) -> None:
    store.plugins_dir().joinpath("fake-bot").mkdir(parents=True, exist_ok=True)
    (store.plugins_dir() / "fake-bot" / "plugin.json").write_text(json.dumps({
        "id": "fake-bot", "name": "假通道", "runtime": "engine", "driver": "fake",
        "category": "channel",
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        "openminis.plugins.registry.load_adapter", lambda driver: FakeAdapter
    )
    monkeypatch.setattr(
        "openminis.plugins.registry.has_driver", lambda driver: driver == "fake"
    )


@pytest.mark.asyncio
async def test_engine_plugin_start_stop(data_dir, monkeypatch):
    _install_fake(monkeypatch)
    runtime = PluginRuntime()
    status = await runtime.start("fake-bot")
    assert status["state"] == STATE_CONNECTED
    assert runtime.is_running("fake-bot")
    assert store.is_enabled("fake-bot")

    await runtime.stop("fake-bot")
    assert not runtime.is_running("fake-bot")
    assert not store.is_enabled("fake-bot")


@pytest.mark.asyncio
async def test_manual_plugin_refuses_start(data_dir, tmp_path):
    src = tmp_path / "mystery"
    src.mkdir()
    (src / "readme.md").write_text("x", encoding="utf-8")
    manifest = store.import_plugin(src)
    runtime = PluginRuntime()
    with pytest.raises(ManifestError):
        await runtime.start(manifest.id)


@pytest.mark.asyncio
async def test_engine_plugin_start_requires_config(data_dir, monkeypatch):
    _install_fake(monkeypatch)
    (store.plugins_dir() / "fake-bot" / "plugin.json").write_text(json.dumps({
        "id": "fake-bot", "name": "假通道", "runtime": "engine", "driver": "fake",
        "fields": [{"key": "token", "label": "令牌", "required": True}],
    }, ensure_ascii=False), encoding="utf-8")
    runtime = PluginRuntime()
    with pytest.raises(ManifestError) as err:
        await runtime.start("fake-bot")
    assert "令牌" in str(err.value)


@pytest.mark.asyncio
async def test_process_plugin_runs_and_logs(data_dir, tmp_path):
    root = store.plugins_dir() / "echo-bot"
    root.mkdir(parents=True)
    (root / "sayer.py").write_text(
        "import time, sys\n"
        "print('hello from plugin', flush=True)\n"
        "sys.stdout.flush()\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    (root / "plugin.json").write_text(json.dumps({
        "id": "echo-bot", "name": "回声", "runtime": "process",
        "category": "other",
        "process": {"command": "python", "args": ["sayer.py"], "autoRestart": False},
    }, ensure_ascii=False), encoding="utf-8")

    runtime = PluginRuntime()
    status = await runtime.start("echo-bot")
    assert status["running"] is True and status["pid"]
    await asyncio.sleep(1.2)
    texts = " ".join(row["text"] for row in runtime.logs("echo-bot"))
    assert "hello from plugin" in texts
    await runtime.stop("echo-bot")
    assert runtime.is_running("echo-bot") is False


# ---------------------------------------------------------------------------
# 通道桥
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_bridge_maps_conversation_and_replies(data_dir, monkeypatch):
    from openminis.server import chat_store, main as server_main

    chat_store.set_database_path(data_dir / "plugins_bridge.db")
    adapter = FakeAdapter(plugin_id="fake-bot", config={})
    adapter.set_state(STATE_CONNECTED)
    bridge = ConversationBridge(plugin_id="fake-bot", adapter=adapter)

    async def fake_run_chat(client_id, msg):
        # 真链路里这是 _run_chat：往挂上的 sink 推帧
        sink = server_main.manager.active[client_id]
        await sink.send_json({"type": "delta", "text": "你"})
        await sink.send_json({"type": "delta", "text": "好"})
        await sink.send_json({"type": "done"})
        await chat_store.append_turn(
            msg["sessionId"], "assistant", "你好", model_label="m",
        )

    monkeypatch.setattr(server_main, "_run_chat", fake_run_chat)

    msg = IncomingMessage(scope="c2c", peer_id="u1", sender_id="u1", text="在吗",
                          message_id="m1")
    await bridge.handle(msg)
    # 第一条是「收到」回执（先响应再干活），然后是真正的回复
    assert adapter.sent == ["收到，正在处理…", "你好"]

    sid = bridge.active_session("c2c:u1")
    assert sid
    rows = await chat_store.load_messages(sid)
    assert [r.role for r in rows] == ["assistant"]     # 回复落到了引擎会话里
    # 桥断开了订阅者，别再往已关的客户端推
    assert all(not k.startswith("bot:") for k in server_main.manager.active)


@pytest.mark.asyncio
async def test_bridge_commands_new_list_resume(data_dir, monkeypatch):
    from openminis.server import chat_store

    chat_store.set_database_path(data_dir / "plugins_cmd.db")
    adapter = FakeAdapter(plugin_id="fake-bot", config={})
    bridge = ConversationBridge(plugin_id="fake-bot", adapter=adapter)
    msg = IncomingMessage(scope="c2c", peer_id="u1", sender_id="u1", text="/help")

    await bridge.handle(msg)
    assert "我可以直接聊天" in adapter.sent[-1]

    old = await bridge._ensure_session(msg)
    await bridge.handle(IncomingMessage(scope="c2c", peer_id="u1", sender_id="u1",
                                       text="/new"))
    new = bridge.active_session("c2c:u1")
    assert new and new != old

    await bridge.handle(IncomingMessage(scope="c2c", peer_id="u1", sender_id="u1",
                                       text="/list"))
    assert "这个会话的对话" in adapter.sent[-1]

    await bridge.handle(IncomingMessage(scope="c2c", peer_id="u1", sender_id="u1",
                                       text="/resume 2"))
    assert bridge.active_session("c2c:u1") == old

    await bridge.handle(IncomingMessage(scope="c2c", peer_id="u1", sender_id="u1",
                                       text="/ping"))
    assert adapter.sent[-1] == "pong"


@pytest.mark.asyncio
async def test_unknown_slash_message_is_treated_as_text(data_dir, monkeypatch):
    """认不出的斜杠开头消息不能吞掉 —— 当普通提问送进引擎。"""
    from openminis.server import chat_store, main as server_main

    chat_store.set_database_path(data_dir / "plugins_slash.db")
    adapter = FakeAdapter(plugin_id="p", config={})
    bridge = ConversationBridge(plugin_id="p", adapter=adapter)
    called: dict[str, str] = {}

    async def fake_run_chat(client_id, msg):
        called["text"] = str(msg.get("text") or "")

    monkeypatch.setattr(server_main, "_run_chat", fake_run_chat)

    await bridge.handle(
        IncomingMessage(scope="c2c", peer_id="u", sender_id="u", text="/我不认识这个")
    )
    assert called["text"] == "/我不认识这个"


# ---------------------------------------------------------------------------
# 「由哪个 agent 接待」：可选身份 / 子代理，按选择分流
# ---------------------------------------------------------------------------
def test_agent_options_lists_identities_and_subagents(data_dir):
    from openminis.plugins import options as plugin_options
    from openminis.settings.store import SettingsStore

    store = SettingsStore.get()
    data = store.load()
    # 子代理要出现在候选里
    data.setdefault("subagents", {})["researcher"] = {
        "id": "researcher", "name": "研究员", "emoji": "🔬",
        "description": "负责查资料", "persona": "你是研究员",
        "providerId": "gw", "model": "m1", "tools": [],
    }
    store.save(data)

    items = plugin_options.agent_options()
    values = [i["value"] for i in items]
    assert values[0] == plugin_options.FOLLOW_CURRENT          # 默认跟随当前身份
    assert "identity:assistant" in values
    assert "subagent:researcher" in values
    groups = {i["group"] for i in items}
    assert {"默认", "主 agent 身份", "子代理（助理）"} <= groups


def test_parse_target():
    from openminis.plugins.options import parse_target

    assert parse_target("") == ("", "")
    assert parse_target("subagent:abc") == ("subagent", "abc")
    assert parse_target("identity:coder") == ("identity", "coder")


def test_select_field_options_are_injected(data_dir):
    """清单只声明 optionsFrom，真正的可选值由引擎在读取状态时回填。"""
    root = store.plugins_dir() / "gate"
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.json").write_text(json.dumps({
        "id": "gate", "name": "网关", "runtime": "manual", "category": "tool",
        "fields": [{
            "key": "agentId", "label": "由哪个 agent 接待",
            "type": "select", "optionsFrom": "agents",
        }],
    }, ensure_ascii=False), encoding="utf-8")

    field = PluginRuntime().status("gate")["fields"][0]
    # 清单本身不带选项
    assert store.get("gate").fields[0].to_dict()["optionsFrom"] == "agents"
    assert "options" not in store.get("gate").fields[0].to_dict()
    # 但界面拿到的那份已经解析好了
    assert field["options"][0]["value"] == ""
    assert any(o["value"] == "identity:assistant" for o in field["options"])


@pytest.mark.asyncio
async def test_bridge_uses_selected_identity(data_dir, monkeypatch):
    """选了某个身份 → 跑一轮时把 identityId 带进 _run_chat。"""
    from openminis.server import chat_store, main as server_main

    chat_store.set_database_path(data_dir / "plugins_agent.db")
    adapter = FakeAdapter(plugin_id="q", config={"agentId": "identity:coder"})
    bridge = ConversationBridge(plugin_id="q", adapter=adapter)
    seen: dict[str, object] = {}

    async def fake_run_chat(client_id, msg):
        seen.update(msg)

    monkeypatch.setattr(server_main, "_run_chat", fake_run_chat)

    await bridge.handle(
        IncomingMessage(scope="c2c", peer_id="u", sender_id="u", text="帮我看下")
    )
    assert seen["identityId"] == "coder"
    assert seen["text"] == "帮我看下"


@pytest.mark.asyncio
async def test_bridge_hands_turn_to_subagent(data_dir, monkeypatch):
    """选了子代理 → 这一路对话不经过 _run_chat，直接由子代理跑。"""
    from openminis.agent import subagents as subagent_registry
    from openminis.server import chat_store, main as server_main

    chat_store.set_database_path(data_dir / "plugins_sub.db")
    adapter = FakeAdapter(plugin_id="q", config={"agentId": "subagent:researcher"})
    bridge = ConversationBridge(plugin_id="q", adapter=adapter)

    async def fake_run_subagent(store, subagent_id, task, session_id):
        assert subagent_id == "researcher" and task == "帮我查一下"
        # 子代理的内层循环会把过程发到事件总线 —— 桥接过来就是流式回复
        from openminis.agent.subagent_events import emit

        await emit({"type": "subagentDelta", "text": "查到"})
        await emit({"type": "subagentDelta", "text": "了三条"})
        return "查到了三条"

    async def boom(*_a, **_k):  # pragma: no cover - 走到这里就是分流错了
        raise AssertionError("子代理模式下不该再走 _run_chat")

    monkeypatch.setattr(subagent_registry, "run_subagent", fake_run_subagent)
    monkeypatch.setattr(server_main, "_run_chat", boom)

    await bridge.handle(
        IncomingMessage(scope="c2c", peer_id="u", sender_id="u", text="帮我查一下")
    )
    # 回执在最前，尾巴补一条，不重复整段
    assert adapter.sent == ["收到，正在处理…", "查到了三条"]


@pytest.mark.asyncio
async def test_bridge_subagent_failure_is_reported(data_dir, monkeypatch):
    from openminis.agent import subagents as subagent_registry
    from openminis.agent.subagents import SubagentError
    from openminis.server import chat_store

    chat_store.set_database_path(data_dir / "plugins_subfail.db")
    adapter = FakeAdapter(plugin_id="q", config={"agentId": "subagent:ghost"})
    bridge = ConversationBridge(plugin_id="q", adapter=adapter)

    async def missing(*_a, **_k):
        raise SubagentError("subagent 不存在: ghost")

    monkeypatch.setattr(subagent_registry, "run_subagent", missing)
    await bridge.handle(
        IncomingMessage(scope="c2c", peer_id="u", sender_id="u", text="在吗")
    )
    assert "用不了" in adapter.sent[-1] and "ghost" in adapter.sent[-1]


# ---------------------------------------------------------------------------
# QQ 协议层
# ---------------------------------------------------------------------------
def test_normalize_c2c_and_group():
    c2c = normalize_event("C2C_MESSAGE_CREATE", {
        "id": "m1", "content": " 你好 ", "author": {"user_openid": "u_1"},
        "msg_seq": 3,
    })
    assert c2c is not None
    assert c2c.scope == "c2c" and c2c.peer_id == "u_1" and c2c.text == "你好"
    assert c2c.conv_key == "c2c:u_1" and c2c.msg_seq == 3

    group = normalize_event("GROUP_AT_MESSAGE_CREATE", {
        "id": "m2", "content": "帮我看看", "group_openid": "g_9",
        "author": {"member_openid": "u_2"},
    })
    assert group is not None
    assert group.scope == "group" and group.peer_id == "g_9"
    assert group.sender_id == "u_2" and group.is_group

    assert normalize_event("SOMETHING_ELSE", {}) is None


def test_qq_split_respects_limit_and_language():
    adapter = QQAdapter(plugin_id="qq-bot", config={}, max_message_chars=200)
    text = "第一段。" * 40 + "\n" + "second part. " * 20
    chunks = adapter.split_text(text)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)
    assert "".join(chunks) == text


def test_qq_allowlist_only_guards_private_chat():
    adapter = QQAdapter(plugin_id="qq-bot", config={"allowFrom": ["u_ok"]})
    ok = IncomingMessage(scope="c2c", peer_id="u_ok", sender_id="u_ok", text="hi")
    bad = IncomingMessage(scope="c2c", peer_id="u_x", sender_id="u_x", text="hi")
    group = IncomingMessage(scope="group", peer_id="g", sender_id="u_x", text="hi")
    assert adapter._allowed(ok) is True
    assert adapter._allowed(bad) is False
    assert adapter._allowed(group) is True      # 群里被 @ 到就算找上门了


def test_qq_configured_needs_both_fields():
    assert QQAdapter(plugin_id="q", config={"appId": "1"}).configured is False
    assert QQAdapter(
        plugin_id="q", config={"appId": "1", "clientSecret": "s"}
    ).configured is True


def test_qq_msg_seq_increments_across_replies():
    """同一条入站消息回多条时 msg_seq 必须互不相同，否则官方按 (msg_id, msg_seq) 丢弃。"""
    adapter = QQAdapter(plugin_id="q", config={})
    msg = IncomingMessage(scope="c2c", peer_id="u", sender_id="u", text="hi", message_id="m1")

    assert adapter._reserve_seq(msg, 3) == 1          # 一段回复占 1/2/3
    assert adapter._reserve_seq(msg, 1) == 4          # 下一次接着往下
    assert adapter._reserve_seq(msg, 2) == 5

    other = IncomingMessage(scope="c2c", peer_id="u", sender_id="u", text="hi", message_id="m2")
    assert adapter._reserve_seq(other, 1) == 1        # 不同消息各自从 1 起
    assert adapter._reserve_seq(None, 1) is None      # 主动消息不需要 seq


@pytest.mark.asyncio
async def test_qq_send_text_splits_with_ascending_seq(monkeypatch):
    adapter = QQAdapter(
        plugin_id="q", config={"maxMessageChars": 100, "sendChunkDelayMs": 1}
    )
    sent: list[dict] = []

    async def fake_api(path, *, method="POST", body=None):
        sent.append(dict(body or {}))
        return {}

    monkeypatch.setattr(adapter, "_api", fake_api)

    msg = IncomingMessage(scope="group", peer_id="g", sender_id="u", text="x", message_id="m9")
    await adapter.send_text(msg, "补" * 250)

    assert len(sent) >= 3
    assert [b["msg_seq"] for b in sent] == list(range(1, len(sent) + 1))
    assert all(b["msg_id"] == "m9" and b["msg_type"] == 0 for b in sent)
    assert all(len(b["content"]) <= 100 for b in sent)


# ---------------------------------------------------------------------------
# 插件工具：插件**不只是通道** —— tools 段让 agent 多一项能力
# ---------------------------------------------------------------------------
def _install_tool_plugin(data_dir, tmp_path, *, enabled: bool) -> str:
    """造一个纯工具插件（无驱动、无进程常驻），装进插件目录。"""
    root = store.plugins_dir() / "word-count"
    root.mkdir(parents=True, exist_ok=True)
    (root / "count.py").write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "print(f\"{payload['args']['text']} 有 {len(payload['args']['text'])} 个字\")\n",
        encoding="utf-8",
    )
    (root / "plugin.json").write_text(json.dumps({
        "id": "word-count",
        "name": "字数统计",
        "runtime": "manual",
        "category": "tool",
        "tools": [{
            "id": "count",
            "name": "统计字数",
            "description": "数一段文字有多少个字",
            "parameters": {"text": {"type": "string", "description": "要统计的文字"}},
            "required": ["text"],
            "command": ["python", "count.py"],
            "timeoutSec": 20,
        }],
    }, ensure_ascii=False), encoding="utf-8")
    if enabled:
        store.set_enabled("word-count", True)
    return "word-count"


def test_manifest_parses_tool_spec():
    manifest = ChannelManifest.from_dict({
        "id": "wc", "name": "字数", "runtime": "manual", "category": "tool",
        "tools": [{
            "id": "count", "description": "数数",
            "parameters": {"text": {"type": "string", "enum": ["a", "b"]}},
            "required": ["text"], "command": ["python", "count.py"],
        }],
    })
    spec = manifest.tools[0]
    assert spec.command == ("python", "count.py")
    assert spec.parameters["text"]["enum"] == ["a", "b"]
    assert spec.required == ("text",)
    assert manifest.to_dict()["tools"][0]["id"] == "count"


def test_manifest_tool_needs_command():
    with pytest.raises(ManifestError):
        ChannelManifest.from_dict({
            "id": "x", "runtime": "manual", "tools": [{"id": "t"}],
        })
    # 字符串命令不接受 —— 拼参数不可靠，报错要说得清除
    with pytest.raises(ManifestError) as err:
        ChannelManifest.from_dict({
            "id": "x", "runtime": "manual",
            "tools": [{"id": "t", "command": "python t.py"}],
        })
    assert "数组" in str(err.value)


def test_manifest_allows_engine_plugin_with_only_tools():
    """没有 driver 但有 tools 的包不该被拒 —— 它是纯能力包，不需要驱动。"""
    manifest = ChannelManifest.from_dict({
        "id": "x", "name": "只给工具", "tools": [{"id": "t", "command": ["echo"]}],
    })
    assert manifest.driver == "" and manifest.tools


def test_plugin_tool_id_is_namespaced():
    from openminis.plugins import tools as plugin_tools

    assert plugin_tools.tool_full_id("qq-bot", "send") == "qq_bot__send"
    assert plugin_tools.tool_full_id("my.plug", "a b") == "my_plug__a_b"


@pytest.mark.asyncio
async def test_tool_plugin_registers_and_runs(data_dir):
    """启用 → 出现在 agent 工具注册表里 → 调用真能跑起来并回输出。"""
    from openminis.plugins import tools as plugin_tools
    from openminis.settings.catalog import build_tool_registry, known_tool_ids

    _install_tool_plugin(data_dir, None, enabled=False)
    assert plugin_tools.tool_ids() == set()           # 没启用就不挂
    assert "word_count__count" not in known_tool_ids()

    runtime = PluginRuntime()
    await runtime.start("word-count")                 # manual + tools 允许启用
    assert store.is_enabled("word-count")

    name = "word_count__count"
    assert plugin_tools.tool_ids() == {name}
    assert name in known_tool_ids()                   # 设置里能勾选它

    registry = build_tool_registry([name])
    assert name in registry
    assert registry[name].definition.description == "数一段文字有多少个字"

    result = await registry[name].executor('{"text": "你好世界"}', "sess-1")
    assert result.success
    assert "4 个字" in result.output

    await runtime.stop("word-count")
    assert plugin_tools.tool_ids() == set()


@pytest.mark.asyncio
async def test_tool_plugin_reports_failure_and_timeout(data_dir):
    from openminis.plugins import tools as plugin_tools

    root = store.plugins_dir() / "boom"
    root.mkdir(parents=True, exist_ok=True)
    (root / "die.py").write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
    (root / "slow.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    (root / "plugin.json").write_text(json.dumps({
        "id": "boom", "name": "出错插件", "runtime": "manual", "category": "tool",
        "tools": [
            {"id": "die", "command": ["python", "die.py"]},
            {"id": "slow", "command": ["python", "slow.py"], "timeoutSec": 1},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    store.set_enabled("boom", True)

    manifest = store.get("boom")
    die = next(t for t in manifest.tools if t.id == "die")
    slow = next(t for t in manifest.tools if t.id == "slow")

    bad = await plugin_tools.run_tool(manifest, die, "{}", "")
    assert bad.success is False and "失败" in bad.output

    hung = await plugin_tools.run_tool(manifest, slow, "{}", "")
    assert hung.success is False and hung.timed_out is True


@pytest.mark.asyncio
async def test_tool_plugin_refuses_cwd_escape(data_dir):
    """工具的 cwd 不能跑到插件目录外面去。"""
    from openminis.plugins import tools as plugin_tools

    root = store.plugins_dir() / "escapee"
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.json").write_text(json.dumps({
        "id": "escapee", "name": "越界", "runtime": "manual", "category": "tool",
        "tools": [{"id": "x", "command": ["python", "x.py"], "cwd": "../../.."}],
    }, ensure_ascii=False), encoding="utf-8")
    store.set_enabled("escapee", True)

    manifest = store.get("escapee")
    result = await plugin_tools.run_tool(manifest, manifest.tools[0], "{}", "")
    assert result.success is False
    assert "越界" in result.output


# ---------------------------------------------------------------------------
# 通道图片：入站落盘 + 出站富媒体
# ---------------------------------------------------------------------------
def test_attachment_is_image_prefers_content_type():
    assert attachment_is_image({"content_type": "image/jpeg"}) is True
    assert attachment_is_image({"content_type": "application/pdf"}) is False
    # 只给「通用二进制」时才回头看文件名/URL 后缀
    assert attachment_is_image(
        {"content_type": "application/octet-stream", "filename": "a.PNG"}
    ) is True
    assert attachment_is_image({"url": "https://x/y.webp?x=1"}) is True
    assert attachment_is_image({"filename": "notes.txt"}) is False


def test_attachment_kind_and_name():
    from openminis.plugins.base import attachment_kind, attachment_name

    assert attachment_kind({"content_type": "image/png"}) == "image"
    assert attachment_kind({"content_type": "application/pdf", "filename": "a.pdf"}) == "file"
    assert attachment_kind({}) == ""            # 没名字没 URL：跳过，别落个空文件
    assert attachment_name({"url": "https://x/y/report.xlsx?sig=1"}) == "report.xlsx"


def test_channel_file_name_sanitizes_suffix():
    from openminis.plugins.base import channel_file_name

    # 正常后缀保留
    assert channel_file_name("报表.xlsx", prefix="qq").endswith(".xlsx")
    # 可执行后缀一律换成兜底 —— 别让落盘文件名本身成为隐患
    assert channel_file_name("evil.exe", prefix="qq").endswith(".bin")
    # 没有后缀 → 兜底
    assert channel_file_name("README", prefix="qq").endswith(".bin")
    # 图片的兜底是 .jpg（否则引擎认不出是图、前端也不渲染）
    assert channel_file_name("blob", prefix="qq", fallback_suffix=".jpg").endswith(".jpg")


def test_attachment_ref_filter_extracts_and_holds_split_refs():
    from openminis.plugins.bridge import _AttachmentRefFilter

    refs = _AttachmentRefFilter()
    text, found = refs.feed("看图 ![生成图](C:/a/b.p")
    assert text == "看图 "        # 半截引用被压住，不能当正文漏出去
    assert found == []
    text2, found2 = refs.feed("ng)")
    assert text2 == "" and found2 == [("image", "C:/a/b.png")]
    assert refs.flush() == ("", [])

    # 文件引用走同一套
    refs_f = _AttachmentRefFilter()
    text3, found3 = refs_f.feed("报表给你 [附件: 月报.xlsx](C:/a/月报.xlsx) 收好")
    assert found3 == [("file", "C:/a/月报.xlsx")]
    assert text3 == "报表给你  收好"

    # 网络图 / 内联串不是本地文件，原样留着，别去发一个本地路径
    refs2 = _AttachmentRefFilter()
    text4, found4 = refs2.feed("![x](https://example.com/a.png)")
    assert text4 == "![x](https://example.com/a.png)" and found4 == []

    # 收尾：补不齐的半截引用当普通文本吐出来，别吞字
    refs3 = _AttachmentRefFilter()
    refs3.feed("看图 ![半截")
    assert refs3.flush() == ("![半截", [])


@pytest.mark.asyncio
async def test_bridge_ingests_incoming_images(data_dir, monkeypatch):
    """入站图片：落进工作区 uploads/，并以 markdown 路径引用喂给引擎。"""
    from openminis.core import context
    from openminis.server import chat_store, main as server_main

    chat_store.set_database_path(data_dir / "plugins_img_in.db")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    adapter = FakeAdapter(plugin_id="p", config={})
    adapter.attachment_bytes["https://cdn/a.png"] = png
    adapter.attachment_names["https://cdn/a.png"] = "cat.png"
    bridge = ConversationBridge(plugin_id="p", adapter=adapter)
    seen: dict[str, str] = {}

    async def fake_run_chat(client_id, msg):
        seen["text"] = str(msg.get("text") or "")

    monkeypatch.setattr(server_main, "_run_chat", fake_run_chat)
    att = {"content_type": "image/png", "url": "https://cdn/a.png", "filename": "cat.png"}

    await bridge.handle(IncomingMessage(
        scope="c2c", peer_id="u1", sender_id="u1", text="这是什么",
        message_id="m1", attachments=[att],
    ))
    text = seen["text"]
    assert "这是什么" in text
    m = re.search(r"!\[[^\]]*\]\(([^)]+)\)", text)
    assert m, text
    path = Path(m.group(1))
    assert path.is_file() and path.read_bytes() == png
    workspace = context.app_context().external_files_dir.resolve()
    assert workspace in path.resolve().parents     # 必须落在工作区内，否则引擎拒收
    assert "uploads" in path.parts

    # 只发图、一个字都没说 —— 也不该被当成空消息丢掉
    await bridge.handle(IncomingMessage(
        scope="c2c", peer_id="u1", sender_id="u1", text="",
        message_id="m2", attachments=[att],
    ))
    assert seen["text"].startswith("![")


@pytest.mark.asyncio
async def test_bridge_ingests_incoming_files(data_dir, monkeypatch):
    """入站文件：也落工作区，贴成 ``[附件: 名字](路径)``（引擎认这种引用）。"""
    from openminis.server import chat_store, main as server_main

    chat_store.set_database_path(data_dir / "plugins_file_in.db")
    adapter = FakeAdapter(plugin_id="p", config={})
    adapter.attachment_bytes["https://cdn/月报.xlsx"] = b"PK\x03\x04xlsx"
    adapter.attachment_names["https://cdn/月报.xlsx"] = "月报.xlsx"
    bridge = ConversationBridge(plugin_id="p", adapter=adapter)
    seen: dict[str, str] = {}

    async def fake_run_chat(client_id, msg):
        seen["text"] = str(msg.get("text") or "")

    monkeypatch.setattr(server_main, "_run_chat", fake_run_chat)
    await bridge.handle(IncomingMessage(
        scope="c2c", peer_id="u", sender_id="u", text="看下这个",
        attachments=[{
            "content_type": "application/vnd.ms-excel",
            "url": "https://cdn/月报.xlsx", "filename": "月报.xlsx",
        }],
    ))
    text = seen["text"]
    m = re.search(r"\[附件: ([^\]]+)\]\(([^)]+)\)", text)
    assert m, text
    saved = Path(m.group(2))
    assert saved.is_file() and saved.read_bytes() == b"PK\x03\x04xlsx"
    assert saved.suffix == ".xlsx"
    assert "看下这个" in text


@pytest.mark.asyncio
async def test_bridge_refuses_executable_attachments(data_dir, monkeypatch):
    """可执行文件不收 —— 但必须回一句，别让用户以为发出去了。"""
    from openminis.server import chat_store, main as server_main

    chat_store.set_database_path(data_dir / "plugins_exe.db")
    adapter = FakeAdapter(plugin_id="p", config={})
    adapter.attachment_bytes["https://cdn/x.exe"] = b"MZ"
    bridge = ConversationBridge(plugin_id="p", adapter=adapter)
    seen: dict[str, str] = {}

    async def fake_run_chat(client_id, msg):
        seen["text"] = str(msg.get("text") or "")

    monkeypatch.setattr(server_main, "_run_chat", fake_run_chat)
    await bridge.handle(IncomingMessage(
        scope="c2c", peer_id="u", sender_id="u", text="跑一下",
        attachments=[{
            "content_type": "application/octet-stream",
            "url": "https://cdn/x.exe", "filename": "x.exe",
        }],
    ))
    assert seen["text"] == "跑一下"                      # 没被塞进引用
    assert any("不收" in s and "x.exe" in s for s in adapter.sent)


@pytest.mark.asyncio
async def test_bridge_sends_file_refs_from_text(data_dir, monkeypatch):
    """正文里的 ``[附件: 名字](本地路径)`` 也要真发成附件，正文里不留 markdown。"""
    from openminis.server import chat_store, main as server_main

    chat_store.set_database_path(data_dir / "plugins_file_out.db")
    adapter = FakeAdapter(plugin_id="p", config={})
    bridge = ConversationBridge(plugin_id="p", adapter=adapter)
    book = "C:/tmp/report.xlsx"

    async def fake_run_chat(client_id, msg):
        sink = server_main.manager.active[client_id]
        await sink.send_json({"type": "delta", "text": "报表好了："})
        await sink.send_json({"type": "delta", "text": f"[附件: 月报.xlsx]({book})"})
        await sink.send_json({"type": "done"})

    monkeypatch.setattr(server_main, "_run_chat", fake_run_chat)
    await bridge.handle(
        IncomingMessage(scope="c2c", peer_id="u", sender_id="u", text="出个报表")
    )
    assert adapter.files == [book]
    assert adapter.images == []
    assert "[附件" not in "".join(adapter.sent)
    assert "报表好了：" in "".join(adapter.sent)


@pytest.mark.asyncio
async def test_bridge_sends_generated_images_and_strips_refs(data_dir, monkeypatch):
    """出站图片：toolEnd 的 images 与正文里的 markdown 引用都单独发，正文不留 markdown。"""
    from openminis.server import chat_store, main as server_main

    chat_store.set_database_path(data_dir / "plugins_img_out.db")
    adapter = FakeAdapter(plugin_id="p", config={})
    bridge = ConversationBridge(plugin_id="p", adapter=adapter)
    img = "C:/tmp/generated/cat.png"

    async def fake_run_chat(client_id, msg):
        sink = server_main.manager.active[client_id]
        await sink.send_json({"type": "delta", "text": "画好了，见图 "})
        await sink.send_json({
            "type": "toolEnd", "id": "t1", "name": "image_gen", "ok": True,
            "images": [img],
        })
        # 模型自己又写了一遍引用 —— 同一张图不该发两遍
        await sink.send_json({"type": "delta", "text": f"![生成图]({img})"})
        await sink.send_json({"type": "done"})

    monkeypatch.setattr(server_main, "_run_chat", fake_run_chat)
    await bridge.handle(
        IncomingMessage(scope="c2c", peer_id="u", sender_id="u", text="画只猫")
    )

    assert adapter.images == [img]
    joined = "".join(adapter.sent)
    assert "画好了，见图" in joined
    assert "![" not in joined            # markdown 引用不该进聊天正文


@pytest.mark.asyncio
async def test_bridge_falls_back_when_channel_cannot_send_images(data_dir, monkeypatch):
    """平台不支持富媒体时退化成发一条「[图片] 路径」，不能静默丢。"""
    from openminis.server import chat_store, main as server_main

    chat_store.set_database_path(data_dir / "plugins_img_fallback.db")

    class PlainAdapter(FakeAdapter):
        pass

    adapter = PlainAdapter(plugin_id="p", config={})
    # 退回基类实现（不支持图片）
    adapter.send_image = ChannelAdapter.send_image.__get__(adapter, PlainAdapter)
    bridge = ConversationBridge(plugin_id="p", adapter=adapter)
    img = "C:/tmp/generated/cat.png"

    async def fake_run_chat(client_id, msg):
        sink = server_main.manager.active[client_id]
        await sink.send_json({"type": "toolEnd", "id": "t1", "name": "image_gen",
                              "ok": True, "images": [img]})

    monkeypatch.setattr(server_main, "_run_chat", fake_run_chat)
    await bridge.handle(
        IncomingMessage(scope="c2c", peer_id="u", sender_id="u", text="画只猫")
    )
    assert any("[图片]" in s for s in adapter.sent)


@pytest.mark.asyncio
async def test_qq_send_image_uploads_then_sends_rich_media(tmp_path, monkeypatch):
    """QQ 发图必须两步：先 /files 拿 file_info，再 msg_type 7 那条消息。"""
    pic = tmp_path / "a.png"
    pic.write_bytes(b"\x89PNG" + b"1" * 16)

    adapter = QQAdapter(plugin_id="qq", config={"appId": "1", "clientSecret": "s"})
    calls: list[tuple[str, dict]] = []

    async def fake_api(path, *, method="POST", body=None):
        calls.append((path, dict(body or {})))
        return {"file_info": "FI-1", "ttl": 300} if path.endswith("/files") else {}

    monkeypatch.setattr(adapter, "_api", fake_api)
    msg = IncomingMessage(scope="group", peer_id="G1", sender_id="U1", text="",
                          message_id="m7")

    assert await adapter.send_image(msg, pic) is True
    assert calls[0][0] == "/v2/groups/G1/files"
    assert calls[0][1]["file_type"] == 1
    assert calls[0][1]["srv_send_msg"] is False
    assert base64.b64decode(calls[0][1]["file_data"]) == pic.read_bytes()

    assert calls[1][0] == "/v2/groups/G1/messages"
    assert calls[1][1]["msg_type"] == 7
    assert calls[1][1]["media"] == {"file_info": "FI-1"}
    assert calls[1][1]["msg_id"] == "m7" and calls[1][1]["msg_seq"] == 1
    # 图片也占了一个 seq，紧接着的文字回复必须往后排
    assert adapter._reserve_seq(msg, 1) == 2

    # 上传失败 → 如实返回 False 并记日志，不抛给上层
    async def bad_api(path, *, method="POST", body=None):
        raise QQBotError("boom")

    monkeypatch.setattr(adapter, "_api", bad_api)
    assert await adapter.send_image(msg, pic) is False


@pytest.mark.asyncio
async def test_qq_fetch_attachment_downloads_bytes(monkeypatch):
    adapter = QQAdapter(plugin_id="qq", config={"appId": "1", "clientSecret": "s"})
    seen: list[tuple[str, dict]] = []

    class FakeResp:
        status_code = 200
        content = b"PNGDATA"

    class FakeHttp:
        async def get(self, url, headers=None):
            seen.append((url, headers or {}))
            return FakeResp()

    async def fake_token():
        return "tok"

    monkeypatch.setattr(adapter, "_access_token", fake_token)
    monkeypatch.setattr(adapter, "_http", lambda: FakeHttp())

    got = await adapter.fetch_attachment({
        "url": "https://cdn/a.jpg", "filename": "a.jpg", "content_type": "image/jpeg",
    })
    assert got == (b"PNGDATA", "a.jpg")
    assert seen[0][1]["Authorization"] == "QQBot tok"
    assert await adapter.fetch_attachment({}) is None      # 没 URL 就别硬来


@pytest.mark.asyncio
async def test_qq_send_image_converts_webp_to_png(tmp_path, monkeypatch):
    """QQ 只收 png/jpg —— webp 先转码再传，否则平台只会回一句看不懂的失败。"""
    from PIL import Image

    webp = tmp_path / "a.webp"
    Image.new("RGB", (4, 4), (200, 30, 30)).save(webp, format="WEBP")

    adapter = QQAdapter(plugin_id="qq", config={"appId": "1", "clientSecret": "s"})
    seen: list[dict] = []

    async def fake_api(path, *, method="POST", body=None):
        seen.append(dict(body or {}))
        return {"file_info": "FI"} if path.endswith("/files") else {}

    monkeypatch.setattr(adapter, "_api", fake_api)
    msg = IncomingMessage(scope="c2c", peer_id="U1", sender_id="U1", text="")
    assert await adapter.send_image(msg, webp) is True

    raw = base64.b64decode(seen[0]["file_data"])
    assert raw != webp.read_bytes()
    assert Image.open(__import__("io").BytesIO(raw)).format == "PNG"


@pytest.mark.asyncio
async def test_qq_send_file_passes_file_name(tmp_path, monkeypatch):
    """QQ 发文件：file_type=4，且**必须**带 file_name —— 否则客户端显示「未命名」。"""
    doc = tmp_path / "月报.xlsx"
    doc.write_bytes(b"PK\x03\x04" + b"9" * 24)

    adapter = QQAdapter(plugin_id="qq", config={"appId": "1", "clientSecret": "s"})
    calls: list[tuple[str, dict]] = []

    async def fake_api(path, *, method="POST", body=None):
        calls.append((path, dict(body or {})))
        return {"file_info": "FI-FILE"} if path.endswith("/files") else {}

    monkeypatch.setattr(adapter, "_api", fake_api)
    msg = IncomingMessage(scope="c2c", peer_id="U1", sender_id="U1", text="",
                          message_id="m9")

    assert await adapter.send_file(msg, doc) is True
    assert calls[0][0] == "/v2/users/U1/files"
    assert calls[0][1]["file_type"] == 4
    assert calls[0][1]["file_name"] == "月报.xlsx"
    assert calls[0][1]["srv_send_msg"] is False
    assert base64.b64decode(calls[0][1]["file_data"]) == doc.read_bytes()

    assert calls[1][1]["msg_type"] == 7
    assert calls[1][1]["media"] == {"file_info": "FI-FILE"}
    assert calls[1][1]["msg_seq"] == 1

    # 图片那条路不该带 file_name（官方只在文件类型上认它）
    pic = tmp_path / "a.png"
    pic.write_bytes(b"\x89PNG" + b"1" * 8)
    calls.clear()
    assert await adapter.send_image(msg, pic) is True
    assert calls[0][1]["file_type"] == 1
    assert "file_name" not in calls[0][1]


@pytest.mark.asyncio
async def test_qq_warns_when_passive_reply_quota_used_up(tmp_path, monkeypatch):
    """被动回复有次数上限；附件各占一次，逼近时要在日志里说出来。"""
    pic = tmp_path / "a.png"
    pic.write_bytes(b"\x89PNG" + b"1" * 8)

    adapter = QQAdapter(plugin_id="qq", config={"appId": "1", "clientSecret": "s"})

    async def fake_api(path, *, method="POST", body=None):
        return {"file_info": "FI"} if path.endswith("/files") else {}

    monkeypatch.setattr(adapter, "_api", fake_api)
    msg = IncomingMessage(scope="c2c", peer_id="U1", sender_id="U1", text="",
                          message_id="m11")

    for _ in range(4):                       # 单聊上限就是 4
        assert await adapter.send_image(msg, pic) is True
    warns = [row["text"] for row in adapter.logs() if row["level"] == "warn"]
    assert any("官方上限 4" in t for t in warns)


def test_builtin_refresh_keeps_user_config(data_dir, tmp_path, monkeypatch):
    """内置清单升级：refresh 换掉清单、保留用户填的配置。

    这个坑很隐蔽 —— 内置插件装过之后数据目录那份**不会**自动跟着升级，于是
    新加的配置项在界面上永远不出现，用户只看到「我明明装了却没有那一项」。
    """
    fake_builtin = tmp_path / "builtin"
    (fake_builtin / "gg").mkdir(parents=True)
    spec = fake_builtin / "gg" / "plugin.json"
    spec.write_text(json.dumps({
        "id": "gg", "name": "假内置", "runtime": "engine", "driver": "fake",
        "fields": [{"key": "token", "label": "令牌", "type": "password", "secret": True}],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(store, "builtin_dir", lambda: fake_builtin)

    store.install_builtin("gg")
    store.write_config("gg", {"token": "secret-value"})
    assert store.builtin_outdated("gg") is False

    # 包内清单升级（多出一个配置项）
    spec.write_text(json.dumps({
        "id": "gg", "name": "假内置", "runtime": "engine", "driver": "fake",
        "fields": [
            {"key": "token", "label": "令牌", "type": "password", "secret": True},
            {"key": "n", "label": "新项", "type": "text"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    assert store.builtin_outdated("gg") is True

    manifest = store.install_builtin("gg", refresh=True)
    assert [f.key for f in manifest.fields] == ["token", "n"]
    assert store.read_config("gg").get("token") == "secret-value"   # 配置没被抹掉
    assert store.builtin_outdated("gg") is False

    # 不传 refresh 时依旧幂等：不会把配置洗掉，也不会误报有更新
    store.install_builtin("gg")
    assert store.read_config("gg").get("token") == "secret-value"


@pytest.mark.asyncio
async def test_channel_reply_has_no_drive_letter(data_dir, monkeypatch):
    """发到 IM 的正文不出现盘符 —— 对方看到的是沙箱写法。"""
    from openminis.core import context
    from openminis.server import chat_store, main as server_main

    chat_store.set_database_path(data_dir / "plugins_path.db")
    adapter = FakeAdapter(plugin_id="p", config={})
    bridge = ConversationBridge(plugin_id="p", adapter=adapter)
    ws = context.app_context().external_files_dir
    raw = (ws / "image" / "a.png").as_posix()

    async def fake_run_chat(client_id, msg):
        sink = server_main.manager.active[client_id]
        await sink.send_json({"type": "delta", "text": f"图在这儿：{raw}\n"})
        await sink.send_json({"type": "done"})

    monkeypatch.setattr(server_main, "_run_chat", fake_run_chat)
    await bridge.handle(
        IncomingMessage(scope="c2c", peer_id="u", sender_id="u", text="看图")
    )
    joined = "".join(adapter.sent)
    assert "C:" not in joined and str(Path.home()) not in joined
    assert "/var/minis/workspace/image/a.png" in joined


@pytest.mark.asyncio
async def test_channel_media_fallback_uses_sandbox_path(data_dir):
    """平台发不了图时退回发路径文本 —— 那条文本也不带盘符。"""
    from openminis.core import context

    class PlainAdapter(FakeAdapter):
        pass

    adapter = PlainAdapter(plugin_id="p", config={})
    # 退回基类实现（不支持富媒体）
    adapter.send_image = ChannelAdapter.send_image.__get__(adapter, PlainAdapter)
    raw = (context.app_context().external_files_dir / "image" / "b.png").as_posix()

    ok = await adapter.send_image(None, raw)
    assert ok is False
    assert adapter.sent and "C:" not in adapter.sent[0]
    assert adapter.sent[0].startswith("[图片] /var/minis/workspace/")


def test_qq_collects_attachments_from_msg_elements():
    """引用回复/图文混排时附件挂在 msg_elements 里，也要收。

    少收这一处，用户看到的就是「我明明发了图，机器人说没看到」。
    """
    from openminis.plugins.drivers.qq import describe_inbound, normalize_event

    data = {
        "author": {"member_openid": "U1"},
        "group_openid": "G1",
        "content": "<@!BOT1> 识图",
        "id": "m1",
        "msg_elements": [
            {"msg_idx": "REFIDX_a", "content": "看这张"},
            {"msg_idx": "REFIDX_b",
             "attachments": [{"content_type": "image/jpeg", "url": "https://cdn/a.jpg",
                              "filename": "a.jpg"}]},
        ],
    }
    msg = normalize_event("GROUP_AT_MESSAGE_CREATE", data)
    assert msg is not None
    assert len(msg.attachments) == 1
    assert msg.attachments[0]["url"] == "https://cdn/a.jpg"
    assert msg.text == "识图"                       # @ 占位符被清掉
    info = describe_inbound(data)
    assert "附件 1" in info and "msg_elements 2" in info


def test_qq_content_mention_placeholder_is_stripped():
    from openminis.plugins.drivers.qq import normalize_event

    msg = normalize_event("C2C_MESSAGE_CREATE", {
        "author": {"user_openid": "U"},
        "content": "<@!E4F4AEA33253A2797FB897C50B81D7ED>   帮我看看",
        "id": "m2",
    })
    assert msg is not None
    assert msg.text == "帮我看看"
    assert "<@" not in msg.text


def test_describe_inbound_says_when_nothing_attached():
    """排障靠它：一眼看出是平台没推还是我们没认。"""
    from openminis.plugins.drivers.qq import describe_inbound

    info = describe_inbound({"content": "识图", "id": "m3"})
    assert "附件 0(无)" in info


def test_attachment_image_accepts_bare_image_type():
    """有的平台 content_type 只给大类 ``image``。"""
    assert attachment_is_image({"content_type": "image"}) is True
    assert attachment_is_image({"content_type": "voice"}) is False


# ---------------------------------------------------------------------------
# 「干了几分钟，结果发不回去」
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_qq_switches_to_proactive_after_window_expires(data_dir, monkeypatch):
    """被动回复窗口过了就改走主动消息 —— 否则平台静默拒收，用户只看到机器人装死。

    现场：群里 @ 一张图 → 识图几十秒、模型还反复调 → 等要发时已过群聊的 5 分钟
    上限 → 带 msg_id 的回复被丢弃。
    """
    import time as _time

    adapter = QQAdapter(plugin_id="qq", config={"appId": "1", "clientSecret": "s"})
    seen: list[dict] = []

    async def fake_api(path, *, method="POST", body=None):
        seen.append(dict(body or {}))
        return {}

    monkeypatch.setattr(adapter, "_api", fake_api)

    fresh = IncomingMessage(scope="group", peer_id="G1", sender_id="U1", text="x",
                            message_id="m1", received_at=_time.time())
    await adapter.send_text(fresh, "结果")
    assert seen[-1]["msg_id"] == "m1" and seen[-1]["msg_seq"] == 1

    stale = IncomingMessage(scope="group", peer_id="G1", sender_id="U1", text="x",
                            message_id="m2", received_at=_time.time() - 400)
    seen.clear()
    await adapter.send_text(stale, "迟到的结果")
    assert "msg_id" not in seen[-1]          # 主动消息：不带 msg_id / msg_seq
    assert "msg_seq" not in seen[-1]
    assert any("被动回复窗口已过" in row["text"] for row in adapter.logs())

    # 单聊窗口长得多（60 分钟），同样的时间差仍然是正常被动回复
    c2c = IncomingMessage(scope="c2c", peer_id="U1", sender_id="U1", text="x",
                          message_id="m3", received_at=_time.time() - 400)
    seen.clear()
    await adapter.send_text(c2c, "结果")
    assert seen[-1]["msg_id"] == "m3"


@pytest.mark.asyncio
async def test_qq_resends_proactively_when_passive_send_fails(data_dir, monkeypatch):
    """窗口刚好在发送前一刻关掉 → 去掉 msg_id 再发一次。"""
    adapter = QQAdapter(plugin_id="qq", config={"appId": "1", "clientSecret": "s"})
    calls: list[dict] = []

    async def fake_api(path, *, method="POST", body=None):
        body = dict(body or {})
        calls.append(body)
        if "msg_id" in body:
            raise QQBotError("QQ 接口 400：msg_id 已过期", status=400)
        return {}

    monkeypatch.setattr(adapter, "_api", fake_api)
    msg = IncomingMessage(scope="group", peer_id="G1", sender_id="U1", text="x",
                          message_id="m1")
    await adapter.send_text(msg, "结果")
    assert len(calls) == 2
    assert "msg_id" in calls[0] and "msg_id" not in calls[1]
    assert any("主动消息补发成功" in row["text"] for row in adapter.logs())


@pytest.mark.asyncio
async def test_bridge_refreshes_typing_while_running(data_dir, monkeypatch):
    """跑一轮期间持续刷新「正在输入」—— 平台的输入状态只活几秒。"""
    count = {"n": 0}

    class TypingAdapter(FakeAdapter):
        async def send_typing(self, msg):
            count["n"] += 1

    adapter = TypingAdapter(plugin_id="p", config={"typingIntervalSec": 1})
    bridge = ConversationBridge(plugin_id="p", adapter=adapter)
    msg = IncomingMessage(scope="c2c", peer_id="u", sender_id="u", text="看图")

    task = bridge._start_typing_heartbeat(msg)
    assert task is not None
    await asyncio.sleep(2.6)
    task.cancel()
    assert count["n"] >= 2          # 至少刷了两次，不是开头打一下就没了


@pytest.mark.asyncio
async def test_bridge_delivers_images_the_model_forgot_to_send(data_dir, monkeypatch):
    """模型没调 send、也没写引用时，本轮新出的图也要送到用户手里。

    现场：技能脚本生图（2 分 06 秒那条 shell）成功、read_image 也成功，但整轮
    **没有任何 send** —— 用户在 QQ 那侧只看到「画好了」，一张图都没有。
    """
    import time as _time
    from openminis.core import context

    adapter = FakeAdapter(plugin_id="p", config={})
    bridge = ConversationBridge(plugin_id="p", adapter=adapter)

    ws = context.app_context().external_files_dir
    # 技能脚本会把图写进子目录（魔搭写 image/modelscope/）
    out_dir = ws / "image" / "modelscope"
    out_dir.mkdir(parents=True, exist_ok=True)
    img = out_dir / "made_by_skill.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0")
    started = _time.time() - 1

    msg = IncomingMessage(scope="c2c", peer_id="u", sender_id="u", text="画一只猫")
    sent: set[str] = set()
    await bridge._deliver_new_images(msg, started, sent)
    assert adapter.images == [str(img)], adapter.images
    assert str(img) in sent

    # 已经发过的（正文引用 / toolEnd 帧发过）不能重复发
    await bridge._deliver_new_images(msg, started, sent)
    assert adapter.images == [str(img)]


def test_media_scan_is_recursive(data_dir):
    """扫描要递归 —— 魔搭把图写在 image/modelscope/ 子目录里。"""
    import time as _time
    from openminis.core import context
    from openminis.server.media_scan import collect_recent_images

    root = Path(context.app_context().external_files_dir)
    nested = root / "image" / "modelscope"
    nested.mkdir(parents=True, exist_ok=True)
    img = nested / "sub_dir_image.jpg"
    img.write_bytes(b"x")
    found = collect_recent_images(since=_time.time() - 5)
    assert str(img) in found, found


@pytest.mark.asyncio
async def test_bridge_tops_up_newest_when_model_delivered_a_stale_image(data_dir):
    """模型发错图时的兜底：本轮没有新产物、但它已经交付过 → 把**最新那张**补上。

    现场：用户说「发过来」，模型从会话历史里抄了上一轮的路径（几个钟头前那张），
    而本轮压根没有新落盘的图 —— 只按「本轮新图」扫是扫不到的，用户就只收到一张
    错图。既然模型已经表露了交付意图，把最近的那张也送到。
    """
    from openminis.core import context

    adapter = FakeAdapter(plugin_id="p", config={})
    bridge = ConversationBridge(plugin_id="p", adapter=adapter)

    out_dir = context.app_context().external_files_dir / "image" / "modelscope"
    out_dir.mkdir(parents=True, exist_ok=True)
    stale = out_dir / "yesterday.jpg"
    stale.write_bytes(b"\xff\xd8\xff\xe0")
    newest = out_dir / "just_now.jpg"
    newest.write_bytes(b"\xff\xd8\xff\xe0")
    # 真实的「昨天的图」和「刚生成的图」差着几个钟头；同一秒落盘会让 mtime 打平，
    # 排序退化 —— 这里把时间差显式造出来。
    import os

    old = time.time() - 3600
    os.utime(stale, (old, old))

    msg = IncomingMessage(scope="c2c", peer_id="u", sender_id="u", text="发过来")
    sent = {str(stale)}                      # 模型交付了那张旧的
    await bridge._deliver_new_images(msg, time.time(), sent)
    assert adapter.images == [str(newest)], adapter.images


@pytest.mark.asyncio
async def test_bridge_does_not_top_up_when_nothing_was_delivered(data_dir):
    """本轮没有产物、模型也没有交付意图 → 什么都别补（否则闲聊也会蹦出图）。"""
    from openminis.core import context

    adapter = FakeAdapter(plugin_id="p", config={})
    bridge = ConversationBridge(plugin_id="p", adapter=adapter)
    out_dir = context.app_context().external_files_dir / "image" / "modelscope"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "old.jpg").write_bytes(b"\xff\xd8\xff\xe0")

    msg = IncomingMessage(scope="c2c", peer_id="u", sender_id="u", text="在吗")
    await bridge._deliver_new_images(msg, time.time(), set())
    assert adapter.images == []


@pytest.mark.asyncio
async def test_bridge_only_blames_capability_when_adapter_cannot_send_media(data_dir):
    """适配器**实现了**富媒体、只是这次失败（窗口/权限）时，别把日志写成
    「这个通道不支持直接发附件」—— 那句话把平台问题说成了能力问题，误导排障。
    """
    from openminis.core import context

    class FailingMedia(FakeAdapter):
        async def send_image(self, msg, path, **kw):
            return False          # 实现了，但这次发不出去

    adapter = FailingMedia(plugin_id="p", config={})
    bridge = ConversationBridge(plugin_id="p", adapter=adapter)
    img = context.app_context().external_files_dir / "image" / "x.jpg"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"\xff\xd8\xff\xe0")

    msg = IncomingMessage(scope="c2c", peer_id="u", sender_id="u", text="x")
    await bridge._send_attachments(msg, [("image", str(img))], set())
    logs = "".join(str(row["text"]) for row in adapter.logs())
    assert "不支持直接发附件" not in logs

    # 而真正「没实现」的适配器还是要说明白（基类默认实现会退回发路径文本）
    class PlainAdapter(ChannelAdapter):
        """只实现文字，不实现富媒体 —— 基类的 send_image 会退回发路径文本。"""

        driver = "plain"

        async def start(self):
            pass

        async def stop(self):
            pass

        async def send_text(self, msg, text, **kw):
            self.texts = getattr(self, "texts", [])
            self.texts.append(text)

    plain = PlainAdapter(plugin_id="p", config={})
    bridge2 = ConversationBridge(plugin_id="p", adapter=plain)
    await bridge2._send_attachments(msg, [("image", str(img))], set())
    logs2 = "".join(str(row["text"]) for row in plain.logs())
    assert "不支持直接发附件" in logs2


# ---------------------------------------------------------------------------
# 「先响应，再干活」
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_bridge_acks_before_running_the_turn(data_dir, monkeypatch):
    """收到消息立刻回一句 —— 否则一轮跑几分钟，用户以为消息发丢了。

    现场：群里 @ 机器人生图，屏幕上什么都没有（「正在输入」只活几秒、群里也不
    显眼），用户中途又发了一条「发过来」。
    """
    from openminis.server import chat_store, main as server_main

    chat_store.set_database_path(data_dir / "ack.db")
    adapter = FakeAdapter(plugin_id="p", config={})
    bridge = ConversationBridge(plugin_id="p", adapter=adapter)

    async def fake_run_chat(client_id, msg):
        # 模仿「干活很慢」：此时回执应该已经发出去了。
        assert adapter.sent and adapter.sent[0] == "收到，正在处理…"

    monkeypatch.setattr(server_main, "_run_chat", fake_run_chat)
    await bridge.handle(
        IncomingMessage(scope="c2c", peer_id="u", sender_id="u", text="画一只猫")
    )
    assert adapter.sent[0] == "收到，正在处理…"
    logs = "".join(str(row["text"]) for row in adapter.logs())
    assert "已回执" in logs


@pytest.mark.asyncio
async def test_bridge_ack_is_configurable_and_skips_commands(data_dir, monkeypatch):
    from openminis.server import chat_store, main as server_main

    chat_store.set_database_path(data_dir / "ack2.db")

    async def fake_run_chat(client_id, msg):
        return None

    monkeypatch.setattr(server_main, "_run_chat", fake_run_chat)

    # 关掉：一条回执都不发
    off = FakeAdapter(plugin_id="p", config={"ackText": ""})
    await ConversationBridge(plugin_id="p", adapter=off).handle(
        IncomingMessage(scope="c2c", peer_id="u", sender_id="u", text="在吗")
    )
    assert "收到，正在处理…" not in off.sent

    # 自定义文案
    custom = FakeAdapter(plugin_id="p", config={"ackText": "稍等，我看一下"})
    await ConversationBridge(plugin_id="p", adapter=custom).handle(
        IncomingMessage(scope="c2c", peer_id="u", sender_id="u", text="在吗")
    )
    assert custom.sent[0] == "稍等，我看一下"

    # 斜杠命令秒回，不需要回执（纯噪音）
    cmd = FakeAdapter(plugin_id="p", config={})
    await ConversationBridge(plugin_id="p", adapter=cmd).handle(
        IncomingMessage(scope="c2c", peer_id="u", sender_id="u", text="/help")
    )
    assert "收到，正在处理…" not in cmd.sent
    assert cmd.sent                       # 但 /help 自己得有回复


@pytest.mark.asyncio
async def test_qq_ack_gives_way_to_the_real_result():
    """额度不足时放弃回执 —— 不能为了「收到」把结果挤掉（群聊只有 5 次）。"""
    from openminis.plugins.drivers.qq import QQAdapter

    adapter = QQAdapter(plugin_id="qq", config={"appId": "1", "clientSecret": "s"})
    sent: list[str] = []

    async def fake_api(path, *, method="POST", body=None):
        sent.append(str((body or {}).get("content") or ""))
        return {}

    adapter._api = fake_api  # type: ignore[assignment]
    msg = IncomingMessage(scope="group", peer_id="G1", sender_id="U1", text="x",
                          message_id="m1", received_at=time.time())
    assert await adapter.send_ack(msg, "收到，正在处理…") is True
    assert sent == ["收到，正在处理…"]

    # 已经回掉了多半额度（5 次里用掉 3 次）→ 回执让位
    sent.clear()
    adapter._replies["m2"] = (3, time.time())
    msg2 = IncomingMessage(scope="group", peer_id="G1", sender_id="U1", text="x",
                           message_id="m2", received_at=time.time())
    assert await adapter.send_ack(msg2, "收到，正在处理…") is False
    assert sent == []
    logs = "".join(str(row["text"]) for row in adapter.logs())
    assert "留给结果" in logs
