"""Embedded search branch capability resolution.

Ported from: packages/core/src/embedded-search/capability.ts
Original package: @zcode/core (embedded-search)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

__all__ = [
    "ENABLE_EMBEDDED_SEARCH_BRANCH",
    "EmbeddedSearchBranchCapabilityReason",
    "EmbeddedSearchBranchCapabilityContext",
    "EmbeddedSearchBranchCapabilityDecision",
    "EmbeddedSearchBranchCapability",
    "evaluate_embedded_search_branch_capability",
    "resolve_embedded_search_branch_capability",
]

# 默认进入 provider-visible embedded search branch。Bash 的 find/grep prelude
# 注入由执行层单独控制，不能因为当前 shell 不支持 function 注入就改变模型看到的
# tool/prompt surface。
ENABLE_EMBEDDED_SEARCH_BRANCH = True

# PORT: ``type EmbeddedSearchBranchCapabilityReason``。
EmbeddedSearchBranchCapabilityReason = Literal[
    "supported",
    "disabled_by_global_flag",
    "bash_unavailable",
]


@dataclass(slots=True)
class EmbeddedSearchBranchCapabilityContext:
    """Mirrors ``interface EmbeddedSearchBranchCapabilityContext``."""

    bash_available: bool
    embedded_search_branch_enabled: Optional[bool] = None


@dataclass(slots=True)
class EmbeddedSearchBranchCapabilityDecision:
    """Mirrors ``interface EmbeddedSearchBranchCapabilityDecision``."""

    reason: EmbeddedSearchBranchCapabilityReason
    use_embedded_search_branch: bool


# PORT: 兼容别名——本文件早先以 ``EmbeddedSearchBranchCapability`` 命名其返回值类型，
# 宿主 ``tool/executor/call_runner.py`` 沿用了该名字。上游真实名字是
# ``EmbeddedSearchBranchCapabilityDecision``，别名指向权威定义以免两份漂移。
EmbeddedSearchBranchCapability = EmbeddedSearchBranchCapabilityDecision


def evaluate_embedded_search_branch_capability(
    context: EmbeddedSearchBranchCapabilityContext,
) -> EmbeddedSearchBranchCapabilityDecision:
    """Mirrors the non-exported ``evaluateEmbeddedSearchBranchCapability(context)``."""
    # PORT: TS ``context.embeddedSearchBranchEnabled ?? ENABLE_EMBEDDED_SEARCH_BRANCH``——
    # 用 ``??`` 而非 ``||``，显式 ``False`` 必须被尊重。
    branch_enabled = (
        context.embedded_search_branch_enabled
        if context.embedded_search_branch_enabled is not None
        else ENABLE_EMBEDDED_SEARCH_BRANCH
    )

    if not branch_enabled:
        return EmbeddedSearchBranchCapabilityDecision(
            reason="disabled_by_global_flag",
            use_embedded_search_branch=False,
        )

    if not context.bash_available:
        return EmbeddedSearchBranchCapabilityDecision(
            reason="bash_unavailable",
            use_embedded_search_branch=False,
        )

    return EmbeddedSearchBranchCapabilityDecision(
        reason="supported",
        use_embedded_search_branch=True,
    )


def resolve_embedded_search_branch_capability(
    input: EmbeddedSearchBranchCapabilityContext,
) -> EmbeddedSearchBranchCapabilityDecision:
    """Mirrors ``resolveEmbeddedSearchBranchCapability(input)``.

    PORT: TS 入参是匿名结构 ``{ bashAvailable; embeddedSearchBranchEnabled? }``；本包用
    具名 ``EmbeddedSearchBranchCapabilityContext`` 承载（与 ``path_policy`` 等 core API 的
    对象化先例一致）。同时容忍裸 ``dict``，以免宿主注入层用法退化。
    """
    if isinstance(input, dict):
        input = EmbeddedSearchBranchCapabilityContext(
            bash_available=bool(
                input["bash_available"]
                if "bash_available" in input
                else input.get("bashAvailable")
            ),
            embedded_search_branch_enabled=(
                input["embedded_search_branch_enabled"]
                if "embedded_search_branch_enabled" in input
                else input.get("embeddedSearchBranchEnabled")
            ),
        )
    return evaluate_embedded_search_branch_capability(input)
