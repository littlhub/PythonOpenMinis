"""静态前端托管的兜底行为。

从仓库 clone 下来时 ``web/dist`` 是不存在的 —— 它是构建产物,被
``.gitignore`` 排除。这里锁住「缺产物时给出能照做的提示」这条:它曾经被
包在 ``if WEB_DIST.exists()`` 里,于是 ``/`` 压根没注册,访问首页只会撞上
FastAPI 默认的 ``{"detail":"Not Found"}`` —— 既没说是前端没构建,也没说
该怎么构建。这种失败模式不报错、只在浏览器里显示一句 JSON,很难自查。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openminis.server import main as srv


@pytest.fixture()
def client():
    with TestClient(srv.app) as c:
        yield c


def test_missing_dist_returns_an_actionable_hint(client, monkeypatch, tmp_path):
    """dist 缺失时首页要告诉人「跑 npm run build」,而不是默认 404。"""
    monkeypatch.setattr(srv, "index", tmp_path / "index.html")
    r = client.get("/")
    assert r.status_code == 503
    assert r.headers["content-type"].startswith("text/html")
    assert "npm run build" in r.text
    # 后端本身是好的,别让人以为整个服务挂了
    assert "web/dist" in r.text


def test_missing_dist_does_not_swallow_api_404(client, monkeypatch, tmp_path):
    """``/api`` 下的未知路径必须仍是 JSON 404 —— 不能被 SPA 兜底吞掉。

    首页那个提示页只在「浏览器路径」上出现;接口探测拿到的必须还是
    结构化的 404,否则前端会把提示页当成接口响应去解析。
    """
    monkeypatch.setattr(srv, "index", tmp_path / "index.html")
    r = client.get("/api/definitely-not-a-route")
    assert r.status_code == 404
    assert r.json()["detail"] == "Not Found"


def test_built_dist_is_served(client):
    """构建过之后首页应当是真正的应用页(而不是提示页)。"""
    if not (srv.WEB_DIST / "index.html").exists():
        pytest.skip("web/dist 尚未构建")
    r = client.get("/")
    assert r.status_code == 200
    assert "<!doctype html" in r.text.lower()
