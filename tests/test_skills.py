"""Skill install / list / read / uninstall (Web 技能 pane + agent bundles)."""

from __future__ import annotations

from pathlib import Path

import pytest

from openminis.core import context
from openminis.settings.chat_service import identity_system_prompt
from openminis.settings.store import SettingsStore
from openminis.skills import SkillStore
from openminis.skills.store import BUILTIN_TOOLS_SKILL, SkillError
from openminis.tools.skill_use_tool import SkillUseTool


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


# ---------------------------------------------------------------------------
# 技能激活 = 主 agent 的技能调用范围（skill_use 只认已激活的技能）
# ---------------------------------------------------------------------------
def _settings(tmp_path):
    return SettingsStore(path=tmp_path / "settings.json")


def test_active_skills_roundtrip(isolated_skills):
    store = _settings(isolated_skills)
    assert store.active_skills() == []
    store.set_skill_active("visioncustom", True)
    assert store.active_skills() == ["visioncustom"]
    store.set_skill_active("visioncustom", True)  # idempotent
    assert store.active_skills() == ["visioncustom"]
    store.set_skill_active("visioncustom", False)
    assert store.active_skills() == []
    assert _settings(isolated_skills).active_skills() == []  # persisted


@pytest.mark.asyncio
async def test_skill_use_gated_by_activation(isolated_skills, monkeypatch):
    _write_skill(isolated_skills / "skills", "visioncustom", "识图打标",
                 body="流程：先切图再打标")
    store = _settings(isolated_skills)
    monkeypatch.setattr(SettingsStore, "get", classmethod(lambda cls: store))

    res = await SkillUseTool.execute('{"tool_title":"t","name":"visioncustom"}', "s1")
    assert res.success is False
    assert "未激活" in res.output

    store.set_skill_active("visioncustom", True)
    res = await SkillUseTool.execute('{"tool_title":"t","name":"visioncustom"}', "s1")
    assert res.success is True
    assert "先切图再打标" in res.output   # SKILL.md 正文
    assert "run.py" in res.output        # 自带脚本提示


@pytest.mark.asyncio
async def test_skill_use_unknown_name(isolated_skills, monkeypatch):
    store = _settings(isolated_skills)
    monkeypatch.setattr(SettingsStore, "get", classmethod(lambda cls: store))
    res = await SkillUseTool.execute('{"tool_title":"t","name":"nope"}', "s1")
    assert res.success is False
    assert "没有名为" in res.output


def test_active_skills_enter_system_prompt(isolated_skills, monkeypatch):
    """已激活技能只把「名字+一句话」放进 prompt（全文由 skill_use 加载）。"""
    _write_skill(isolated_skills / "skills", "visioncustom", "识图打标")
    store = _settings(isolated_skills)
    monkeypatch.setattr(SettingsStore, "get", classmethod(lambda cls: store))

    assert "可用技能" not in identity_system_prompt(store)
    store.set_skill_active("visioncustom", True)
    prompt = identity_system_prompt(store)
    assert "可用技能" in prompt and "visioncustom" in prompt


def test_retrieval_discipline_enters_system_prompt(isolated_skills, monkeypatch):
    """联网检索纪律随 persona 注入：先检索、优先国内来源、不凭记忆猜国外站。"""
    store = _settings(isolated_skills)
    monkeypatch.setattr(SettingsStore, "get", classmethod(lambda cls: store))
    prompt = identity_system_prompt(store)
    assert "联网检索纪律" in prompt
    assert "web_search" in prompt and "web_fetch" in prompt
    assert "国内" in prompt


def test_skills_api_activate(isolated_skills, monkeypatch):
    from fastapi.testclient import TestClient

    from openminis.server.main import app

    _write_skill(isolated_skills / "skills", "visioncustom", "识图打标")
    store = _settings(isolated_skills)
    monkeypatch.setattr(SettingsStore, "get", classmethod(lambda cls: store))
    with TestClient(app) as c:
        entry = next(s for s in c.get("/api/skills").json()["skills"]
                     if s["name"] == "visioncustom")
        assert entry["active"] is False

        r = c.post("/api/skills/visioncustom/activate")
        assert r.status_code == 200, r.text
        assert r.json()["active"] == ["visioncustom"]
        assert c.get("/api/skills").json()["active"] == ["visioncustom"]

        assert c.post("/api/skills/visioncustom/deactivate").json()["active"] == []
        assert c.post("/api/skills/nope/activate").status_code == 404


