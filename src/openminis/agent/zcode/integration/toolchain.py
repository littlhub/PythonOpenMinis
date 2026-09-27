"""ZCode 工具链 ↔ 应用工具面：桥接与命名空间隔离。

ZCode 是**外部 agent**：它的 tool 集与主智能体不是同一套（名字、语义、结果形态都
不同）。因此这里不做「把 ZCode 工具收编成应用工具」的包含式映射，而是：

1. **命名空间隔离**：ZCode 工具在应用侧统一用 ``zcode_<name>`` 作 id
   （``zcode_read`` / ``zcode_bash`` …），内核内部仍叫 ``Read`` / ``Bash``。
   两边同名相交的工具**并存**（应用 ``file_read`` 与 ZCode ``zcode_read`` 都是
   合法选项），谁都不覆盖谁 —— 取并集。
2. **相交登记**：``host_equivalent`` 记录「功能相交」的对应关系（供界面标注与
   默认选择使用），但它**不是别名**：模型调 ``zcode_read`` 走的一定是 ZCode 内核的
   Read handler，不会偷偷路由到应用工具。
3. **能力如实**：依赖尚未移植的宿主端口（如 ``WebSearch`` 依赖 model 层、``Task*``
   依赖后台任务端口）的工具**不注册**，而不是注册一个必然报错的壳。

:data:`HOST_PORT_FACTORIES` 说明每个工具需要哪些宿主端口，``build_zcode_runtime``
按需拼装 —— 这也是它「能独立运行」的前提：只要端口齐，不经主智能体即可执行。
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

from ...zcode.contracts.ids import SessionId, TraceId, create_turn_id
from .host_ports import (
    AppExecutionPort,
    InMemorySessionStorePort,
    LocalFileSystemPort,
    SimpleHttpClientPort,
    read_field,
)
from .skills import ZcodeSkillPort

__all__ = [
    "ZCODE_TOOL_ID_PREFIX",
    "ZcodeToolSpec",
    "ZCODE_TOOLCHAIN",
    "HostToolError",
    "zcode_tool_ids",
    "zcode_tool_catalog",
    "zcode_tool_spec",
    "host_tool_spec",
    "resolve_tool_spec",
    "borrowed_tool_catalog",
    "use_host_session",
    "current_host_session",
    "build_agent_tool_definition",
    "ZcodeHostRuntime",
    "build_zcode_runtime",
    "DEFAULT_ZCODE_TOOL_IDS",
]

ZCODE_TOOL_ID_PREFIX = "zcode"

#: 内核工具名（``entry.metadata.name``）→ 应用侧 id 后缀（小写下划线）。
_ID_SUFFIX: dict[str, str] = {
    "Read": "read",
    "Write": "write",
    "Edit": "edit",
    "Bash": "bash",
    "Glob": "glob",
    "Grep": "grep",
    "WebFetch": "web_fetch",
    "TodoRead": "todo_read",
    "TodoWrite": "todo_write",
    "Skill": "skill",
}

#: 「功能相交」登记：ZCode 工具 ↔ 主智能体已有工具。**仅用于界面标注**，
#: 不参与执行路由（见模块 docstring 第 2 条）。
_INTERSECTS: dict[str, str] = {
    "Read": "file_read",
    "Write": "file_write",
    "Edit": "file_edit",
    "Bash": "shell_execute",
    "Glob": "search_files",
    "Grep": "search_files",
    "WebFetch": "web_fetch",
    "Skill": "skill_use",
}

#: 需要但**尚未移植**、故暂不注册的工具：注册一个必然失败的壳比不注册更糟
#: （模型会把失败当环境问题反复重试）。
_PENDING: dict[str, str] = {
    "WebSearch": "依赖未移植的 Model 层（provider-native web search）",
    "EnterPlanMode": "依赖未移植的 SessionModePort",
    "ExitPlanMode": "依赖未移植的 SessionModePort",
    "AskUserQuestion": "依赖宿主的交互式提问通道",
    "TaskOutput": "依赖未移植的后台任务端口",
    "TaskStop": "依赖未移植的后台任务端口",
    "Agent": "应用侧的委派走 subagent_delegate；内核 Agent/Task 需 SubagentPort",
    "Task": "应用侧的委派走 subagent_delegate；内核 Agent/Task 需 SubagentPort",
}

#: 每个工具需要哪些宿主端口。键是端口名，值说明用途（拼装与排障都靠它）。
_TOOL_PORTS: dict[str, tuple[str, ...]] = {
    "Read": ("fs",),
    "Write": ("fs",),
    "Edit": ("fs",),
    "Bash": ("exec",),
    "Glob": ("fs",),
    "Grep": ("fs",),
    "WebFetch": ("http",),
    "TodoRead": ("store",),
    "TodoWrite": ("store",),
    "Skill": ("skill",),
}


@dataclass(slots=True)
class ZcodeToolSpec:
    """一个 ZCode 内核工具在应用侧的登记项。"""

    id: str
    name: str
    entry: Any
    needs: tuple[str, ...] = ()
    host_equivalent: Optional[str] = None
    display_name: str = ""
    #: ``"kernel"`` = ZCode 自带工具；``"host"`` = 从主智能体**借**来的工具。
    #: 借用方向是双向的：主智能体也能借 ZCode 的工具（``zcode_*`` 并入工具目录）。
    origin: str = "kernel"

    @property
    def description(self) -> str:
        metadata = getattr(self.entry, "metadata", None)
        return str(getattr(metadata, "description", "") or "")


def _kernel_tools() -> dict[str, Any]:
    """内核 ``built_in_tools`` 按工具名索引。"""
    from ..tool.handlers import built_in_tools

    out: dict[str, Any] = {}
    for entry in built_in_tools:
        metadata = getattr(entry, "metadata", None)
        name = getattr(metadata, "name", None)
        if name:
            out[str(name)] = entry
    return out


def _build_specs() -> list[ZcodeToolSpec]:
    kernel = _kernel_tools()
    specs: list[ZcodeToolSpec] = []
    for kernel_name, suffix in _ID_SUFFIX.items():
        entry = kernel.get(kernel_name)
        if entry is None:  # 内核未注册该工具（裁剪过）
            continue
        specs.append(
            ZcodeToolSpec(
                id=f"{ZCODE_TOOL_ID_PREFIX}_{suffix}",
                name=kernel_name,
                entry=entry,
                needs=_TOOL_PORTS.get(kernel_name, ()),
                host_equivalent=_INTERSECTS.get(kernel_name),
                display_name=f"ZCode · {kernel_name}",
            )
        )
    return specs


#: ZCode 可执行工具链（按内核注册顺序）。
ZCODE_TOOLCHAIN: list[ZcodeToolSpec] = _build_specs()

_BY_ID: dict[str, ZcodeToolSpec] = {spec.id: spec for spec in ZCODE_TOOLCHAIN}
_BY_NAME: dict[str, ZcodeToolSpec] = {spec.name: spec for spec in ZCODE_TOOLCHAIN}


def zcode_tool_ids() -> set[str]:
    """ZCode 工具的全部应用侧 id（并入 ``known_tool_ids()`` 用）。"""
    return set(_BY_ID)


def zcode_tool_spec(tool_id: str) -> Optional[ZcodeToolSpec]:
    return _BY_ID.get(tool_id)


def pending_zcode_tool_names() -> dict[str, str]:
    """暂未注册的内核工具及原因（供诊断/界面提示，勿注册为可选工具）。"""
    return dict(_PENDING)


def zcode_tool_catalog() -> list[dict[str, Any]]:
    """工具目录条目（与 ``settings.catalog.TOOL_CATALOG`` 同形）。"""
    out: list[dict[str, Any]] = []
    for spec in ZCODE_TOOLCHAIN:
        description = spec.description.split("\n")[0].strip()
        item: dict[str, Any] = {
            "id": spec.id,
            "name": spec.display_name,
            # ZCode 工具的描述来自内核 provider description，取首行做目录摘要。
            "description": description,
            "category": "ZCode",
            "source": "zcode",
        }
        if spec.host_equivalent:
            item["intersects"] = spec.host_equivalent
        out.append(item)
    return out


#: ZCode 子代理的默认工具集：内核可执行工具链的全部。
DEFAULT_ZCODE_TOOL_IDS: list[str] = [spec.id for spec in ZCODE_TOOLCHAIN]


# ---------------------------------------------------------------------------
# 工具互借：宿主工具 → 外部 agent
#
# 用户要求「外部 subagent 与主 agent 的 tool / skill 可以相互借用」。方向一
# （主 agent 借 ZCode 工具）由 ``zcode_*`` 并入应用工具目录实现；这里是方向二：
# 把**主智能体的工具**适配成内核 ``ToolEntry`` 形状，交给 ZCode 的循环使用。
#
# 内核只要求 entry 有三样东西：``metadata``（名字/描述）、``input_schema``
# （JSON Schema）、``handler(args, context)``。桥接层把这三点补齐，于是内核完全
# 不需要知道宿主存在 —— 这正是「外部 agent 能独立运行」的同一套端口思路。
# ---------------------------------------------------------------------------
#: 借来的宿主工具要落在**宿主会话**的工作区上（而不是外部 agent 的内部会话 id）。
#: 用 contextvar 而不是塞进内核 context：内核类型不该为了宿主的需要长字段。
_HOST_SESSION: ContextVar[str] = ContextVar("zcode_host_session", default="")


def current_host_session() -> str:
    """当前正在借宿主工具的那个宿主会话 id（没有则空串）。"""
    return _HOST_SESSION.get()


@contextmanager
def use_host_session(session_id: str) -> Iterator[None]:
    """在这个作用域内，借来的宿主工具用 ``session_id`` 解析工作区。"""
    token = _HOST_SESSION.set(str(session_id or ""))
    try:
        yield
    finally:
        _HOST_SESSION.reset(token)


def _param_schema(param: Any) -> dict[str, Any]:
    """``AgentToolParam``（或已是 dict）→ JSON Schema 片段。"""
    if isinstance(param, dict):
        out = {str(k): v for k, v in param.items() if k in ("type", "description", "enum", "items")}
        out.setdefault("type", "string")
        return out
    out: dict[str, Any] = {"type": str(getattr(param, "type", "string") or "string")}
    description = str(getattr(param, "description", "") or "")
    if description:
        out["description"] = description
    enum_values = getattr(param, "enum_values", None) or getattr(param, "enum", None)
    if enum_values:
        out["enum"] = [str(v) for v in enum_values]
    return out


def definition_to_json_schema(definition: Any) -> dict[str, Any]:
    """应用 ``AgentToolDefinition`` → 内核 ``input_schema``（JSON Schema）。"""
    raw_params = getattr(definition, "parameters", None) or {}
    properties: dict[str, Any] = {}
    if isinstance(raw_params, dict):
        for key, param in raw_params.items():
            properties[str(key)] = _param_schema(param)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    required = [str(item) for item in (getattr(definition, "required", None) or ())]
    if required:
        schema["required"] = required
    return schema


def _host_result_text(result: Any) -> str:
    """宿主工具结果 → 模型可读文本。"""
    if result is None:
        return "(no output)"
    if isinstance(result, str):
        return result
    content = getattr(result, "content", None)
    if content is None:
        content = getattr(result, "text", None)
    if content is None and isinstance(result, dict):
        content = result.get("content") or result.get("text")
    if content is None:
        return json.dumps(result, ensure_ascii=False, default=str)
    return str(content)


class HostToolError(Exception):
    """宿主工具**业务失败**（``ToolExecutionResult.success is False``）。

    借来的工具失败了必须如实告诉模型 —— 内核循环把异常转成 ``is_error`` 的工具结果，
    模型看到宿主自己的错误文案才能改正；静默返回一段错误文本会让它以为调用成功。
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "host_tool_failed"


class _HostToolEntry:
    """把宿主工具适配成内核 ``ToolEntry`` 形状（``metadata``/``input_schema``/``handler``）。"""

    def __init__(self, tool_id: str, definition: Any) -> None:
        from ..tool.types import ToolMetadata

        #: 借用过来的工具沿用宿主原名（不叫 ``zcode_*``）—— 模型看到的就是它本来的工具。
        self.id = tool_id
        self.input_schema = definition_to_json_schema(definition)
        self.metadata = ToolMetadata(
            name=tool_id,
            read_only=False,
            destructive=False,
            concurrent_safe=True,
            side_effect_scope="workspace",
            risk_level="medium",
            needs_approval=False,
            description=str(getattr(definition, "description", "") or ""),
        )

    async def handler(self, args: Any, context: Any = None) -> Any:
        """执行宿主工具。端口与权限都由宿主自己的注册表决定，这里只做转接。"""
        import asyncio

        from ....settings.catalog import build_tool_registry

        tool = build_tool_registry([self.id]).get(self.id)
        if tool is None:
            raise RuntimeError(f"宿主工具当前不可用: {self.id}")
        executor = getattr(tool, "executor", None)
        if executor is None:
            raise RuntimeError(f"宿主工具没有执行器: {self.id}")

        session_id = current_host_session() or str(getattr(context, "session_id", "") or "")
        payload = json.dumps(dict(args or {}), ensure_ascii=False)
        outcome = executor(payload, session_id)
        if asyncio.iscoroutine(outcome):
            outcome = await outcome

        text = _host_result_text(outcome)
        if getattr(outcome, "success", True) is False:
            raise HostToolError(text)
        return text


_BORROWED_CACHE: dict[str, Optional[ZcodeToolSpec]] = {}

#: 不外借的宿主工具：``subagent_delegate`` 借出去就是「子代理再委派子代理」的递归
#: 入口（主智能体自己的子代理循环同样把它剔掉），且 ZCode 有自己的委派语义。
_NON_BORROWABLE: frozenset[str] = frozenset({"subagent_delegate"})


def host_tool_spec(tool_id: str) -> Optional[ZcodeToolSpec]:
    """取（或建）一个「从宿主借来的工具」登记项；宿主没有这个工具则 ``None``。

    只接受应用工具目录里**真实存在且可执行**的 id：借一个不存在的工具，等于给模型
    一个必然失败的选项。
    """
    key = str(tool_id or "")
    if not key or key in _NON_BORROWABLE:
        return None
    if key in _BORROWED_CACHE:
        return _BORROWED_CACHE[key]

    spec: Optional[ZcodeToolSpec] = None
    try:
        from ....settings.catalog import build_tool_registry, known_tool_ids

        if key in known_tool_ids():
            tool = build_tool_registry([key]).get(key)
            definition = getattr(tool, "definition", None)
            if tool is not None and definition is not None:
                spec = ZcodeToolSpec(
                    id=key,
                    name=key,
                    entry=_HostToolEntry(key, definition),
                    needs=(),
                    host_equivalent=None,
                    display_name=f"Borrowed · {key}",
                    origin="host",
                )
    except Exception:  # pragma: no cover - 宿主目录不可用时视为借不到
        spec = None

    _BORROWED_CACHE[key] = spec
    return spec


def resolve_tool_spec(tool_id: str) -> Optional[ZcodeToolSpec]:
    """统一的工具解析：先看 ZCode 自带，再尝试向宿主借。

    外部 agent 的工具集 = 自带工具 ∪ 借来的宿主工具 —— 这正是「相互借用」的落点。
    """
    spec = zcode_tool_spec(tool_id)
    if spec is not None:
        return spec
    return host_tool_spec(tool_id)


def borrowed_tool_catalog() -> list[dict[str, Any]]:
    """可借用的宿主工具清单（供界面标注「外部 agent 可借用」）。"""
    out: list[dict[str, Any]] = []
    try:
        from ....settings.catalog import all_tool_catalog
    except Exception:  # pragma: no cover
        return out
    zcode_ids = zcode_tool_ids()
    for item in all_tool_catalog():
        tool_id = str(item.get("id") or "")
        if not tool_id or tool_id in zcode_ids:
            continue
        row = dict(item)
        row["borrowable"] = True
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# 工具定义转换：内核 JSON Schema → 应用 AgentToolDefinition
# ---------------------------------------------------------------------------
def build_agent_tool_definition(spec: ZcodeToolSpec) -> Any:
    """把内核 ``ToolEntry.inputSchema``（JSON Schema）转成应用的 ``AgentToolDefinition``。

    只搬运 ``properties`` / ``required`` 与标量元信息；嵌套对象/数组原样透传成
    ``object``/``array`` 类型描述 —— 应用侧的 ``AgentToolParam`` 只支持标量 + enum，
    深层结构由 provider 的 schema 序列化负责，不能在桥接层丢字段。
    """
    from ....data.model.agent_tool_definition import AgentToolDefinition, AgentToolParam

    schema = getattr(spec.entry, "input_schema", None) or {}
    if not isinstance(schema, dict):
        schema = {}
    raw_props = schema.get("properties") or {}
    required = tuple(str(item) for item in (schema.get("required") or ()))

    params: dict[str, AgentToolParam] = {}
    for key, raw in raw_props.items():
        if not isinstance(raw, dict):
            continue
        declared_type = str(raw.get("type") or "string")
        if declared_type in ("integer", "number", "boolean", "array", "object"):
            pass
        else:
            declared_type = "string"
        description = str(raw.get("description") or "")
        enum_values = raw.get("enum")
        params[str(key)] = AgentToolParam(
            type=declared_type,
            description=description,
            enum_values=[str(v) for v in enum_values] if isinstance(enum_values, list) else None,
        )

    return AgentToolDefinition(
        name=spec.id,
        description=spec.description,
        parameters=params,
        required=list(required),
    )


# ---------------------------------------------------------------------------
# 宿主运行时：端口拼装 + 逐次调用的 ToolExecutionContext
# ---------------------------------------------------------------------------
@dataclass
class ZcodeHostRuntime:
    """ZCode 独立运行所需的宿主端口集合（会话级）。"""

    session_id: str
    working_directory: str
    workspace_root: str
    file_system_port: Any = None
    execution_port: Any = None
    session_store: Any = None
    skill_port: Any = None
    http_client_port: Any = None
    read_file_state: dict[str, Any] = field(default_factory=dict)
    abort_signal: Any = None

    def port(self, key: str) -> Any:
        return {
            "fs": self.file_system_port,
            "exec": self.execution_port,
            "store": self.session_store,
            "skill": self.skill_port,
            "http": self.http_client_port,
        }.get(key)

    def supports(self, spec: ZcodeToolSpec) -> bool:
        return all(self.port(name) is not None for name in spec.needs)

    def make_context(
        self,
        *,
        tool_call_id: str,
        turn_id: Optional[str] = None,
        emit_event: Optional[Callable[[Any], Any]] = None,
    ) -> Any:
        """构造一次工具调用的 ``ToolExecutionContext``。

        ZCode 的 handler 通过 context 读端口，所以「独立运行」在实现上就是：
        把这一组宿主端口塞进 context，内核不需要知道宿主是谁。
        """
        from ..tool.types import ToolExecutionContext

        abort_signal = self.abort_signal
        if abort_signal is None:
            from ..core.abort import AbortController

            abort_signal = AbortController().signal
            self.abort_signal = abort_signal

        return ToolExecutionContext(
            tool_call_id=tool_call_id,
            trace_id=TraceId(str(tool_call_id)),
            abort_signal=abort_signal,
            working_directory=self.working_directory,
            workspace_root=self.workspace_root,
            session_id=SessionId(str(self.session_id)),
            turn_id=create_turn_id() if turn_id is None else turn_id,
            span_id=str(tool_call_id),
            file_system_port=self.file_system_port,
            execution_port=self.execution_port,
            session_store=self.session_store,
            skill_port=self.skill_port,
            http_client_port=self.http_client_port,
            read_file_state=self.read_file_state,
            emit_event=emit_event,
            runtime_scope="main",
            set_working_directory=self._set_working_directory,
        )

    def _set_working_directory(self, path: str) -> None:
        """持久 shell 的 cd 结果回写到运行时，使后续工具看到新 cwd。"""
        resolved = str(path or "").strip()
        if resolved:
            self.working_directory = os.path.abspath(resolved)


