"""Bash command rule evaluator — match saved permission rules against invocations.

Ported from: packages/core/src/tool/handlers/bash-command-rule-evaluator.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

# PORT: TS 从 ``@zcode/contracts`` 取 ``PermissionRuleBehavior`` / ``PermissionRuleValue``；
# 本包这两个契约类型尚未有对应已移植 contracts 模块，按 ``permission/ports.py`` 的临时落点先例
# 从那里引用（真实来源见该文件 ``# PORT:`` 注释）。
from ...permission.ports import PermissionRuleBehavior, PermissionRuleValue
from ...permission.rule_matching import wildcard_to_regexp

__all__ = ["BashRuleEvaluationInput", "evaluate_bash_rules"]


@dataclass(slots=True)
class BashRuleEvaluationInput:
    """Mirrors ``interface BashRuleEvaluationInput``."""

    all_subject_groups: Sequence[Sequence[str]]
    behavior: PermissionRuleBehavior
    exact_commands: Sequence[str]
    required_subject_groups: Sequence[Sequence[str]]
    rules: Sequence[PermissionRuleValue]
    safe: bool


def evaluate_bash_rules(input: BashRuleEvaluationInput) -> bool:
    """Mirrors ``evaluateBashRules(input)``."""
    if any(not rule.rule_content for rule in input.rules):
        return True
    if any(len(command) > 0 for command in input.exact_commands) and any(
        (rule.rule_content if rule.rule_content is not None else "") in input.exact_commands
        for rule in input.rules
    ):
        return True
    if not input.safe:
        return False

    subject_groups = (
        input.required_subject_groups if input.behavior == "allow" else input.all_subject_groups
    )
    if len(subject_groups) == 0:
        return False
    if input.behavior != "allow":
        return any(
            any(
                any(_matches_invocation_rule(subject, rule.rule_content) for rule in input.rules)
                for subject in subjects
            )
            for subjects in subject_groups
        )
    return all(
        any(
            any(_matches_invocation_rule(subject, rule.rule_content) for rule in input.rules)
            for subject in subjects
        )
        for subjects in subject_groups
    )


def _matches_invocation_rule(subject: str, rule_content: Optional[str]) -> bool:
    """Mirrors the non-exported ``matchesInvocationRule(subject, ruleContent)``."""
    if not rule_content:
        return True
    if rule_content.endswith(":*"):
        prefix = rule_content[:-2]
        return (
            subject == prefix
            or subject.startswith(f"{prefix} ")
            or subject.startswith(f"{prefix}\t")
        )
    if "*" in rule_content:
        return wildcard_to_regexp(rule_content).search(subject) is not None
    return subject == rule_content
