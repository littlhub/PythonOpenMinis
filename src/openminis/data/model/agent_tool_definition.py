"""Provider-agnostic tool definition.

Ported from: src/android/app/src/main/java/com/openminis/app/data/model/AgentToolDefinition.kt
Original package: com.openminis.app.data.model
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["AgentToolDefinition", "AgentToolParam"]


@dataclass(frozen=True, slots=True)
class AgentToolParam:
    """One JSON-schema property of a tool's parameter object."""

    type: str
    description: str
    enum_values: tuple[str, ...] | None = None

    def __init__(self, type: str, description: str, enum_values: list[str] | None = None) -> None:
        object.__setattr__(self, "type", type)
        object.__setattr__(self, "description", description)
        object.__setattr__(
            self, "enum_values", tuple(enum_values) if enum_values is not None else None
        )

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"type": self.type, "description": self.description}
        if self.enum_values is not None:
            out["enum"] = list(self.enum_values)
        return out

    def to_gemini_json(self) -> dict[str, object]:
        """Gemini wants the type upper-cased (``STRING``, not ``string``)."""
        out: dict[str, object] = {
            "type": self.type.upper(),
            "description": self.description,
        }
        if self.enum_values is not None:
            out["enum"] = list(self.enum_values)
        return out


@dataclass(frozen=True, slots=True)
class AgentToolDefinition:
    """Provider-agnostic tool definition.

    Each tool registers with this structure, and providers convert it to their
    native format (Anthropic input_schema, Gemini function_declarations, OpenAI
    function calling).

    PORT: the Kotlin originals build ``org.json.JSONObject`` trees. Python
    returns plain ``dict`` — same wire shape, and it serialises directly with
    ``json.dumps`` for HTTP transport.
    """

    name: str
    description: str
    parameters: dict[str, AgentToolParam] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    property_ordering: tuple[str, ...] | None = None

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, AgentToolParam] | None = None,
        required: list[str] | None = None,
        property_ordering: list[str] | None = None,
    ) -> None:
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "parameters", dict(parameters or {}))
        object.__setattr__(self, "required", tuple(required or ()))
        object.__setattr__(
            self,
            "property_ordering",
            tuple(property_ordering) if property_ordering is not None else None,
        )

    # --- provider encodings ---------------------------------------------
    def to_anthropic_json(self) -> dict[str, object]:
        """``{name, description, input_schema: {type:object, properties, required}}``."""
        schema: dict[str, object] = {
            "type": "object",
            "properties": {k: p.to_json() for k, p in self.parameters.items()},
        }
        if self.required:
            schema["required"] = list(self.required)
        return {"name": self.name, "description": self.description, "input_schema": schema}

    def to_gemini_json(self) -> dict[str, object]:
        """``{name, description, parameters: {type:OBJECT, properties, required}}``."""
        params: dict[str, object] = {
            "type": "OBJECT",
            "properties": {k: p.to_gemini_json() for k, p in self.parameters.items()},
        }
        if self.required:
            params["required"] = list(self.required)
        if self.property_ordering is not None:
            params["propertyOrdering"] = list(self.property_ordering)
        return {"name": self.name, "description": self.description, "parameters": params}

    def to_openai_json(self) -> dict[str, object]:
        """``{type:function, function: {name, description, parameters: {...}}}``."""
        params: dict[str, object] = {
            "type": "object",
            "properties": {k: p.to_json() for k, p in self.parameters.items()},
        }
        if self.required:
            params["required"] = list(self.required)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": params,
            },
        }
