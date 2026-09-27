"""Permission suggestions — default permission updates for an ask.

Ported from: packages/core/src/tool/executor/permission-suggestions.ts
Original package: @zcode/core (tool/executor)
"""

from __future__ import annotations

from typing import Any, Optional

from ...permission.ports import (
    OFFICIAL_CUA_PERMISSION_RULE_TOOL_NAME,
    PermissionCapabilityGroup,
    PermissionRuleValue,
    PermissionUpdate,
)

__all__ = ["build_default_permission_updates"]

# PORT: 真实来源 TS ``const PROJECT_RULE_INPUT_KEYS = [...] as const``。
_PROJECT_RULE_INPUT_KEYS: tuple[str, ...] = ("command", "url", "file_path", "path", "pattern")


def build_default_permission_updates(
    tool_name: str,
    input: Any,
    capability_group: Optional[PermissionCapabilityGroup] = None,
) -> list[PermissionUpdate]:
    """Mirrors ``buildDefaultPermissionUpdates(toolName, input, capabilityGroup?)``."""
    if capability_group is not None:
        if capability_group != PermissionCapabilityGroup.OFFICIAL_CUA:
            # PORT: TS ``throw new Error(...)`` —— 普通 Error，非 CoreError。
            # Python 侧选 ``ValueError``（入参枚举取值不受支持）。文案逐字保留。
            raise ValueError(f"Unsupported permission capability group: {capability_group}")

        return [
            PermissionUpdate(
                behavior="allow",
                rules=[PermissionRuleValue(tool_name=OFFICIAL_CUA_PERMISSION_RULE_TOOL_NAME)],
            )
        ]

    rule_content = _rule_content_from_input(input)
    return [
        PermissionUpdate(
            behavior="allow",
            rules=[
                # TS ``...(ruleContent ? { ruleContent } : {})``：只在真值（非空串）时带上。
                PermissionRuleValue(
                    tool_name=tool_name,
                    rule_content=rule_content if rule_content else None,
                )
            ],
        )
    ]


def _rule_content_from_input(input: Any) -> Optional[str]:
    """Mirrors ``ruleContentFromInput(input)``."""
    if isinstance(input, str) and len(input.strip()) > 0:
        return input
    # PORT: TS ``typeof input !== "object" || input === null``。
    if not isinstance(input, dict):
        return None
    record: dict[str, Any] = input
    for key in _PROJECT_RULE_INPUT_KEYS:
        value = record.get(key)
        if isinstance(value, str) and len(value.strip()) > 0:
            return value
    return None
