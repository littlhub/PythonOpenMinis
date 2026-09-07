"""LLM agent tool schema types (definition + param).

Ported from: src/android/app/src/main/java/com/openminis/app/data/model/AgentToolDefinition.kt
Original package: com.openminis.app.data.model

# PORT: forward-reference stub. Defines only the two symbols the
# agent/tools/mcp/shared port imports. The full data.model port (serialization,
# JSON schema emission) lands in the dedicated data.model port; these keep the
# shape + field names the LLM tool-call JSON schema depends on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["AgentToolParam", "AgentToolDefinition"]


@dataclass(slots=True)
class AgentToolParam:
    """A single parameter declared by an agent tool.

    Mirrors the Kotlin data class — ``type``/``description`` are mandatory,
    ``enumValues`` carries the allowed values for enum-typed params.
    """

    type: str
    description: str
    enumValues: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type, "description": self.description}
        if self.enumValues is not None:
            d["enum"] = list(self.enumValues)
        return d


@dataclass(slots=True)
class AgentToolDefinition:
    """Provider-agnostic tool definition consumed by the agent loop.

    ``parameters`` maps param name → :class:`AgentToolParam`;
    ``required`` / ``propertyOrdering`` preserve JSON-schema argument order.
    """

    name: str
    description: str
    parameters: dict[str, AgentToolParam] = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    propertyOrdering: list[str] = field(default_factory=list)

    def to_json_schema(self) -> dict[str, Any]:
        """Emit the OpenAI/Anthropic/Gemini ``input_schema`` body.

        PORT: mirrors AgentTools.kt semantics — ``propertyOrdering`` drives the
        object key order so the LLM sees arguments in the authored order.
        """
        props: dict[str, Any] = {}
        order = self.propertyOrdering or list(self.parameters.keys())
        for key in order:
            if key in self.parameters:
                props[key] = self.parameters[key].to_dict()
        # include any param not present in the ordering, as a safety net
        for key, param in self.parameters.items():
            if key not in props:
                props[key] = param.to_dict()
        return {
            "type": "object",
            "properties": props,
            "required": list(self.required),
        }
