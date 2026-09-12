"""Settings: provider/identity store + REST API + chat assembly."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openminis.data.model import LLMMessage, LLMStreamChunk
from openminis.server.main import app
from openminis.settings import chat_service
from openminis.settings.chat_service import ChatSetupError, build_chat_setup
from openminis.settings.store import SettingsStore


@pytest.fixture()
def store(tmp_path, monkeypatch):
    s = SettingsStore(path=tmp_path / "settings.json")
    monkeypatch.setattr(SettingsStore, "get", classmethod(lambda cls: s))
    return s


def _put(client: TestClient, **payload) -> dict:
    r = client.put("/api/settings", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# store / REST
# ---------------------------------------------------------------------------
def test_default_settings_have_catalog(store):
    with TestClient(app) as c:
        body = c.get("/api/settings").json()
    assert body["activeIdentityId"] == "assistant"
    assert body["activeProviderId"] is None
    assert len(body["identities"]) == 4
    # no vendor instance configured yet — the catalog of *types* is separate
    assert body["providers"] == []
    assert len(body["providerTypes"]) == 6
    assert any(t["type"] == "anthropic" and t["engine"] == "anthropic"
               for t in body["providerTypes"])
    assert [t["id"] for t in body["toolCatalog"]] == [
        "shell_execute",
        "file_read",
        "file_write",
        "file_edit",
        "read_image",
        "image_gen",
        "browser_use",
        "memory_write",
        "memory_get",
        "ls",
        "search_files",
        "web_fetch",
        "web_search",
        "subagent_delegate",
        "skill_use",
    ]


def test_put_provider_activate_identity(store):
    with TestClient(app) as c:
        body = _put(
            c,
            providers=[{"type": "anthropic", "apiKey": "sk-secret-1",
                        "model": "claude-sonnet-5", "baseUrl": ""}],
            activeProviderId="anthropic",
            activeIdentityId="coder",
            identityEdits=[],
        )
        anthropic = next(p for p in body["providers"] if p["type"] == "anthropic")
        assert anthropic["hasKey"] is True
        assert anthropic["isActive"] is True
        assert "apiKey" not in anthropic  # secrets are never echoed
        assert body["activeIdentityId"] == "coder"
        # persisted with the secret intact
        assert store.load()["providers"]["anthropic"]["apiKey"] == "sk-secret-1"


def test_multiple_instances_of_same_type(store):
    """Two OpenAI-compatible gateways must coexist as separate instances."""
    with TestClient(app) as c:
        body = _put(
            c,
            providers=[
                {"id": "gw-a", "type": "openAI", "label": "网关A",
                 "apiKey": "sk-a", "model": "gpt-4o", "baseUrl": "https://a/v1"},
                {"id": "gw-b", "type": "openAI", "label": "网关B",
                 "apiKey": "sk-b", "model": "qwen-max", "baseUrl": "https://b/v1"},
            ],
            activeProviderId="gw-b",
        )
        assert [p["id"] for p in body["providers"]] == ["gw-a", "gw-b"]
        assert [p["type"] for p in body["providers"]] == ["openAI", "openAI"]
        assert [p["label"] for p in body["providers"]] == ["网关A", "网关B"]
        assert [p["isActive"] for p in body["providers"]] == [False, True]
    saved = store.load()["providers"]
    assert saved["gw-a"]["apiKey"] == "sk-a"
    assert saved["gw-b"]["apiKey"] == "sk-b"
    assert saved["gw-a"]["baseUrl"] == "https://a/v1"
    assert store.load()["activeProviderId"] == "gw-b"


def test_duplicate_instance_id_is_rejected(store):
    with TestClient(app) as c:
        r = c.put("/api/settings", json={
            "providers": [
                {"id": "gw-a", "type": "openAI", "apiKey": "1", "model": "", "baseUrl": ""},
                {"id": "gw-a", "type": "openAI", "apiKey": "2", "model": "", "baseUrl": ""},
            ],
        })
        assert r.status_code == 400


def test_blank_key_on_update_keeps_secret(store):
    with TestClient(app) as c:
        _put(c, providers=[{"type": "anthropic", "apiKey": "sk-a", "model": "", "baseUrl": ""}])
        _put(c, providers=[{"type": "anthropic", "apiKey": "", "model": "", "baseUrl": ""}])
    assert store.load()["providers"]["anthropic"]["apiKey"] == "sk-a"


def test_activating_unconfigured_provider_is_rejected(store):
    with TestClient(app) as c:
        r = c.put("/api/settings", json={"activeProviderId": "anthropic"})
        assert r.status_code == 400


def test_identity_tool_override(store):
    with TestClient(app) as c:
        body = _put(c, identityEdits=[{"id": "writer", "enabledTools": ["file_read"]}])
        writer = next(i for i in body["identities"] if i["id"] == "writer")
        assert writer["enabledTools"] == ["file_read"]


def test_custom_identity_roundtrip(store):
    with TestClient(app) as c:
        body = _put(
            c,
            activeIdentityId="translator",
            customIdentities=[{
                "id": "translator", "name": "译者", "emoji": "🌐",
                "description": "中英互译",
                "persona": "你是专业译者,输出地道、忠实。",
                "enabledTools": [],
            }],
        )
        ids = [i["id"] for i in body["identities"]]
        assert "translator" in ids
        assert body["activeIdentityId"] == "translator"


# ---------------------------------------------------------------------------
# chat assembly
# ---------------------------------------------------------------------------
def test_chat_setup_without_provider_raises(store):
    with pytest.raises(ChatSetupError):
        build_chat_setup(store)


def test_chat_setup_engine_not_ported(store):
    store.apply_full({
        "providers": [{"type": "gemini", "apiKey": "k", "model": "", "baseUrl": ""}],
        "activeProviderId": "gemini",
    })
    with pytest.raises(ChatSetupError, match="尚未移植"):
        build_chat_setup(store)


class _FakeProvider:
    """Text-only fake; never touches the network."""

    def stream_message(self, messages, system_prompt=None, max_tokens=0,
                       temperature=None, image_parts=None, tools=None,
                       thinking_level=None):
        async def gen():
            yield LLMStreamChunk.Text("hi from fake provider")
            yield LLMStreamChunk.Finished("end_turn")
        return gen()


@pytest.mark.asyncio
async def test_chat_setup_roundtrip_with_fake_engine(store, monkeypatch):
    store.apply_full({
        "providers": [{"type": "anthropic", "apiKey": "sk-x", "model": "claude-sonnet-5", "baseUrl": ""}],
        "activeProviderId": "anthropic",
        "activeIdentityId": "coder",
    })
    monkeypatch.setattr(chat_service, "build_provider", lambda t, c: _FakeProvider())
    provider, runtime, options, identity, conf = build_chat_setup(store)
    assert identity.id == "coder"
    assert sorted(runtime.tools) == [
        "file_edit",
        "file_read",
        "file_write",
        "image_gen",
        "ls",
        "memory_get",
        "memory_write",
        "read_image",
        "search_files",
        "shell_execute",
        "skill_use",
        "subagent_delegate",
        "web_fetch",
        "web_search",
    ]
    assert options.system_prompt  # identity persona injected

    messages = [LLMMessage(LLMMessage.Role.USER, "hello")]
    await runtime.run(provider, messages, session_id="test-s", options=options)
    from openminis.data.model.agent_content_part import Text

    joined = "".join(
        p.text for p in messages[-1].content_parts if isinstance(p, Text)
    )
    assert "hi from fake provider" in joined


# ---------------------------------------------------------------------------
# WebSocket: unconfigured chat points the user to settings
# ---------------------------------------------------------------------------
def test_ws_chat_without_provider_guides_user(store, isolated_chat_db):
    with TestClient(app) as c:
        with c.websocket_connect("/ws") as ws:
            ws.send_json({"type": "chat", "text": "hi"})
            # no session yet → server creates one and announces it
            first = ws.receive_json()
            assert first["type"] == "chatSession"
            assert first["sessionId"]
            second = ws.receive_json()
            assert second["type"] == "delta"
            assert "模型服务" in second["text"]
            third = ws.receive_json()
            assert third["type"] == "done"
            assert third.get("sessionId") == first["sessionId"]


# ---------------------------------------------------------------------------
# custom / remote models (自定义模型 + 按 Base URL 拉取模型列表)
# ---------------------------------------------------------------------------
def test_custom_model_id_is_preserved(store):
    """A model id outside the static catalog must be used verbatim, not
    silently replaced by an engine default."""
    from openminis.settings.chat_service import build_provider

    store.apply_full({
        "providers": [{
            "type": "openAI", "apiKey": "sk-x",
            "model": "my-gw/qwen-max-custom", "baseUrl": "",
        }],
    })
    provider = build_provider("openAI", store.load()["providers"]["openAI"])
    assert provider.model.id == "my-gw/qwen-max-custom"


def _model_server():
    """Local HTTP server advertising fake models on /models and /v1/models."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path == "/secret/models":
                status = 401
                body = b""
            elif self.path == "/v1/models":
                body = json.dumps({"data": [{"id": "gw-v1-a"}, {"id": "gw-v1-b"}]}).encode()
                status = 200
            elif self.path == "/models":
                body = json.dumps({"data": [{"id": "gw-plain-1"}]}).encode()
                status = 200
            else:
                status = 404
                body = b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # noqa: ANN002
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


