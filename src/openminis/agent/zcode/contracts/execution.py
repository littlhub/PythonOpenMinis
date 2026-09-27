"""Execution Port — subprocess execution boundary.

Ported from: packages/contracts/src/interfaces/execution.port.ts
Original package: @zcode/contracts (interfaces)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Awaitable, Callable, Optional, Protocol, Union

from .tracing import ExecutionContext, TraceContext

__all__ = [
    "ExecutionCommand",
    "ExecutionShellDialect",
    "ExecutionShellSource",
    "ExecutionShellDisplay",
    "ExecutionShellSelection",
    "ExecutionShellOverride",
    "ExecutionEnvBase",
    "ExecutionEnvOverlay",
    "EmbeddedSearchCommandBackend",
    "EmbeddedSearchInternalCliBackend",
    "EmbeddedSearchArgv0DispatchBackend",
    "EmbeddedSearchNativeBinariesBackend",
    "EmbeddedSearchBackend",
    "ExecutionEmbeddedSearchPrelude",
    "ExecutionSandboxPolicy",
    "ExecutionOutputLimit",
    "ExecutionRequest",
    "ExecutionStatus",
    "ExecutionFailureType",
    "ExecutionFailure",
    "ExecutionStreamResult",
    "ExecutionResult",
    "ExecutionOutputPreview",
    "ExecutionEvent",
    "ExecutionRunOptions",
    "BackgroundExecutionStatus",
    "BackgroundExecutionStartResult",
    "BackgroundExecutionSnapshot",
    "BackgroundBashOutputResult",
    "ExecutionPort",
]


# ---------------------------------------------------------------------------
# Shell selection
# ---------------------------------------------------------------------------

# PORT: TS 是判别联合 ``{mode:"argv"} | {mode:"shell"}``。Python 用一个 dataclass
# 承载两支的字段，``mode`` 作判别键（与 §1「as const 对象 → enum」精神一致，
# 调用点只需读 ``.mode`` 即可，无需 isinstance 分支）。
@dataclass(slots=True)
class ExecutionCommand:
    """Mirrors ``type ExecutionCommand`` (discriminated union)."""

    # "argv" | "shell"
    mode: str
    # mode == "argv"
    file: Optional[str] = None
    args: Optional[list[str]] = None
    # mode == "shell"
    command: Optional[str] = None
    # PORT: TS ``shell?: true | string``
    shell: Union[bool, str, None] = None
    # PORT: 内部 Bash 工具的 shell 选择画像，不得经 hooks / 用户配置 / 通用 shell 执行暴露。
    shell_profile: Optional[str] = None  # "posix-bash"
    # PORT: ZCode runtime 提供的 Bash shell 选择。当前消费方仅在
    # shellProfile == "posix-bash" 时使用；通用 shell 执行必须忽略。
    shell_override: Optional["ExecutionShellSelection"] = None


class ExecutionShellDialect(str, Enum):
    """Mirrors ``type ExecutionShellDialect``."""

    CMD = "cmd"
    POSIX = "posix"
    GIT_BASH = "git-bash"


class ExecutionShellSource(str, Enum):
    """Mirrors ``type ExecutionShellSource``."""

    AUTO_DETECTED = "auto-detected"
    USER_CONFIG = "user-config"
    LEGACY_FALLBACK = "legacy-fallback"


@dataclass(slots=True)
class ExecutionShellDisplay:
    """Mirrors ``interface ExecutionShellDisplay``.

    稳定、provider 可见的 shell 名。**绝不要**在这里放绝对路径。
    例："bash"、"zsh"、"Git Bash"、"CMD"、"system shell"。
    """

    name: str


@dataclass(slots=True)
class ExecutionShellSelection:
    """Mirrors ``interface ExecutionShellSelection``."""

    # Shell 语法与 cwd 捕获包装语义。
    # PORT: TS ``ExecutionShellDialect | "legacy-shell"``
    dialect: Union[ExecutionShellDialect, str]
    # 本次选择为何存在。
    source: ExecutionShellSource
    # provider 可见的稳定名。
    display: ExecutionShellDisplay
    # UI/settings 的稳定 id，或 resolver 生成的 auto id。
    id: Optional[str] = None
    # 人类可读的诊断标签，可比 display.name 更详细。
    label: Optional[str] = None
    # ZCode 解析出具体 shell 时的可执行文件路径；legacy shell 兜底时省略。
    path: Optional[str] = None


# PORT: TS ``export type ExecutionShellOverride = ExecutionShellSelection``
ExecutionShellOverride = ExecutionShellSelection

# PORT: TS ``type ExecutionEnvBase = "inherit" | "empty"``
ExecutionEnvBase = str


@dataclass(slots=True)
class ExecutionEnvOverlay:
    """Mirrors ``interface ExecutionEnvOverlay``."""

    base: Optional[ExecutionEnvBase] = None
    set: Optional[dict[str, str]] = None
    unset: Optional[list[str]] = None


# ---------------------------------------------------------------------------
# Embedded search
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class EmbeddedSearchCommandBackend:
    """Mirrors ``interface EmbeddedSearchCommandBackend``.

    Bash 层 embedded find/grep/rg 的执行策略。刻意放在 provider 可见策略之下，
    使 runtime 之后可以替换实现。
    """

    command: str
    args: Optional[list[str]] = None
    # backend 调用需要的环境变量。桌面端 Electron Helper 执行 zcode.cjs 时
    # 必须带 ELECTRON_RUN_AS_NODE=1，否则会按 Electron 子进程启动。
    env: Optional[dict[str, str]] = None


@dataclass(slots=True)
class EmbeddedSearchInternalCliBackend(EmbeddedSearchCommandBackend):
    """Mirrors ``interface EmbeddedSearchInternalCliBackend``."""

    kind: str = "internal-cli"


@dataclass(slots=True)
class EmbeddedSearchArgv0DispatchBackend(EmbeddedSearchCommandBackend):
    """Mirrors ``interface EmbeddedSearchArgv0DispatchBackend``."""

    kind: str = "argv0-dispatch"


@dataclass(slots=True)
class EmbeddedSearchNativeBinariesBackend:
    """Mirrors ``interface EmbeddedSearchNativeBinariesBackend``."""

    find_command: str
    grep_command: str
    rg_command: str
    kind: str = "native-binaries"


# PORT: TS ``type EmbeddedSearchBackend`` 三支联合；Python 用 ``Union`` 别名。
EmbeddedSearchBackend = Union[
    EmbeddedSearchInternalCliBackend,
    EmbeddedSearchArgv0DispatchBackend,
    EmbeddedSearchNativeBinariesBackend,
]


@dataclass(slots=True)
class ExecutionEmbeddedSearchPrelude:
    """Mirrors ``interface ExecutionEmbeddedSearchPrelude``."""

    backend: EmbeddedSearchBackend
    kind: str = "embedded-search"
    # false 时不定义 find/grep，只保留与原契约一致的 rg fallback。
    find_and_grep_enabled: Optional[bool] = None


# ---------------------------------------------------------------------------
# Sandbox / output limits
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ExecutionSandboxPolicy:
    """Mirrors ``interface ExecutionSandboxPolicy``."""

    enabled: bool = False
    profile: Optional[str] = None
    dangerously_disable_sandbox: Optional[bool] = None
    metadata: Optional[dict[str, Any]] = None


@dataclass(slots=True)
class ExecutionOutputLimit:
    """Mirrors ``interface ExecutionOutputLimit``."""

    max_inline_bytes: Optional[int] = None
    max_buffer_bytes: Optional[int] = None
    # 是否把 stdout/stderr 落盘为 execution output 文件。
    # 通用执行的 on_truncate 在 inline 截断后开始写盘；Bash 从 spawn 起直接写盘，
    # 此选项只控制结算后的文件保留。
    # PORT: TS ``"none" | "on_truncate" | "always"``
    persist_output: Optional[str] = None
    # 通用执行为每路写盘硬上限；Bash 为合并文件的软阈值，每 5 秒检查，严格超出后终止。
    max_persisted_bytes: Optional[int] = None
    # 通用 pipe 执行的 artifact 上限；Bash 直写保留完整文件，不使用此上限。
    max_artifact_bytes: Optional[int] = None
    # 持久化流达到 maxPersistedBytes 时是否停止进程树。
    # 仅控制通用 collector 执行。Bash 的文件软阈值在前后台都生效，不受此开关影响。
    kill_process_on_persisted_limit: Optional[bool] = None


# ---------------------------------------------------------------------------
# Execution request / result
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ExecutionRequest:
    """Mirrors ``interface ExecutionRequest``."""

    command: ExecutionCommand
    # 归一化后的绝对 cwd。Bash 会从 session cwd 解析省略或相对的 cwd。
    cwd: Optional[str] = None
    # 仅 Bash 工具使用的内部 prelude。**绝不可**从用户 hooks 或通用命令执行器接受。
    bash_prelude: Optional[ExecutionEmbeddedSearchPrelude] = None
    # 内部状态捕获。默认 false，且只能由 Bash 工具在前台执行时开启。
    # 不得用于 hooks 或通用 shell 命令。
    capture_cwd_after_success: Optional[bool] = None
    env: Optional[ExecutionEnvOverlay] = None
    # PORT: TS ``string | Uint8Array`` → ``str | bytes``
    stdin: Union[str, bytes, None] = None
    timeout_ms: Optional[int] = None
    output_limit: Optional[ExecutionOutputLimit] = None
    sandbox: Optional[ExecutionSandboxPolicy] = None
    trace: Optional[TraceContext] = None


# PORT: ``type ExecutionStatus = "completed" | "failed" | ...``
ExecutionStatus = str

# PORT: ``type ExecutionFailureType = "spawn_error" | "timeout" | ...``
ExecutionFailureType = str


@dataclass(slots=True)
class ExecutionFailure:
    """Mirrors ``interface ExecutionFailure``."""

    type: ExecutionFailureType
    message: str
    cause: Optional[Any] = None


@dataclass(slots=True)
class ExecutionStreamResult:
    """Mirrors ``interface ExecutionStreamResult``."""

    text: str
    bytes: int
    truncated: bool
    artifact_path: Optional[str] = None
    artifact_bytes: Optional[int] = None
    artifact_truncated: Optional[bool] = None


@dataclass(slots=True)
class ExecutionResult:
    """Mirrors ``interface ExecutionResult``."""

    status: ExecutionStatus
    stdout: ExecutionStreamResult
    stderr: ExecutionStreamResult
    duration_ms: int
    timed_out: bool
    cancelled: bool
    started_at: datetime
    completed_at: datetime
    exit_code: Optional[int] = None
    signal: Optional[str] = None
    pid: Optional[int] = None
    error: Optional[ExecutionFailure] = None
    # 命令成功后捕获的内部 runtime 状态；永不作为 provider 可见输出。
    resolved_cwd: Optional[str] = None


@dataclass(slots=True)
class ExecutionOutputPreview:
    """Mirrors ``interface ExecutionOutputPreview``.

    单次有界尾读计算的 Bash 进度，不持有完整命令输出。
    """

    text: str
    full_text: str
    total_lines: int
    total_bytes: int
    lines_estimated: bool


# PORT: TS 是五支判别联合。Python 用单一 dataclass 承载全部字段，``type`` 作判别键。
@dataclass(slots=True)
class ExecutionEvent:
    """Mirrors ``type ExecutionEvent``.

    Bash 只发送 started/progress/completed/failed；逐 chunk 输出事件仅用于 pipe 执行。
    """

    # "started" | "stdout" | "stderr" | "progress" | "completed" | "failed"
    type: str
    timestamp: datetime
    # "started" | "progress"
    pid: Optional[int] = None
    # "stdout" | "stderr"
    chunk: Optional[bytes] = None
    text: Optional[str] = None
    # "progress"
    elapsed_ms: Optional[int] = None
    stdout_bytes: Optional[int] = None
    stderr_bytes: Optional[int] = None
    output_preview: Optional[ExecutionOutputPreview] = None
    stdout_tail: Optional[str] = None
    stderr_tail: Optional[str] = None
    # "completed"
    result: Optional[ExecutionResult] = None
    # "failed"
    error: Optional[ExecutionFailure] = None


@dataclass(slots=True)
class ExecutionRunOptions:
    """Mirrors ``interface ExecutionRunOptions``."""

    # PORT: TS ``AbortSignal`` → 本包 ``core.abort.AbortSignal``；为避免 contracts → core
    # 的反向依赖，这里用 ``Any`` 承载，调用方传入真实 ``AbortSignal``。
    signal: Optional[Any] = None
    # PORT: TS ``(event) => void | Promise<void>``
    on_event: Optional[Callable[[ExecutionEvent], Union[Awaitable[None], None]]] = None
    context: Optional[ExecutionContext] = None


# PORT: ``type BackgroundExecutionStatus = "running" | ExecutionStatus``
BackgroundExecutionStatus = str


@dataclass(slots=True)
class BackgroundExecutionStartResult:
    """Mirrors ``interface BackgroundExecutionStartResult``."""

    task_id: str
    started_at: datetime
    # PORT: 字面量类型 ``"running"``，保留原值。
    status: str = "running"
    pid: Optional[int] = None
    output_path: Optional[str] = None
    stderr_persisted_output_path: Optional[str] = None
    stdout_persisted_output_path: Optional[str] = None


@dataclass(slots=True)
class BackgroundExecutionSnapshot:
    """Mirrors ``interface BackgroundExecutionSnapshot``."""

    task_id: str
    status: BackgroundExecutionStatus
    started_at: datetime
    completed_at: Optional[datetime] = None
    pid: Optional[int] = None
    stderr_bytes: Optional[int] = None
    stderr_tail: Optional[str] = None
    stdout_bytes: Optional[int] = None
    stdout_tail: Optional[str] = None
    output_path: Optional[str] = None
    stderr_persisted_output_path: Optional[str] = None
    stdout_persisted_output_path: Optional[str] = None
    result: Optional[ExecutionResult] = None
    error: Optional[ExecutionFailure] = None


# PORT-STATUS: partial
# ``BackgroundBashOutputResult`` 来自 ``@zcode/shared``，该 workspace 包不在本仓
# （``packages/shared`` 未随源提供），无可对照的定义。按 ``readBackgroundBashOutput``
# 的调用点语义（"只读已登记的后台 Bash，固定读取文件尾部 8 KiB"）给出最小占位。
# 待 ``@zcode/shared`` 到位后按原定义替换。
@dataclass(slots=True)
class BackgroundBashOutputResult:
    """Mirrors ``BackgroundBashOutputResult`` from ``@zcode/shared`` (placeholder)."""

    task_id: str
    text: str
    truncated: bool = False
    # PORT: 上游固定读取尾部 8 KiB。
    tail_bytes: int = 8192
    bytes: Optional[int] = None


# ---------------------------------------------------------------------------
# Port
# ---------------------------------------------------------------------------

class ExecutionPort(Protocol):
    """Mirrors ``interface ExecutionPort`` — subprocess execution boundary.

    # PORT: 可选方法在 TS 里用 ``method?``。Python 的 ``Protocol`` 无「可选成员」语义，
    # 因此可选方法仍声明为成员，但实现方可缺席：调用点必须先用 ``hasattr`` /
    # ``getattr(x, "m", None)`` 探测（原 TS 用 ``typeof port.m === "function"``），
    # 本包已提供 ``supports_bash_background_lifecycle`` 这类显式探测 helper。
    """

    async def run(
        self,
        request: ExecutionRequest,
        options: Optional[ExecutionRunOptions] = None,
    ) -> ExecutionResult:
        raise NotImplementedError

    async def start(
        self,
        request: ExecutionRequest,
        options: Optional[ExecutionRunOptions] = None,
    ) -> BackgroundExecutionStartResult:
        raise NotImplementedError

    async def get_background_task(
        self, task_id: str
    ) -> Optional[BackgroundExecutionSnapshot]:
        raise NotImplementedError

    async def read_background_bash_output(
        self, task_id: str, session_id: str
    ) -> BackgroundBashOutputResult:
        raise NotImplementedError

    async def cancel_background_task(
        self, task_id: str
    ) -> Optional[BackgroundExecutionSnapshot]:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError
