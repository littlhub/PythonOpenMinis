"""Filesystem read API (right-panel 项目文件)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openminis.core import context
from openminis.server.main import app


@pytest.fixture
def fs_root(tmp_path, monkeypatch):
    """Point AppContext.external_files_dir at an isolated tmp + seed files."""
    ctx = context.AppContext(data_dir=tmp_path, cache_dir=tmp_path / "cache")
    context.set_app_context(ctx)
    root = ctx.external_files_dir
    (root / "a.txt").write_text("hello a", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "b.py").write_text("print(1)\nprint(2)\n", encoding="utf-8")
    (root / "sub" / "big.bin").write_bytes(b"\x00\x01\x02\x03")
    (root / "sub" / "deep").mkdir()
    (root / "sub" / "deep" / "c.md").write_text("# hi\n\nworld\n", encoding="utf-8")
    return root


def test_fs_root(fs_root):
    with TestClient(app) as c:
        r = c.get("/api/fs/root")
        assert r.status_code == 200
        info = r.json()
        assert info["exists"] is True
        assert info["name"] == fs_root.name


def test_fs_tree(fs_root):
    with TestClient(app) as c:
        r = c.get("/api/fs/tree?depth=3")
        assert r.status_code == 200
        body = r.json()
        assert body["isDir"] is True
        names = [c["name"] for c in body["children"]]
        assert "a.txt" in names and "sub" in names
        sub = next(c for c in body["children"] if c["name"] == "sub")
        sub_children = [c["name"] for c in sub["children"]]
        assert "b.py" in sub_children and "big.bin" in sub_children
        deep = next(c for c in sub["children"] if c["name"] == "deep")
        assert any(c["name"] == "c.md" for c in deep["children"])


def test_fs_read_text(fs_root):
    with TestClient(app) as c:
        r = c.get("/api/fs/read?path=sub/b.py")
        assert r.status_code == 200
        body = r.json()
        assert body["content"].startswith("print(1)")
        assert body["truncated"] is False
        assert body["lines"] == 2


def test_fs_read_binary_blocked(fs_root):
    with TestClient(app) as c:
        r = c.get("/api/fs/read?path=sub/big.bin")
        assert r.status_code == 415


def test_fs_read_escape_blocked(fs_root):
    with TestClient(app) as c:
        # ``..`` chains out of the workspace root
        r = c.get("/api/fs/read?path=../../etc/passwd")
        assert r.status_code == 400


def test_fs_read_directory_rejected(fs_root):
    with TestClient(app) as c:
        r = c.get("/api/fs/read?path=sub")
        assert r.status_code == 400


def test_fs_missing(fs_root):
    with TestClient(app) as c:
        r = c.get("/api/fs/read?path=does/not/exist.txt")
        assert r.status_code == 404