@pytest.mark.asyncio
async def test_fetch_remote_models_hits_endpoints():
    from openminis.settings.remote_models import (
        ModelsFetchError,
        fetch_remote_models,
    )

    srv, port = _model_server()
    try:
        r = await fetch_remote_models("openAI", f"http://127.0.0.1:{port}/v1", "sk-x")
        assert r.models == ["gw-v1-a", "gw-v1-b"]
        assert r.source.endswith("/v1/models")

        r = await fetch_remote_models("openAI", f"http://127.0.0.1:{port}", "sk-x")
        assert r.models == ["gw-plain-1"]
        assert r.source.endswith("/models")

        with pytest.raises(ModelsFetchError, match="认证失败"):
            await fetch_remote_models(
                "openAI", f"http://127.0.0.1:{port}/secret", "bad-key"
            )
    finally:
        srv.shutdown()


@pytest.mark.asyncio
async def test_fetch_models_api_endpoint(store):
    """POST /api/settings/fetch-models persists hints and GET merges them."""
    store.apply_full({
        "providers": [{"type": "openAI", "apiKey": "sk-x", "model": "", "baseUrl": ""}],
    })
    srv, port = _model_server()
    try:
        with TestClient(app) as c:
            r = c.post("/api/settings/fetch-models", json={
                "type": "openAI",
                "baseUrl": f"http://127.0.0.1:{port}/v1",
                "apiKey": "",  # blank → falls back to stored secret
            })
            assert r.status_code == 200, r.text
            assert r.json()["models"] == ["gw-v1-a", "gw-v1-b"]

            view = c.get("/api/settings").json()
            openai = next(p for p in view["providers"] if p["type"] == "openAI")
            ids = [m["id"] for m in openai["models"]]
            assert "gw-v1-a" in ids  # remote hint merged after built-ins
            remote = [m for m in openai["models"] if m["id"] == "gw-v1-a"][0]
            assert "远程" in remote["name"]
    finally:
        srv.shutdown()


