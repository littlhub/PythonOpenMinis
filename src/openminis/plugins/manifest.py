"""[T-channels-plugins] 插件清单（plugin.json）。

插件分两类，靠 ``runtime`` 区分：

- ``engine``：引擎内驱动（Python 实现），比如内置的 QQ 通道；清单只需 ``driver``。
- ``process``：**外部程序**（node / python / exe），比如 dsh-bridge 这类现成的
  桥接项目；清单里给 ``process`` 段（command/args/cwd/env/healthUrl），引擎负责
  起停、健康检查与日志收集。没有清单的 zip 也能导入 —— 会按项目类型（Node 的
  package.json / Python 的入口文件）推断出一份，见 ``store.import_plugin``。
- ``manual``：引擎不起连接。**但有 ``tools`` 段的插件仍可「启用」** —— 它不需要
  常驻，工具是 agent 调用时才临时起一次。

插件的能力**不限于通道**：``tools`` 段让插件给 agent 加工具（声明式 —— 引擎把
模型的调用参数以 JSON 从 stdin 喂给插件目录里的命令，stdout 就是工具输出）。
于是「装一个插件」既能接通一个 IM，也能给 agent 添一项新能力。

``category`` 只影响界面分组：``channel``（通道，出现在「通道」页）/ ``tool`` /
``other``（出现在「插件」页）。

字段类型：
- ``text`` / ``password``：单行输入（``secret=True`` 的回传时脱敏）
- ``number``：整数输入（带 min/max）
- ``switch``：布尔开关
- ``list``：一行一个的字符串列表（白名单、允许的群号…）
- ``csv``：逗号分隔的字符串列表
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: 清单里允许出现的字段类型。``select`` 的选项来自运行时状态（见 ``optionsFrom``）。
FIELD_TYPES = ("text", "password", "number", "switch", "list", "csv", "select")

#: ``select`` 字段可以去哪几处取选项。引擎侧的解析见 ``plugins.options``。
OPTION_SOURCES = ("agents",)

#: 插件运行形态。
RUNTIME_ENGINE = "engine"
RUNTIME_PROCESS = "process"
#: 识别不出入口的包：只登记（能看到、能删）；有 ``tools`` 段时按需起工具进程。
RUNTIME_MANUAL = "manual"
RUNTIMES = (RUNTIME_ENGINE, RUNTIME_PROCESS, RUNTIME_MANUAL)

#: 界面分组。
CATEGORY_CHANNEL = "channel"
CATEGORY_TOOL = "tool"
CATEGORIES = (CATEGORY_CHANNEL, CATEGORY_TOOL, "other")

#: 清单文件与配置文件的名字（放在插件自己那个目录里）。
MANIFEST_NAME = "plugin.json"
CONFIG_NAME = "config.json"

#: 配置文件里记录「是否启用」的键（不是用户配置，属于插件状态）。
ENABLED_KEY = "_enabled"

#: 外部程序插件（``runtime == process``）自动获得的配置项：可执行文件搜索目录。
#:
#: 引擎进程往往是用户从 .bat / exe 起的，PATH 很干净；而 node 这类运行时经常只
#: 装在某个用户目录里。``which`` 找不到就直接报「找不到可执行文件：node」，用户
#: 不知道该改哪里 —— 所以每个外部程序插件都白送这一项，填一次即可。
RUNTIME_PATH_KEY = "runtimePath"

#: 上面那一项在界面上的样子（清单里没声明时自动补上）。
RUNTIME_PATH_FIELD = {
    "key": RUNTIME_PATH_KEY,
    "label": "可执行文件搜索目录",
    "type": "list",
    "help": (
        "一行一个目录。引擎会先在这些目录里找 startup 命令用到的程序"
        "（node / npm / python…），并把它加进子进程的 PATH。"
        "报「找不到可执行文件：node」时，把 node.exe 所在的目录填进来即可"
        "（Windows 上通常是 C:\\Program Files\\nodejs）。"
    ),
}

#: 一次插件工具调用的默认超时（秒）。
DEFAULT_TOOL_TIMEOUT_SEC = 60
#: 插件工具输出喂给模型的字符上限（超出截断，省上下文）。
TOOL_OUTPUT_MAX_CHARS = 20000


class ManifestError(ValueError):
    """清单不合法（缺字段、类型不认识、驱动不存在）。"""


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    type: str = "text"
    required: bool = False
    secret: bool = False
    default: Any = None
    help: str = ""
    minimum: int | None = None
    maximum: int | None = None
    placeholder: str = ""
    #: ``select`` 专用：选项从哪来（``"agents"`` = 当前身份 + 子代理）。
    #: 清单是静态的，而可选 agents 是运行时状态，所以这里存的是「来源」，
    #: 真正的一组值由 ``plugins.options`` 在读取时注入。
    options_from: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "FieldSpec":
        if not isinstance(raw, dict):
            raise ManifestError("字段定义必须是对象")
        key = str(raw.get("key") or "").strip()
        if not key:
            raise ManifestError("字段缺少 key")
        kind = str(raw.get("type") or "text").strip()
        if kind not in FIELD_TYPES:
            raise ManifestError(f"字段 {key} 的类型不认识：{kind}")
        return cls(
            key=key,
            label=str(raw.get("label") or key),
            type=kind,
            required=bool(raw.get("required")),
            secret=bool(raw.get("secret")),
            default=raw.get("default"),
            help=str(raw.get("help") or ""),
            minimum=raw.get("min") if isinstance(raw.get("min"), int) else None,
            maximum=raw.get("max") if isinstance(raw.get("max"), int) else None,
            placeholder=str(raw.get("placeholder") or ""),
            options_from=str(raw.get("optionsFrom") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "secret": self.secret,
            "help": self.help,
        }
        if self.default is not None:
            out["default"] = self.default
        if self.minimum is not None:
            out["min"] = self.minimum
        if self.maximum is not None:
            out["max"] = self.maximum
        if self.placeholder:
            out["placeholder"] = self.placeholder
        if self.options_from:
            out["optionsFrom"] = self.options_from
        return out


@dataclass(frozen=True)
class PluginToolSpec:
    """插件给 agent 加的一个工具（清单里的 ``tools`` 项）。

    执行方式是**声明式**的：agent 调这个工具时，引擎在插件目录里起一次
    ``command``，把 ``{"args": <模型传的参数>, "sessionId": …, "pluginDir": …}``
    以 JSON 从 stdin 喂进去，进程的 stdout 就是工具输出。这样插件用什么语言写
    都行（python / node / 编译出来的 exe），引擎不需要理解它的内部。
    """

    id: str
    name: str = ""
    description: str = ""
    #: JSON Schema 的 ``properties``：``{参数名: {type, description, enum?}}``。
    parameters: dict[str, dict[str, Any]] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    #: 启动命令（相对插件目录解析）；``python`` 会被换成引擎自己的解释器。
    command: tuple[str, ...] = ()
    cwd: str = "."
    timeout_sec: int = DEFAULT_TOOL_TIMEOUT_SEC
    #: 需要用户先配好哪些字段才允许调用（默认不要求）。
    requires_config: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return self.name or self.id

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PluginToolSpec":
        if not isinstance(raw, dict):
            raise ManifestError("tools 里的每一项都必须是对象")
        tool_id = str(raw.get("id") or "").strip()
        if not tool_id:
            raise ManifestError("工具缺少 id")
        if not all(c.isalnum() or c in "-_." for c in tool_id):
            raise ManifestError(f"工具 id 只能用字母/数字/._-：{tool_id}")
        command = raw.get("command")
        # 刻意**只接受数组**：`"python count.py"` 这种字符串没法可靠地切成
        # 参数（路径带空格就完了），与其猜不如让作者写清楚。
        if not isinstance(command, list) or not [c for c in command if str(c).strip()]:
            raise ManifestError(
                f"工具 {tool_id} 的 command 要写成数组，例如 "
                '["python", "count.py"]'
            )
        params = raw.get("parameters")
        if params is not None and not isinstance(params, dict):
            raise ManifestError(f"工具 {tool_id} 的 parameters 必须是对象")
        clean_params: dict[str, dict[str, Any]] = {}
        for key, spec in (params or {}).items():
            if not isinstance(spec, dict):
                raise ManifestError(f"工具 {tool_id} 的参数 {key} 必须是对象")
            item: dict[str, Any] = {
                "type": str(spec.get("type") or "string"),
                "description": str(spec.get("description") or ""),
            }
            enum = spec.get("enum")
            if isinstance(enum, list) and enum:
                item["enum"] = [str(v) for v in enum]
            clean_params[str(key)] = item
        required_raw = raw.get("required")
        required = tuple(
            str(v) for v in required_raw if str(v).strip()
        ) if isinstance(required_raw, list) else ()
        timeout = raw.get("timeoutSec")
        try:
            timeout_sec = int(timeout) if timeout is not None else DEFAULT_TOOL_TIMEOUT_SEC
        except (TypeError, ValueError):
            timeout_sec = DEFAULT_TOOL_TIMEOUT_SEC
        needs = raw.get("requiresConfig")
        return cls(
            id=tool_id,
            name=str(raw.get("name") or ""),
            description=str(raw.get("description") or ""),
            parameters=clean_params,
            required=required,
            command=tuple(str(c) for c in command),
            cwd=str(raw.get("cwd") or "."),
            timeout_sec=max(1, min(timeout_sec, 3600)),
            requires_config=tuple(
                str(v) for v in needs if str(v).strip()
            ) if isinstance(needs, list) else (),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "name": self.label,
            "description": self.description,
            "parameters": dict(self.parameters),
            "required": list(self.required),
            "command": list(self.command),
            "cwd": self.cwd,
            "timeoutSec": self.timeout_sec,
        }
        if self.requires_config:
            out["requiresConfig"] = list(self.requires_config)
        return out


@dataclass(frozen=True)
class ChannelManifest:
    id: str
    name: str
    driver: str = ""
    version: str = "1.0.0"
    icon: str = "🔌"
    description: str = ""
    author: str = ""
    category: str = CATEGORY_CHANNEL
    runtime: str = RUNTIME_ENGINE
    #: 外部程序插件的启动参数（runtime == process 时有意义）。
    process: dict[str, Any] = field(default_factory=dict)
    fields: tuple[FieldSpec, ...] = field(default_factory=tuple)
    #: 插件给 agent 加的工具（声明式，调用时才起进程）。
    tools: tuple[PluginToolSpec, ...] = field(default_factory=tuple)
    #: 内置插件（随引擎发布）还是用户导入的。
    builtin: bool = False
    #: 插件目录（安装后才知道，读取时回填）。
    path: Path | None = None

    @property
    def is_process(self) -> bool:
        return self.runtime == RUNTIME_PROCESS

    @property
    def is_manual(self) -> bool:
        return self.runtime == RUNTIME_MANUAL

    @property
    def secrets(self) -> tuple[str, ...]:
        return tuple(f.key for f in self.fields if f.secret)

    def defaults(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for spec in self.fields:
            if spec.default is None:
                continue
            out[spec.key] = spec.default
        return out

    def command_line(self) -> tuple[str, list[str], str]:
        """外部程序插件的启动命令行：``(command, args, cwd)``。"""
        proc = self.process or {}
        command = str(proc.get("command") or "").strip()
        if not command:
            raise ManifestError(f"插件 {self.id} 是外部程序但没有 process.command")
        args = [str(a) for a in (proc.get("args") or [])]
        cwd = str(proc.get("cwd") or ".")
        return command, args, cwd

    def to_dict(self, *, include_fields: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "icon": self.icon,
            "version": self.version,
            "driver": self.driver,
            "description": self.description,
            "author": self.author,
            "category": self.category,
            "runtime": self.runtime,
            "builtin": self.builtin,
        }
        if self.process:
            # 外部程序段里可能带 env（含密钥），回传时脱敏
            safe = {k: v for k, v in self.process.items() if k != "env"}
            if self.process.get("env"):
                safe["envKeys"] = sorted(self.process["env"].keys())
            out["process"] = safe
        if include_fields:
            out["fields"] = [f.to_dict() for f in self.fields]
        if self.tools:
            out["tools"] = [t.to_dict() for t in self.tools]
        return out

    @classmethod
    def load(cls, path: Path, *, builtin: bool = False) -> "ChannelManifest":
        """从插件目录读清单（``path`` 是目录或 plugin.json 本身）。"""
        file = path if path.is_file() else path / MANIFEST_NAME
        try:
            raw = json.loads(file.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ManifestError(f"找不到插件清单：{file}") from exc
        except json.JSONDecodeError as exc:
            raise ManifestError(f"插件清单不是合法 JSON：{exc}") from exc
        if not isinstance(raw, dict):
            raise ManifestError("插件清单必须是 JSON 对象")
        return cls.from_dict(raw, path=file.parent, builtin=builtin)

    @classmethod
    def from_dict(
        cls, raw: dict[str, Any], *, path: Path | None = None, builtin: bool = False
    ) -> "ChannelManifest":
        plugin_id = str(raw.get("id") or "").strip()
        if not plugin_id:
            raise ManifestError("插件清单缺少 id")
        if not all(c.isalnum() or c in "-_." for c in plugin_id):
            raise ManifestError(f"插件 id 只能用字母/数字/._-：{plugin_id}")
        runtime = str(raw.get("runtime") or RUNTIME_ENGINE).strip()
        if runtime not in RUNTIMES:
            raise ManifestError(f"runtime 只能是 engine / process / manual：{runtime}")
        driver = str(raw.get("driver") or "").strip()
        tools_raw = raw.get("tools")
        tools: list[PluginToolSpec] = []
        if isinstance(tools_raw, list):
            tools = [PluginToolSpec.from_dict(t) for t in tools_raw]
        if runtime == RUNTIME_ENGINE and not driver and not tools:
            # 有 tools 的插件可以是纯能力包（不需要驱动）：工具调用时才起进程。
            raise ManifestError("引擎内插件缺少 driver（引擎要靠它找到实现）")
        process = raw.get("process")
        if runtime == RUNTIME_PROCESS:
            if not isinstance(process, dict) or not str(process.get("command") or "").strip():
                raise ManifestError("外部程序插件缺少 process.command")
        category = str(raw.get("category") or CATEGORY_CHANNEL).strip()
        if category not in CATEGORIES:
            category = "other"
        fields_raw = raw.get("fields")
        fields: list[FieldSpec] = []
        if isinstance(fields_raw, list):
            fields = [FieldSpec.from_dict(f) for f in fields_raw]
        if runtime == RUNTIME_PROCESS and not any(
            f.key == RUNTIME_PATH_KEY for f in fields
        ):
            # 外部程序插件一律自带「可执行文件搜索目录」—— 见 RUNTIME_PATH_KEY 的说明。
            fields.append(FieldSpec.from_dict(RUNTIME_PATH_FIELD))
        return cls(
            id=plugin_id,
            name=str(raw.get("name") or plugin_id),
            icon=str(raw.get("icon") or "🔌"),
            version=str(raw.get("version") or "1.0.0"),
            driver=driver,
            description=str(raw.get("description") or ""),
            author=str(raw.get("author") or ""),
            category=category,
            runtime=runtime,
            process=dict(process) if isinstance(process, dict) else {},
            fields=tuple(fields),
            tools=tuple(tools),
            builtin=builtin,
            path=path,
        )


__all__ = [
    "ChannelManifest",
    "FieldSpec",
    "PluginToolSpec",
    "ManifestError",
    "FIELD_TYPES",
    "OPTION_SOURCES",
    "MANIFEST_NAME",
    "CONFIG_NAME",
    "ENABLED_KEY",
    "RUNTIME_PATH_KEY",
    "RUNTIME_PATH_FIELD",
    "DEFAULT_TOOL_TIMEOUT_SEC",
    "TOOL_OUTPUT_MAX_CHARS",
    "RUNTIME_ENGINE",
    "RUNTIME_PROCESS",
    "RUNTIME_MANUAL",
    "RUNTIMES",
    "CATEGORY_CHANNEL",
    "CATEGORY_TOOL",
    "CATEGORIES",
]
