"""聊天附件链路：上传只留路径、上下文里没有 base64、识图在后端拿路径读图。

回归的起点是一个真实故障：前端「上传图片」用 ``FileReader.readAsDataURL``
把几 MB 的 base64 塞进受控 ``<textarea>``，浏览器排版直接把页面拖死
（「OpenMinis 无响应」），而且 base64 还会整段进上下文烧 token。
现在改成：上传落盘 → 消息里只有路径 → 需要「看懂」时由后端读字节交识图槽。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from openminis.server import upload_api
from openminis.server.main import app
from openminis.settings.attachments import (
    build_attachment_context,
    extract_attachments,
)
from openminis.settings.store import SettingsStore

PIL = pytest.importorskip("PIL.Image", reason="附件缩边需要 Pillow")


@pytest.fixture()
def store(tmp_path, monkeypatch):
    s = SettingsStore(path=tmp_path / "settings.json")
    monkeypatch.setattr(SettingsStore, "get", classmethod(lambda cls: s))
    return s


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """工作区指到 tmp —— 上传目录与路径围栏都跟着走。"""
    ws = tmp_path / "workspace"
    (ws / "uploads").mkdir(parents=True, exist_ok=True)

    class _Ctx:
        external_files_dir = ws
        data_dir = tmp_path

    import openminis.settings.attachments as att
    import openminis.tools.file_read_tool as frt
    import openminis.tools.read_image_tool as rit

    monkeypatch.setattr(att, "app_context", lambda: _Ctx())
    monkeypatch.setattr(upload_api, "app_context", lambda: _Ctx())
    monkeypatch.setattr(frt, "app_context", lambda: _Ctx())
    monkeypatch.setattr(rit, "app_context", lambda: _Ctx())
    return ws


def _png(path, size=(64, 48)):
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (10, 120, 200)).save(path)
    return path


def _ctx(store, ws, text):
    return asyncio.run(build_attachment_context(store, text))


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def test_extract_drops_inline_base64(workspace):
    """内联 base64 必须被剥掉 —— 它就是拖死页面的元凶。"""
    text = "看这张 ![p.png](data:image/png;base64," + ("A" * 5000) + ")"
    cleaned, refs, dropped = extract_attachments(text)
    assert dropped == 1
    assert "base64" in cleaned  # 只留一句人话说明
    assert "AAAA" not in cleaned
    assert refs == []


def test_extract_classifies_image_and_file(workspace):
    png = _png(workspace / "uploads" / "a.png")
    doc = workspace / "uploads" / "n.txt"
    doc.write_text("hi", encoding="utf-8")
    text = f"![a.png]({png.as_posix()}) 还有 [附件: n.txt]({doc.as_posix()})"
    cleaned, refs, dropped = extract_attachments(text)
    assert dropped == 0
    kinds = sorted((r.kind, r.alt) for r in refs)
    assert kinds == [("file", "n.txt"), ("image", "a.png")]
    assert all(r.host_path is not None for r in refs)
    # 引用本身留在正文里（模型要能从文本里读到路径）
    assert png.as_posix() in cleaned


def test_extract_rejects_path_outside_workspace(workspace, tmp_path):
    outside = tmp_path / "outside.png"
    _png(outside)
    _cleaned, refs, _d = extract_attachments(f"![x]({outside.as_posix()})")
    # 工作区外 → 不当作本地附件（降级成普通 url 引用，不给路径）
    assert refs and refs[0].kind == "url"
    assert refs[0].host_path is None


# ---------------------------------------------------------------------------
# 上下文：只有路径，没有字节
# ---------------------------------------------------------------------------
def test_path_mode_keeps_only_path(store, workspace):
    png = _png(workspace / "uploads" / "p.png")
    store.apply_full({
        "providers": [{"id": "gw", "type": "openAI", "apiKey": "k",
                       "model": "gpt-4o", "baseUrl": ""}],
        "activeProviderId": "gw",
        "modelSlots": {"vision": {"instanceId": "gw", "model": "gpt-4o"}},
        "agent": {"imageContextMode": "path"},
    })
    bundle = _ctx(store, workspace, f"这是什么 ![p.png]({png.as_posix()})")
    assert bundle.image_parts == []
    assert png.as_posix() in bundle.prompt
    assert "<user-attached-files>" in bundle.prompt
    assert "base64" not in bundle.prompt
    # 落库/展示用的干净文本里不掺附件说明
    assert "<user-attached-files>" not in bundle.text
    assert "read_image" in bundle.prompt


def test_path_mode_tells_main_agent_to_delegate_vision(store, workspace):
    """path 模式：不把图交给后端，而是**指路**给识图子代理。"""
    from openminis.agent.subagents import upsert_subagent

    png = _png(workspace / "uploads" / "p.png")
    store.apply_full({
        "providers": [{"id": "gw", "type": "openAI", "apiKey": "k",
                       "model": "gpt-4o", "baseUrl": ""}],
        "activeProviderId": "gw",
        "agent": {"imageContextMode": "path"},
    })
    upsert_subagent(store, {
        "id": "vision-bot", "name": "识图助手", "emoji": "👁️",
        "providerId": "gw", "providerType": "openAI", "model": "gpt-4o",
        "tools": ["read_image"], "skills": ["visioncustom"], "maxRounds": 4,
    })
    bundle = _ctx(store, workspace, f"这是啥 ![p.png]({png.as_posix()})")
    assert bundle.image_parts == []
    assert png.as_posix() in bundle.prompt
    # 后端**不**抢着描述图，只让主 agent 自己委派
    assert "subagent_delegate" in bundle.prompt
    assert "vision-bot" in bundle.prompt


def test_path_mode_falls_back_to_read_image_without_subagent(store, workspace):
    png = _png(workspace / "uploads" / "p.png")
    store.apply_full({
        "providers": [{"id": "gw", "type": "openAI", "apiKey": "k",
                       "model": "gpt-4o", "baseUrl": ""}],
        "activeProviderId": "gw",
        "modelSlots": {"vision": {"instanceId": "gw", "model": "gpt-4o"}},
        "agent": {"imageContextMode": "path"},
    })
    bundle = _ctx(store, workspace, f"这是啥 ![p.png]({png.as_posix()})")
    assert bundle.image_parts == []
    assert "read_image" in bundle.prompt
    assert "base64" not in bundle.prompt


def test_path_mode_says_so_when_no_vision_available(store, workspace):
    png = _png(workspace / "uploads" / "p.png")
    store.apply_full({
        "providers": [{"id": "gw", "type": "openAI", "apiKey": "k",
                       "model": "gpt-4o", "baseUrl": ""}],
        "activeProviderId": "gw",
        "agent": {"imageContextMode": "path"},
    })
    bundle = _ctx(store, workspace, f"这是啥 ![p.png]({png.as_posix()})")
    assert "无法看到图片内容" in bundle.prompt


def test_inline_mode_attaches_bytes_but_not_base64_text(store, workspace):
    png = _png(workspace / "uploads" / "p.png")
    store.apply_full({"agent": {"imageContextMode": "inline", "imageMaxEdge": 32}})
    bundle = _ctx(store, workspace, f"看图 ![p.png]({png.as_posix()})")
    assert len(bundle.image_parts) == 1
    part = bundle.image_parts[0]
    assert isinstance(part.data, (bytes, bytearray))
    # 字节走多模态通道，正文里依然只有路径
    assert "base64" not in bundle.prompt
    assert png.as_posix() in bundle.prompt


def test_inline_downscales_to_max_edge(store, workspace):
    png = _png(workspace / "uploads" / "big.png", size=(800, 400))
    store.apply_full({"agent": {"imageContextMode": "inline", "imageMaxEdge": 128}})
    bundle = _ctx(store, workspace, f"![big.png]({png.as_posix()})")
    from io import BytesIO

    from PIL import Image

    assert len(bundle.image_parts) == 1
    with Image.open(BytesIO(bundle.image_parts[0].data)) as im:
        assert max(im.size) == 128


def test_missing_file_degrades_gracefully(store, workspace):
    ghost = workspace / "uploads" / "ghost.png"
    bundle = _ctx(store, workspace, f"![ghost.png]({ghost.as_posix()})")
    assert bundle.image_parts == []
    assert bundle.text  # 原文保留，不炸


# ---------------------------------------------------------------------------
# 上传接口
# ---------------------------------------------------------------------------
def test_upload_returns_path_inside_workspace(workspace):
    _png(workspace / "src.png")
    with TestClient(app) as c:
        with open(workspace / "src.png", "rb") as fh:
            r = c.post("/api/upload", files={"file": ("shot.png", fh, "image/png")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "image"
    assert body["path"].endswith(".png")
    # 必须是工作区内的正斜杠绝对路径（工具层要能解析它）
    assert "\\" not in body["path"]
    assert "/uploads/" in body["path"]
    assert (workspace / "uploads").as_posix() in body["path"]
    assert (workspace / "uploads").joinpath(body["storedName"]).is_file()


def test_upload_raw_roundtrip(workspace):
    _png(workspace / "src.png")
    with TestClient(app) as c:
        with open(workspace / "src.png", "rb") as fh:
            up = c.post("/api/upload", files={"file": ("s.png", fh, "image/png")}).json()
        got = c.get(f"/api/upload/raw?name={up['storedName']}")
        assert got.status_code == 200
        assert got.content[:8] == b"\x89PNG\r\n\x1a\n"
        # 目录穿越必须被拒
        assert c.get("/api/upload/raw?name=../settings.json").status_code == 400


def test_upload_rejects_oversize(workspace, monkeypatch):
    monkeypatch.setattr(upload_api, "MAX_BYTES", 16)
    with TestClient(app) as c:
        r = c.post("/api/upload", files={"file": ("x.bin", b"y" * 64, "application/octet-stream")})
    assert r.status_code == 400
    assert "过大" in r.json()["detail"]


def test_upload_rejects_executable(workspace):
    with TestClient(app) as c:
        r = c.post("/api/upload", files={"file": ("evil.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 400


def test_read_image_accepts_uploaded_absolute_path(store, workspace):
    """上传回来的绝对路径必须能被 read_image 解析（否则模型「看不到」图）。"""
    from openminis.tools.read_image_tool import ReadImageTool

    png = _png(workspace / "uploads" / "r.png")
    result = ReadImageTool.execute(
        f'{{"tool_title":"t","path":"{png.as_posix()}"}}', "db-sess"
    )
    assert result.success is True, result.output
    assert "64x48" in result.output


def test_chat_persists_clean_text_without_attachment_noise(store, workspace):
    """落库的用户轮不该带上附件说明块（否则历史会越滚越脏）。"""
    png = _png(workspace / "uploads" / "p.png")
    bundle = _ctx(store, workspace, f"看 ![p.png]({png.as_posix()})")
    assert "<user-attached-files>" not in bundle.text
    assert "本轮" not in bundle.text
    assert bundle.text.strip().endswith(")")