def test_agent_defaults_and_config_helper(store):
    cfg = store.agent_config()
    assert cfg["maxContextTokens"] == 511998
    assert cfg["maxMemoryRounds"] == 30
    assert cfg["maxToolSteps"] == 40
    assert cfg["deepThinking"] is False


def test_apply_full_agent_partial_merge_and_clamp(store):
    out = store.apply_full({
        "agent": {"maxToolSteps": 7, "deepThinking": True},
    })
    cfg = out["agent"]
    assert cfg["maxToolSteps"] == 7
    assert cfg["deepThinking"] is True
    # unset keys keep defaults (partial merge)
    assert cfg["maxContextTokens"] == 511998
    assert cfg["maxMemoryRounds"] == 30
    # out-of-range values clamp instead of failing the whole save
    out2 = store.apply_full({"agent": {"maxContextTokens": 10 ** 12}})
    assert out2["agent"]["maxContextTokens"] == 100_000_000
    # non-numeric values fail loudly (whole-save 400) so the UI surfaces them
    import pytest as _pytest

    from openminis.settings.store import SettingsError

    with _pytest.raises(SettingsError):
        store.apply_full({"agent": {"maxMemoryRounds": "not-a-number"}})


# ---------------------------------------------------------------------------
# model override (scheduled tasks pin a model)
# ---------------------------------------------------------------------------
def test_chat_setup_model_override_does_not_rewrite_stored_choice(store, monkeypatch):
    """``instance_id`` / ``model_id`` 只作用于本次调用。

    定时任务靠它钉住模型,所以这条路径必须能在不碰用户配置的前提下换模型 ——
    如果就地改了 ``conf``(它是 live settings dict 里的那个对象),用户「当前
    模型」会在半夜被一个定时任务悄悄改掉,而且毫无痕迹。
    """
    store.apply_full({
        "providers": [
            {"id": "openAI", "type": "openAI", "apiKey": "k",
             "model": "gpt-5.2", "baseUrl": ""},
            {"id": "anthropic", "type": "anthropic", "apiKey": "k",
             "model": "claude-sonnet-5", "baseUrl": ""},
        ],
        "activeProviderId": "openAI",
    })
    seen: dict = {}

    def _fake_build(provider_id, conf):
        seen["provider_id"] = provider_id
        seen["conf"] = dict(conf)
        return _FakeProvider()

    monkeypatch.setattr(chat_service, "build_provider", _fake_build)

    _, _, _, _, conf = build_chat_setup(
        store, instance_id="anthropic", model_id="claude-opus-4-8"
    )
    assert seen["provider_id"] == "anthropic"
    assert conf["model"] == "claude-opus-4-8"
    assert conf["type"] == "anthropic"

    # 存盘里的选择原封不动
    saved = store.load()["providers"]
    assert saved["openAI"]["model"] == "gpt-5.2"
    assert saved["anthropic"]["model"] == "claude-sonnet-5"


