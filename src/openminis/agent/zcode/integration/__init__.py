"""ZCode ↔ 宿主集成层。

ZCode 在内核里是**独立 agent**（自带 tool / skill / loop / prompt），本包负责把它
接到 OpenMinis 的宿主能力上，同时**保持两套资源并存**：

- :mod:`~openminis.agent.zcode.integration.host_ports` —— 端口的本机实现
  （文件系统 / 执行 / todo 存储 / HTTP）。
- :mod:`~openminis.agent.zcode.integration.skills` —— ZCode 自己的技能根发现与
  ``SkillPort``。
- :mod:`~openminis.agent.zcode.integration.toolchain` —— 工具链登记（``zcode_*``
  命名空间、相交登记、端口需求、宿主运行时拼装）。
- :mod:`~openminis.agent.zcode.integration.agent` —— 独立运行的循环与系统提示。

命名空间约定：应用工具（``file_read``/``shell_execute``…）与 ZCode 工具
（``zcode_read``/``zcode_bash``…）**同时在册**。相交的能力不做别名路由 ——
选中哪个 id 就走哪条实现。
"""

from __future__ import annotations

import os
from typing import Any, Optional

from .agent import (
    ZCODE_DEFAULT_MAX_TOKENS,
    ZCODE_DEFAULT_MAX_TURNS,
    ZcodeRunOptions,
    ZcodeRunResult,
    build_zcode_system_prompt,
    format_zcode_tool_output,
    run_zcode_agent,
    run_zcode_external_agent,
)
from .host_ports import (
    AppExecutionPort,
    InMemorySessionStorePort,
    LocalFileSystemPort,
    SimpleHttpClientPort,
)
from .skills import (
    ZcodeSkillPort,
    ZcodeSkillRoot,
    discover_skills_at_roots,
    discover_zcode_skills,
    expand_skill_root,
    generated_host_skill_names,
    host_skill_roots,
    resolve_zcode_skill_roots,
    roots_from_patterns,
)
from .toolchain import (
    DEFAULT_ZCODE_TOOL_IDS,
    ZCODE_TOOL_ID_PREFIX,
    ZCODE_TOOLCHAIN,
    HostToolError,
    ZcodeHostRuntime,
    ZcodeToolSpec,
    borrowed_tool_catalog,
    build_agent_tool_definition,
    build_zcode_runtime,
    current_host_session,
    host_tool_spec,
    pending_zcode_tool_names,
    resolve_tool_spec,
    use_host_session,
    zcode_tool_catalog,
    zcode_tool_ids,
    zcode_tool_spec,
)

__all__ = [
    "ZCODE_DEFAULT_MAX_TURNS",
    "ZCODE_DEFAULT_MAX_TOKENS",
    "ZCODE_TOOL_ID_PREFIX",
    "ZCODE_TOOLCHAIN",
    "DEFAULT_ZCODE_TOOL_IDS",
    "HostToolError",
    "ZcodeHostRuntime",
    "ZcodeRunOptions",
    "ZcodeRunResult",
    "ZcodeSkillPort",
    "ZcodeSkillRoot",
    "ZcodeToolSpec",
    "AppExecutionPort",
    "InMemorySessionStorePort",
    "LocalFileSystemPort",
    "SimpleHttpClientPort",
    "build_agent_tool_definition",
    "build_zcode_runtime",
    "build_zcode_system_prompt",
    "borrowed_tool_catalog",
    "current_host_session",
    "discover_skills_at_roots",
    "discover_zcode_skills",
    "expand_skill_root",
    "format_zcode_tool_output",
    "generated_host_skill_names",
    "get_session_runtime",
    "execute_zcode_tool_id",
    "host_skill_roots",
    "host_tool_spec",
    "pending_zcode_tool_names",
    "reset_session_runtimes",
    "resolve_tool_spec",
    "resolve_zcode_skill_roots",
    "roots_from_patterns",
    "run_zcode_agent",
    "run_zcode_external_agent",
    "use_host_session",
    "zcode_tool_catalog",
    "zcode_tool_ids",
    "zcode_tool_spec",
]

# ---------------------------------------------------------------------------
# 会话级运行时缓存
#
# 主智能体/其它子代理**直接调用**某个 ``zcode_*`` 工具时（并集里 ZCode 那半边），
# 需要一份按会话复用的运行时：todo 列表要留在会话里、持久 shell 不能被每次调用重建。
# ---------------------------------------------------------------------------
_SESSION_RUNTIMES: dict[str, ZcodeHostRuntime] = {}


def get_session_runtime(
    session_id: str,
    working_directory: Optional[str] = None,
    *,
    workspace_root: Optional[str] = None,
) -> ZcodeHostRuntime:
    """取（或建）一个会话的 ZCode 宿主运行时。"""
    key = str(session_id or "")
    runtime = _SESSION_RUNTIMES.get(key)
    cwd = os.path.abspath(working_directory or os.getcwd())
    if runtime is None:
        runtime = build_zcode_runtime(
            session_id=key,
            working_directory=cwd,
            workspace_root=workspace_root or cwd,
        )
        _SESSION_RUNTIMES[key] = runtime
        return runtime
    if working_directory:
        runtime.working_directory = cwd
    if workspace_root:
        runtime.workspace_root = os.path.abspath(workspace_root)
    return runtime


def reset_session_runtimes(session_id: Optional[str] = None) -> None:
    """丢弃会话运行时（会话结束/测试隔离）。"""
    if session_id is None:
        _SESSION_RUNTIMES.clear()
        return
    _SESSION_RUNTIMES.pop(str(session_id), None)


async def execute_zcode_tool_id(
    tool_id: str,
    args: Any,
    *,
    session_id: str,
    working_directory: Optional[str] = None,
    tool_call_id: Optional[str] = None,
) -> str:
    """直接执行一个 ``zcode_*`` 工具，返回模型可读文本。

    主智能体侧调用 ZCode 工具的**唯一入口** —— 保证它一定走内核 handler，而不是
    被应用侧的同类工具顶替。
    """
    spec = zcode_tool_spec(tool_id)
    if spec is None:
        raise KeyError(f"Unknown ZCode tool id: {tool_id}")
    runtime = get_session_runtime(session_id, working_directory)
    context = runtime.make_context(tool_call_id=tool_call_id or f"call-{tool_id}")
    handler = getattr(spec.entry, "handler", None)
    if handler is None:
        raise RuntimeError(f"ZCode tool {spec.name} has no handler")
    output = await handler(dict(args or {}), context)
    return format_zcode_tool_output(output)