#: 端口构造器：``key -> factory(runtime_kwargs) -> port``。可被宿主替换
#: （测试里塞假端口，或换成沙箱内的实现）。
HOST_PORT_FACTORIES: dict[str, Callable[..., Any]] = {
    # ``workspace_root`` 必须传进去：文件端口靠它做**落盘护栏**（内核的 path_policy
    # 刻意不做 containment，见 ``host_ports`` 的「落盘护栏」一节）。漏掉这一句，
    # ZCode 的写文件就能落到任意位置 —— 实测过。
    "fs": lambda workspace_root, **kw: LocalFileSystemPort(workspace_root=workspace_root),
    "exec": lambda session_id, **kw: AppExecutionPort(session_id),
    "store": lambda **kw: InMemorySessionStorePort(),
    "skill": lambda working_directory, **kw: ZcodeSkillPort(
        working_directory,
        borrow_host_skills=bool(kw.get("borrow_host_skills", True)),
        host_skill_filter=kw.get("borrowed_skill_names"),
    ),
    "http": lambda **kw: SimpleHttpClientPort(),
}


def build_zcode_runtime(
    *,
    session_id: str,
    working_directory: str,
    workspace_root: Optional[str] = None,
    need: tuple[str, ...] = ("fs", "exec", "store", "skill", "http"),
    session_store: Any = None,
    overrides: Optional[dict[str, Any]] = None,
    borrow_host_skills: bool = True,
    borrowed_skill_names: Optional[list[str]] = None,
) -> ZcodeHostRuntime:
    """按需拼装宿主运行时。

    ``session_store`` 由调用方传入可跨工具调用复用的实例（todo 列表要留在会话里），
    其余端口按 ``HOST_PORT_FACTORIES`` 现建。``borrow_host_skills`` 决定技能端口是否
    把宿主技能库一并纳入（「外部 agent 借主 agent 的技能」）；``borrowed_skill_names``
    进一步指定**只借哪几个**（``None`` = 整个技能库）。
    """
    overrides = overrides or {}
    working_directory = os.path.abspath(working_directory)
    runtime = ZcodeHostRuntime(
        session_id=session_id,
        working_directory=working_directory,
        workspace_root=os.path.abspath(workspace_root or working_directory),
        session_store=session_store,
    )
    kwargs = {
        "session_id": session_id,
        "working_directory": working_directory,
        "workspace_root": runtime.workspace_root,
        "borrow_host_skills": borrow_host_skills,
        "borrowed_skill_names": borrowed_skill_names,
    }
    for key in need:
        if key == "store" and session_store is not None:
            continue
        if key in overrides:
            setattr(runtime, f"{_ATTR_BY_PORT[key]}", overrides[key])
            continue
        factory = HOST_PORT_FACTORIES.get(key)
        if factory is None:
            continue
        setattr(runtime, _ATTR_BY_PORT[key], factory(**kwargs))
    return runtime


_ATTR_BY_PORT: dict[str, str] = {
    "fs": "file_system_port",
    "exec": "execution_port",
    "store": "session_store",
    "skill": "skill_port",
    "http": "http_client_port",
}


# PORT: 保留导入以便类型检查器识别统一的双读工具（host_ports.read_field 复用点）。
_ = read_field