def test_chat_setup_model_override_without_instance_uses_active(store, monkeypatch):
    """只给 model_id 时,实例仍是活动实例 —— 钉的是模型,不是厂商。"""
    store.apply_full({
        "providers": [
            {"id": "openAI", "type": "openAI", "apiKey": "k",
             "model": "gpt-5.2", "baseUrl": ""},
        ],
        "activeProviderId": "openAI",
    })
    seen: dict = {}

    def _fake_build(provider_id, conf):
        seen["provider_id"] = provider_id
        seen["conf"] = dict(conf)
        return _FakeProvider()

    monkeypatch.setattr(chat_service, "build_provider", _fake_build)
    build_chat_setup(store, model_id="agnes-2.5-flash")

    assert seen["provider_id"] == "openAI"
    assert seen["conf"]["model"] == "agnes-2.5-flash"
    assert store.load()["providers"]["openAI"]["model"] == "gpt-5.2"


def test_chat_setup_unknown_instance_raises(store):
    store.apply_full({
        "providers": [{"id": "openAI", "type": "openAI", "apiKey": "k",
                       "model": "gpt-5.2", "baseUrl": ""}],
        "activeProviderId": "openAI",
    })
    with pytest.raises(ChatSetupError):
        build_chat_setup(store, instance_id="nope")


# ---------------------------------------------------------------------------
# attribution snapshot
# ---------------------------------------------------------------------------
def test_attribution_snapshot_records_the_overridden_model(store):
    """快照必须记「这次真正服务的模型」,不是活动模型 —— 定时任务钉了模型时,
    两者不同,而用量页要按实际发生的算。"""
    from openminis.settings.chat_service import attribution_snapshot

    conf = {"id": "openAI", "type": "openAI", "model": "agnes-2.5-flash"}
    snap = attribution_snapshot(store, conf)
    assert snap["model_id"] == "agnes-2.5-flash"
    assert snap["provider_type"] == "openAI"
    assert snap["provider_instance_id"] == "openAI"
    # 不在目录里的自定义模型保留原始 id 作为显示名,而不是塌成 None
    assert snap["model_display_name"] == "agnes-2.5-flash"


def test_attribution_snapshot_falls_back_to_active_instance(store):
    """conf 里没有 id 时退回活动实例 id —— 快照少了实例 id 就没法把用量
    归到具体厂商,而这是「同一模型挂在两个网关上」时唯一能区分的字段。"""
    from openminis.settings.chat_service import attribution_snapshot

    store.apply_full({
        "providers": [{"id": "openAI", "type": "openAI", "apiKey": "k",
                       "model": "gpt-5.2", "baseUrl": ""}],
        "activeProviderId": "openAI",
    })
    snap = attribution_snapshot(store, {"type": "openAI", "model": "gpt-5.2"})
    assert snap["provider_instance_id"] == "openAI"


