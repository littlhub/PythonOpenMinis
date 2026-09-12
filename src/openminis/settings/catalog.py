"""Static catalog for the settings UI: model providers, models, identities
(role-based agents) and the tools that exist in this port.

Kotlin counterpart spread across ``config/ConfigBuiltins.kt`` (scalar fields),
``data/model/ProviderConfig.kt`` (ProviderType / provider instances) and
``agent/SoulStore.kt`` (role files). This module keeps the *catalog* (what the
UI may offer) separate from the *store* (what the user configured).
"""

from __future__ import annotations

import json
import types
from dataclasses import dataclass, field

from ..agent.agent_runtime import ToolExecutor
from ..core.logging import get_logger
from ..data.model import LLMModel
from ..data.model.agent_tool_definition import AgentToolDefinition
from ..tools.agent_tools import AgentTools
from ..tools.browser_use_tool import BrowserUseTool
from ..tools.file_edit_tool import FileEditTool
from ..tools.file_read_tool import FileReadTool
from ..tools.file_write_tool import FileWriteTool
from ..tools.image_gen_tool import ImageGenTool
from ..tools.ls_tool import LsTool
from ..tools.memory_tools import (
    MemoryGetTool,
    MemoryWriteTool,
    memory_get_definition,
    memory_write_definition,
)
from ..tools.read_image_tool import ReadImageTool
from ..tools.search_files_tool import SearchFilesTool
from ..tools.shell_execute_tool import ShellExecuteTool
from ..tools.subagent_tool import SubagentDelegateTool
from ..tools.skill_use_tool import SkillUseTool
from ..tools.tool_execution_result import ToolExecutionResult
from ..tools.vision_group_resolver import VisionGroupResolver
from ..tools.web_fetch_tool import WebFetchTool
from ..tools.web_search_tool import WebSearchTool

logger = get_logger(__name__)

__all__ = [
    "ProviderMeta",
    "PROVIDER_TYPES",
    "MODEL_GROUPS",
    "Identity",
    "BUILTIN_IDENTITIES",
    "TOOL_CATALOG",
    "build_tool_registry",
    "make_agent_tools",
    "lookup_model",
    "engine_for",
    "ENGINE_READY",
]

# ---------------------------------------------------------------------------
# model providers
# ---------------------------------------------------------------------------
ENGINE_READY = {"anthropic", "openai"}  # engines actually ported

MODEL_GROUPS: dict[str, list[tuple[str, str]]] = {
    "anthropic": [(m.id, m.display_name) for m in LLMModel.all_anthropic],
    "openAI": [(m.id, m.display_name) for m in LLMModel.all_openai],
    "gemini": [(m.id, m.display_name) for m in LLMModel.all_gemini],
    "openRouter": [(m.id, m.display_name) for m in LLMModel.all_openrouter],
    "xAI": [(m.id, m.display_name) for m in LLMModel.all_xai],
}


def lookup_model(model_id: str) -> LLMModel | None:
    """Find a catalogued LLMModel by its id (None → caller keeps free-form)."""
    for models in MODEL_GROUPS.values():
        for mid, _ in models:
            if mid == model_id:
                return _MODEL_CACHE[mid]
    return None


_MODEL_CACHE: dict[str, LLMModel] = {}


def _warm_model_cache() -> None:
    for models in MODEL_GROUPS.values():
        for mid, _ in models:
            if mid not in _MODEL_CACHE:
                # LLMModel has no public lookup-by-id; rebuild cheaply from the
                # class attributes when needed instead of caching all instances
                # here (they are singletons on the class anyway).
                for attr in dir(LLMModel):
                    if attr.startswith("_"):
                        continue
                    inst = getattr(LLMModel, attr)
                    if isinstance(inst, LLMModel) and inst.id == mid:
                        _MODEL_CACHE[mid] = inst
                        break


_warm_model_cache()


@dataclass(frozen=True)
class ProviderMeta:
    type: str
    label: str
    engine: str | None  # engine name when ported (anthropic/openai), else None
    default_model: str
    note: str = ""


#: Order shown in the UI. ``engine=None`` providers are offered so the stored
#: config stays cross-platform compatible, but chat can only activate engine
#: ports that actually exist (``ENGINE_READY``).
PROVIDER_TYPES: list[ProviderMeta] = [
    ProviderMeta("anthropic", "Anthropic", "anthropic", "claude-sonnet-5",
                 note="引擎就绪"),
    ProviderMeta("openAI", "OpenAI", "openai", "gpt-5.2", note="引擎就绪"),
    ProviderMeta("gemini", "Google Gemini", None, "gemini-2.5-pro", note="引擎未移植"),
    ProviderMeta("openRouter", "OpenRouter", None, "", note="引擎未移植"),
    ProviderMeta("xAI", "xAI (Grok)", None, "", note="引擎未移植"),
    ProviderMeta("kimiCode", "Kimi Code", None, "", note="引擎未移植"),
]


