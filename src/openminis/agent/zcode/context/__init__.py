"""Context Builder exports.

Ported from: packages/core/src/context/index.ts
Original package: @zcode/core (context)
"""

from __future__ import annotations

from .builder import *  # noqa: F401,F403  # export * from "./builder.js"
from .types import *  # noqa: F401,F403  # export * from "./types.js"
from .utils import *  # noqa: F401,F403  # export * from "./utils.js"

# Section builders (for testing)
from .sections.cli_prefix import build_cli_prefix_section  # noqa: F401
from .sections.current_date import build_current_date_section  # noqa: F401
from .sections.desktop import build_desktop_context_section  # noqa: F401
from .sections.env_info import (  # noqa: F401
    build_env_info_section,
    build_git_system_context_section,
)
from .sections.identity import build_identity_section  # noqa: F401
from .sections.memory import build_memory_section  # noqa: F401
from .sections.skills import build_skills_section  # noqa: F401

# PORT: sections/workflow-actor.ts 未移植（scope: non-coding）
# 原 index.ts 在此处 `export { buildWorkflowActorIdentitySection } from "./sections/workflow-actor.js";`
# builder.ts 的 workflowActor 分支以端口占位保留（见 builder.build_workflow_actor_identity_section）。