# ---------------------------------------------------------------------------
# model override (scheduled tasks pin a model)
# ---------------------------------------------------------------------------
def test_chat_setup_model_override_does_not_rewrite_stored_choice(store, monkeypatch):
    """``instance_id`` / ``model_id`` 只作用于本次调用。

    定时任务靠它钉住模型,所以这条路径必须能在不碰用户配置的前提下换模型 ——
    如果就地改了 ``conf``(它是 live settings dict 里的那个对象),用户「当前
    模型」会在半夜被一个定时任务悄悄改掉,而且毫无痕迹。
    """
    store.apply_full({
        "providers": [
            {"id": "openAI", "type": "openAI", "apiKey": "k",
             "model": "gpt-5.2", "baseUrl": ""},
            {"id": "anthropic", "type": "anthropic", "apiKey": "k",
             "model": "claude-sonnet-5", "baseUrl": ""},
        ],
        "activeProviderId": "openAI",
    })
    seen: dict = {}

    def _fake_build(provider_id, conf):
        seen["provider_id"] = provider_id
        seen["conf"] = dict(conf)
        return _FakeProvider()

    monkeypatch.setattr(chat_service, "build_provider", _fake_build)

    _, _, _, _, conf = build_chat_setup(
        store, instance_id="anthropic", model_id="claude-opus-4-8"
    )
    assert seen["provider_id"] == "anthropic"
    assert conf["model"] == "claude-opus-4-8"
    assert conf["type"] == "anthropic"

    # 存盘里的选择原封不动
    saved = store.load()["providers"]
    assert saved["openAI"]["model"] == "gpt-5.2"
    assert saved["anthropic"]["model"] == "claude-sonnet-5"


def test_chat_setup_model_override_without_instance_uses_active(store, monkeypatch):
    """只给 model_id 时,实例仍是活动实例 —— 钉的是模型,不是厂商。"""
    store.apply_full({
        "providers": [
            {"id": "openAI", "type": "openAI", "apiKey": "k",
             "model": "gpt-5.2", "baseUrl": ""},
        ],
        "activeProviderId": "openAI",
    })
    seen: dict = {}

    def _fake_build(provider_id, conf):
        seen["provider_id"] = provider_id
        seen["conf"] = dict(conf)
        return _FakeProvider()

    monkeypatch.setattr(chat_service, "build_provider", _fake_build)
    build_chat_setup(store, model_id="agnes-2.5-flash")

    assert seen["provider_id"] == "openAI"
    assert seen["conf"]["model"] == "agnes-2.5-flash"
    assert store.load()["providers"]["openAI"]["model"] == "gpt-5.2"


def test_chat_setup_unknown_instance_raises(store):
    store.apply_full({
        "providers": [{"id": "openAI", "type": "openAI", "apiKey": "k",
                       "model": "gpt-5.2", "baseUrl": ""}],
        "activeProviderId": "openAI",
    })
    with pytest.raises(ChatSetupError):
        build_chat_setup(store, instance_id="nope")


# ---------------------------------------------------------------------------
# attribution snapshot
# ---------------------------------------------------------------------------
def test_attribution_snapshot_records_the_overridden_model(store):
    """快照必须记「这次真正服务的模型」,不是活动模型 —— 定时任务钉了模型时,
    两者不同,而用量页要按实际发生的算。"""
    from openminis.settings.chat_service import attribution_snapshot

    conf = {"id": "openAI", "type": "openAI", "model": "agnes-2.5-flash"}
    snap = attribution_snapshot(store, conf)
    assert snap["model_id"] == "agnes-2.5-flash"
    assert snap["provider_type"] == "openAI"
    assert snap["provider_instance_id"] == "openAI"
    # 不在目录里的自定义模型保留原始 id 作为显示名,而不是塌成 None
    assert snap["model_display_name"] == "agnes-2.5-flash"


def test_attribution_snapshot_falls_back_to_active_instance(store):
    """conf 里没有 id 时退回活动实例 id —— 快照少了实例 id 就没法把用量
    归到具体厂商,而这是「同一模型挂在两个网关上」时唯一能区分的字段。"""
    from openminis.settings.chat_service import attribution_snapshot

    store.apply_full({
        "providers": [{"id": "openAI", "type": "openAI", "apiKey": "k",
                       "model": "gpt-5.2", "baseUrl": ""}],
        "activeProviderId": "openAI",
    })
    snap = attribution_snapshot(store, {"type": "openAI", "model": "gpt-5.2"})
    assert snap["provider_instance_id"] == "openAI"