def engine_for(provider_type: str) -> str | None:
    for p in PROVIDER_TYPES:
        if p.type == provider_type:
            return p.engine
    return None


def provider_label(provider_type: str) -> str:
    for p in PROVIDER_TYPES:
        if p.type == provider_type:
            return p.label
    return provider_type


# ---------------------------------------------------------------------------
# identities — role-based agents. Picking an identity drives the system prompt
# (persona) and, by default, the set of tools/skills the agent may call.
# ---------------------------------------------------------------------------
@dataclass
class Identity:
    id: str
    name: str
    emoji: str
    description: str
    persona: str
    recommended_tools: list[str] = field(default_factory=list)
    builtin: bool = True
    #: tools currently enabled for this identity (None → recommended_tools)
    enabled_tools: list[str] | None = field(default=None)

    def effective_tools(self) -> list[str]:
        return (
            list(self.enabled_tools)
            if self.enabled_tools is not None
            else list(self.recommended_tools)
        )


BUILTIN_IDENTITIES: list[Identity] = [
    Identity(
        id="assistant",
        name="通用助手",
        emoji="🤖",
        description="均衡的通用助理,可读文件、执行命令并完成任务。",
        persona=(
            "你是一个乐于助人的通用 AI 助理。回答保持简洁、准确、口语化。"
            "需要信息时可以读取工作区文件或运行 shell 命令来核实,不要凭空编造。"
        ),
        recommended_tools=[
            "shell_execute",
            "file_read",
            "file_write",
            "file_edit",
            "ls",
            "search_files",
            "web_fetch",
            "web_search",
            "read_image",
            "image_gen",
            "subagent_delegate",
            "skill_use",
            "memory_write",
            "memory_get",
        ],
    ),
    Identity(
        id="coder",
        name="编程专家",
        emoji="🧑‍💻",
        description="软件开发与调试。主动读代码、跑命令、验证结果。",
        persona=(
            "你是一名资深软件工程师。定位问题时要先读相关源码,再运行命令复现与验证。"
            "给方案时给出具体改动,并说明理由。遵守用户项目的既有风格与约定。"
        ),
        recommended_tools=[
            "shell_execute",
            "file_read",
            "file_write",
            "file_edit",
            "ls",
            "search_files",
            "web_fetch",
            "web_search",
            "read_image",
            "image_gen",
            "subagent_delegate",
            "skill_use",
            "memory_write",
            "memory_get",
        ],
    ),
    Identity(
        id="analyst",
        name="数据分析师",
        emoji="📊",
        description="数据整理、统计与分析,结论要有依据。",
        persona=(
            "你是一名数据分析师。分析数据前先确认数据位置与格式,用命令探查文件结构,"
            "用可复现的方式处理数据;输出结论时要区分事实与推断,标注口径。"
        ),
        recommended_tools=[
            "shell_execute",
            "file_read",
            "file_write",
            "file_edit",
            "ls",
            "search_files",
            "read_image",
            "skill_use",
            "memory_write",
            "memory_get",
        ],
    ),
    Identity(
        id="writer",
        name="写作编辑",
        emoji="✍️",
        description="中文写作、润色与文档整理,通常不需要执行命令。",
        persona=(
            "你是一名写作与编辑助手,擅长中文。行文自然、克制,避免空话套话;"
            "需要素材时可以读取文件,但不要随意执行有副作用的命令。"
        ),
        recommended_tools=["file_read", "skill_use", "memory_write", "memory_get"],
    ),
]

#: Model-view mapping used for "按推荐恢复" — each identity lists which tools
#: the AI would auto-enable for that role.
IDENTITY_TOOL_MATCH: dict[str, list[str]] = {
    "assistant": [
        "shell_execute",
        "file_read",
        "file_write",
        "file_edit",
        "ls",
        "search_files",
        "web_fetch",
        "web_search",
        "read_image",
        "image_gen",
        "subagent_delegate",
        "skill_use",
        "memory_write",
        "memory_get",
    ],
    "coder": [
        "shell_execute",
        "file_read",
        "file_write",
        "file_edit",
        "ls",
        "search_files",
        "web_fetch",
        "web_search",
        "read_image",
        "image_gen",
        "subagent_delegate",
        "skill_use",
        "memory_write",
        "memory_get",
    ],
    "analyst": [
        "shell_execute",
        "file_read",
        "file_write",
        "file_edit",
        "ls",
        "search_files",
        "read_image",
        "image_gen",
        "skill_use",
        "memory_write",
        "memory_get",
    ],
    "writer": ["file_read", "read_image", "image_gen", "skill_use", "memory_write", "memory_get"],
}


