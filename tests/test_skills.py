"""Skill install / list / read / uninstall (Web 技能 pane + agent bundles)."""

from __future__ import annotations

from pathlib import Path

import pytest

from openminis.core import context
from openminis.skills import SkillStore
from openminis.skills.store import BUILTIN_TOOLS_SKILL, SkillError


@pytest.fixture()
def isolated_skills(tmp_path, monkeypatch):
    """Relocate data_dir so installs never touch the real ~/openminis."""
    monkeypatch.setenv("MINIS_HOME", str(tmp_path))
    context.set_app_context(context.AppContext(data_dir=tmp_path, cache_dir=tmp_path))
    yield tmp_path
    context._context = None  # type: ignore[attr-defined]


def _write_skill(root: Path, name: str, description: str, body: str = "正文") -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    (d / "scripts").mkdir()
    (d / "scripts" / "run.py").write_text("print(1)\n", encoding="utf-8")
    return d


def test_ensure_installed_seeds_builtins(isolated_skills):
    store = SkillStore()
    installed = store.ensure_installed()
    assert "memory-capture" in installed
    names = {e.name for e in store.list()}
    assert {"memory-capture", "session-compact", BUILTIN_TOOLS_SKILL} <= names


def test_ensure_installed_is_idempotent_and_preserves_edits(isolated_skills):
    store = SkillStore()
    store.ensure_installed()
    target = store.root / "memory-capture" / "SKILL.md"
    target.write_text("---\nname: memory-capture\ndescription: 本地改过\n---\n\n改过\n",
                      encoding="utf-8")
    assert store.ensure_installed() == []  # nothing re-installed
    assert "改过" in store.read("memory-capture")


def test_builtin_tools_manifest_lists_registry(isolated_skills):
    store = SkillStore()
    store.ensure_installed()
    tools = {t.name for t in SkillStore.builtin_tools()}
    assert {"shell_execute", "file_write", "memory_write"} <= tools
    entry = store.get(BUILTIN_TOOLS_SKILL)
    assert entry is not None and entry.generated
    assert "shell_execute" in entry.body


def test_install_from_directory(isolated_skills, tmp_path):
    store = SkillStore()
    src = _write_skill(tmp_path / "incoming", "demo", "演示技能")
    entry = store.install(src)
    assert entry.name == "demo"
    assert entry.source == "user"
    assert entry.scripts == ("run.py",)
    assert "正文" in store.read("demo")


def test_install_rejects_duplicate_without_force(isolated_skills, tmp_path):
    store = SkillStore()
    src = _write_skill(tmp_path / "incoming", "demo", "演示技能")
    store.install(src)
    with pytest.raises(SkillError):
        store.install(src)
    _write_skill(tmp_path / "incoming2", "demo", "演示技能 v2", body="第二版")
    assert "第二版" in store.install(tmp_path / "incoming2", force=True).body


def test_install_from_zip(isolated_skills, tmp_path):
    import zipfile

    bundle = _write_skill(tmp_path / "src", "zipped", "压缩包技能")
    archive = tmp_path / "zipped.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(bundle / "SKILL.md", "zipped/SKILL.md")
    store = SkillStore()
    assert store.install(archive).name == "zipped"
    assert "正文" in store.read("zipped")


def test_install_requires_manifest(isolated_skills, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "README.md").write_text("nope", encoding="utf-8")
    with pytest.raises(SkillError):
        SkillStore().install(empty)


def test_uninstall(isolated_skills, tmp_path):
    store = SkillStore()
    store.ensure_installed()
    src = _write_skill(tmp_path / "incoming", "demo", "演示技能")
    store.install(src)
    assert store.uninstall("demo") is True
    assert store.get("demo") is None
    assert store.uninstall("demo") is False
    with pytest.raises(SkillError):  # generated bundle is protected
        store.uninstall(BUILTIN_TOOLS_SKILL)


def test_api_roundtrip(isolated_skills, tmp_path):
    from fastapi.testclient import TestClient

    from openminis.server.main import app

    store = SkillStore()
    store.ensure_installed()
    src = _write_skill(tmp_path / "incoming", "demo", "演示技能")
    with TestClient(app) as c:
        listed = c.get("/api/skills").json()
        assert {s["name"] for s in listed["skills"]} >= {
            "memory-capture",
            BUILTIN_TOOLS_SKILL,
        }
        assert {"shell_execute"} <= {t["name"] for t in listed["tools"]}

        r = c.post("/api/skills/install", json={"source": str(src)})
        assert r.status_code == 200, r.text
        assert r.json()["skill"]["name"] == "demo"

        again = c.post("/api/skills/install", json={"source": str(src)})
        assert again.status_code == 400

        detail = c.get("/api/skills/demo").json()
        assert "正文" in detail["content"]

        assert c.delete("/api/skills/demo").json()["ok"] is True
        assert c.get("/api/skills/demo").status_code == 404
