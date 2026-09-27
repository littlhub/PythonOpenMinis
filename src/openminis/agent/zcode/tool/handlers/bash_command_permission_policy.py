"""Bash command permission policy — stable-prefix rules + suggested updates.

Ported from: packages/core/src/tool/handlers/bash-command-permission-policy.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional, Sequence

# PORT: TS 从 ``@zcode/contracts`` 取 ``PermissionRuleValue`` / ``PermissionUpdate``；
# 本包尚无对应 contracts 模块，按 ``permission/ports.py`` 的临时落点先例从那里引用。
from ...permission.ports import PermissionRuleValue, PermissionUpdate
from .bash_command_parser import (
    BashCommandAnalysis,
    BashCommandInvocation,
    analyze_bash_command,
    is_bash_command_permission_safe,
)
from .bash_command_rule_evaluator import BashRuleEvaluationInput, evaluate_bash_rules
from .bash_semantics import is_runtime_read_only_bash_command
from .generated.bash_command_registry import (
    BASH_COMMAND_REGISTRY,
    BashCommandRegistryNode,
)
from ..types import ToolPermissionRulePolicy, ToolRuntimePermissionCapabilityContext

__all__ = ["resolve_bash_permission_rule_policy"]


MAX_SUGGESTED_RULES = 5
ARG_IS_COMMAND = 1
ARG_IS_MODULE = 2
HIGH_RISK_ROOT_COMMANDS = frozenset(
    [
        "bash",
        "chgrp",
        "chmod",
        "chown",
        "cmd",
        "dd",
        "fish",
        "mkfs",
        "mount",
        "powershell",
        "pwsh",
        "rm",
        "rmdir",
        "sh",
        "umount",
        "zsh",
    ]
)
WRAPPER_OPTIONS_WITH_VALUES: dict[str, frozenset[str]] = {
    "command": frozenset(),
    "env": frozenset(["-C", "-S", "-u", "--argv0", "--chdir", "--split-string", "--unset"]),
    "nohup": frozenset(),
    "sudo": frozenset(
        [
            "-C",
            "-D",
            "-R",
            "-T",
            "-a",
            "-c",
            "-g",
            "-h",
            "-p",
            "-r",
            "-t",
            "-u",
            "--askpass",
            "--chdir",
            "--chroot",
            "--close-from",
            "--group",
            "--host",
            "--prompt",
            "--role",
            "--type",
            "--user",
        ]
    ),
    "time": frozenset(["-f", "-o", "--format", "--output"]),
}
WRAPPER_OPTIONS = frozenset(["-p", "-v", "-V", "--ignore-environment"])
SCRIPT_ACTIONS: dict[str, frozenset[str]] = {
    "bun": frozenset(["run"]),
    "deno": frozenset(["task"]),
    "npm": frozenset(["run", "run-script"]),
    "pnpm": frozenset(["run"]),
    "yarn": frozenset(["run"]),
}
TARGET_ACTIONS = frozenset(["just", "make"])
PYTHON_EXECUTABLES = frozenset(["python", "python3", "py"])
FAMILY_DEPTH_OVERRIDES: dict[str, dict[str, int]] = {
    "aws": {"*": 2},
    "az": {"*": 2},
    "docker": {"compose": 2},
    "gcloud": {"*": 3},
    "kubectl": {"config": 2},
}


def resolve_bash_permission_rule_policy(
    input: Any,
    context: Optional[ToolRuntimePermissionCapabilityContext] = None,
) -> Optional[ToolPermissionRulePolicy]:
    """Mirrors ``resolveBashPermissionRulePolicy(input, context?)``."""
    command = _read_command(input)
    if command is None:
        return None
    return _create_bash_permission_rule_policy(command, context)


def _create_bash_permission_rule_policy(
    command: str,
    context: Optional[ToolRuntimePermissionCapabilityContext] = None,
) -> ToolPermissionRulePolicy:
    raw_command = command.strip()
    exact_commands = [raw_command] if command == raw_command else [command, raw_command]
    analysis = analyze_bash_command(command)
    safe = _is_analysis_safe_for_prefix(analysis)
    all_subject_groups = (
        [_build_invocation_rule_subjects(invocation) for invocation in analysis.commands]
        if safe
        else []
    )
    required_commands = (
        [
            invocation
            for invocation in analysis.commands
            if not is_runtime_read_only_bash_command(invocation.command_text, context)
        ]
        if safe
        else []
    )
    required_subject_groups = [
        _build_invocation_rule_subjects(invocation) for invocation in required_commands
    ]
    suggested_permission_updates = _build_suggested_updates(raw_command, safe, required_commands)

    def evaluate_rules(behavior: Any, rules: Sequence[Any]) -> bool:
        return evaluate_bash_rules(
            BashRuleEvaluationInput(
                all_subject_groups=all_subject_groups,
                behavior=behavior,
                exact_commands=exact_commands,
                required_subject_groups=required_subject_groups,
                rules=rules,
                safe=safe,
            )
        )

    return ToolPermissionRulePolicy(
        evaluate_rules=evaluate_rules,
        suggested_permission_updates=suggested_permission_updates,
    )


def _build_invocation_rule_subjects(invocation: BashCommandInvocation) -> list[str]:
    raw_subject = _normalize_invocation(invocation)
    stable_prefix = _resolve_stable_command_prefix(invocation)
    # 保存规则会移除 --dir/-C 等全局 flag，但旧 evaluator 只拿原始 invocation
    # 比较，导致 UI 明明保存了 `pnpm run lint:*`，下一轮仍无法命中。保留 raw subject
    # 兼容历史 wildcard，同时加入相同 resolver 得出的稳定 action subject。
    return [raw_subject, stable_prefix] if stable_prefix and stable_prefix != raw_subject else [raw_subject]


def _build_suggested_updates(
    raw_command: str,
    safe: bool,
    required_commands: Sequence[BashCommandInvocation],
) -> list[PermissionUpdate]:
    if len(raw_command) == 0:
        return []
    if not safe or len(required_commands) == 0 or len(required_commands) > MAX_SUGGESTED_RULES:
        return _exact_update(raw_command)

    rules: list[PermissionRuleValue] = []
    seen: set[str] = set()
    for invocation in required_commands:
        prefix = _resolve_stable_command_prefix(invocation)
        if not prefix:
            return _exact_update(raw_command)
        rule_content = f"{prefix}:*"
        if rule_content in seen:
            continue
        seen.add(rule_content)
        rules.append(PermissionRuleValue(tool_name="Bash", rule_content=rule_content))
    if len(rules) == 0 or len(rules) > MAX_SUGGESTED_RULES:
        return _exact_update(raw_command)
    return [PermissionUpdate(behavior="allow", rules=rules)]


def _exact_update(raw_command: str) -> list[PermissionUpdate]:
    return [
        PermissionUpdate(
            behavior="allow",
            rules=[PermissionRuleValue(tool_name="Bash", rule_content=raw_command)],
        )
    ]


def _is_analysis_safe_for_prefix(analysis: BashCommandAnalysis) -> bool:
    return (
        is_bash_command_permission_safe(analysis)
        and not analysis.has_redirects
        and len(analysis.commands) > 0
        and all(
            not invocation.has_redirects
            and not invocation.has_dynamic_words
            # PORT: TS ``staticAssignmentTokens(invocation) !== undefined`` —— 空数组也算
            # 定义（JS 空数组为 truthy），故用 ``is not None``。
            and _static_assignment_tokens(invocation) is not None
            for invocation in analysis.commands
        )
    )


def _normalize_invocation(invocation: BashCommandInvocation) -> str:
    return " ".join([*(_static_assignment_tokens(invocation) or []), *invocation.argv])


@dataclass(slots=True)
class _UnwrappedCommand:
    executable: str
    next_index: int
    prefix: list[str]


def _resolve_stable_command_prefix(invocation: BashCommandInvocation) -> Optional[str]:
    assignments = _static_assignment_tokens(invocation)
    # PORT: TS ``!assignments`` 仅对 ``undefined`` 成立（空数组 truthy），故用 ``is None``。
    if assignments is None or len(invocation.argv) == 0:
        return None
    unwrapped = _unwrap_command(invocation.argv)
    if not unwrapped:
        return None
    executable_name = _executable_basename(unwrapped.executable)
    if executable_name in HIGH_RISK_ROOT_COMMANDS:
        return None

    prefix = [*assignments, *unwrapped.prefix, unwrapped.executable]
    remaining = invocation.argv[unwrapped.next_index :]
    direct_override = _resolve_depth_override(executable_name, remaining)
    if direct_override:
        prefix.extend(direct_override)
        return _serialize_prefix(prefix)

    node = BASH_COMMAND_REGISTRY.get(executable_name)
    if not node:
        return None
    override = _resolve_depth_override(executable_name, _skip_leading_known_options(node, remaining))
    if override:
        prefix.extend(override)
        return _serialize_prefix(prefix)

    index = 0
    matched_action = False
    while index < len(remaining):
        option_end = _skip_known_option(node, remaining, index)
        if option_end is not None:
            index = option_end
            continue

        token = remaining[index]
        child = _find_option_or_child(node[3], token)
        if child:
            prefix.append(token)
            matched_action = True
            node = child
            index += 1
            continue

        if (node[2] & (ARG_IS_COMMAND | ARG_IS_MODULE)) != 0 and not _looks_like_path_or_url(token):
            prefix.append(token)
            matched_action = True
        break

    return _serialize_prefix(prefix) if matched_action else None


def _find_option_or_child(
    candidates: Sequence[BashCommandRegistryNode],
    token: str,
) -> Optional[BashCommandRegistryNode]:
    """``candidates.find(candidate => candidate[0].includes(token))``."""
    for candidate in candidates:
        if token in candidate[0]:
            return candidate
    return None


def _skip_leading_known_options(
    node: BashCommandRegistryNode,
    args: Sequence[str],
) -> list[str]:
    index = 0
    while index < len(args):
        option_end = _skip_known_option(node, args, index)
        if option_end is None:
            break
        index = option_end
    return list(args[index:])


def _unwrap_command(argv: Sequence[str]) -> Optional[_UnwrappedCommand]:
    prefix: list[str] = []
    index = 0
    wrapper_depth = 0
    while index < len(argv):
        token = argv[index]
        name = _executable_basename(token)
        options_with_values = WRAPPER_OPTIONS_WITH_VALUES.get(name)
        if options_with_values is None:
            return _UnwrappedCommand(executable=token, next_index=index + 1, prefix=prefix)
        wrapper_depth += 1
        if wrapper_depth > 2:
            return None
        prefix.append(token)
        index += 1
        while index < len(argv):
            wrapper_token = argv[index]
            if name == "env" and _is_static_assignment_token(wrapper_token):
                prefix.append(wrapper_token)
                index += 1
                continue
            option_name = (
                wrapper_token[: wrapper_token.index("=")]
                if "=" in wrapper_token
                else wrapper_token
            )
            if option_name in options_with_values:
                index += 1 if "=" in wrapper_token else 2
                continue
            if option_name in WRAPPER_OPTIONS or wrapper_token.startswith("-"):
                index += 1
                continue
            break
    return None


def _resolve_depth_override(
    executable_name: str,
    args: Sequence[str],
) -> Optional[list[str]]:
    if (
        executable_name in PYTHON_EXECUTABLES
        and (args[0] if len(args) > 0 else None) == "-m"
        and _is_stable_action_token(args[1] if len(args) > 1 else None)
    ):
        return [args[0], args[1]]
    script_actions = SCRIPT_ACTIONS.get(executable_name)
    if (
        script_actions is not None
        and (args[0] if len(args) > 0 else "") in script_actions
        and _is_stable_action_token(args[1] if len(args) > 1 else None)
    ):
        return [args[0], args[1]]
    if executable_name in TARGET_ACTIONS and _is_stable_action_token(
        args[0] if len(args) > 0 else None
    ):
        return [args[0]]
    family_depth = FAMILY_DEPTH_OVERRIDES.get(executable_name)
    depth: Optional[int] = None
    if family_depth is not None:
        key = args[0] if len(args) > 0 else ""
        # TS ``familyDepth?.[args[0] ?? ""] ?? familyDepth?.["*"]``。
        depth = family_depth.get(key)
        if depth is None:
            depth = family_depth.get("*")
    if depth and len(args) >= depth:
        action = list(args[:depth])
        if all(_is_stable_action_token(token) for token in action):
            return action
    return None


def _skip_known_option(
    node: BashCommandRegistryNode,
    args: Sequence[str],
    index: int,
) -> Optional[int]:
    token = args[index]
    if not token.startswith("-") or token == "-":
        return None
    if token == "--":
        return index + 1
    option_name = token[: token.index("=")] if "=" in token else token
    option = _find_registry_option(node[1], option_name)
    if option is None:
        return None
    return index + (2 if option[1] == 1 and "=" not in token else 1)


def _find_registry_option(
    candidates: Sequence[Any],
    option_name: str,
) -> Optional[Any]:
    """``node[1].find(candidate => candidate[0].includes(optionName))``."""
    for candidate in candidates:
        if option_name in candidate[0]:
            return candidate
    return None


def _static_assignment_tokens(invocation: BashCommandInvocation) -> Optional[list[str]]:
    tokens: list[str] = []
    for assignment in invocation.env_assignments:
        if not assignment.name or assignment.value is None:
            return None
        token = f"{assignment.name}={assignment.value}"
        if not _is_static_assignment_token(token):
            return None
        tokens.append(token)
    return tokens


# PORT: JS ``/^[A-Za-z_][A-Za-z0-9_]*=[A-Za-z0-9_./:@,+-]*$/``。
_STATIC_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=[A-Za-z0-9_./:@,+-]*$")


def _is_static_assignment_token(token: str) -> bool:
    return _STATIC_ASSIGNMENT_RE.search(token) is not None


_WHITESPACE_RE = re.compile(r"\s")


def _is_stable_action_token(token: Optional[str]) -> bool:
    return (
        bool(token)
        and not token.startswith("-")
        and not _looks_like_path_or_url(token)
        and _WHITESPACE_RE.search(token) is None
    )


_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _looks_like_path_or_url(token: str) -> bool:
    return (
        "://" in token
        or token.startswith("./")
        or token.startswith("../")
        or token.startswith("/")
        or token.startswith("~")
        or _DRIVE_PATH_RE.search(token) is not None
    )


def _serialize_prefix(tokens: Sequence[str]) -> Optional[str]:
    if len(tokens) < 2 or any(
        len(token) == 0 or _WHITESPACE_RE.search(token) is not None for token in tokens
    ):
        return None
    return " ".join(tokens)


def _executable_basename(token: str) -> str:
    normalized = token.replace("\\", "/")
    return normalized[normalized.rindex("/") + 1 :].lower() if "/" in normalized else normalized.lower()


def _read_command(input: Any) -> Optional[str]:
    if not isinstance(input, dict):
        return None
    command = input.get("command")
    return command if isinstance(command, str) else None