# ---------------------------------------------------------------------------
# tools actually ported (skills/tools available to identities today)
# ---------------------------------------------------------------------------
def _tool_desc(tool_id: str) -> str:
    """Pull a human-friendly description straight from the tool's own
    AgentToolDefinition. Keeps the catalog in sync with each tool's
    description (and avoids the previous "all-or-nothing" catch-all that
    silently turned every unknown id into its id).
    """
    try:
        if tool_id == "file_read":
            return FileReadTool.definition().description
        if tool_id == "file_write":
            return FileWriteTool.definition().description
        if tool_id == "file_edit":
            return FileEditTool.definition().description
        if tool_id == "read_image":
            return ReadImageTool.definition().description
        if tool_id == "image_gen":
            return ImageGenTool.definition().description
        if tool_id == "shell_execute":
            return ShellExecuteTool.definition().description
        if tool_id == "browser_use":
            return BrowserUseTool.definition().description
        if tool_id == "memory_write":
            return memory_write_definition().description
        if tool_id == "memory_get":
            return memory_get_definition().description
        if tool_id == "ls":
            return LsTool.definition().description
        if tool_id == "search_files":
            return SearchFilesTool.definition().description
        if tool_id == "web_fetch":
            return WebFetchTool.definition().description
        if tool_id == "web_search":
            return WebSearchTool.definition().description
        if tool_id == "subagent_delegate":
            return SubagentDelegateTool.definition().description
        if tool_id == "skill_use":
            return SkillUseTool.definition().description
    except Exception:  # pragma: no cover - defensive
        pass
    return tool_id


# T-android mirror: the Kotlin catalog exposes every tool that has been
# ported, with name + description. The UI groups them by category via
# ``category`` so a Settings → "已启用技能/工具" view can show them
# the way the Android app does (Read files, Edit files, Memory, Browser).
TOOL_CATALOG: list[dict] = [
    {
        "id": "shell_execute",
        "name": "执行命令",
        "description": _tool_desc("shell_execute"),
        "category": "Shell",
    },
    {
        "id": "file_read",
        "name": "读文件",
        "description": _tool_desc("file_read"),
        "category": "Files",
    },
    {
        "id": "file_write",
        "name": "写文件",
        "description": _tool_desc("file_write"),
        "category": "Files",
    },
    {
        "id": "file_edit",
        "name": "编辑文件",
        "description": _tool_desc("file_edit"),
        "category": "Files",
    },
    {
        "id": "read_image",
        "name": "读图片",
        "description": _tool_desc("read_image"),
        "category": "Vision",
    },
    {
        "id": "image_gen",
        "name": "生成图片",
        "description": _tool_desc("image_gen"),
        "category": "Vision",
    },
    {
        "id": "browser_use",
        "name": "浏览器",
        "description": _tool_desc("browser_use"),
        "category": "Browser",
    },
    {
        "id": "memory_write",
        "name": "写入记忆",
        "description": _tool_desc("memory_write"),
        "category": "Memory",
    },
    {
        "id": "memory_get",
        "name": "读取记忆",
        "description": _tool_desc("memory_get"),
        "category": "Memory",
    },
    {
        "id": "ls",
        "name": "列目录",
        "description": _tool_desc("ls"),
        "category": "Files",
    },
    {
        "id": "search_files",
        "name": "搜索文件",
        "description": _tool_desc("search_files"),
        "category": "Files",
    },
    {
        "id": "web_fetch",
        "name": "抓取网页",
        "description": _tool_desc("web_fetch"),
        "category": "Web",
    },
    {
        "id": "web_search",
        "name": "联网搜索",
        "description": _tool_desc("web_search"),
        "category": "Web",
    },
    {
        "id": "subagent_delegate",
        "name": "委派子代理",
        "description": _tool_desc("subagent_delegate"),
        "category": "Agent",
    },
    {
        "id": "skill_use",
        "name": "加载技能",
        "description": _tool_desc("skill_use"),
        "category": "Skill",
    },
]

