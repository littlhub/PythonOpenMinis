"""Knowledge search API (Web 知识库页)."""

from __future__ import annotations

import pytest

from openminis.core import context
from openminis.server import chat_store  # noqa: F401  (db init consistency)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Isolated data dir so skill/memory scans never touch ~/openminis."""
    monkeypatch.setenv("MINIS_HOME", str(tmp_path))
    context.set_app_context(context.AppContext(data_dir=tmp_path, cache_dir=tmp_path))
    yield tmp_path
    context._context = None


def _seed_memory(tmp_path: object) -> None:
    mem = tmp_path / "memory"  # type: ignore[operator]
    (mem / "daily").mkdir(parents=True, exist_ok=True)
    (mem / "daily" / "2026-09-08.md").write_text(
        "# 日志\n\n用户偏好中文回复\n", encoding="utf-8"
    )
    # 知识库与记忆分开：knowledge/ 是独立目录
    kn = tmp_path / "knowledge"  # type: ignore[operator]
    kn.mkdir(exist_ok=True)
    (kn / "tooling.md").write_text(
        "# 工具链\n\n用 uv 管理依赖\n", encoding="utf-8"
    )


def test_knowledge_search_lists_bundled_docs(env):
    from fastapi.testclient import TestClient

    from openminis.server.main import app

    _seed_memory(env)
    with TestClient(app) as c:
        r = c.get("/api/knowledge?limit=10")
        assert r.status_code == 200
        body = r.json()
        assert body["sources"]["doc"] >= 1
        kinds = {it["kind"] for it in body["items"]}
        assert "doc" in kinds


def test_knowledge_search_filters_by_query_and_kind(env):
    from fastapi.testclient import TestClient

    from openminis.server.main import app

    _seed_memory(env)
    with TestClient(app) as c:
        r = c.get("/api/knowledge?q=中文回复&kind=memory")
        assert r.status_code == 200
        names = [it["source"] for it in r.json()["items"]]
        assert "daily/2026-09-08.md" in names

        # 知识不是记忆：按 kind=knowledge 才搜得到（记忆与知识分开）
        mem_hits = [it["source"] for it in c.get("/api/knowledge?q=uv&kind=memory").json()["items"]]
        assert "tooling.md" not in mem_hits
        kn_hits = [it["source"] for it in c.get("/api/knowledge?q=uv&kind=knowledge").json()["items"]]
        assert "tooling.md" in kn_hits


def test_knowledge_content_reads_memory_and_docs(env):
    from fastapi.testclient import TestClient

    from openminis.server.main import app

    _seed_memory(env)
    with TestClient(app) as c:
        assert c.get("/api/knowledge/content/memory/daily/2026-09-08.md").json()["content"].startswith(
            "# 日志"
        )
        assert "uv" in c.get("/api/knowledge/content/knowledge/tooling.md").json()["content"]
        doc = c.get("/api/knowledge/content/doc/README.md")
        assert doc.status_code == 200
        assert "Python" in doc.json()["content"]


def test_knowledge_rejects_unknown_and_traversal(env):
    from fastapi.testclient import TestClient

    from openminis.server.main import app

    _seed_memory(env)
    with TestClient(app) as c:
        assert c.get("/api/knowledge/content/nope/x.md").status_code == 400
        assert c.get("/api/knowledge/content/memory/..%2F..%2Fetc/passwd").status_code in {
            400,
            404,
        }
        assert c.get("/api/knowledge/content/doc/secrets.py").status_code == 404
