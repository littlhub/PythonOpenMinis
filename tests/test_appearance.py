"""本地背景图上传。

``appearance.background`` 支持 ``file:<name>`` 指向本机上传的图片。这个路由
自己管字节，config 那边只存一个小字符串。

值得钉住的是三件事：换图不留垃圾、非法文件名出不去目录、以及拒绝时不要
「先写 config 再发现图片没存上」。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openminis.core import context
from openminis.server.main import app

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture()
def client(tmp_path):
    """Point the process-wide context at a tmp dir so uploads never land in the
    real data directory (which would also leave the user's own background
    deleted — the upload path clears the previous file)."""
    previous = context.app_context()
    context.set_app_context(context.AppContext(data_dir=tmp_path, cache_dir=tmp_path))
    try:
        with TestClient(app) as c:
            c.data_dir = tmp_path  # type: ignore[attr-defined]
            yield c
    finally:
        context.set_app_context(previous)


def _upload(client, filename: str, data: bytes = PNG_BYTES, mime: str = "image/png"):
    return client.post(
        "/api/appearance/background",
        files={"file": (filename, data, mime)},
    )


def test_upload_stores_the_file_and_returns_a_spec(client) -> None:
    r = _upload(client, "wall.png")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["spec"] == "file:background.png"
    assert (client.data_dir / "backgrounds" / "background.png").is_file()


def test_uploaded_image_is_served_back(client) -> None:
    spec = _upload(client, "wall.png").json()["spec"]
    name = spec.split(":", 1)[1]

    r = client.get(f"/api/appearance/background/{name}")
    assert r.status_code == 200
    assert r.content == PNG_BYTES


def test_switching_extension_leaves_only_one_file(client) -> None:
    """背景是单选,所以旧图必须被删掉。

    否则用户每试一张图就在数据目录里留一份,而没有任何入口能清理它 ——
    这类「看得见的设置、看不见的占用」很难被发现。
    """
    _upload(client, "a.png")
    _upload(client, "b.jpg", mime="image/jpeg")

    files = sorted(p.name for p in (client.data_dir / "backgrounds").iterdir())
    assert files == ["background.jpg"]


def test_rejects_unsupported_suffix(client) -> None:
    r = _upload(client, "notes.txt", b"hello", "text/plain")
    assert r.status_code == 400
    assert "格式" in r.json()["detail"]


def test_rejects_empty_file(client) -> None:
    r = _upload(client, "empty.png", b"")
    assert r.status_code == 400


def test_rejects_oversized_file(client) -> None:
    from openminis.server.appearance_api import MAX_BYTES

    r = _upload(client, "huge.png", b"x" * (MAX_BYTES + 1))
    assert r.status_code == 400
    assert "过大" in r.json()["detail"]


@pytest.mark.parametrize(
    "name",
    ["../settings.json", "..%2Fsettings.json", "sub/dir.png", "back\\slash.png", "noext"],
)
def test_get_rejects_escaping_names(client, name: str) -> None:
    """名字虽然由服务端生成,但这个 GET 是直接可达的 —— 决定路径的不能是
    调用方给的字符串。"""
    assert client.get(f"/api/appearance/background/{name}").status_code in (400, 404)


def test_get_missing_image_is_404(client) -> None:
    assert client.get("/api/appearance/background/background.png").status_code == 404
