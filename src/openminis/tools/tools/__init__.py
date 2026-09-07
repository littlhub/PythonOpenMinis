"""
mcp.tools - 工具集包

SkillMCP 实际承载的自包含工具：memory（RAG 记忆）/ repofetch（外部依赖拉取）/
showtagger（计费监控）等，均不依赖外部 agent 框架。

agent.tools.* 命名空间下的框架工具属于外部 agent 体系（本项目未安装），
全部走容错导入：任一工具缺依赖仅警告并置 None，绝不拖垮整个包。
"""

try:
    from common.log import logger
except ImportError:
    import logging
    logger = logging.getLogger("mcp.tools")


def _try_import(name, *mods, attr=None):
    """安全导入：成功返回 (True, 对象)，失败警告并返回 (False, None)。

    依次尝试 mods 里的模块；attr 指定时取模块的该属性，否则取模块里与
    name 同名的顶层对象。
    """
    for m in mods:
        try:
            if attr:
                import importlib
                obj = getattr(importlib.import_module(m), attr)
            else:
                obj = getattr(__import__(m, fromlist=["*"]), name)
            return True, obj
        except Exception as e:
            logger.warning(f"[Tools] {name}（{m}）未加载：{e}")
    return False, None


# ---- 基础框架工具（外部 agent 命名空间，缺失时全部为 None） ----
_ok, BaseTool = _try_import("BaseTool", "agent.tools.base_tool",
                            "agentorg.tools.base_tool", attr="BaseTool")
_ok, ToolManager = _try_import("ToolManager", "agent.tools.tool_manager",
                               "agentorg.tools.tool_manager", attr="ToolManager")

# ---- 文件操作工具 ----
_ok, Read = _try_import("Read", "agent.tools.read.read", "agentorg.tools.read.read",
                        attr="Read")
_ok, Write = _try_import("Write", "agent.tools.write.write",
                         "agentorg.tools.write.write", attr="Write")
_ok, Edit = _try_import("Edit", "agent.tools.edit.edit", "agentorg.tools.edit.edit",
                        attr="Edit")
_ok, Bash = _try_import("Bash", "agent.tools.bash.bash", "agentorg.tools.bash.bash",
                        attr="Bash")
_ok, Ls = _try_import("Ls", "agent.tools.ls.ls", "agentorg.tools.ls.ls", attr="Ls")
_ok, Send = _try_import("Send", "agent.tools.send.send", "agentorg.tools.send.send",
                        attr="Send")
_ok, SearchFiles = _try_import("SearchFiles", "agent.tools.search_files.search_files",
                               "agentorg.tools.search_files.search_files",
                               attr="SearchFiles")

# ---- 记忆工具（外部 agent 命名空间版本；SkillMCP 自包含版在 mcp.tools.memory） ----
_ok, MemorySearchTool = _try_import(
    "MemorySearchTool", "agent.tools.memory.memory_search",
    "agentorg.tools.memory.memory_search", attr="MemorySearchTool")
_ok, MemoryGetTool = _try_import(
    "MemoryGetTool", "agent.tools.memory.memory_get",
    "agentorg.tools.memory.memory_get", attr="MemoryGetTool")

# ---- 自进化 / 子代理 工具 ----
_ok, EvolutionUndoTool = _try_import(
    "EvolutionUndoTool", "agent.tools.evolution_undo.evolution_undo",
    "agentorg.tools.evolution_undo.evolution_undo", attr="EvolutionUndoTool")
_ok, SubagentTool = _try_import(
    "SubagentTool", "agent.tools.subagent.subagent",
    "agentorg.tools.subagent.subagent", attr="SubagentTool")

# ---- 可选依赖工具（缺依赖仅警告，置 None） ----
def _try_optional(name, *mods, attr=None):
    ok, obj = _try_import(name, *mods, attr=attr)
    if ok:
        return obj
    return None

EnvConfig = _try_optional("EnvConfig", "agent.tools.env_config.env_config",
                          "agentorg.tools.env_config.env_config", attr="EnvConfig")
SchedulerTool = _try_optional("SchedulerTool", "agent.tools.scheduler.scheduler_tool",
                              "agentorg.tools.scheduler.scheduler_tool",
                              attr="SchedulerTool")
WebSearch = _try_optional("WebSearch", "agent.tools.web_search.web_search",
                          "agentorg.tools.web_search.web_search", attr="WebSearch")
WebFetch = _try_optional("WebFetch", "agent.tools.web_fetch.web_fetch",
                         "agentorg.tools.web_fetch.web_fetch", attr="WebFetch")
Vision = _try_optional("Vision", "agent.tools.vision.vision",
                       "agentorg.tools.vision.vision", attr="Vision")

# ---- BrowserTool：playwright 在 browser_service 内部软导入，缺失仅警告 ----
BrowserTool = _try_optional("BrowserTool", "agent.tools.browser.browser_tool",
                            "agentorg.tools.browser.browser_tool",
                            attr="BrowserTool")

# ---- MCP 工具（按需加载，缺失仅警告） ----
McpTool = _try_optional("McpTool", "agent.tools.mcp.mcp_tool",
                        "agentorg.tools.mcp.mcp_tool", attr="McpTool")
McpClientRegistry = _try_optional(
    "McpClientRegistry", "agent.tools.mcp.mcp_client",
    "agentorg.tools.mcp.mcp_client", attr="McpClientRegistry")

# 兼容占位（原 agentorg 导出里存在但本包不加载的项）
GoogleSearch = None
FileSave = None
Terminal = None

__all__ = [
    "BaseTool", "ToolManager",
    "Read", "Write", "Edit", "Bash", "Ls", "Send", "SearchFiles",
    "MemorySearchTool", "MemoryGetTool",
    "EvolutionUndoTool", "SubagentTool",
    "EnvConfig", "SchedulerTool", "WebSearch", "WebFetch", "Vision",
    "BrowserTool", "McpTool", "McpClientRegistry",
]
