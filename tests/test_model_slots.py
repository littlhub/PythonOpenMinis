"""模型类型（能力标签）+ 用途分槽（对话/识图/生图）+ 图片进上下文的方式。

补 test_settings.py 的存储/API 覆盖面：这里聚焦「模型类型」这条新链路。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openminis.server.main import app
from openminis.settings.chat_service import build_chat_setup
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
# 模型类型覆盖
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


def test_model_types_accepts_legacy_string_and_multimodal(store):
    """旧格式(单字符串 / "multimodal")在读取时归一化为标签列表。"""
    store.apply_full({
        "providers": [{"id": "gw", "type": "openAI", "apiKey": "k",
                       "model": "gpt-4o",
                       "modelTypes": {"old": "llm", "mm": "multimodal"}}],
    })
    assert store.model_types("gw") == {"old": ["llm"], "mm": ["llm", "vision"]}


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
        assert p["modelCapability"] == "llm"
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


# ---------------------------------------------------------------------------
# 用途分槽
# ---------------------------------------------------------------------------
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
        assert img["suggested"] == {"instanceId": "gen", "model": "dall-e-3"}
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


def test_vision_slot_marks_resolver_configured(store):
    """识图槽配好后 ``VisionGroupResolver.is_configured()`` 必须为真 ——
    否则主模型无视觉时 read_image 根本不会被暴露给 agent。"""
    from openminis.tools.vision_group_resolver import VisionGroupResolver

    store.apply_full({
        "providers": [{"id": "gw", "type": "openAI", "apiKey": "k",
                       "model": "gpt-4o", "baseUrl": ""}],
        "activeProviderId": "gw",
    })
    assert VisionGroupResolver.is_configured() is False
    assert VisionGroupResolver.candidates() == []
    store.apply_full({"modelSlots": {
        "vision": {"instanceId": "gw", "model": "qwen-vl-max"}}})
    assert VisionGroupResolver.is_configured() is True
    assert VisionGroupResolver.candidates() == [
        {"instanceId": "gw", "model": "qwen-vl-max"}]
    assert VisionGroupResolver.group_name() == "OpenAI"


def test_read_image_tool_exposed_when_vision_slot_configured(store):
    """``make_agent_tools`` 的 read_image 暴露条件要认识图槽。"""
    from openminis.settings.catalog import make_agent_tools

    # 无视觉的主模型 + 无识图槽 → 不暴露 read_image
    names = [d.name for d in make_agent_tools(supports_image_input=False)]
    assert "read_image" not in names
    # 配了识图槽 → 暴露
    names2 = [
        d.name
        for d in make_agent_tools(
            supports_image_input=False, vision_group_configured=True)
    ]
    assert "read_image" in names2
    # image_gen 始终暴露（未配置时调用会说明）
    assert "image_gen" in names


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
        IMAGE_SLOT_DISCIPLINE,
        SUBAGENT_PLAN_DISCIPLINE,
        identity_system_prompt,
        image_context_discipline,
    )

    # 默认（path + 不走子代理）：read_image 走识图槽
    assert image_context_discipline(store) == IMAGE_SLOT_DISCIPLINE
    assert IMAGE_SLOT_DISCIPLINE in identity_system_prompt(store)
    # 开启「识图走子代理」：纪律换成委派版
    store.apply_full({"agent": {"imageVisionSubagent": True}})
    from openminis.settings.chat_service import IMAGE_SUBAGENT_DISCIPLINE

    assert image_context_discipline(store) == IMAGE_SUBAGENT_DISCIPLINE
    # 子代理助理开启时，系统提示里带「先规划代办」纪律
    assert SUBAGENT_PLAN_DISCIPLINE in identity_system_prompt(store)
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


# ---------------------------------------------------------------------------
# 子代理助理开关
# ---------------------------------------------------------------------------
def test_subagent_toggle_gates_delegate_tool(store):
    """关掉「调用子agent助理」后,subagent_delegate 要从 schema + 执行器里移除。"""
    store.apply_full({
        "providers": [{"id": "gw", "type": "openAI", "apiKey": "k",
                       "model": "gpt-4o", "baseUrl": "https://x/v1"}],
        "activeProviderId": "gw",
    })
    # 默认开启 -> 工具在（默认身份 assistant 推荐里含 subagent_delegate）
    assert store.agent_config()["subagentEnabled"] is True
    _, runtime, _, _, _ = build_chat_setup(store)
    assert "subagent_delegate" in runtime.tools
    assert "subagent_delegate" in {d.name for d in runtime.tool_definitions()}
    # 关掉 -> 执行器与 schema 都没有
    store.apply_full({"agent": {"subagentEnabled": False}})
    _, runtime2, _, _, _ = build_chat_setup(store)
    assert "subagent_delegate" not in runtime2.tools
    # 其它工具不受影响
    assert "shell_execute" in runtime2.tools
    # 重新开启 -> 回来
    store.apply_full({"agent": {"subagentEnabled": True}})
    _, runtime3, _, _, _ = build_chat_setup(store)
    assert "subagent_delegate" in runtime3.tools


def test_subagent_toggle_accepts_loose_bool(store):
    store.apply_full({"agent": {"subagentEnabled": "off"}})
    assert store.agent_config()["subagentEnabled"] is False
    store.apply_full({"agent": {"subagentEnabled": True}})
    assert store.agent_config()["subagentEnabled"] is True


# ---------------------------------------------------------------------------
# 自定义模型类型 (JSON / 类型 / URL)
# ---------------------------------------------------------------------------
def test_custom_model_types_roundtrip(store):
    store.apply_full({"customModelTypes": [
        {"id": "my-tts", "label": "我家语音", "type": "tts",
         "url": "https://api.example.com/v1",
         "json": '{"method":"POST","path":"/audio/speech"}'},
    ]})
    cts = store.custom_model_types()
    assert len(cts) == 1
    assert cts[0]["id"] == "my-tts"
    assert cts[0]["label"] == "我家语音"
    assert cts[0]["type"] == "tts"
    assert cts[0]["url"] == "https://api.example.com/v1"
    assert '"POST"' in cts[0]["json"]
    # 不传则保留
    store.apply_full({"agent": {"maxToolSteps": 12}})
    assert len(store.custom_model_types()) == 1
    # 传空列表 = 清空
    store.apply_full({"customModelTypes": []})
    assert store.custom_model_types() == []


def test_custom_model_types_validates_id(store):
    with pytest.raises(Exception):
        store.apply_full({"customModelTypes": [{"id": "bad id!", "label": "x"}]})
    with pytest.raises(Exception):
        store.apply_full({"customModelTypes": [
            {"id": "dup", "label": "a"}, {"id": "dup", "label": "b"}]})


def test_custom_model_types_validates_json(store):
    with pytest.raises(Exception):
        store.apply_full({"customModelTypes": [
            {"id": "x", "label": "x", "json": "{not json"}]})


def test_model_can_be_tagged_with_custom_type(store):
    """自定义类型 id 要能作为标签存下来,并出现在用途目录里。"""
    with TestClient(app) as c:
        body = _put(
            c,
            customModelTypes=[
                {"id": "my-tts", "label": "我家语音", "type": "tts", "url": "", "json": ""},
            ],
            providers=[{"id": "gw", "type": "openAI", "apiKey": "k",
                        "baseUrl": "", "model": "gpt-4o",
                        "modelTypes": {"gpt-4o": ["llm", "my-tts"]}}],
            activeProviderId="gw",
        )
        # 用途目录里含自定义
        ids = [x["id"] for x in body["capabilities"]]
        assert "my-tts" in ids
        my = next(x for x in body["capabilities"] if x["id"] == "my-tts")
        assert my["label"] == "我家语音"
        # 覆盖里保留自定义 + 内置
        assert body["providers"][0]["modelCapabilities"] == ["llm", "my-tts"]
        assert "我家语音" in body["providers"][0]["modelCapabilityLabels"]


def test_deleting_custom_type_leaves_stored_tag(store):
    """删掉自定义类型后,模型上遗留的悬空标签在读取时被丢弃。"""
    store.apply_full({
        "customModelTypes": [{"id": "my-tts", "label": "我家语音"}],
        "providers": [{"id": "gw", "type": "openAI", "apiKey": "k",
                       "model": "gpt-4o",
                       "modelTypes": {"gpt-4o": ["llm", "my-tts"]}}],
    })
    assert store.model_types("gw")["gpt-4o"] == ["llm", "my-tts"]
    store.apply_full({"customModelTypes": []})
    assert store.model_types("gw")["gpt-4o"] == ["llm"]