VALID_TOOLS = {t["id"] for t in TOOL_CATALOG}


def _wrap_static_executor(definition: AgentToolDefinition, sync_callable):  # noqa: ANN001
    """Adapt a synchronous static executor to the async ToolExecutor contract.

    Used for tools whose ``execute`` is a plain function — file_* and the
    memory tools.
    """

    async def executor(args_json: str, session_id: str, **_kw) -> None:
        return sync_callable(args_json, session_id)

    tool = types.SimpleNamespace(
        name=definition.name,
        definition=definition,
        executor=executor,
    )
    return tool


def _wrap_async_static_executor(definition: AgentToolDefinition, async_callable):  # noqa: ANN001
    """Adapt an async static executor (browser_use today) to the
    ToolExecutor contract.
    """

    async def executor(args_json: str, session_id: str, **_kw) -> None:
        return await async_callable(args_json, session_id)

    tool = types.SimpleNamespace(
        name=definition.name,
        definition=definition,
        executor=executor,
    )
    return tool


def _chat_model_has_native_vision(store) -> bool:  # noqa: ANN001
    """主对话模型是否自带视觉（多模态 / 识图）。

    判定用「能力」而不是硬编码模型名：用户在设置里把某模型标成 llm，就等于
    宣告"它看不了图"，read_image 便会走识图槽描述。取不到信息时返回 True
    （保守：宁可多给字节，也不要无谓地多花一次描述调用）。
    """
    try:
        from .model_capability import CAP_VISION

        binding = store.slot_binding("chat")
        if binding is None:
            return True
        conf, model = binding
        caps = store.resolve_model_capabilities(str(conf.get("id") or ""), model)
        return CAP_VISION in caps
    except Exception:  # pragma: no cover - settings unreadable
        return True


def _image_mode(store) -> str:  # noqa: ANN001
    """``"path"``（默认，图片不进上下文）或 ``"inline"``。"""
    try:
        mode = str(store.agent_config().get("imageContextMode") or "path").lower()
    except Exception:  # pragma: no cover - settings unreadable
        return "path"
    return mode if mode in ("path", "inline") else "path"


_NO_VISION_SLOT_HINT = (
    "当前未配置「识图」模型，图片内容无法读取（只拿到路径与尺寸）。"
    "如需理解图片，请在 设置 → 模型服务 → 用途分槽 把「识图」槽指向一个"
    "识图/多模态模型；或请用户直接用文字描述图片内容。不要凭路径猜测图片内容。"
)


async def _read_image_with_vision(args_json: str, session_id: str, **_kw):
    """``read_image`` —— 图片默认**只回路径**，理解交给识图槽。

    上下文策略由 ``agent.imageContextMode`` 决定：

    * ``path``（默认）—— 图片字节**不进上下文**。工具结果只留 "路径|尺寸|字节数"，
      需要"看图"时由识图槽模型返回**文本描述**（文本开销，远小于 base64），
      或者由子代理代办、主上下文只记路径。
    * ``inline`` —— 把字节交给多模态主模型（原项目行为，长期吃上下文）。

    无论哪种模式，槽位缺失/调用失败都不会让回合崩掉：改成可读的提示文本。
    """
    result = ReadImageTool.execute(args_json, session_id)
    if not result.success:
        return result

    try:
        from .store import SettingsStore

        store = SettingsStore.get()
        mode = _image_mode(store)
        has_vision = _chat_model_has_native_vision(store)
    except Exception:  # pragma: no cover - settings unreadable
        return result

    # inline 且主模型自带视觉 → 原样返回字节（运行时附到下一轮请求）
    if mode == "inline" and has_vision:
        return result
    if not result.image_data:
        return result

    base_text = result.output
    title = result.tool_title or ReadImageTool.NAME

    desc: str | None = None
    try:
        from .vision_service import describe_image

        try:
            prompt = str(json.loads(args_json).get("prompt") or "")
        except Exception:  # pragma: no cover - args already validated upstream
            prompt = ""
        desc = await describe_image(
            store,
            result.image_data,
            result.image_mime_type or "image/jpeg",
            prompt=prompt,
            image_path=result.image_file_path,
        )
    except Exception:  # pragma: no cover - never break the loop over this
        logger.debug("read_image vision routing failed", exc_info=True)
        desc = None

    if desc:
        # 只回文本 —— 图片字节不进上下文
        return ToolExecutionResult(
            output=f"{base_text}\n\n{desc}", success=True, tool_title=title
        )

    # 没有识图槽（或描述失败）：明确说明只有路径，避免模型硬猜图片内容
    tail = _NO_VISION_SLOT_HINT
    if mode == "path" and has_vision:
        tail = (
            "当前为「图片只传路径」模式（agent.imageContextMode=path），"
            "因此没有把图片直接交给模型。若你确实需要直读图片，可请用户在"
            " 模型设置 把图片模式改为 inline，或在 设置 → 模型服务 → 用途分槽 "
            "配置「识图」模型。不要凭路径猜测图片内容。"
        )
    return ToolExecutionResult(
        output=f"{base_text}\n\n{tail}", success=True, tool_title=title
    )


