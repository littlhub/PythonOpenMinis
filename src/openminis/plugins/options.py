"""[T-plugins-manager] 插件配置里的「动态选项」。

清单（plugin.json）是随包发布的静态文件，但有些配置项的可选值来自**运行时
状态** —— 最典型的就是「由哪个 agent 接待这个通道」：可选的是主 agent 的各个
身份（``assistant`` 与用户自定义身份）加上助理页里配好的子代理，这些东西随时
会变，不可能写死在清单里。

所以清单里只声明**来源**（``"optionsFrom": "agents"``），真正的选项在这里解析，
由 ``registry.status()`` 回填给界面。取值约定：

- ``""``        —— 跟随当前身份（默认，网页端切身份它也跟着变）
- ``identity:x``—— 固定用身份 ``x`` 的人设与工具集
- ``subagent:x``—— 交给子代理 ``x`` 跑（它有自己的模型、人设、工具）
"""

from __future__ import annotations

from typing import Any

#: 「跟随当前身份」的取值，也是默认。
FOLLOW_CURRENT = ""
IDENTITY_PREFIX = "identity:"
SUBAGENT_PREFIX = "subagent:"


def agent_options() -> list[dict[str, Any]]:
    """给 ``select`` 用的一组 agent 选项（身份 + 子代理）。"""
    out: list[dict[str, Any]] = [
        {
            "value": FOLLOW_CURRENT,
            "label": "跟随当前身份（默认）",
            "group": "默认",
            "kind": "follow",
        }
    ]

    try:
        from ..settings.store import SettingsStore

        store = SettingsStore.get()
        for ident in store.identities_all():
            out.append({
                "value": f"{IDENTITY_PREFIX}{ident.id}",
                "label": f"{ident.emoji or '🧩'} {ident.name}",
                "description": ident.description or "",
                "group": "主 agent 身份",
                "kind": "identity",
            })
    except Exception:  # pragma: no cover - 设置读不到不该拖垮插件页
        pass

    try:
        from ..agent import subagents as subagent_registry
        from ..settings.store import SettingsStore

        store = SettingsStore.get()
        for sub in subagent_registry.list_subagents(store):
            out.append({
                "value": f"{SUBAGENT_PREFIX}{sub.get('id')}",
                "label": f"{sub.get('emoji') or '🤖'} {sub.get('name') or sub.get('id')}",
                "description": sub.get("description") or "",
                "group": "子代理（助理）",
                "kind": "subagent",
            })
    except Exception:  # pragma: no cover - 助理页没配过子代理很正常
        pass

    return out


#: ``optionsFrom`` 取值 → 选项生成函数。
SOURCES = {"agents": agent_options}


def resolve(source: str) -> list[dict[str, Any]]:
    return (SOURCES.get(str(source or "")) or (lambda: []))()


def attach_options(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 ``optionsFrom`` 解析成 ``options``（原地补进字段 dict）。"""
    for field in fields:
        source = str(field.get("optionsFrom") or "")
        if source:
            field["options"] = resolve(source)
    return fields


def parse_target(value: str) -> tuple[str, str]:
    """``"subagent:abc"`` → ``("subagent", "abc")``；空值 → ``("", "")``。"""
    raw = str(value or "").strip()
    if raw.startswith(SUBAGENT_PREFIX):
        return "subagent", raw[len(SUBAGENT_PREFIX):].strip()
    if raw.startswith(IDENTITY_PREFIX):
        return "identity", raw[len(IDENTITY_PREFIX):].strip()
    return "", ""


__all__ = [
    "FOLLOW_CURRENT",
    "IDENTITY_PREFIX",
    "SUBAGENT_PREFIX",
    "SOURCES",
    "agent_options",
    "resolve",
    "attach_options",
    "parse_target",
]
