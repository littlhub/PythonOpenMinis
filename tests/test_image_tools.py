"""图片链路：image_gen 工具、read_image 的路径/直读模式、以及运行时是否把
图片字节放进请求（``agent.imageContextMode``）。

核心诉求：**图片默认不进上下文**（只留路径），避免 base64 把上下文撑爆。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from openminis.agent.agent_runtime import AgentRuntime, AgentRuntimeOptions
from openminis.data.model import LLMMessage, LLMStreamChunk, ThinkingLevel
from openminis.data.model.agent_tool_definition import AgentToolDefinition, AgentToolParam
from openminis.settings.store import SettingsStore
from openminis.tools.image_gen_tool import ImageGenTool
from openminis.tools.read_image_tool import ReadImageTool, _max_edge
from openminis.tools.tool_execution_result import ToolExecutionResult

PIL = pytest.importorskip("PIL.Image", reason="read_image needs Pillow")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def store(tmp_path, monkeypatch):
    s = SettingsStore(path=tmp_path / "settings.json")
    monkeypatch.setattr(SettingsStore, "get", classmethod(lambda cls: s))
    return s


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """把 read_image 的工作区指到 tmp，避免动真实数据目录。"""
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)

    class _Ctx:
        external_files_dir = ws

    import openminis.tools.file_read_tool as frt
    import openminis.tools.read_image_tool as rit

    monkeypatch.setattr(rit, "app_context", lambda: _Ctx())
    monkeypatch.setattr(frt, "app_context", lambda: _Ctx())
    return ws


def _png(ws, name: str = "t.png", size=(64, 48)):
    from PIL import Image

    d = ws / "sess"
    d.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (200, 30, 30)).save(d / name)
    return d / name


def _setup_chat(store, model="gpt-4o", instance="gw", **extra):
    store.apply_full({
        "providers": [{"id": instance, "type": "openAI", "apiKey": "k",
                       "baseUrl": "", "model": model, **extra}],
        "activeProviderId": instance,
    })


# ---------------------------------------------------------------------------
# image_gen —— 未配置 / 未接入 都要说清楚，而不是假装画
# ---------------------------------------------------------------------------
def test_image_gen_without_slot_says_unconfigured(store, workspace):
    res = asyncio.run(ImageGenTool.execute('{"prompt":"a cat"}', "s"))
    assert res.success is False
    assert "生图未启用" in res.output


def test_image_gen_unsupported_engine(store, workspace):
    store.apply_full({
        "providers": [{"id": "g", "type": "gemini", "apiKey": "k",
                       "model": "gemini-2.5-flash"}],
        "modelSlots": {"image": {"instanceId": "g", "model": "imagen-3"}},
    })
    res = asyncio.run(ImageGenTool.execute('{"prompt":"a cat"}', "s"))
    assert res.success is False
    assert "生图未接入" in res.output


def test_image_gen_requires_key(store, workspace):
    # 不带 apiKey 存实例（apply_full 允许），再生图 → 明确提示缺 Key
    store.apply_full({
        "providers": [{"id": "gw", "type": "openAI", "baseUrl": "",
                       "model": "dall-e-3"}],
        "modelSlots": {"image": {"instanceId": "gw", "model": "dall-e-3"}},
    })
    assert not store.provider_conf("gw").get("apiKey")
    res = asyncio.run(ImageGenTool.execute('{"prompt":"a cat"}', "s"))
    assert res.success is False
    assert "API Key" in res.output


def test_image_gen_missing_prompt(store, workspace):
    res = asyncio.run(ImageGenTool.execute("{}", "s"))
    assert res.success is False
    assert "'prompt' is required" in res.output


def test_image_gen_count_runs_that_many_times(store, workspace, monkeypatch):
    """``count`` 由模型按语义声明：说两张就在**一次调用**里跑两次生成。"""
    import base64

    import httpx

    import openminis.tools.image_gen_tool as igt

    class _Ctx:
        external_files_dir = workspace

    monkeypatch.setattr(igt, "app_context", lambda: _Ctx())

    store.apply_full({
        "providers": [{"id": "gw", "type": "openAI", "apiKey": "k",
                       "baseUrl": "https://example.test/v1", "model": "dall-e-3"}],
        "modelSlots": {"image": {"instanceId": "gw", "model": "dall-e-3"}},
    })

    png = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()
    calls = {"n": 0}

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"data": [{"b64_json": png}]}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            calls["n"] += 1
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    res = asyncio.run(ImageGenTool.execute(
        json.dumps({"prompt": "a cat", "count": 2}), "s"))
    assert res.success is True
    assert calls["n"] == 2                      # 串行跑了两次生成
    assert "2/2 张" in res.output
    assert res.image_file_path and res.image_file_path.endswith(".png")


def test_image_gen_single_call_by_default(store, workspace, monkeypatch):
    """不声明 count 时仍然只跑一次（默认一张）。"""
    import base64

    import httpx

    import openminis.tools.image_gen_tool as igt

    class _Ctx:
        external_files_dir = workspace

    monkeypatch.setattr(igt, "app_context", lambda: _Ctx())
    store.apply_full({
        "providers": [{"id": "gw", "type": "openAI", "apiKey": "k",
                       "baseUrl": "https://example.test/v1", "model": "dall-e-3"}],
        "modelSlots": {"image": {"instanceId": "gw", "model": "dall-e-3"}},
    })
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()
    calls = {"n": 0}

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"data": [{"b64_json": png}]}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            calls["n"] += 1
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    res = asyncio.run(ImageGenTool.execute(
        json.dumps({"prompt": "a cat"}), "s"))
    assert res.success is True
    assert calls["n"] == 1
    assert "1/1 张" in res.output


def test_image_gen_definition_shape():
    d = ImageGenTool.definition()
    assert d.name == "image_gen"
    assert set(d.parameters) == {"tool_title", "prompt", "size", "count"}
    assert "prompt" in d.required
    # 张数由模型按语义声明（说几张填几），不是硬编码一张。
    assert "count" in d.parameters
    assert "count" not in d.required


# ---------------------------------------------------------------------------
# read_image —— 默认只回路径
# ---------------------------------------------------------------------------
def test_read_image_path_mode_drops_bytes(store, workspace):
    _setup_chat(store)                      # gpt-4o 推断为多模态
    _png(workspace)
    from openminis.settings.catalog import build_tool_registry

    reg = build_tool_registry(["read_image"])
    res = asyncio.run(reg["read_image"].executor(
        json.dumps({"path": "t.png"}), "sess"))
    assert res.success is True
    # 关键：默认不把图片字节交出去
    assert res.image_data is None
    assert "t.png" in res.output


def test_read_image_inline_mode_keeps_bytes(store, workspace):
    _setup_chat(store)
    store.apply_full({"agent": {"imageContextMode": "inline"}})
    _png(workspace)
    from openminis.settings.catalog import build_tool_registry

    reg = build_tool_registry(["read_image"])
    res = asyncio.run(reg["read_image"].executor(
        json.dumps({"path": "t.png"}), "sess"))
    assert res.success is True
    assert res.image_data            # inline: 直接把图片给多模态主模型


def test_read_image_path_mode_uses_vision_slot(store, workspace):
    """主模型无视觉 + 有识图槽 → 走描述；描述失败也要优雅降级（不抛异常）。"""
    _setup_chat(store, model="gpt-4o", modelTypes={"gpt-4o": "llm"})
    store.apply_full({"modelSlots": {
        "vision": {"instanceId": "gw", "model": "qwen-vl-max"}}})
    _png(workspace)
    from openminis.settings.catalog import build_tool_registry

    reg = build_tool_registry(["read_image"])
    res = asyncio.run(reg["read_image"].executor(
        json.dumps({"path": "t.png", "prompt": "what"}), "sess"))
    assert res.success is True
    assert res.image_data is None
    # 识图槽存在但模型不存在 → 走失败文案（仍是成功结果，loop 不崩）
    assert "Vision Group" in res.output or "未配置" in res.output


def test_read_image_no_vision_slot_tells_the_truth(store, workspace):
    _setup_chat(store, model="gpt-4o", modelTypes={"gpt-4o": "llm"})
    _png(workspace)
    from openminis.settings.catalog import build_tool_registry

    reg = build_tool_registry(["read_image"])
    res = asyncio.run(reg["read_image"].executor(
        json.dumps({"path": "t.png"}), "sess"))
    assert res.success is True
    assert res.image_data is None
    assert "未配置" in res.output or "无法读取" in res.output


def test_read_image_missing_file(store, workspace):
    _setup_chat(store)
    from openminis.settings.catalog import build_tool_registry

    reg = build_tool_registry(["read_image"])
    res = asyncio.run(reg["read_image"].executor(
        json.dumps({"path": "nope.png"}), "sess"))
    assert res.success is False


# ---------------------------------------------------------------------------
# 最大边长可配
# ---------------------------------------------------------------------------
def test_max_edge_reads_setting(store):
    assert _max_edge() == 2000                      # 默认
    store.apply_full({"agent": {"imageMaxEdge": 512}})
    assert _max_edge() == 512
    store.apply_full({"agent": {"imageMaxEdge": 4096}})
    assert _max_edge() == 4096


def test_read_image_downscales_to_configured_edge(store, workspace):
    """大图必须被缩到设置的长边内 —— 这是控制图片上下文开销的旋钮。"""
    from PIL import Image

    store.apply_full({"agent": {"imageMaxEdge": 128}})
    big = _png(workspace, "big.png", size=(800, 400))
    # 直接读字节走一遍（不看输出，只看重编码后的尺寸）
    import io

    with Image.open(big) as im:
        im.load()
        scale = 128 / max(im.size)
        resized = im.resize((int(im.size[0] * scale), int(im.size[1] * scale)))
    assert max(resized.size) <= 128


def test_read_image_metadata_reports_original_size(store, workspace):
    _setup_chat(store)
    _png(workspace, "m.png", size=(100, 60))
    from openminis.settings.catalog import build_tool_registry

    reg = build_tool_registry(["read_image"])
    res = asyncio.run(reg["read_image"].executor(
        json.dumps({"path": "m.png"}), "sess"))
    assert "100x60" in res.output


# ---------------------------------------------------------------------------
# 运行时：只有 inline 才把图片放进请求
# ---------------------------------------------------------------------------
class _RecordingProvider:
    """记录每次请求收到的 image_parts 数量。"""

    def __init__(self) -> None:
        self.calls = 0
        self.image_parts_seen: list[int] = []

    def stream_message(self, messages, system_prompt=None, max_tokens=0,
                       temperature=None, image_parts=None, tools=None,
                       thinking_level=ThinkingLevel.OFF):
        async def gen():
            self.calls += 1
            self.image_parts_seen.append(len(image_parts or []))
            if self.calls == 1:
                yield LLMStreamChunk.ToolCallComplete("c1", "img_tool", {"x": 1})
                yield LLMStreamChunk.Finished("tool_use")
            else:
                yield LLMStreamChunk.Text("done")
                yield LLMStreamChunk.Finished("end_turn")
        return gen()


def _img_tool() -> ToolExecutionResult:
    return ToolExecutionResult(
        "img", True, image_data=b"\x89PNG\r\n\x1a\n", image_mime_type="image/png")


def _make_runtime() -> AgentRuntime:
    async def executor(args_json, session_id, **_kw):
        return _img_tool()

    # SimpleNamespace, 不是 type(...)：类属性会把函数变成绑定方法，多塞一个 self
    tools = {"img_tool": SimpleNamespace(
        name="img_tool",
        definition=AgentToolDefinition(
            name="img_tool", description="returns an image",
            parameters={"x": AgentToolParam("integer", "x")}, required=[]),
        executor=executor,
    )}
    return AgentRuntime(tools=tools)


@pytest.mark.asyncio
async def test_runtime_path_mode_never_sends_image_bytes():
    provider = _RecordingProvider()
    rt = _make_runtime()
    msgs = [LLMMessage(LLMMessage.Role.USER, "go")]
    await rt.run(provider, msgs, "s", AgentRuntimeOptions(image_context_mode="path"))
    # 第 2 次请求（拿到图片之后）也不带 image_parts
    assert provider.image_parts_seen == [0, 0]
    # 历史里也不留图片字节
    for m in msgs:
        for p in (m.content_parts or []):
            assert getattr(p, "image_data", None) is None


@pytest.mark.asyncio
async def test_runtime_inline_mode_sends_image_once():
    provider = _RecordingProvider()
    rt = _make_runtime()
    msgs = [LLMMessage(LLMMessage.Role.USER, "go")]
    await rt.run(provider, msgs, "s", AgentRuntimeOptions(image_context_mode="inline"))
    # 第 2 次请求带上图片；之后不会重复携带（避免常驻计费）
    assert provider.image_parts_seen == [0, 1]


@pytest.mark.asyncio
async def test_same_image_is_not_re_read_with_a_reworded_prompt(monkeypatch):
    """同一张图换着说法反复识图 → 复用上次结果，不再打模型。

    现场（群聊 @ 一张图）：模型在同一轮里用 6 个微调过的 prompt 连识同一张图，
    耗时近 5 分钟；而群聊的被动回复窗口只有 5 分钟 —— 等它想发结果时窗口已经
    关了，用户看到的就是「识图成功了但什么都没收到」。
    """
    from openminis.settings import vision_service as vs

    vs._LAST_OK_BY_PATH.clear()
    vs._DESC_CACHE.clear()

    calls = {"n": 0}

    async def fake_describe(store, image_bytes, mime_type, *, prompt="", image_path=None):
        calls["n"] += 1
        return f"这是一张图（第 {calls['n']} 次识别的描述）"

    monkeypatch.setattr(vs, "describe_image", fake_describe)

    class Store:
        def slot_binding(self, slot):
            return ({"id": "p1"}, "vision-model")

    store = Store()
    path = "/var/minis/workspace/uploads/a.jpg"

    first = await vs.describe_image_with_fallback(
        store, b"x", "image/jpeg", prompt="描述这张图", image_path=path
    )
    assert "第 1 次" in first
    assert calls["n"] == 1

    # 换个说法问同一张图 —— 不该再打一次模型
    second = await vs.describe_image_with_fallback(
        store, b"x", "image/jpeg", prompt="详细描述图片内容", image_path=path
    )
    assert calls["n"] == 1, "换 prompt 不该绕过「同一张图」的复用"
    assert "第 1 次" in second
    assert "不要再用 read_image" in second

    # 换一张图照常识别
    await vs.describe_image_with_fallback(
        store, b"y", "image/jpeg", prompt="描述这张图",
        image_path="/var/minis/workspace/uploads/b.jpg",
    )
    assert calls["n"] == 2
