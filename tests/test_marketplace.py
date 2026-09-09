"""Tests for the skill marketplace (sources + URL install)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import pytest

from openminis.core import context
from openminis.skills import marketplace
from openminis.skills.store import SkillError, SkillStore


def _write_skill(root: Path, name: str = "demo") -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: demo skill\n---\n\nbody\n",
        encoding="utf-8",
    )
    return d


def _zip_bytes(name: str = "packed") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{name}/SKILL.md", f"---\nname: {name}\ndescription: from url\n---\nbody")
    return buf.getvalue()


class _FakeResp:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, n: int) -> bytes:
        chunk, self._payload = self._payload[:n], self._payload[n:]
        return chunk

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def test_list_sources_contains_skill_and_mcp_kinds():
    sources = marketplace.list_sources()
    kinds = {s["kind"] for s in sources}
    assert "skill" in kinds and "mcp" in kinds
    assert all(s["url"].startswith("https://") for s in sources)
    # curated copies — mutating the result must not affect the registry
    sources[0]["name"] = "mutated"
    assert marketplace.list_sources()[0]["name"] != "mutated"


def test_install_from_url_rejects_non_http(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIS_HOME", str(tmp_path))
    context.set_app_context(context.AppContext(data_dir=tmp_path, cache_dir=tmp_path))
    store = SkillStore()
    with pytest.raises(SkillError):
        marketplace.install_from_url(store, "ftp://example.com/x.zip")


def test_install_from_url_downloads_and_installs(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIS_HOME", str(tmp_path))
    context.set_app_context(context.AppContext(data_dir=tmp_path, cache_dir=tmp_path))
    store = SkillStore()
    payload = _zip_bytes("packed")
    monkeypatch.setattr(
        marketplace.urllib.request, "urlopen", lambda req, timeout: _FakeResp(payload)
    )
    result = marketplace.install_from_url(store, "https://example.com/packed.zip")
    assert result["skill"]["name"] == "packed"
    assert result["bytes"] == len(payload)
    assert store.get("packed") is not None


def test_install_from_url_empty_download(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIS_HOME", str(tmp_path))
    context.set_app_context(context.AppContext(data_dir=tmp_path, cache_dir=tmp_path))
    store = SkillStore()
    monkeypatch.setattr(
        marketplace.urllib.request, "urlopen", lambda req, timeout: _FakeResp(b"")
    )
    with pytest.raises(SkillError, match="为空"):
        marketplace.install_from_url(store, "https://example.com/empty.zip")


def test_marketplace_rest_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIS_HOME", str(tmp_path))
    context.set_app_context(context.AppContext(data_dir=tmp_path, cache_dir=tmp_path))
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openminis.server.marketplace_api import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    res = client.get("/api/marketplace")
    assert res.status_code == 200
    assert any(s["kind"] == "mcp" for s in res.json()["sources"])

    payload = _zip_bytes("restskill")
    monkeypatch.setattr(
        marketplace.urllib.request, "urlopen", lambda req, timeout: _FakeResp(payload)
    )
    res = client.post(
        "/api/marketplace/install-url",
        json={"url": "https://example.com/restskill.zip"},
    )
    assert res.status_code == 200
    assert res.json()["skill"]["name"] == "restskill"

    res = client.post(
        "/api/marketplace/install-url", json={"url": "not-a-url"}
    )
    assert res.status_code == 400


def test_install_from_url_force_overwrites(tmp_path, monkeypatch):
    store = SkillStore()
    payload = _zip_bytes("dup")
    monkeypatch.setattr(
        marketplace.urllib.request, "urlopen", lambda req, timeout: _FakeResp(payload)
    )
    marketplace.install_from_url(store, "https://example.com/dup.zip")
    with pytest.raises(SkillError):
        marketplace.install_from_url(store, "https://example.com/dup.zip")
    result = marketplace.install_from_url(
        store, "https://example.com/dup.zip", force=True
    )
    assert result["skill"]["name"] == "dup"
