"""Workflow script path — describe a script path for the model-facing surface.

Ported from: packages/core/src/tool/handlers/workflow-script-path.ts
Original package: @zcode/core (tool/handlers)

# PORT: out-of-scope module (dynamic workflows excluded per ZCORE_PORTING.md §0.0),
# but its ``describeWorkflowScriptPath`` helper is required by
# ``tool/executor/background_tasks.py`` for terminal-run notifications. The
# upstream function is a pure path helper (no workflow engine dependency), so it
# is ported verbatim.
"""

from __future__ import annotations

import os

__all__ = ["describe_workflow_script_path"]


def describe_workflow_script_path(
    absolute_path: str, cwd: str | None
) -> str:
    """把脚本文件的绝对路径写成模型面该看到的样子。

    在会话工作目录之下就给工作区相对路径，否则原样给绝对路径。

    「在工作目录之下」的判据是 ``path.relative`` 的结果既不以 ``..`` 开头、也不是
    绝对路径——这一条同时挡住了两种不能相对化的情形：目录外的兄弟路径
    （``../other/x.dwf.ts``，相对写法读起来像在说工作区里有这么个东西），以及
    Windows 上的跨盘符（``C:`` → ``D:``，``path.relative`` 直接给回绝对路径）。
    空结果（路径恰好等于工作目录）同样退回绝对路径：空串不是一个能
    ``Edit`` 的文件名。

    ``cwd`` 缺席即宿主没有工作目录概念（部分端口 stub / 无会话上下文），此时
    无从相对化。
    """
    if cwd is None or cwd == "":
        return absolute_path
    relative = os.path.relpath(absolute_path, cwd)
    if relative == "" or relative.startswith("..") or os.path.isabs(relative):
        return absolute_path
    return relative
