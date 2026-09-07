"""Provider / model registry types referenced by VisionGroupResolver.

Ported from: src/android/app/src/main/java/com/openminis/app/data/model/ModelEntry.kt
and .../ProviderInstance.kt, .../data/repository/ProviderRepository.kt,
.../provider/ProviderFactory.kt
Original package: com.openminis.app.data.model + com.openminis.app.provider

# PORT: forward-reference stub. Defines only the symbols the tools/mcp port
# imports. Method bodies are placeholders — the dedicated provider/ port fills
# them with real LLM transport. Import succeeds; full behavior arrives later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from openminis.data.model.llm import ImagePart, LLMMessage

__all__ = [
    "ModelEntry",
    "ProviderInstance",
    "ProviderRepository",
    "ProviderFactory",
]


@dataclass(slots=True)
class ModelEntry:
    """A model within a provider instance."""

    model: "Model"  # noqa: F821  (Model defined below for self-reference)


@dataclass(slots=True)
class Model:
    """Minimal model descriptor (id, display name, modality)."""

    id: str
    displayName: str = ""
    hasImageInput: bool = False


@dataclass(slots=True)
class ProviderInstance:
    """A configured provider instance (endpoint + creds + label)."""

    id: str
    label: str = ""
    model: Model = field(default_factory=lambda: Model(id=""))


# Re-bind ModelEntry.model to the real Model dataclass.
ModelEntry.__init__.__annotations__["model"] = Model


class Provider(Protocol):
    """Protocol for an LLM provider client (see VisionGroupResolver.describe)."""

    name: str

    async def sendMessage(
        self,
        messages: list[LLMMessage],
        systemPrompt: str = "",
        maxTokens: int = 2048,
        imageParts: list[ImagePart] | None = None,
    ) -> Any: ...


class ProviderRepository:
    """Placeholder for the real repository. See module docstring."""

    def resolveVisionCandidates(self, loadBalanceSeed: int = 0) -> list[tuple[ProviderInstance, ModelEntry]]:
        """Kotlin: ``resolveVisionCandidates(loadBalanceSeed=)``.

        # PORT: stub returns no candidates; the real port resolves the
        # configured Vision Group.
        """
        return []

    def visionGroupName(self) -> str | None:
        return None

    def usableApiKey(self, instance: ProviderInstance) -> str | None:
        """Kotlin: ``usableApiKey`` — returns "" for keyless endpoints.

        # PORT: stub returns None (no credential) — see T-empty-key-compat.
        """
        return None


class ProviderFactory:
    """Builds a provider client from an instance + key + model."""

    @staticmethod
    def create(
        instance: ProviderInstance,
        apiKey: str,
        model: ModelEntry | Model,
        context: Any | None = None,
    ) -> Provider:
        raise NotImplementedError("ProviderFactory.create — filled by the provider/ port")


# Convenience aliases so call sites can reference the nested ModelEntry.model
# shape without surprises.
ModelEntry.model = Model  # type: ignore[assignment]