def build_tool_registry(enabled_ids: list[str]) -> dict[str, ToolExecutor]:
    """Instantiate the enabled tools as a runtime ``ToolExecutor`` registry.

    ``shell_execute`` is stateful: it keeps a per-session persistent shell via
    the default coordinator, matching the sandbox semantics used by the TUI.
    """
    out: dict[str, ToolExecutor] = {}
    want = [t for t in enabled_ids if t in VALID_TOOLS]
    for tool_id in want:
        if tool_id == "file_read":
            out[tool_id] = _wrap_static_executor(
                FileReadTool.definition(), FileReadTool.execute
            )
        elif tool_id == "file_write":
            out[tool_id] = _wrap_static_executor(
                FileWriteTool.definition(), FileWriteTool.execute
            )
        elif tool_id == "file_edit":
            out[tool_id] = _wrap_static_executor(
                FileEditTool.definition(), FileEditTool.execute
            )
        elif tool_id == "read_image":
            # 走识图槽兜底（主模型无视觉时用识图模型描述图片）
            out[tool_id] = _wrap_async_static_executor(
                ReadImageTool.definition(), _read_image_with_vision
            )
        elif tool_id == "image_gen":
            out[tool_id] = _wrap_async_static_executor(
                ImageGenTool.definition(), ImageGenTool.execute
            )
        elif tool_id == "shell_execute":
            shell = ShellExecuteTool()
            tool = types.SimpleNamespace(
                name=shell.NAME,
                definition=ShellExecuteTool.definition(),
                executor=shell.execute,
            )
            out[tool_id] = tool
        elif tool_id == "browser_use":
            out[tool_id] = _wrap_async_static_executor(
                BrowserUseTool.definition(), BrowserUseTool.execute
            )
        elif tool_id == "memory_write":
            out[tool_id] = _wrap_static_executor(
                MemoryWriteTool.definition(), MemoryWriteTool.execute
            )
        elif tool_id == "memory_get":
            out[tool_id] = _wrap_static_executor(
                MemoryGetTool.definition(), MemoryGetTool.execute
            )
        elif tool_id == "ls":
            out[tool_id] = _wrap_async_static_executor(
                LsTool.definition(), LsTool.execute
            )
        elif tool_id == "search_files":
            out[tool_id] = _wrap_async_static_executor(
                SearchFilesTool.definition(), SearchFilesTool.execute
            )
        elif tool_id == "web_fetch":
            out[tool_id] = _wrap_async_static_executor(
                WebFetchTool.definition(), WebFetchTool.execute
            )
        elif tool_id == "web_search":
            out[tool_id] = _wrap_async_static_executor(
                WebSearchTool.definition(), WebSearchTool.execute
            )
        elif tool_id == "subagent_delegate":
            out[tool_id] = _wrap_async_static_executor(
                SubagentDelegateTool.definition(), SubagentDelegateTool.execute
            )
        elif tool_id == "skill_use":
            out[tool_id] = _wrap_async_static_executor(
                SkillUseTool.definition(), SkillUseTool.execute
            )
    return out


# ---------------------------------------------------------------------------
# Re-export the central tool-registry helper from the catalog layer so the
# rest of the app only needs ``openminis.settings`` to build agent tool
# schemas.
# ---------------------------------------------------------------------------
def make_agent_tools(
    supports_image_input: bool = True,
    vision_group_configured: bool | None = None,
    memory_enabled: bool = True,
) -> list[AgentToolDefinition]:
    """Return the AgentToolDefinition list for the agent loop.

    ``vision_group_configured`` defaults to the static resolver's answer so
    callers don't have to plumb a config object in just to gate
    ``read_image``.
    """
    if vision_group_configured is None:
        vision_group_configured = VisionGroupResolver.is_configured()
    return AgentTools.make_agent_tools(
        supports_image_input=supports_image_input,
        vision_group_configured=vision_group_configured,
        memory_enabled=memory_enabled,
    )
