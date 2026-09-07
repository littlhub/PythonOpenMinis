"""Declarative catalog of each model's thinking-level ceiling.

Ported from: src/android/app/src/main/java/com/openminis/app/provider/ThinkingLevelCatalog.kt
Original package: com.openminis.app.provider
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from openminis.data.model import LLMModel, ModelEntry, ThinkingLevel

__all__ = ["ThinkingLevelCatalog"]


class ThinkingLevelCatalog:
    """[T-android-thinking-level-arch] Declarative catalog of thinking ceilings."""

    @dataclass(slots=True)
    class _Rule:
        match: Callable[[str], bool]
        max: ThinkingLevel

    rules: list["ThinkingLevelCatalog._Rule"] = [
        # GPT-5.6 family: sol / terra / luna all reach MAX.
        _Rule(lambda it: it.startswith("gpt-5.6-sol") or it.startswith("gpt-5.6-terra"), ThinkingLevel.MAX),
        _Rule(lambda it: it.startswith("gpt-5.6-luna"), ThinkingLevel.MAX),
        _Rule(lambda it: it.startswith("gpt-5.5"), ThinkingLevel.XHIGH),
        # Third-party models known to top out at high.
        _Rule(lambda it: "mimo" in it or "agnes" in it, ThinkingLevel.HIGH),
        _Rule(lambda it: "seed-" in it or "bytedance-seed" in it, ThinkingLevel.HIGH),
        _Rule(lambda it: ThinkingLevelCatalog._normalized_has_prefix(it, "claude-opus-4"), ThinkingLevel.MAX),
    ]

    @staticmethod
    def _normalized_has_prefix(id: str, prefix: str) -> bool:
        """Prefix match that treats "." and "-" interchangeably in the version
        separator so a rule matches whether the id is dotted or hyphenated."""
        return id.replace(".", "-").startsWith(prefix)  # type: ignore[attr-defined]

    @classmethod
    def declared_max_level(cls, model_id: str) -> Optional[ThinkingLevel]:
        """Null means the catalog doesn't cover this model — fall through to
        the supportsReasoning default."""
        lid = model_id.lower()
        for rule in cls.rules:
            if rule.match(lid):
                return rule.max
        return None


def _catalog_max_thinking_level(model: LLMModel) -> ThinkingLevel:
    """[T-android-thinking-level-arch] How high can this model's thinking go?
    Resolved through the built-in tiers only (no user override)."""
    if model.supports_reasoning is False:
        return ThinkingLevel.OFF
    last = model.selectable_thinking_levels[-1] if model.selectable_thinking_levels else None
    if last is not None:
        return last
    return ThinkingLevelCatalog.declared_max_level(model.id) or ThinkingLevel.XHIGH


# NOTE: `LLMModel.catalog_max_thinking_level` and
# `ModelEntry.effective_max_thinking_level` are defined in
# `openminis.data.model` (mirroring the Kotlin extension properties) so the
# data layer stays self-contained; both delegate here for the catalog rule.

