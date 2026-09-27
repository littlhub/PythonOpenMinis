"""外部 agent 注册与调用（描述符驱动）。

接入一个新的外部 agent（Codex / Claude Code / 自研 CLI / 远端服务）**不需要改代码**：
在 ``~/openminis/agents/`` 放一份 YAML 或 JSON 协议描述符即可，应用下次加载时会把它
当成一个内置子代理列出来，委派时按描述符调用并把回传信息带回对话。

协议字段全集见 ``PROTOCOL.md``；内置描述符（ZCode）在 ``descriptors/``。
"""

from __future__ import annotations

from typing import Optional

from .protocol import (
    DESCRIPTOR_VERSION,
    ExternalAgentDescriptor,
    ExternalAgentError,
    builtin_descriptor_dir,
    descriptor_load_errors,
    extract_path,
    get_descriptor,
    load_descriptor_file,
    load_descriptors,
    mini_yaml_load,
    registered_agent_ids,
    reload_descriptors,
    render_template,
    user_descriptor_dir,
)
from .runner import ExternalAgentResult, run_external_agent

__all__ = [
    "DESCRIPTOR_VERSION",
    "ExternalAgentDescriptor",
    "ExternalAgentError",
    "ExternalAgentResult",
    "builtin_descriptor_dir",
    "collect_external_agent_skills",
    "collect_external_agent_tools",
    "descriptor_load_errors",
    "extract_path",
    "external_agent_load_errors",
    "external_agent_tool_root_errors",
    "external_agent_tool_surface",
    "external_skill_targets",
    "get_descriptor",
    "list_external_agent_views",
    "load_descriptor_file",
    "load_descriptors",
    "mini_yaml_load",
    "registered_agent_ids",
    "reload_descriptors",
    "render_template",
    "run_external_agent",
    "user_descriptor_dir",
]


def external_agent_tool_surface(descriptor: ExternalAgentDescriptor, workspace: str) -> dict:
    """一个外部 agent 的工具面：声明式 + ``tools.roots`` 目录导入。

    返回 ``items``（归一后的工具声明）、``roots``（已展开的目录）、``errors``
    （坏清单文件的原因）、``declared``（描述符里显式声明的 id）。界面据此区分
    「它自己带的」与「从目录导进来的」。
    """
    declared = [dict(t) for t in descriptor.tools]
    declared_ids = [str(t.get("id")) for t in declared if t.get("id")]
    roots: list[str] = []
    errors: list[str] = []
    items = list(declared)

    if descriptor.tool_roots:
        try:
            from .tool_roots import discover_tool_manifests

            outcome = discover_tool_manifests(
                list(descriptor.tool_roots), workspace=str(workspace or ".")
            )
            roots = list(outcome.roots)
            errors = list(outcome.errors)
            seen = set(declared_ids)
            for tool in outcome.tools:
                if tool.id in seen:
                    continue
                seen.add(tool.id)
                items.append(tool.as_dict())
        except Exception as exc:  # pragma: no cover - 扫描器不可用时只剩声明式工具
            errors.append(f"{type(exc).__name__}: {exc}")

    return {
        "items": items,
        "roots": roots,
        "errors": errors,
        "declared": declared_ids,
    }


def external_agent_tool_root_errors(workspace: str) -> list[str]:
    """``tools.roots`` 目录里读不懂的清单文件及原因（排障用）。

    坏文件**只跳过它自己** —— 和描述符加载同一套容错：一个写坏的 ``foo.json``
    不该让整个工具目录失效。返回 ``"<agentId>: <文件>: <原因>"`` 形式的行。
    """
    out: list[str] = []
    for descriptor in load_descriptors().values():
        if not descriptor.tool_roots:
            continue
        surface = external_agent_tool_surface(descriptor, workspace)
        for reason in surface["errors"]:
            out.append(f"{descriptor.id}: {reason}")
    return out


def list_external_agent_views(workspace: Optional[str] = None) -> list[dict]:
    """给界面/接口用的外部 agent 摘要列表。

    ``workspace`` 只有 ``tools.roots`` / ``skills.roots`` 的相对路径展开需要它，
    省略时按 ``os.getcwd()``。
    """
    import os as _os

    ws = str(workspace or _os.getcwd())
    out: list[dict] = []
    for descriptor in load_descriptors().values():
        surface = external_agent_tool_surface(descriptor, ws)
        out.append(
            {
                "id": descriptor.id,
                "name": descriptor.name,
                "emoji": descriptor.emoji,
                "description": descriptor.description,
                "transport": descriptor.transport,
                "builtin": descriptor.builtin,
                "enabled": descriptor.enabled,
                "tools": [str(t["id"]) for t in surface["items"] if t.get("id")],
                "declaredTools": surface["declared"],
                "toolRoots": list(descriptor.tool_roots),
                "toolRootPaths": surface["roots"],
                "toolRootErrors": surface["errors"],
                "skillRoots": list(descriptor.skill_roots),
                "borrowTools": bool(descriptor.borrow_tools),
                "borrowSkills": bool(descriptor.borrow_skills),
                "source": descriptor.source_path,
            }
        )
    return out


