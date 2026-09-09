"""Central registry of agent tool definitions.

Ported from: src/android/app/src/main/java/com/openminis/app/tools/AgentTools.kt
Original package: com.openminis.app.tools

Builds the list of :class:`AgentToolDefinition` used by the agent loop, with
the same gating semantics as the Android port:

- ``read_image`` is exposed when the main model has native image input OR
  a Vision Group is configured (so a non-vision model can still get a text
  description via the Vision Group).
- ``memory_write`` and ``memory_get`` are exposed only while memory is
  enabled (mirrors the iOS ``memoryEnabled`` gate).

Stateless or near-stateless tools (file_read, file_write, file_edit,
read_image, memory_write, memory_get) are listed by their ``definition()``
function so providers can render the JSON schema. Stateful tools
(``shell_execute``, ``browser_use``) carry an executor instance; the
registry returns just the schema and the actual executor lives in
``build_tool_registry`` (see ``openminis.settings.catalog``).
"""

from __future__ import annotations

from ..data.model.agent_tool_definition import AgentToolDefinition
from .browser_use_tool import BrowserUseTool
from .file_edit_tool import FileEditTool
from .file_read_tool import FileReadTool
from .file_write_tool import FileWriteTool
from .ls_tool import LsTool
from .memory_tools import memory_get_definition, memory_write_definition
from .read_image_tool import ReadImageTool
from .search_files_tool import SearchFilesTool
from .shell_execute_tool import ShellExecuteTool
from .subagent_tool import SubagentDelegateTool
from .vision_group_resolver import VisionGroupResolver
from .web_fetch_tool import WebFetchTool
from .web_search_tool import WebSearchTool

__all__ = ["AgentTools", "make_agent_tools"]


class AgentTools:
    """Static namespace mirroring the Kotlin ``object AgentTools``."""

    @staticmethod
    def make_agent_tools(
        supports_image_input: bool = True,
        vision_group_configured: bool = False,
        memory_enabled: bool = True,
    ) -> list[AgentToolDefinition]:
        out: list[AgentToolDefinition] = [
            ShellExecuteTool.definition(),
            FileReadTool.definition(),
            FileWriteTool.definition(),
            FileEditTool.definition(),
            # First-agent toolset additions (see tools/firstagenttools):
            # listing + content/name search + web fetch/search. Cheap to expose
            # unconditionally — web_search reports "not configured" at call time.
            LsTool.definition(),
            SearchFilesTool.definition(),
        ]
        # [T-android-vision-group / GH#182] when the main model can't see
        # natively but a Vision Group is bound, still expose read_image so
        # the tool can route through a describing member.
        if supports_image_input or vision_group_configured:
            out.append(ReadImageTool.definition())
        out.append(BrowserUseTool.definition())
        out.append(WebFetchTool.definition())
        out.append(WebSearchTool.definition())
        # Delegation to configured subagents — needs a subagent registry entry
        # at call time; schema exposure is harmless.
        out.append(SubagentDelegateTool.definition())
        # [T-memory-toggle-gates-injection-and-tools-android] memory off
        # means memory_write / memory_get are dropped from the schema so the
        # model can't even attempt them.
        if memory_enabled:
            out.append(memory_write_definition())
            out.append(memory_get_definition())
        return out

    # -----------------------------------------------------------------
    # Convenience accessors kept for tests / future shell help text
    # -----------------------------------------------------------------
    @staticmethod
    def all_definition_names(
        supports_image_input: bool = True,
        vision_group_configured: bool = False,
        memory_enabled: bool = True,
    ) -> list[str]:
        return [
            d.name
            for d in AgentTools.make_agent_tools(
                supports_image_input=supports_image_input,
                vision_group_configured=vision_group_configured,
                memory_enabled=memory_enabled,
            )
        ]


#: Module-level alias for the original Kotlin singleton entry point.
make_agent_tools = AgentTools.make_agent_tools

#: Provide a default for ``vision_group_configured`` that reads the static
#: ``VisionGroupResolver.is_configured()`` so callers that don't care can
#: just import this helper.
def default_vision_group_configured() -> bool:
    return VisionGroupResolver.is_configured()
