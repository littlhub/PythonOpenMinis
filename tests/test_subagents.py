"""Subagents: config CRUD + registry + LLM planner + delegate tool.

A subagent is a worker profile (persona / model / tools / skills) the main
agent can hand a task to. These tests cover the configuration surface in
``openminis.agent.subagents`` and the runtime delegation in
``openminis.tools.subagent_tool`` — no network in any of them.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from openminis.agent.subagents import (
    SubagentError,
    build_registry,
    get_subagent,
    list_subagents,
    plan_subagent,
    slugify_name,
    upsert_subagent,
)
from openminis.data.model import LLMStreamChunk
from openminis.server.main import app
from openminis.settings import chat_service
from openminis.settings.store import SettingsStore
from openminis.tools.subagent_tool import SubagentDelegateTool


@pytest.fixture()
def store(tmp_path, monkeypatch):
    s = SettingsStore(path=tmp_path / "settings.json")
    monkeypatch.setattr(SettingsStore, "get", classmethod(lambda cls: s))
    return s


def _configure(store):
    """Give anthropic a key so it counts as a usable provider."""
    store.apply_full({
        "providers": [{"type": "anthropic", "apiKey": "sk-test",
                       "model": "claude-sonnet-5", "baseUrl": ""}],
        "activeProviderId": "anthropic",
    })


# ---------------------------------------------------------------------------
# CRUD + validation
# ---------------------------------------------------------------------------
def test_upsert_fills_defaults(store):
    _configure(store)
    cfg = upsert_subagent(store, {
        "id": "writer", "name": "写作助手", "providerType": "anthropic",
        "model": "claude-sonnet-5", "tools": ["file_read"],
    })
    assert cfg["id"] == "writer"
    assert cfg["emoji"] == "🤖"          # default icon
    assert "写作助手" in cfg["persona"]   # persona auto-filled from the name
    assert cfg["maxRounds"] == 6
    assert cfg["mcpServers"] == []
    assert get_subagent(store, "writer")["model"] == "claude-sonnet-5"
    assert [s["id"] for s in list_subagents(store)] == ["writer"]


def test_upsert_rejects_bad_id(store):
    _configure(store)
    with pytest.raises(SubagentError):
        upsert_subagent(store, {"id": "Bad Id!", "name": "x"})
    with pytest.raises(SubagentError):
        upsert_subagent(store, {"id": "", "name": "x"})


def test_upsert_rejects_unknown_tool(store):
    _configure(store)
    with pytest.raises(SubagentError, match="未知工具"):
        upsert_subagent(store, {
            "id": "writer", "name": "x", "providerType": "anthropic",
            "model": "m", "tools": ["not_a_tool"],
        })


def test_upsert_rejects_unconfigured_provider(store):
    # no key configured → nothing is usable yet
    with pytest.raises(SubagentError, match="模型服务"):
        upsert_subagent(store, {
            "id": "writer", "name": "x", "providerType": "anthropic",
            "model": "m",
        })


def test_upsert_rejects_unported_engine(store):
    store.apply_full({
        "providers": [{"type": "gemini", "apiKey": "k", "model": "", "baseUrl": ""}],
        "activeProviderId": "gemini",
    })
    with pytest.raises(SubagentError, match="尚未移植"):
        upsert_subagent(store, {
            "id": "writer", "name": "x", "providerType": "gemini", "model": "m",
        })


def test_update_existing_and_delete(store):
    _configure(store)
    upsert_subagent(store, {"id": "writer", "name": "写作助手",
                            "providerType": "anthropic", "model": "m1"})
    updated = upsert_subagent(store, {"name": "写作助手 v2",
                                      "providerType": "anthropic", "model": "m2",
                                      "maxRounds": 99}, sid="writer")
    assert updated["model"] == "m2"
    assert updated["maxRounds"] == 12          # clamped
    assert updated["persona"]                  # never wiped to empty

    from openminis.agent.subagents import delete_subagent
    assert delete_subagent(store, "writer") is True
    assert delete_subagent(store, "writer") is False


def test_slugify_name():
    assert slugify_name("Writer 01") == "writer-01"
    assert slugify_name("写作助手") == "subagent"   # no latin chars → fallback
    assert slugify_name("2fast") == "s-2fast"       # can't start with a digit


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def test_registry_only_offers_real_resources(store):
    _configure(store)
    reg = build_registry(store)
    anthropic = next(p for p in reg["providers"] if p["type"] == "anthropic")
    assert anthropic["usable"] is True and anthropic["hasKey"] is True
    assert not any(p["usable"] for p in reg["providers"] if p["type"] == "gemini")
    assert any(t["id"] == "subagent_delegate" for t in reg["tools"])
    assert all(m["providerType"] for m in reg["models"])
    assert reg["mcpServers"] == [] and reg["mcpNote"]


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------
def test_rest_crud(store):
    _configure(store)
    with TestClient(app) as c:
        body = c.get("/api/subagents").json()
        assert body["subagents"] == []
        assert body["registry"]["providers"]

        r = c.post("/api/subagents", json={
            "id": "writer", "name": "写作助手", "providerType": "anthropic",
            "model": "claude-sonnet-5", "tools": ["file_read"], "skills": [],
        })
        assert r.status_code == 200, r.text
        assert r.json()["subagent"]["persona"]

        dup = c.post("/api/subagents", json={
            "id": "writer", "name": "重复", "providerType": "anthropic",
            "model": "claude-sonnet-5",
        })
        assert dup.status_code == 400

        up = c.put("/api/subagents/writer", json={
            "name": "写作助手 v2", "providerType": "anthropic",
            "model": "claude-sonnet-5", "tools": ["file_read", "file_write"],
        })
        assert up.status_code == 200, up.text
        assert up.json()["subagent"]["tools"] == ["file_read", "file_write"]

        assert c.delete("/api/subagents/writer").json() == {"ok": True}
        assert c.delete("/api/subagents/writer").status_code == 404


def test_rest_registry_endpoint(store):
    _configure(store)
    with TestClient(app) as c:
        reg = c.get("/api/subagents/registry").json()
    assert "providers" in reg and "models" in reg and "tools" in reg


# ---------------------------------------------------------------------------
# Planner (LLM is injected — never the network)
# ---------------------------------------------------------------------------
class _PlanProvider:
    """Returns a fixed JSON payload, optionally inside a ```json fence."""

    def __init__(self, obj: dict):
        self.obj = obj

    def stream_message(self, messages, system_prompt=None, max_tokens=0,
                       temperature=None, image_parts=None, tools=None,
                       thinking_level=None):
        payload = json.dumps(self.obj, ensure_ascii=False)

        async def gen():
            yield LLMStreamChunk.Text(f"```json\n{payload}\n```")
            yield LLMStreamChunk.Finished("end_turn")
        return gen()


@pytest.mark.asyncio
async def test_plan_filters_fabricated_ids(store):
    _configure(store)
    res = await plan_subagent(store, "我要一个写作 subagent", provider=_PlanProvider({
        "name": "写作助手", "emoji": "✍️", "description": "写长文",
        "persona": "你是写作编辑。", "providerType": "anthropic",
        "model": "claude-sonnet-5",
        "tools": ["file_read", "not_a_tool"], "skills": ["not_a_skill"],
        "maxRounds": 4,
    }))
    sub = res["subagent"]
    assert res["saved"] is False
    assert sub["tools"] == ["file_read"]   # fabricated tool id dropped
    assert sub["skills"] == []             # unknown skill dropped
    assert sub["providerType"] == "anthropic"
    assert sub["maxRounds"] == 4
    assert store.load()["subagents"] == {}  # nothing persisted without autoSave


@pytest.mark.asyncio
async def test_plan_auto_save(store):
    _configure(store)
    res = await plan_subagent(store, "写代码的", auto_save=True,
                              provider=_PlanProvider({
                                  "name": "coder", "providerType": "anthropic",
                                  "model": "claude-sonnet-5",
                                  "tools": ["file_read"],
                              }))
    assert res["saved"] is True
    assert "coder" in store.load()["subagents"]


@pytest.mark.asyncio
async def test_plan_requires_usable_provider(store):
    # nothing configured → refuse before ever calling the LLM
    with pytest.raises(SubagentError, match="还没有可用的模型服务"):
        await plan_subagent(store, "x", provider=_PlanProvider({}))


def test_plan_endpoint_rejects_empty_request(store):
    with TestClient(app) as c:
        r = c.post("/api/subagents/plan", json={"request": "  "})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Delegate tool
# ---------------------------------------------------------------------------
class _FakeProvider:
    def stream_message(self, messages, system_prompt=None, max_tokens=0,
                       temperature=None, image_parts=None, tools=None,
                       thinking_level=None):
        async def gen():
            yield LLMStreamChunk.Text("子代理完成")
            yield LLMStreamChunk.Finished("end_turn")
        return gen()


@pytest.mark.asyncio
async def test_delegate_unknown_subagent(store):
    res = await SubagentDelegateTool.execute(
        json.dumps({"tool_title": "t", "subagent": "nope", "task": "x"}), "s1")
    assert res.success is False
    assert "不存在" in res.output


@pytest.mark.asyncio
async def test_delegate_requires_task(store):
    _configure(store)
    upsert_subagent(store, {"id": "writer", "name": "写作助手",
                            "providerType": "anthropic", "model": "m"})
    res = await SubagentDelegateTool.execute(
        json.dumps({"tool_title": "t", "subagent": "writer", "task": " "}), "s1")
    assert res.success is False
    assert "task" in res.output


@pytest.mark.asyncio
async def test_delegate_runs_inner_loop(store, monkeypatch):
    _configure(store)
    # deliberately includes subagent_delegate: the inner loop must drop it so
    # a worker can never delegate again (no infinite recursion).
    upsert_subagent(store, {
        "id": "writer", "name": "写作助手", "providerType": "anthropic",
        "model": "claude-sonnet-5", "tools": ["file_read", "subagent_delegate"],
    })
    monkeypatch.setattr(chat_service, "build_provider",
                        lambda t, c: _FakeProvider())
    res = await SubagentDelegateTool.execute(
        json.dumps({"tool_title": "t", "subagent": "writer", "task": "写一篇文章"}),
        "s1")
    assert res.success is True, res.output
    assert "子代理完成" in res.output
