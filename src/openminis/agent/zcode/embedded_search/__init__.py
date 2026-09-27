"""Embedded search — provider-visible branch capability + Bash prelude injection.

Ported from: packages/core/src/embedded-search/**
Original package: @zcode/core (embedded-search)
"""

from __future__ import annotations

from .capability import (
    ENABLE_EMBEDDED_SEARCH_BRANCH,
    EmbeddedSearchBranchCapability,
    EmbeddedSearchBranchCapabilityContext,
    EmbeddedSearchBranchCapabilityDecision,
    EmbeddedSearchBranchCapabilityReason,
    evaluate_embedded_search_branch_capability,
    resolve_embedded_search_branch_capability,
)
from .shell import (
    ENABLE_EMBEDDED_SEARCH_BASH_PRELUDE,
    should_inject_embedded_search_bash_prelude,
    supports_embedded_search_shell_selection,
)

__all__ = [
    "ENABLE_EMBEDDED_SEARCH_BRANCH",
    "ENABLE_EMBEDDED_SEARCH_BASH_PRELUDE",
    "EmbeddedSearchBranchCapability",
    "EmbeddedSearchBranchCapabilityContext",
    "EmbeddedSearchBranchCapabilityDecision",
    "EmbeddedSearchBranchCapabilityReason",
    "evaluate_embedded_search_branch_capability",
    "resolve_embedded_search_branch_capability",
    "should_inject_embedded_search_bash_prelude",
    "supports_embedded_search_shell_selection",
]