def external_agent_load_errors() -> list[str]:
    """最近一次加载中跳过的描述符文件及原因（设置页排障用）。"""
    return list(descriptor_load_errors())


def external_skill_targets(workspace: str) -> list[dict]:
    """每个**启用**的外部 agent 自己的技能根 —— 「给这个 agent 装技能」的落点。

    id 形如 ``<agentId>#<序号>``：序号即描述符 ``skills.roots`` 里的声明顺序。
    界面只回传这个 id，**路径一律在这里解析** —— 不让客户端直接塞任意路径进来。
    """
    out: list[dict] = []
    try:
        from ..zcode.integration import roots_from_patterns
    except Exception:  # pragma: no cover - 扫描器不可用时这一栏就是空的
        return out

    workspace = str(workspace or ".")
    for descriptor in load_descriptors().values():
        if not descriptor.enabled or not descriptor.skill_roots:
            continue
        try:
            roots = roots_from_patterns(list(descriptor.skill_roots), workspace=workspace)
        except Exception:  # pragma: no cover - 单个 agent 失败不该拖累别的
            continue
        for index, root in enumerate(roots):
            out.append(
                {
                    "id": f"{descriptor.id}#{index}",
                    "kind": "external",
                    "label": f"{descriptor.name} · {root.path}",
                    "path": root.path,
                    "scope": root.scope,
                    "agentId": descriptor.id,
                    "agentName": descriptor.name,
                }
            )
    return out


def collect_external_agent_tools(workspace: str) -> list[dict]:
    """所有**启用**的外部 agent 的工具面（含目录导入的），供界面统一展示。

    每项带 ``agentId`` / ``agentName`` / ``declared``（是否描述符里显式声明）/
    ``path``（来自哪个清单文件），界面据此区分「它声明的」与「从目录导进来的」。
    """
    out: list[dict] = []
    ws = str(workspace or ".")
    for descriptor in load_descriptors().values():
        if not descriptor.enabled:
            continue
        surface = external_agent_tool_surface(descriptor, ws)
        declared = set(surface["declared"])
        for item in surface["items"]:
            tool_id = str(item.get("id") or "")
            if not tool_id:
                continue
            out.append(
                {
                    "id": tool_id,
                    "name": str(item.get("name") or tool_id),
                    "description": str(item.get("description") or ""),
                    "agentId": descriptor.id,
                    "agentName": descriptor.name,
                    "declared": tool_id in declared,
                    "path": str(item.get("path") or ""),
                }
            )
    return out


def collect_external_agent_skills(
    workspace: str,
    *,
    host_skill_names: Optional[set[str]] = None,
) -> list[dict]:
    """把所有**启用**的外部 agent 自己的技能收集成一份列表（只读展示用）。

    这些技能的正文不在主智能体技能库里 —— 主智能体既不能激活也不能卸载它们，
    所以把它们单独列出来，并如实标出属于哪个 agent、在哪个根、是否与主智能体同名。

    ``host_skill_names`` 用来标 ``shadowed``（同名不代表同一份正文，不做合并）。
    单个 agent 的技能读不到只跳过它，不影响其它 agent。
    """
    out: list[dict] = []
    descriptors = load_descriptors()
    if not any(d.enabled and d.skill_roots for d in descriptors.values()):
        return out
    try:
        from ..zcode.integration import discover_skills_at_roots
    except Exception:  # pragma: no cover - 扫描器不可用时这一栏就是空的
        return out

    host_names = set(host_skill_names or ())
    workspace = str(workspace or ".")
    for descriptor in descriptors.values():
        if not descriptor.enabled or not descriptor.skill_roots:
            continue
        try:
            outcome = discover_skills_at_roots(
                list(descriptor.skill_roots), workspace=workspace
            )
        except Exception:  # pragma: no cover - 单个 agent 失败不该拖累别的
            continue
        for skill in outcome.skills:
            out.append(
                {
                    "name": skill.name,
                    "description": skill.description,
                    "agentId": descriptor.id,
                    "agentName": descriptor.name,
                    "scope": skill.scope,
                    "source": skill.source,
                    "path": skill.path,
                    # 所属技能根 —— 界面据此定位「它属于哪个可安装落点」。
                    "rootPath": getattr(skill, "root_path", ""),
                    "whenToUse": skill.when_to_use,
                    # 只读：正文不在主智能体技能库里。
                    "readOnly": True,
                    "shadowed": skill.name in host_names,
                }
            )
    return out
