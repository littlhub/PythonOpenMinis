"""从**目录**导入外部 agent 的工具清单（描述符 ``tools.roots``）。

用户要求：接一个外部 agent 时，在 YAML 里把「技能目录 / 工具目录」配好就自动导入
—— 像绘世扫模型目录那样，不用改代码、也不用把每个工具手写在描述符里。

目录形态（与技能根同一套约定：只扫一层，避免深挖无关目录）：

* ``<root>/<name>.json`` / ``<name>.yaml`` / ``<name>.yml`` —— 一个文件一份清单；
* ``<root>/<tool-name>/tool.json``（或 ``tool.yaml`` / ``tool.yml``）—— 一个工具一个目录；
* 文件内容可以是单个工具（含 ``id``）、工具数组、或 ``{tools: [...]}`` 索引。

坏文件只跳过并记原因，不影响其余文件 —— 和描述符加载是同一套容错策略。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    "DiscoveredTool",
    "ToolDiscoveryOutcome",
    "normalize_tool_item",
    "discover_tool_manifests",
]

_TOOL_FILE_STEMS = ("tool", "tools", "manifest")
_SUFFIXES = (".json", ".yaml", ".yml")


@dataclass(slots=True)
class DiscoveredTool:
    """一份工具声明（形态与描述符 ``tools:`` 列表里的元素一致）。"""

    id: str
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    #: 它来自哪个文件（诊断用）。
    path: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "input_schema": dict(self.input_schema),
            "path": self.path,
        }


@dataclass(slots=True)
class ToolDiscoveryOutcome:
    tools: list[DiscoveredTool] = field(default_factory=list)
    roots: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def normalize_tool_item(item: Any, *, path: str = "") -> Optional[DiscoveredTool]:
    """把一份声明归一成 ``DiscoveredTool``；没有 ``id`` 就返回 ``None``。"""
    if isinstance(item, str):
        text = item.strip()
        if not text:
            return None
        return DiscoveredTool(id=text, name=text, description="", path=path)
    if not isinstance(item, dict):
        return None
    tool_id = str(item.get("id") or item.get("name") or "").strip()
    if not tool_id:
        return None
    schema = item.get("input_schema") or item.get("inputSchema") or item.get("parameters") or {}
    if not isinstance(schema, dict):
        schema = {}
    return DiscoveredTool(
        id=tool_id,
        name=str(item.get("name") or tool_id),
        description=str(item.get("description") or ""),
        input_schema=schema,
        path=path,
    )


def _load_manifest(path: str) -> Any:
    """读一份清单文件（.json 走 json，其余走 YAML；YAML 失败时退到 mini 解析器）。"""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        raw = handle.read()
    if path.lower().endswith(".json"):
        return json.loads(raw)
    try:
        import yaml  # type: ignore

        return yaml.safe_load(raw)
    except ImportError:
        from .protocol import mini_yaml_load

        return mini_yaml_load(raw)


def _items_from_payload(payload: Any) -> list[Any]:
    """从一份清单里取出工具条目列表（兼容单条 / 数组 / ``{tools: [...]}``）。"""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("tools", "items", "entries"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        # 单条工具声明
        if any(k in payload for k in ("id", "name")):
            return [payload]
    return []


def _manifest_files(root: str) -> list[str]:
    """根目录下的一层清单文件：直接放的 ``*.json/yaml`` + 一层子目录里的 ``tool.*``。"""
    found: list[str] = []
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name.lower())
    except OSError:
        return found
    for entry in entries:
        try:
            if entry.is_file() and os.path.splitext(entry.name)[1].lower() in _SUFFIXES:
                found.append(entry.path)
            elif entry.is_dir():
                for stem in _TOOL_FILE_STEMS:
                    for suffix in _SUFFIXES:
                        candidate = os.path.join(entry.path, stem + suffix)
                        if os.path.isfile(candidate):
                            found.append(candidate)
        except OSError:
            continue
    return found


def discover_tool_manifests(
    patterns: list[str],
    *,
    workspace: str,
    home_directory: Optional[str] = None,
) -> ToolDiscoveryOutcome:
    """按 ``tools.roots`` 扫描出工具清单。

    同名 id **先到先得**（先声明的根、根内按文件名升序），与技能合并规则一致。
    路径展开（``<workspace>`` / 行首 ``~`` / 相对路径）复用技能的同一套实现 ——
    「技能目录」和「工具目录」在描述符里是同一个心智模型。
    """
    outcome = ToolDiscoveryOutcome()
    if not patterns:
        return outcome

    try:
        from ..zcode.integration import expand_skill_root
    except Exception:  # pragma: no cover - 路径展开器不可用时无从扫描
        outcome.errors.append("路径展开器不可用（zcode.integration 导入失败）")
        return outcome

    seen: set[str] = set()
    for pattern in patterns:
        resolved = expand_skill_root(
            str(pattern), workspace=workspace, home_directory=home_directory
        )
        if resolved is None:
            continue
        if resolved in outcome.roots:
            continue
        outcome.roots.append(resolved)
        if not os.path.isdir(resolved):
            continue
        for path in _manifest_files(resolved):
            try:
                payload = _load_manifest(path)
            except Exception as exc:  # 坏文件只跳过它自己
                outcome.errors.append(f"{path}: {type(exc).__name__}: {exc}")
                continue
            items = _items_from_payload(payload)
            if not items:
                outcome.errors.append(f"{path}: 没有找到工具条目（需要 id 或 tools 列表）")
                continue
            for item in items:
                tool = normalize_tool_item(item, path=path)
                if tool is None:
                    outcome.errors.append(f"{path}: 条目缺少 id")
                    continue
                if tool.id in seen:
                    continue
                seen.add(tool.id)
                outcome.tools.append(tool)
    return outcome