# ---------------------------------------------------------------------------
# 模型类型（能力标签）+ 用途分槽
# ---------------------------------------------------------------------------
def test_model_types_roundtrip_and_clear(store):
    """手动的模型用途覆盖:保存→读回;传空 map 清空;不传则保留。"""
    store.apply_full({
        "providers": [{"id": "gw", "type": "openAI", "apiKey": "k",
                       "model": "gpt-4o", "baseUrl": "",
                       "modelTypes": {"gpt-4o": ["llm"]}}],
    })
    assert store.model_types("gw") == {"gpt-4o": ["llm"]}
    # 不传 modelTypes 的一次普通保存不能把它冲掉
    store.apply_full({"providers": [{"id": "gw", "type": "openAI",
                                     "model": "gpt-4o"}]})
    assert store.model_types("gw") == {"gpt-4o": ["llm"]}
    # 传空 map = 显式清空
    store.apply_full({"providers": [{"id": "gw", "type": "openAI",
                                     "model": "gpt-4o", "modelTypes": {}}]})
    assert store.model_types("gw") == {}


def test_model_types_drops_invalid_capability(store):
    store.apply_full({
        "providers": [{"id": "gw", "type": "openAI", "apiKey": "k",
                       "model": "gpt-4o",
                       "modelTypes": {"gpt-4o": "bogus", "vl": "vision"}}],
    })
    assert store.model_types("gw") == {"vl": ["vision"]}


def test_settings_view_exposes_capability(store):
    with TestClient(app) as c:
        body = _put(
            c,
            providers=[{"id": "gw", "type": "openAI", "apiKey": "k",
                        "baseUrl": "", "model": "gpt-4o",
                        "modelTypes": {"gpt-4o": ["llm"]}}],
            activeProviderId="gw",
        )
        p = body["providers"][0]
        # 覆盖优先 → user
        assert p["modelCapabilities"] == ["llm"]
        assert p["modelCapabilitySource"] == "user"
        assert p["modelTypes"] == {"gpt-4o": ["llm"]}
        # 目录里其它模型走自动推断,并带上 capabilities 字段
        gpt5 = next(m for m in p["models"] if m["id"] == "gpt-5.5")
        assert gpt5["capabilities"] == ["llm", "vision"]
        assert gpt5["capabilitySource"] == "auto"
        # 用途目录 + 八个槽位都在
        assert [x["id"] for x in body["capabilities"]] == [
            "llm", "vision", "image", "model3d",
            "audio", "video", "audio_gen", "video_gen"]
        assert [s["slot"] for s in body["modelSlots"]] == [
            "chat", "vision", "image", "model3d",
            "audio", "video", "audio_gen", "video_gen"]


def test_view_surfaces_custom_current_model_as_candidate(store):
    """手填的自定义模型 id（既不在目录也没远程列表）也要出现在 models 里,
    否则界面上无法给它指定类型。"""
    with TestClient(app) as c:
        body = _put(
            c,
            providers=[{"id": "gw", "type": "openAI", "apiKey": "k",
                        "baseUrl": "", "model": "my-private-model"}],
        )
        ids = [m["id"] for m in body["providers"][0]["models"]]
        assert "my-private-model" in ids


def test_slot_binding_and_chat_translation(store):
    store.apply_full({
        "providers": [{"id": "gw", "type": "openAI", "apiKey": "k",
                       "model": "gpt-4o", "baseUrl": ""}],
        "activeProviderId": "gw",
    })
    assert store.slot_binding("chat") is not None
    assert store.slot_capability("chat") == ["llm", "vision"]
    assert store.slot_binding("vision") is None
    # 配识图/生图槽
    store.apply_full({"modelSlots": {
        "vision": {"instanceId": "gw", "model": "qwen-vl-max"},
        "image": {"instanceId": "gw", "model": "dall-e-3"},
    }})
    assert store.model_slots()["vision"] == {
        "instanceId": "gw", "model": "qwen-vl-max"}
    assert store.slot_capability("vision") == ["llm", "vision"]
    assert store.slot_capability("image") == ["image"]
    # chat 槽写进来会被翻译成 activeProviderId + provider.model
    store.apply_full({"modelSlots": {
        "chat": {"instanceId": "gw", "model": "gpt-5.2"}}})
    assert store.load()["activeProviderId"] == "gw"
    assert store.provider_conf("gw")["model"] == "gpt-5.2"
    # chat 不落进 modelSlots 存储（单一事实来源）
    assert "chat" not in store.model_slots()


