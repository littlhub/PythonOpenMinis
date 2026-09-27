"""Permission rules — merge project/session permission updates into a ruleset.

Ported from: packages/core/src/tool/executor/permission-rules.ts
Original package: @zcode/core (tool/executor)
"""

from __future__ import annotations

from typing import Sequence

from ...permission.ports import PermissionRuleValue, PermissionRuleset, PermissionUpdate

__all__ = ["apply_permission_updates"]


def apply_permission_updates(
    current: PermissionRuleset,
    updates: Sequence[PermissionUpdate],
) -> PermissionRuleset:
    """Mirrors ``applyPermissionUpdates(current, updates)``."""
    # PORT: TS ``{...current, version: 1}`` — 浅拷贝后强制 version=1。
    next_ruleset: PermissionRuleset = {**current, "version": 1}

    for update in updates:
        if update.type != "addRules":
            continue
        existing = _get_permission_rules(next_ruleset, update.behavior)
        next_ruleset = {
            **next_ruleset,
            update.behavior: _dedupe_permission_rules([*existing, *update.rules]),
        }

    return next_ruleset


def _get_permission_rules(ruleset: PermissionRuleset, behavior: str) -> list[PermissionRuleValue]:
    """Mirrors ``getPermissionRules(ruleset, behavior)``."""
    rules = ruleset.get(behavior)
    # PORT: TS ``Array.isArray(rules) ? rules : []``。
    return rules if isinstance(rules, list) else []


def _dedupe_permission_rules(
    rules: Sequence[PermissionRuleValue],
) -> list[PermissionRuleValue]:
    """Mirrors ``dedupePermissionRules(rules)``."""
    seen: set[str] = set()
    deduped: list[PermissionRuleValue] = []

    for rule in rules:
        # TS 模板串里 ``\u0000`` 是 NUL 分隔符，避免 toolName 与 ruleContent 拼接歧义。
        key = f"{rule.tool_name}\u0000{rule.rule_content if rule.rule_content is not None else ''}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(rule)

    return deduped
