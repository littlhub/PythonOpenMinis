"""Tests for openminis/server/system_api.py — the REST surface behind the
Web Settings tree (logs / storage / memory / backup / appinfo).

Every test runs against an isolated MINIS_HOME so no real user data is
touched. The router is mounted on a bare FastAPI app (no lifespan) because the
endpoints only read app_context() paths.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from openminis.server.system_api import _resolve_memory, router


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIS_HOME", str(tmp_path))
    from openminis.core import context

    context.set_app_context(
        context.AppContext(data_dir=tmp_path, cache_dir=tmp_path)
    )
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client, tmp_path
    context._context = None  # type: ignore[attr-defined]


def test_appinfo_reports_dirs(isolated_home):
    client, tmp = isolated_home
    info = client.get("/api/system/appinfo").json()
    assert info["name"] == "OpenMinis"
    assert info["dataDir"] == str(tmp)
    assert info["workspaceDir"].startswith(str(tmp))
    assert info["memoryDir"] == str(tmp / "memory")
    assert info["python"]


def test_logs_empty_then_tail(isolated_home):
    client, tmp = isolated_home
    body = client.get("/api/system/logs").json()
    assert body["lines"] == []  # no file yet -> clean empty answer

    log_dir = tmp / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "minis.log").write_text(
        "\n".join(f"line {i}" for i in range(20)), encoding="utf-8"
    )
    body = client.get("/api/system/logs?lines=5").json()
    assert body["size"] > 0
    assert body["lines"][-1] == "line 19"
    assert len(body["lines"]) == 5

    dl = client.get("/api/system/logs/download")
    assert dl.status_code == 200
    assert b"line 19" in dl.content


def test_storage_counts_dirs_and_files(isolated_home):
    client, tmp = isolated_home
    (tmp / "workspace").mkdir(exist_ok=True)
    (tmp / "workspace" / "a.txt").write_text("x" * 100, encoding="utf-8")
    (tmp / "memory").mkdir(exist_ok=True)
    (tmp / "memory" / "GLOBAL.md").write_text("# keep\n", encoding="utf-8")
    (tmp / "settings.json").write_text("{}", encoding="utf-8")

    body = client.get("/api/system/storage").json()
    assert body["root"] == str(tmp)
    assert body["totalBytes"] >= 100
    names = {it["name"] for it in body["items"]}
    assert any("memory" in n for n in names)
    assert any("workspace" in n for n in names)
    memory_item = next(it for it in body["items"] if "memory" in it["name"])
    assert memory_item["fileCount"] == 1


def test_memory_write_read_list_delete(isolated_home):
    client, tmp = isolated_home
    mem = tmp / "memory"
    mem.mkdir(exist_ok=True)
    (mem / "GLOBAL.md").write_text("# 长期记忆\n- 用户喜欢简洁回复\n", encoding="utf-8")

    listing = client.get("/api/system/memory").json()
    assert listing["dir"] == str(mem)
    assert [f["name"] for f in listing["files"]] == ["GLOBAL.md"]
    assert "简洁" in listing["files"][0]["preview"]

    # read full content
    got = client.get("/api/system/memory/GLOBAL.md").json()
    assert "- 用户喜欢简洁回复" in got["content"]

    # overwrite + create new
    r = client.put("/api/system/memory/GLOBAL.md", json={"content": "# 新内容\n"})
    assert r.status_code == 200
    r = client.put(
        "/api/system/memory/2026-01-01.md", json={"content": "## 08:00\n\n测试\n"}
    )
    assert r.status_code == 200
    assert len(client.get("/api/system/memory").json()["files"]) == 2

    # delete
    assert client.delete("/api/system/memory/2026-01-01.md").json()["ok"] is True
    assert client.delete("/api/system/memory/2026-01-01.md").status_code == 404


@pytest.mark.parametrize(
    "name",
    ["../outside.md", "./x.md", "a//b.md", "a/./b.md", "evil.exe",
     "GLOBAL.MD.exe", "x" * 90, "..%2F..%2Fx.md"],
)
def test_memory_name_is_whitelisted(isolated_home, name):
    """The hostile names exercise the resolver directly (raw HTTP paths get
    normalised by client/router before the guard runs); a benign invalid name
    is checked over HTTP too.

    Category sub-paths (``wiki/topic.md``) are *allowed* — see
    test_memory_category_subpaths.
    """
    with pytest.raises(HTTPException) as exc:
        _resolve_memory(name)
    assert exc.value.status_code == 400

    client, tmp = isolated_home
    (tmp / "memory").mkdir(exist_ok=True)
    assert client.get("/api/system/memory/evil.exe").status_code == 400
    assert (
        client.put("/api/system/memory/evil.exe", json={"content": "x"}).status_code
        == 400
    )
    assert client.delete("/api/system/memory/evil.exe").status_code == 400


def test_memory_category_subpaths(isolated_home):
    """wiki/<topic>.md / rules/RULES.md can be written, listed and read."""
    client, tmp = isolated_home
    mem = tmp / "memory"
    mem.mkdir(exist_ok=True)

    for rel, content in [
        ("wiki/project-conventions.md", "# 项目约定\n\n- 用 uv\n"),
        ("rules/RULES.md", "- 永远说中文\n"),
    ]:
        r = client.put(f"/api/system/memory/{rel}", json={"content": content})
        assert r.status_code == 200, r.text

    names = [f["name"] for f in client.get("/api/system/memory").json()["files"]]
    assert "wiki/project-conventions.md" in names
    assert "rules/RULES.md" in names

    got = client.get("/api/system/memory/wiki/project-conventions.md").json()
    assert "用 uv" in got["content"]
    assert client.get("/api/system/memory/rules/RULES.md").status_code == 200

    # traversal through a subdir is refused by the resolver (raw ".." names
    # cannot even reach the router — httpx normalises them first)
    with pytest.raises(HTTPException):
        _resolve_memory("wiki/../GLOBAL.md")
    assert (
        client.get("/api/system/memory/wiki/../GLOBAL.md").status_code in {400, 404}
    )


def test_backup_zip_contains_settings_memory_prefs(isolated_home):
    client, tmp = isolated_home
    (tmp / "settings.json").write_text('{"activeIdentityId": "coder"}', encoding="utf-8")
    files_dir = tmp / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    (files_dir / "minis_prefs.json").write_text(
        '{"appearance.theme":"dark"}', encoding="utf-8"
    )
    mem = tmp / "memory"
    mem.mkdir(exist_ok=True)
    (mem / "GLOBAL.md").write_text("# keep\n", encoding="utf-8")

    resp = client.get("/api/system/backup/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = set(zf.namelist())
    assert "settings.json" in names
    assert "prefs/minis_prefs.json" in names
    assert "memory/GLOBAL.md" in names
    assert "manifest.json" in names
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["app"] == "openminis"