def test_slot_rejects_unknown_instance(store):
    with TestClient(app) as c:
        r = c.put("/api/settings", json={"modelSlots": {
            "vision": {"instanceId": "nope", "model": "x"}}})
        assert r.status_code == 400
        assert "未配置的厂商" in r.text


def test_slot_clear_and_auto_suggest(store):
    with TestClient(app) as c:
        body = _put(
            c,
            providers=[
                {"id": "gw", "type": "openAI", "apiKey": "k",
                 "baseUrl": "", "model": "gpt-4o"},
                {"id": "gen", "type": "openAI", "apiKey": "k", "baseUrl": "",
                 "model": "dall-e-3"},
            ],
            activeProviderId="gw",
            modelSlots={"image": {"instanceId": "gen", "model": "dall-e-3"}},
        )
        img = next(s for s in body["modelSlots"] if s["slot"] == "image")
        assert img["configured"] is True and img["capabilityOk"] is True
        assert img["capability"] == "image"
        # 清空
        body2 = _put(c, modelSlots={"image": None})
        img2 = next(s for s in body2["modelSlots"] if s["slot"] == "image")
        assert img2["configured"] is False


def test_slot_flags_capability_mismatch(store):
    """把对话模型(多模态)塞进生图槽 → 标记能力不匹配,而不是静默接受。"""
    with TestClient(app) as c:
        body = _put(
            c,
            providers=[{"id": "gw", "type": "openAI", "apiKey": "k",
                        "baseUrl": "", "model": "gpt-4o"}],
            activeProviderId="gw",
            modelSlots={"image": {"instanceId": "gw", "model": "gpt-4o"}},
        )
        img = next(s for s in body["modelSlots"] if s["slot"] == "image")
        assert img["configured"] is True
        assert img["capabilityOk"] is False


# ---------------------------------------------------------------------------
# 图片进上下文的方式 + 最大边长
# ---------------------------------------------------------------------------
def test_image_context_mode_default_and_validation(store):
    assert store.agent_config()["imageContextMode"] == "path"
    assert store.agent_config()["imageMaxEdge"] == 2000
    store.apply_full({"agent": {"imageContextMode": "inline"}})
    assert store.agent_config()["imageContextMode"] == "inline"
    with pytest.raises(Exception):
        store.apply_full({"agent": {"imageContextMode": "weird"}})


def test_image_max_edge_clamped(store):
    store.apply_full({"agent": {"imageMaxEdge": 100}})
    assert store.agent_config()["imageMaxEdge"] == 128   # 下限
    store.apply_full({"agent": {"imageMaxEdge": 99999}})
    assert store.agent_config()["imageMaxEdge"] == 8192  # 上限
    store.apply_full({"agent": {"imageMaxEdge": 1024}})
    assert store.agent_config()["imageMaxEdge"] == 1024


def test_image_context_discipline_switches_with_mode(store):
    from openminis.settings.chat_service import (
        IMAGE_INLINE_DISCIPLINE,
        IMAGE_PATH_DISCIPLINE,
        image_context_discipline,
        identity_system_prompt,
    )

    assert image_context_discipline(store) == IMAGE_PATH_DISCIPLINE
    assert IMAGE_PATH_DISCIPLINE in identity_system_prompt(store)
    store.apply_full({"agent": {"imageContextMode": "inline"}})
    assert image_context_discipline(store) == IMAGE_INLINE_DISCIPLINE


def test_build_chat_setup_passes_image_mode(store):
    store.apply_full({
        "providers": [{"id": "gw", "type": "openAI", "apiKey": "k",
                       "model": "gpt-4o", "baseUrl": "https://x/v1"}],
        "activeProviderId": "gw",
        "agent": {"imageContextMode": "inline"},
    })
    _, _, options, _, _ = build_chat_setup(store)
    assert options.image_context_mode == "inline"
    store.apply_full({"agent": {"imageContextMode": "path"}})
    _, _, options2, _, _ = build_chat_setup(store)
    assert options2.image_context_mode == "path"
