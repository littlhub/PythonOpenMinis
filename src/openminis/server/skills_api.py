"""REST endpoints for the skills pane (Web 技能).

Reads the on-disk ``<data_dir>/skills`` tree: one bundle per skill, each with a
``SKILL.md``. Bundled skills and the generated builtin-tool manifest live in
the same tree, so what the UI lists is exactly what the agent can load.

Activation is separate from installation: ``settings.json`` holds the
``activeSkills`` list, and only activated skills enter the agent's system
prompt / can be loaded by ``skill_use``. That list *is* the main agent's
"技能调用范围".

Install accepts a local directory or a ``.zip`` archive — remote marketplaces
are out of scope for the port, and an on-device agent shouldn't silently fetch
code anyway.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core.logging import get_logger
from ..settings.store import SettingsStore
from ..skills import SkillStore
from ..skills.store import SkillError

logger = get_logger(__name__)

router = APIRouter(prefix="/api/skills", tags=["skills"])


class InstallRequest(BaseModel):
    source: str
    force: bool = False
    #: 安装落点：``"host"``（主智能体技能库，缺省）或 ``"<agentId>#<序号>"``
    #: （某外部 agent 自己的技能根）。路径由服务端从描述符解析，客户端只报 id。
    target: str = "host"


class EnvRequest(BaseModel):
    values: dict[str, str] = {}


def _is_active(entry: Any, active: set[str]) -> bool:
    return entry.name in active or Path(entry.path).name in active


def _skill_dict(entry: Any, active: set[str] | None = None) -> dict[str, Any]:
    return {
        "name": entry.name,
        "description": entry.description,
        "source": entry.source,
        "generated": entry.generated,
        "scripts": list(entry.scripts),
        # SKILL.md 里声明的环境变量名（``metadata.requires.env`` …）
        "env": list(getattr(entry, "env", ()) or ()),
        "path": entry.path,
        "active": bool(active) and _is_active(entry, active or set()),
    }


def _tool_dict(tool: Any) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
        "required": list(tool.required),
    }


HOST_TARGET_ID = "host"


def _install_targets() -> list[dict[str, Any]]:
    """可安装技能的落点清单：主智能体技能库 + 每个启用外部 agent 的技能根。

    这就是「每个 agent 都能加技能」的接口面 —— 外部 agent 的落点由它自己的协议
    描述符 ``skills.roots`` 声明，改描述符即可增删可安装的根，不用改代码。
    """
    store = SkillStore()
    out: list[dict[str, Any]] = [
        {
            "id": HOST_TARGET_ID,
            "kind": "host",
            "label": f"主智能体技能库 · {store.root}",
            "path": str(store.root),
            "scope": "host",
            "agentId": "",
            "agentName": "主智能体",
        }
    ]
    try:
        from ..agent.external_agents import external_skill_targets
    except Exception:  # pragma: no cover - 外部 agent 层不可用时只剩主智能体落点
        logger.debug("external agent layer unavailable", exc_info=True)
        return out

    import os

    try:
        out.extend(external_skill_targets(os.getcwd()))
    except Exception:  # pragma: no cover - 解析失败不该让技能页整体挂掉
        logger.debug("external skill targets unavailable", exc_info=True)
    # 工具目录不是「安装落点」（往它装的是清单文件，不是 SKILL.md），所以只做展示；
    # 但同样来自描述符声明 —— 配好目录即导入。
    return out


def _target_path(target: str) -> tuple[str, Path]:
    """把落点 id 解析成 (规范化 id, 绝对路径)。只认清单里有的 id。"""
    tid = str(target or "").strip() or HOST_TARGET_ID
    match = next((t for t in _install_targets() if t["id"] == tid), None)
    if match is None:
        raise HTTPException(status_code=400, detail=f"unknown_target: {tid}")
    return str(match["id"]), Path(str(match["path"]))


def _external_skills() -> list[dict[str, Any]]:
    """外部 agent 自己的技能（主智能体**不加载**它们，但可以往同一个根里加）。

    它们是**外部 agent 的技能**，不在主智能体技能库里：主智能体这边既不能激活也
    不能卸载（要动就动那个 agent 的技能根）。所以单独一栏，并如实标出它属于哪个
    agent、在哪个根、是不是与主智能体同名。

    「互借」的另一个方向（外部 agent 借主智能体的技能）由外部 agent 侧自己完成。
    而「加技能」两个方向都在这页：装的时候选落点即可（见 ``/install`` 的 ``target``）。
    """
    try:
        from ..agent.external_agents import collect_external_agent_skills
    except Exception:  # pragma: no cover - 外部 agent 层不可用时这一栏就是空的
        logger.debug("external agent layer unavailable", exc_info=True)
        return []

    import os

    try:
        host_names = {e.name for e in SkillStore().list()}
    except Exception:  # pragma: no cover - 技能库读不到就不标 shadowed
        host_names = set()

    try:
        return collect_external_agent_skills(os.getcwd(), host_skill_names=host_names)
    except Exception:  # pragma: no cover - 扫描失败不该让技能页整体挂掉
        logger.debug("external skill scan failed", exc_info=True)
        return []


def _external_tools() -> list[dict[str, Any]]:
    """外部 agent 的工具面（描述符声明 + ``tools.roots`` 目录导入），只读展示。"""
    try:
        from ..agent.external_agents import collect_external_agent_tools
    except Exception:  # pragma: no cover - 外部 agent 层不可用时这一栏就是空的
        logger.debug("external agent layer unavailable", exc_info=True)
        return []

    import os

    try:
        return collect_external_agent_tools(os.getcwd())
    except Exception:  # pragma: no cover - 扫描失败不该让技能页整体挂掉
        logger.debug("external tool scan failed", exc_info=True)
        return []


def _tool_targets() -> list[dict[str, Any]]:
    """外部 agent 的**工具目录**（``tools.roots``）—— 把清单文件丢进去即导入。"""
    try:
        from ..agent.external_agents import load_descriptors
        from ..agent.zcode.integration import expand_skill_root
    except Exception:  # pragma: no cover
        return []

    import os

    workspace = os.getcwd()
    out: list[dict[str, Any]] = []
    for descriptor in load_descriptors().values():
        if not descriptor.enabled or not descriptor.tool_roots:
            continue
        for index, pattern in enumerate(descriptor.tool_roots):
            resolved = expand_skill_root(str(pattern), workspace=workspace)
            if resolved is None:
                continue
            out.append({
                "id": f"{descriptor.id}#tool{index}",
                "kind": "tools",
                "label": f"{descriptor.name} · {resolved}",
                "path": resolved,
                "scope": "project",
                "agentId": descriptor.id,
                "agentName": descriptor.name,
            })
    return out


def _with_target_ids(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """给外部技能补上它所属的**安装落点 id**（界面据此删除/继续往那装）。

    技能根是「落点」，技能目录是根下的一层 —— 用 ``rootPath`` 直接对号；老数据
    没有这个字段时回落到父目录比对。
    """
    by_path = {str(t["path"]): t for t in _install_targets() if t["kind"] == "external"}
    out: list[dict[str, Any]] = []
    for skill in skills:
        item = dict(skill)
        root = str(skill.get("rootPath") or "")
        found = by_path.get(root)
        if found is None:
            parent = str(Path(str(skill.get("path") or "")).parent)
            found = by_path.get(parent)
        item["targetId"] = found["id"] if found else ""
        out.append(item)
    return out


@router.get("")
async def skills_list() -> dict[str, Any]:
    store = SkillStore()
    active = set(SettingsStore.get().active_skills())
    return {
        "skills": [_skill_dict(e, active) for e in store.list()],
        "tools": [_tool_dict(t) for t in SkillStore.builtin_tools()],
        # 外部 agent 自己的技能（只读正文，但可以往它所在的根里继续装）
        "external": _with_target_ids(_external_skills()),
        # 外部 agent 的工具面（描述符声明 + tools.roots 目录导入）
        "externalTools": _external_tools(),
        # 「每个 agent 都能加技能」的落点清单
        "targets": _install_targets(),
        # 只做展示：外部 agent 的工具目录（把清单文件丢进去即导入）
        "toolDirs": _tool_targets(),
        "dir": str(store.root),
        "active": sorted(active),
    }


# -- 环境变量 ---------------------------------------------------------------
# 技能会在 SKILL.md 里声明「我需要这些环境变量」（``metadata.requires.env``，
# 如 modelscope-image 的 MODELSCOPE_API_KEY）。引擎不会自己变出密钥，但可以把
# 「哪些技能需要什么、现在缺哪些」摆到界面上，用户填一次就够。
#
# 值存的是**同一份** ``sandbox.envExtra`` —— 设置页那个「环境变量」编辑器读写的
# 就是它，沙箱 shell 每次执行前会整份注入（``shell_execute_tool._load_env_extra``）。
# 所以在这里填完，技能脚本里的 ``os.getenv`` / ``$VAR`` 立刻就能拿到，不用重启。
def _env_values() -> dict[str, str]:
    from ..core.prefs import get_prefs

    raw = get_prefs().get_string("sandbox.envExtra") or ""
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("sandbox.envExtra 不是合法 JSON，按空处理")
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _env_values_save(values: dict[str, str]) -> None:
    from ..core.prefs import get_prefs

    get_prefs().set_now(
        "sandbox.envExtra", json.dumps(values, ensure_ascii=False, indent=2)
    )


@router.get("/env")
async def skills_env() -> dict[str, Any]:
    """技能声明的环境变量 + 当前值 + 有没有配。"""
    values = _env_values()
    needs: dict[str, list[str]] = {}
    for entry in SkillStore().list():
        if entry.generated:
            continue
        for name in getattr(entry, "env", ()) or ():
            needs.setdefault(name, []).append(entry.name)
    return {
        "required": [
            {"name": name, "skills": sorted(skills), "set": bool(values.get(name))}
            for name, skills in sorted(needs.items())
        ],
        "values": values,
        "count": len(values),
    }


@router.put("/env")
async def skills_env_save(req: EnvRequest) -> dict[str, Any]:
    """整份覆盖环境变量（与设置页那份是同一处存储）。空值 = 删掉该项。"""
    clean = {
        str(k).strip(): str(v)
        for k, v in (req.values or {}).items()
        if str(k).strip() and str(v or "").strip()
    }
    _env_values_save(clean)
    logger.info("skill env updated: %d 项", len(clean))
    return {"ok": True, **await skills_env()}


@router.post("/install")
async def skills_install(req: InstallRequest) -> dict[str, Any]:
    """装一份技能到指定落点（主智能体技能库，或某个外部 agent 自己的技能根）。

    装到外部 agent 的根里**不进** ``activeSkills`` —— 那份技能归那个 agent 自己的
    循环用，主智能体这边既不加载它也不该把它算进自己的调用范围。
    """
    tid, root = _target_path(req.target)
    store = SkillStore(root=root)
    try:
        entry = store.install(req.source, force=req.force)
    except SkillError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    logger.info("skill installed: %s <- %s (target=%s)", entry.name, req.source, tid)
    return {
        "ok": True,
        "skill": _skill_dict(entry, set()),
        "target": tid,
        "path": str(root),
    }


@router.get("/{name}")
async def skill_get(name: str, target: str = HOST_TARGET_ID) -> dict[str, Any]:
    """读一份技能的正文。``target`` 默认主智能体技能库，也可指定外部 agent 的根。"""
    if target != HOST_TARGET_ID:
        _tid, root = _target_path(target)
        store = SkillStore(root=root)
        entry = store.get(name)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"unknown_skill: {name}")
        return {
            **_skill_dict(entry, set()),
            "content": entry.body,
            "readOnly": True,
            "target": target,
        }

    entry = SkillStore().get(name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"unknown_skill: {name}")
    active = set(SettingsStore.get().active_skills())
    return {**_skill_dict(entry, active), "content": entry.body}


@router.post("/{name}/activate")
async def skill_activate(name: str) -> dict[str, Any]:
    """Put a skill in scope for the main agent (可被调用)."""
    entry = SkillStore().get(name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"unknown_skill: {name}")
    store = SettingsStore.get()
    active = store.set_skill_active(entry.name, True)
    return {"ok": True, "name": entry.name, "active": active}


@router.post("/{name}/deactivate")
async def skill_deactivate(name: str) -> dict[str, Any]:
    entry = SkillStore().get(name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"unknown_skill: {name}")
    store = SettingsStore.get()
    active = store.set_skill_active(entry.name, False)
    return {"ok": True, "name": entry.name, "active": active}


@router.delete("/{name}")
async def skill_delete(name: str, target: str = HOST_TARGET_ID) -> dict[str, Any]:
    """卸载一份技能。指定 ``target`` 时删的是那个外部 agent 技能根里的那一份。"""
    if target != HOST_TARGET_ID:
        _tid, root = _target_path(target)
        try:
            removed = SkillStore(root=root).uninstall(name)
        except SkillError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        if not removed:
            raise HTTPException(status_code=404, detail=f"unknown_skill: {name}")
        logger.info("external skill removed: %s (target=%s)", name, target)
        return {"ok": True, "name": name, "target": target}

    try:
        removed = SkillStore().uninstall(name)
    except SkillError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    if not removed:
        raise HTTPException(status_code=404, detail=f"unknown_skill: {name}")
    # don't leave a dangling entry in the active scope
    try:
        SettingsStore.get().set_skill_active(name, False)
    except Exception:  # pragma: no cover - best effort cleanup
        logger.debug("could not clear active flag for %s", name, exc_info=True)
    return {"ok": True, "name": name}
