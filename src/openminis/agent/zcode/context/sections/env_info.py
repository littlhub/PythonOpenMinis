"""Environment Info Section Builder — OS / shell / cwd / git snapshot.

Ported from: packages/core/src/context/sections/env-info.ts
Original package: @zcode/core (context/sections)

# PORT: 本文件不做任何 IO。TS 侧 ``EnvInfo`` 由 `ContextSourcePort`
# （contracts/interfaces/context-source.port.ts）在宿主侧采集好再注入；内核不直接读
# ``process.cwd()`` / ``process.env`` / 跑 ``git``。
"""

from __future__ import annotations

from typing import Optional

from ..types import ContextSection, EnvInfo, Model
from ..utils import estimate_tokens

ENVIRONMENT_HEADING = "# Environment"
WORKING_DIRECTORY_LABEL = "Primary working directory"
IS_GIT_REPOSITORY_LABEL = "Is a git repository"
PLATFORM_LABEL = "Platform"
SHELL_LABEL = "Shell"
OS_VERSION_LABEL = "OS Version"
# const NODE_VERSION_LABEL = "Node version";
# const OPERATING_SYSTEM_LABEL = "Operating system";
GIT_LABEL = "Git"
NOT_A_GIT_REPOSITORY = "not a git repository"
YES_LABEL = "yes"
NO_LABEL = "no"
GIT_SYSTEM_CONTEXT_PREFIX = (
    "gitStatus: This is the git status at the start of the conversation. Note that this status is a snapshot in time, and will not update during the conversation."
)
CURRENT_BRANCH_LABEL = "Current branch"
MAIN_BRANCH_LABEL = "Main branch (you will usually use this for PRs)"
GIT_USER_LABEL = "Git user"
STATUS_LABEL = "Status"
RECENT_COMMITS_LABEL = "Recent commits"
CLEAN_GIT_STATUS = "(clean)"
DIRTY_GIT_STATUS = "(dirty)"
UNKNOWN_GIT_STATUS = "(unknown)"


def build_env_info_section(env_info: EnvInfo, model: Optional[Model] = None) -> ContextSection:
    content = _build_env_info_content(env_info, model)

    return ContextSection(
        name="Environment Info",
        source="env_info",
        injection_target="system",
        cache_hint="dynamic",
        chars=len(content),
        tokens=estimate_tokens(content),
        content=content,
        preview=content[:100],
    )


def build_git_system_context_section(env_info: EnvInfo) -> Optional[ContextSection]:
    if not is_env_info_git_repository(env_info):
        return None

    content = _build_git_system_context_content(env_info)

    return ContextSection(
        name="System Context",
        source="system_context",
        injection_target="system",
        cache_hint="dynamic",
        chars=len(content),
        tokens=estimate_tokens(content),
        content=content,
        preview=content[:100],
    )


def _build_env_info_content(info: EnvInfo, model: Optional[Model] = None) -> str:
    has_git_repository = is_env_info_git_repository(info)
    lines: list[str] = [
        ENVIRONMENT_HEADING,
        "You have been invoked in the following environment:",
        f"- {WORKING_DIRECTORY_LABEL}: {info.cwd}",
        f"- {IS_GIT_REPOSITORY_LABEL}: {YES_LABEL if has_git_repository else NO_LABEL}",
        f"- {PLATFORM_LABEL}: {info.platform}",
        f"- {SHELL_LABEL}: {info.shell}",
        f"- {OS_VERSION_LABEL}: {info.os_version}",
        # 旧环境快照可能携带历史模型字段；渲染只读取本步骤实际执行的 Model。
        *(
            [f"- You are powered by the model named {model.provider_id}/{model.model_id}."]
            if model
            else []
        ),
    ]

    return "\n".join(lines)


def _build_git_system_context_content(info: EnvInfo) -> str:
    lines: list[str] = [GIT_SYSTEM_CONTEXT_PREFIX]

    if info.git_branch:
        lines.append("")
        lines.append(f"{CURRENT_BRANCH_LABEL}: {info.git_branch}")
    if info.git_main_branch:
        lines.append("")
        lines.append(f"{MAIN_BRANCH_LABEL}: {info.git_main_branch}")
    if info.git_user:
        lines.append("")
        lines.append(f"{GIT_USER_LABEL}: {info.git_user}")

    lines.append("")
    lines.append(f"{STATUS_LABEL}:\n{_format_git_status(info)}")
    lines.append("")
    lines.append(f"{RECENT_COMMITS_LABEL}:\n{_format_recent_commits(info)}")

    return "\n".join(lines)


def is_env_info_git_repository(info: EnvInfo) -> bool:
    return (
        info.is_git_repository
        if info.is_git_repository is not None
        else (
            info.git_status != "not_repo"
            if info.git_status is not None
            else bool(info.git_branch)
        )
    )


def _format_git_status(info: EnvInfo) -> str:
    if info.git_status_lines is not None and len(info.git_status_lines) > 0:
        return "\n".join(info.git_status_lines)
    if info.git_status == "dirty":
        return DIRTY_GIT_STATUS
    if info.git_status == "clean":
        return CLEAN_GIT_STATUS
    return UNKNOWN_GIT_STATUS


def _format_recent_commits(info: EnvInfo) -> str:
    if info.recent_commits is not None and len(info.recent_commits) > 0:
        return "\n".join(info.recent_commits)
    return ""
