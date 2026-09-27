"""Bash background lifecycle — the "explicit" vs "auto_on_timeout" capability probe.

Ported from: packages/core/src/tool/handlers/bash-background-lifecycle.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol

from ...contracts.execution import (
    BackgroundExecutionStartResult,
    ExecutionPort,
    ExecutionRequest,
    ExecutionResult,
    ExecutionRunOptions,
)

__all__ = [
    "BashBackgroundLifecycleMode",
    "BashBackgroundLifecycleResult",
    "BashBackgroundLifecycleExecutionPort",
    "supports_bash_background_lifecycle",
]


# PORT: ``type BashBackgroundLifecycleMode = "explicit" | "auto_on_timeout"``
BashBackgroundLifecycleMode = str


# PORT: TS 是两支判别联合 ``{kind:"foreground"; result} | {kind:"backgrounded"; task}``。
# Python 用单一 dataclass 承载两支字段，``kind`` 作判别键。
@dataclass(slots=True)
class BashBackgroundLifecycleResult:
    """Mirrors ``type BashBackgroundLifecycleResult``."""

    # "foreground" | "backgrounded"
    kind: str
    # kind == "foreground"
    result: Optional[ExecutionResult] = None
    # kind == "backgrounded"
    task: Optional[BackgroundExecutionStartResult] = None


class BashBackgroundLifecycleExecutionPort(Protocol):
    """Mirrors ``interface BashBackgroundLifecycleExecutionPort extends ExecutionPort``.

    # PORT: TS 用 ``extends`` 组合已有端口。Python 的 ``Protocol`` 不做接口继承的结构检查
    # （只要实现方同时具备两侧成员即可满足），因此这里只声明**增量**方法；探测仍由
    # ``supports_bash_background_lifecycle`` 完成，语义与上游 ``typeof === "function"`` 一致。
    """

    async def run_bash_with_background_lifecycle(
        self,
        request: ExecutionRequest,
        lifecycle: Any,
        options: Optional[ExecutionRunOptions] = None,
    ) -> BashBackgroundLifecycleResult:
        raise NotImplementedError


def supports_bash_background_lifecycle(execution_port: ExecutionPort) -> bool:
    """Mirrors ``supportsBashBackgroundLifecycle(executionPort)``.

    # PORT: TS 的类型谓词 ``executionPort is BashBackgroundLifecycleExecutionPort`` 在
    # 运行时就是 ``typeof port.runBashWithBackgroundLifecycle === "function"``；Python 用
    # ``callable(getattr(...))`` 等价，返回 ``bool``（调用点据其决定是否走该分支）。
    """
    return callable(
        getattr(execution_port, "run_bash_with_background_lifecycle", None)
    )
