"""外部 agent 协议描述符 —— 数据模型、加载与校验。

设计目标（用户要求）：**可扩展**。接入一个外部 agent（Codex、Claude Code、
自研 CLI、远端服务…）只需要写一份 YAML/JSON 协议描述符，不用改代码。

描述符放两处：

- 内置：``external_agents/descriptors/*.yaml|json``（随包分发，用户不可删）
- 用户：``~/.openminis/agents/*.yaml|json``（覆盖内置同名项；可增删）

字段全集见 ``external_agents/PROTOCOL.md``。核心是 ``transport``：

- ``internal`` —— 进程内 Python 实现，``entry: <module>:<callable>``；
- ``stdio``    —— 起一个子进程，按 ``input.mode`` 喂任务，按 ``output`` 解析回传；
- ``http``     —— POST 到 ``endpoint``，按 ``output`` 解析回传。

两种传输都收敛到同一个 ``ExternalAgentResult``，调用方（子代理委派链路）拿到的
永远是「一段可读文本 + 成败标记」。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "ExternalAgentError",
    "ExternalAgentDescriptor",
    "DESCRIPTOR_VERSION",
    "builtin_descriptor_dir",
    "user_descriptor_dir",
    "load_descriptors",
    "load_descriptor_file",
    "get_descriptor",
    "registered_agent_ids",
    "reload_descriptors",
    "render_template",
    "extract_path",
    "mini_yaml_load",
]

DESCRIPTOR_VERSION = 1

_SUPPORTED_TRANSPORTS = ("internal", "stdio", "http")
_SUPPORTED_INPUT_MODES = ("stdin-json", "stdin-text", "argv-template", "none")
_SUPPORTED_OUTPUT_FORMATS = ("text", "json", "json-lines")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class ExternalAgentError(Exception):
    """描述符不合法，或外部 agent 调用失败。"""


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class ExternalAgentDescriptor:
    """一份外部 agent 协议描述符。"""

    id: str
    name: str
    transport: str
    description: str = ""
    emoji: str = "🔌"
    persona: str = ""
    #: 内置项不可删除（可以覆盖配置）。
    builtin: bool = False
    enabled: bool = True

    # transport=internal
    entry: str = ""

    # transport=stdio
    command: list[str] = field(default_factory=list)
    input_mode: str = "stdin-json"
    input_args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd_mode: str = "workspace"
    timeout_seconds: float = 600.0

    # transport=http
    endpoint: str = ""
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)

    # 回传解析（stdio / http 共用）
    output_format: str = "text"
    result_path: Optional[str] = None
    text_path: Optional[str] = None
    error_path: Optional[str] = None

    # 能力面（供界面展示与子代理配置）
    tools: list[dict[str, Any]] = field(default_factory=list)
    skill_roots: list[str] = field(default_factory=list)
    #: ``tools.roots`` —— 工具清单**目录**（像技能根一样，配好即自动导入）。
    #: 一个工具一个文件 / 一个目录，不用把每个工具手写进描述符。
    tool_roots: list[str] = field(default_factory=list)
    system_prompt: str = ""
    system_prompt_file: str = ""
    #: 与主智能体**互借**：默认开启（用户要求「tool / skill 可以相互借用」）。
    #: 借工具 = 主 agent 的工具在这个外部 agent 里也能用；借技能 = 主 agent 的技能库
    #: 也进入它的技能发现（同名时它自己的胜出）。
    borrow_tools: bool = True
    borrow_skills: bool = True
    #: 描述符文件来源（诊断用）。
    source_path: str = ""

    @property
    def default_tools(self) -> list[str]:
        return [str(t.get("id")) for t in self.tools if t.get("id")]

    def all_tools(self, workspace: str) -> list[dict[str, Any]]:
        """声明式工具 + **工具目录**导入的工具，合并成一份能力面。

        同名 id 以描述符里**显式声明**的为准 —— 显式声明总是压过目录扫描，
        这样「目录里放一批通用的、描述符里钉死几个关键的」两种用法可以并存。
        """
        out = [dict(t) for t in self.tools]
        seen = {str(t.get("id")) for t in out if t.get("id")}
        if self.tool_roots:
            from .tool_roots import discover_tool_manifests

            outcome = discover_tool_manifests(list(self.tool_roots), workspace=workspace)
            for tool in outcome.tools:
                if tool.id in seen:
                    continue
                seen.add(tool.id)
                out.append(tool.as_dict())
        return out

    def tool_ids(self, workspace: str) -> list[str]:
        return [str(t["id"]) for t in self.all_tools(workspace) if t.get("id")]

    def resolved_system_prompt(self) -> str:
        if self.system_prompt.strip():
            return self.system_prompt.strip()
        if self.system_prompt_file:
            candidates = [
                Path(self.system_prompt_file),
                Path(self.source_path).parent / self.system_prompt_file
                if self.source_path
                else Path(self.system_prompt_file),
            ]
            for candidate in candidates:
                try:
                    if candidate.is_file():
                        return candidate.read_text(encoding="utf-8").strip()
                except OSError:
                    continue
        return ""


# ---------------------------------------------------------------------------
# 目录
# ---------------------------------------------------------------------------
def builtin_descriptor_dir() -> Path:
    return Path(__file__).resolve().parent / "descriptors"


def user_descriptor_dir() -> Path:
    """用户描述符目录（``OPENMINIS_AGENTS_DIR`` 可覆盖，便于部署与测试）。"""
    override = os.environ.get("OPENMINIS_AGENTS_DIR")
    if override:
        return Path(override)
    return Path(os.path.expanduser("~")) / "openminis" / "agents"


# ---------------------------------------------------------------------------
# 模板与取值
# ---------------------------------------------------------------------------
_TEMPLATE_VAR = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def render_template(text: str, variables: dict[str, Any]) -> str:
    """把 ``{{var}}`` 替换成变量值；未知变量替换成空串（不抛错）。

    刻意不做条件/循环：模板只负责「把任务塞进命令行」，逻辑属于 runner。
    """
    if not isinstance(text, str) or "{{" not in text:
        return text
    return _TEMPLATE_VAR.sub(lambda m: str(variables.get(m.group(1), "") or ""), text)


_PATH_TOKEN = re.compile(r"\.([A-Za-z_][A-Za-z0-9_-]*)|\[(\d+)\]")


def extract_path(data: Any, path: str) -> Any:
    """按 ``$.a.b[0].c`` 取值；取不到返回 ``None``。

    外部 agent 的回传结构千差万别，取值必须**可声明**，否则每接一个都要写代码。
    """
    if not path:
        return data
    expr = path.strip()
    if expr.startswith("$"):
        expr = expr[1:]
    current = data
    for match in _PATH_TOKEN.finditer(expr):
        if current is None:
            return None
        if match.group(1) is not None:
            key = match.group(1)
            if isinstance(current, dict):
                current = current.get(key)
            else:
                current = getattr(current, key, None)
        else:
            index = int(match.group(2))
            try:
                current = current[index]
            except (IndexError, KeyError, TypeError):
                return None
    return current


# ---------------------------------------------------------------------------
# YAML 读取
#
# 首选 pyyaml（``pyproject`` 已声明依赖）。但它缺席时**不能**让内置描述符一起消失
# —— 包括内置 ZCode，它消失就等于整个外部 agent 面消失。所以带一个极简兜底解析器：
# 只覆盖协议用到的子集（嵌套映射、``- `` 列表、行内 ``[a, b]`` / ``{k: v}``、
# 引号字符串、布尔与数字、``#`` 注释）。**不支持**多行标量/锚点/别名等 YAML 高级特性
# —— 需要那些时请装 pyyaml，或改用 .json。
# ---------------------------------------------------------------------------
def _strip_comment(line: str) -> str:
    in_single = in_double = False
    for index, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            if index == 0 or line[index - 1] in " \t":
                return line[:index]
    return line


def _split_inline(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    in_single = in_double = False
    current: list[str] = []
    for char in text:
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
            elif char == "," and depth == 0:
                parts.append("".join(current))
                current = []
                continue
        current.append(char)
    if current:
        parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _scalar(text: str) -> Any:
    value = text.strip()
    if not value:
        return None
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        return value[1:-1]
    lowered = value.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if lowered in ("null", "~"):
        return None
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [_scalar(part) for part in _split_inline(inner)] if inner else []
    if value.startswith("{") and value.endswith("}"):
        inner = value[1:-1].strip()
        out: dict[str, Any] = {}
        for part in _split_inline(inner):
            key, _, raw = part.partition(":")
            out[key.strip().strip("\"'")] = _scalar(raw)
        return out
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _yaml_lines(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        body = _strip_comment(raw).rstrip()
        if not body.strip():
            continue
        out.append((len(body) - len(body.lstrip(" ")), body))
    return out


def _parse_yaml_mapping(lines: list[tuple[int, str]], index: int) -> tuple[dict[str, Any], int]:
    indent = lines[index][0]
    mapping: dict[str, Any] = {}
    while index < len(lines):
        cur_indent, cur = lines[index]
        if cur_indent != indent or cur.lstrip().startswith("- "):
            break
        key, sep, value = cur.partition(":")
        if not sep:
            break
        index += 1
        if value.strip():
            mapping[key.strip()] = _scalar(value)
        elif index < len(lines) and lines[index][0] > indent:
            nested, index = _parse_yaml_block(lines, index)
            mapping[key.strip()] = nested
        else:
            mapping[key.strip()] = {}
    return mapping, index


def _parse_yaml_list(lines: list[tuple[int, str]], index: int) -> tuple[list[Any], int]:
    indent = lines[index][0]
    items: list[Any] = []
    while index < len(lines):
        cur_indent, cur = lines[index]
        if cur_indent != indent or not cur.lstrip().startswith("- "):
            break
        text = cur.lstrip()[2:].strip()
        index += 1
        if not text:
            if index < len(lines) and lines[index][0] > indent:
                value, index = _parse_yaml_block(lines, index)
            else:
                value = None
            items.append(value)
            continue
        if ":" in text and not text.startswith(("[", "{", "\"", "'")):
            key, _, raw = text.partition(":")
            entry: dict[str, Any] = {}
            if raw.strip():
                entry[key.strip()] = _scalar(raw)
            elif index < len(lines) and lines[index][0] > indent:
                entry[key.strip()], index = _parse_yaml_block(lines, index)
            else:
                entry[key.strip()] = {}
            if index < len(lines) and lines[index][0] > indent:
                rest, index = _parse_yaml_mapping(lines, index)
                entry.update(rest)
            items.append(entry)
        else:
            items.append(_scalar(text))
    return items, index


def _parse_yaml_block(lines: list[tuple[int, str]], index: int) -> tuple[Any, int]:
    if lines[index][1].lstrip().startswith("- "):
        return _parse_yaml_list(lines, index)
    return _parse_yaml_mapping(lines, index)


def mini_yaml_load(text: str) -> Any:
    """协议子集的 YAML 解析（pyyaml 缺席时的兜底）。"""
    lines = _yaml_lines(text)
    if not lines:
        return {}
    value, _ = _parse_yaml_block(lines, 0)
    return value


def _read_descriptor_file(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        data: Any
        try:
            import yaml
        except ImportError:  # pragma: no cover - 依赖缺失时的兜底路径
            data = mini_yaml_load(raw)
        else:
            data = yaml.safe_load(raw)
    else:
        data = json.loads(raw)
    if not isinstance(data, dict):
        raise ExternalAgentError(f"{path.name}: 描述符顶层必须是对象")
    return data


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return []


def _as_str_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()}


def _parse_descriptor(data: dict[str, Any], source_path: str) -> ExternalAgentDescriptor:
    agent_id = str(data.get("id") or "").strip()
    if not _SLUG_RE.match(agent_id):
        raise ExternalAgentError(
            f"{source_path}: id 需为小写字母/数字开头，可用 a-z0-9_-（≤40 字符），实际: {agent_id!r}"
        )
    transport = str(data.get("transport") or "").strip().lower()
    if transport not in _SUPPORTED_TRANSPORTS:
        raise ExternalAgentError(
            f"{source_path}: transport 必须是 {_SUPPORTED_TRANSPORTS} 之一，实际: {transport!r}"
        )

    stdio = data.get("stdio") if isinstance(data.get("stdio"), dict) else {}
    http_conf = data.get("http") if isinstance(data.get("http"), dict) else {}
    input_conf = stdio.get("input") if isinstance(stdio.get("input"), dict) else {}
    output_conf = data.get("output") if isinstance(data.get("output"), dict) else {}
    prompt_conf = data.get("prompt") if isinstance(data.get("prompt"), dict) else {}
    skills_conf = data.get("skills") if isinstance(data.get("skills"), dict) else {}
    caps = data.get("capabilities") if isinstance(data.get("capabilities"), dict) else {}
    borrow_conf = data.get("borrow") if isinstance(data.get("borrow"), dict) else {}

    input_mode = str(input_conf.get("mode") or "stdin-json").strip().lower()
    if input_mode not in _SUPPORTED_INPUT_MODES:
        raise ExternalAgentError(
            f"{source_path}: stdio.input.mode 必须是 {_SUPPORTED_INPUT_MODES} 之一"
        )
    output_format = str(output_conf.get("format") or "text").strip().lower()
    if output_format not in _SUPPORTED_OUTPUT_FORMATS:
        raise ExternalAgentError(
            f"{source_path}: output.format 必须是 {_SUPPORTED_OUTPUT_FORMATS} 之一"
        )

    entry = str(data.get("entry") or "").strip()
    command = _as_str_list(stdio.get("command"))
    endpoint = str(http_conf.get("endpoint") or data.get("endpoint") or "").strip()

    if transport == "internal" and not entry:
        raise ExternalAgentError(f"{source_path}: transport=internal 必须提供 entry: <module>:<callable>")
    if transport == "stdio" and not command:
        raise ExternalAgentError(f"{source_path}: transport=stdio 必须提供 stdio.command")
    if transport == "http" and not endpoint:
        raise ExternalAgentError(f"{source_path}: transport=http 必须提供 http.endpoint")

    tools_raw = data.get("tools")
    tools: list[dict[str, Any]] = []
    tool_roots: list[str] = []
    if isinstance(tools_raw, list):
        for item in tools_raw:
            if isinstance(item, dict) and item.get("id"):
                tools.append(
                    {
                        "id": str(item["id"]),
                        "name": str(item.get("name") or item["id"]),
                        "description": str(item.get("description") or ""),
                        "input_schema": item.get("input_schema") or item.get("inputSchema") or {},
                    }
                )
    elif isinstance(tools_raw, dict):
        # 两种写法都收：``tools: {roots: [...]}`` 与 ``tools: {declared: [...]}``。
        # 目录导入与手写声明可以并存 —— 目录里放常用的，描述符里放必须显式声明的。
        tool_roots = _as_str_list(tools_raw.get("roots") or tools_raw.get("dirs"))
        declared = tools_raw.get("declared") or tools_raw.get("items") or []
        if isinstance(declared, list):
            for item in declared:
                if isinstance(item, dict) and item.get("id"):
                    tools.append(
                        {
                            "id": str(item["id"]),
                            "name": str(item.get("name") or item["id"]),
                            "description": str(item.get("description") or ""),
                            "input_schema": item.get("input_schema")
                            or item.get("inputSchema")
                            or {},
                        }
                    )
    # 顶层 ``toolRoots`` 作为别名（写起来更直白）
    if not tool_roots:
        tool_roots = _as_str_list(data.get("toolRoots") or data.get("tool_roots"))

    return _finish(
        agent_id,
        data,
        transport,
        input_mode,
        output_format,
        entry,
        command,
        endpoint,
        tools,
        source_path,
        stdio,
        http_conf,
        input_conf,
        output_conf,
        prompt_conf,
        skills_conf,
        borrow_conf,
        tool_roots,
    )


def _finish(
    agent_id: str,
    data: dict[str, Any],
    transport: str,
    input_mode: str,
    output_format: str,
    entry: str,
    command: list[str],
    endpoint: str,
    tools: list[dict[str, Any]],
    source_path: str,
    stdio: dict[str, Any],
    http_conf: dict[str, Any],
    input_conf: dict[str, Any],
    output_conf: dict[str, Any],
    prompt_conf: dict[str, Any],
    skills_conf: dict[str, Any],
    borrow_conf: dict[str, Any],
    tool_roots: Optional[list[str]] = None,
) -> ExternalAgentDescriptor:
    return ExternalAgentDescriptor(
        id=agent_id,
        name=str(data.get("name") or agent_id),
        transport=transport,
        description=str(data.get("description") or ""),
        emoji=str(data.get("emoji") or "🔌")[:4],
        persona=str(data.get("persona") or ""),
        builtin=bool(data.get("builtin", False)),
        enabled=bool(data.get("enabled", True)),
        entry=entry,
        command=command,
        input_mode=input_mode,
        input_args=_as_str_list(input_conf.get("args")),
        env=_as_str_map(stdio.get("env")),
        cwd_mode=str(stdio.get("cwd") or "workspace"),
        timeout_seconds=float(stdio.get("timeoutSeconds") or stdio.get("timeout_seconds") or 600),
        endpoint=endpoint,
        method=str(http_conf.get("method") or "POST").upper(),
        headers=_as_str_map(http_conf.get("headers")),
        output_format=output_format,
        result_path=str(output_conf.get("resultPath") or output_conf.get("result_path") or "") or None,
        text_path=str(output_conf.get("textPath") or output_conf.get("text_path") or "") or None,
        error_path=str(output_conf.get("errorPath") or output_conf.get("error_path") or "") or None,
        tools=tools,
        skill_roots=_as_str_list(skills_conf.get("roots")),
        tool_roots=list(tool_roots or []),
        system_prompt=str(prompt_conf.get("system") or ""),
        system_prompt_file=str(prompt_conf.get("systemFile") or prompt_conf.get("system_file") or ""),
        borrow_tools=bool(borrow_conf.get("tools", True)),
        borrow_skills=bool(borrow_conf.get("skills", True)),
        source_path=source_path,
    )


def load_descriptor_file(path: Path) -> ExternalAgentDescriptor:
    """加载单个描述符文件（``.yaml`` / ``.yml`` / ``.json``）。"""
    try:
        data = _read_descriptor_file(path)
    except json.JSONDecodeError as exc:
        raise ExternalAgentError(f"{path.name}: 不是合法 JSON —— {exc}") from exc
    return _parse_descriptor(data, str(path))


def _iter_descriptor_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    out = [
        child
        for child in sorted(directory.iterdir())
        if child.is_file() and child.suffix.lower() in (".yaml", ".yml", ".json")
    ]
    return out


_DESCRIPTOR_CACHE: dict[str, ExternalAgentDescriptor] = {}
_DESCRIPTOR_ERRORS: list[str] = []


def load_descriptors(*, force: bool = False) -> dict[str, ExternalAgentDescriptor]:
    """加载全部描述符（内置 + 用户，用户同名覆盖内置）。

    单个文件坏掉**只跳过它并记下原因** —— 一份写错的 YAML 不该让所有外部 agent
    一起消失。
    """
    global _DESCRIPTOR_CACHE, _DESCRIPTOR_ERRORS
    if _DESCRIPTOR_CACHE and not force:
        return dict(_DESCRIPTOR_CACHE)

    loaded: dict[str, ExternalAgentDescriptor] = {}
    errors: list[str] = []
    for directory in (builtin_descriptor_dir(), user_descriptor_dir()):
        for path in _iter_descriptor_files(directory):
            try:
                descriptor = load_descriptor_file(path)
            except ExternalAgentError as exc:
                errors.append(str(exc))
                continue
            loaded[descriptor.id] = descriptor
    _DESCRIPTOR_CACHE = loaded
    _DESCRIPTOR_ERRORS = errors
    return dict(loaded)


def reload_descriptors() -> dict[str, ExternalAgentDescriptor]:
    return load_descriptors(force=True)


def descriptor_load_errors() -> list[str]:
    """最近一次加载中跳过的文件及原因（诊断面）。"""
    load_descriptors()
    return list(_DESCRIPTOR_ERRORS)


def get_descriptor(agent_id: str) -> Optional[ExternalAgentDescriptor]:
    return load_descriptors().get(str(agent_id))


def registered_agent_ids() -> set[str]:
    """已注册的外部 agent id 集合（子代理把这些视为内置项）。"""
    return {aid for aid, d in load_descriptors().items() if d.enabled}