# ---------- 技能根目录 YAML 配置 + 只读放行 ----------

def test_configured_skills_root_reads_yaml(isolated_skills):
    """skills.yaml 的 root 指向哪里，技能库就在哪里（相对路径相对数据目录）。"""
    from openminis.skills.store import configured_skills_root

    custom = isolated_skills / "my-skills"
    custom.mkdir()
    (isolated_skills / "skills.yaml").write_text(
        f"root: {custom.as_posix()}\n", encoding="utf-8"
    )
    assert configured_skills_root() == custom
    # 相对路径也支持
    (isolated_skills / "skills.yaml").write_text("root: alt-skills\n", encoding="utf-8")
    assert configured_skills_root() == (isolated_skills / "alt-skills").resolve()
    # 没配置时回默认
    (isolated_skills / "skills.yaml").unlink()
    assert configured_skills_root() == isolated_skills / "skills"


def test_skill_store_uses_yaml_root(isolated_skills):
    (isolated_skills / "skills.yaml").write_text("root: alt\n", encoding="utf-8")
    store = SkillStore()
    assert store.root == (isolated_skills / "alt").resolve()


def test_readonly_roots_allows_skills_dir(isolated_skills):
    """ls / search_files / file_read 能读技能库目录（配置在 yaml 里的根）。"""
    from openminis.tools.path_utils import readonly_roots, resolve_workspace_path

    custom = isolated_skills / "my-skills"
    skill_dir = _write_skill(custom, "demo", "演示")
    (isolated_skills / "skills.yaml").write_text(
        f"root: {custom.as_posix()}\n", encoding="utf-8"
    )

    assert readonly_roots() == (custom,)
    # 绝对路径放行
    assert resolve_workspace_path(str(skill_dir)) == skill_dir.resolve()
    # 相对形式（skills/demo）也放行
    resolved = resolve_workspace_path("skills/demo")
    assert resolved is not None and resolved.name == "demo"
    # 白名单外依旧拒绝
    assert resolve_workspace_path("C:/Windows") is None


def test_file_read_can_read_skill_md(isolated_skills):
    from openminis.tools.file_read_tool import FileReadTool

    custom = isolated_skills / "my-skills"
    skill_dir = _write_skill(custom, "demo", "演示", body="技能正文")
    (isolated_skills / "skills.yaml").write_text(
        f"root: {custom.as_posix()}\n", encoding="utf-8"
    )
    tool = FileReadTool()
    result = tool.execute(
        '{"path": "%s"}' % (skill_dir / "SKILL.md").as_posix(), "s1"
    )
    assert result.success and "技能正文" in result.output


def test_search_files_no_type_error(isolated_skills):
    """回归：Path.is_dir() 不接受 follow_symlinks，之前直接 TypeError。"""
    from openminis.tools.search_files_tool import _search_names
    import time as _time

    custom = isolated_skills / "my-skills"
    skill_dir = _write_skill(custom, "demo", "演示")
    (custom / "sub").mkdir()
    found, _ = _search_names(
        custom, "SKILL.md", ignore_case=False, no_ignore=True,
        max_results=10, deadline=_time.monotonic() + 5,
    )
    # 技能库在 workspace 外，_rel 只回文件名 —— 有结果就说明没再抛 TypeError
    assert found


def test_skills_block_lists_real_paths(isolated_skills):
    """技能清单要带 SKILL.md 真实路径 + 失败降级提示。"""
    from openminis.skills import SkillStore as _S

    SkillStore().ensure_installed()
    store = SettingsStore()
    store.set_skill_active("builtin-tools", True)
    prompt = identity_system_prompt(store)
    assert "SKILL.md：" in prompt
    assert "技能失败降级" in prompt
    assert "image_gen" in prompt


def test_chat_setup_always_enables_skill_use(isolated_skills):
    """回归：老 settings.json 的 enabled_tools 没有 skill_use，聊天里调
    skill_use 只会收到 Unknown tool —— 必须按能力开关补齐。"""
    from openminis.settings.chat_service import build_chat_setup

    store = SettingsStore()
    store.apply_full({
        "providers": [{"id": "gw", "type": "openAI", "apiKey": "k",
                       "model": "gpt-4o", "baseUrl": ""}],
        "activeProviderId": "gw",
        "identity": {"toolOverrides": {"general": []}},
    })
    _provider, runtime, _opts, _identity, _conf = build_chat_setup(store)
    assert "skill_use" in runtime.tools
