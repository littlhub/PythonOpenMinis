"""Memory Extraction — prompt building and the extraction scheduler.

Ported from: packages/core/src/memory/extraction.ts
Original package: @zcode/core (memory)
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    Generic,
    Optional,
    Protocol,
    Sequence,
    TypeVar,
    Union,
)

from ..contracts.ids import MessageId
from ..core.abort import AbortController, AbortSignal
from .memory_file_path import resolve_contained_memory_file_path
from .recall.manifest import format_memory_manifest
from .recall.types import MemoryManifestEntry

MINIMUM_USER_WORDS = 3

# PORT: TS ``type MemoryExtractionExecutionStatus = "success" | "no-op" | "error" | "aborted"``。
MemoryExtractionExecutionStatus = str


# ---------------------------------------------------------------------------
# PORT: @zcode/contracts 本地数据占位（MessageWithParts / ToolPart 未移植）
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class MessagePartState:
    """PORT: ``part.state`` 的本地等价形状；``input`` 为工具入参字典。"""

    input: dict[str, Any] = dict


@dataclass(slots=True)
class MessagePart:
    """PORT: ``MessageWithParts["parts"][number]`` 的本地等价 dataclass。"""

    type: str
    text: str = ""
    ignored: bool = False
    synthetic: bool = False
    tool: Optional[str] = None
    state: MessagePartState = field(default_factory=MessagePartState)


@dataclass(slots=True)
class MessageInfo:
    """PORT: ``message.info`` 的本地等价形状。"""

    id: MessageId
    role: str
    synthetic: bool = False
    visibility: Optional[str] = None


@dataclass(slots=True)
class MessageWithParts:
    """PORT: @zcode/contracts ``MessageWithParts`` 未移植，保留本地等价 dataclass。"""

    info: MessageInfo
    parts: list[MessagePart]


@dataclass(slots=True)
class MemoryExtractionSnapshot:
    """Mirrors ``interface MemoryExtractionSnapshot``."""

    boundary_message_id: MessageId
    durable_messages: Sequence[MessageWithParts]
    memory_root: str
    working_directory: str
    workspace_root: str


@dataclass(slots=True)
class MemoryExtractionExecutionInput:
    """Mirrors the inline argument object of the ``execute`` callback."""

    abort_signal: AbortSignal
    message_count: int
    snapshot: Any  # TSnapshot at call site


@dataclass(slots=True)
class MemoryExtractionDecision:
    """Mirrors the discriminated ``MemoryExtractionDecision`` union.

    # PORT: TS 联辨别为 ``{ decision: "run"; messageCount }`` 或
    # ``{ decision: "skip"; messageCount; reason }``；Python 合并为单一 dataclass，
    # ``reason`` 仅 skip 时非空（语义等价）。
    """

    decision: str  # "run" | "skip"
    message_count: int
    reason: Optional[str] = None


T = TypeVar("T")

TSnapshot = TypeVar("TSnapshot", bound=MemoryExtractionSnapshot)


class MemoryExtractionScheduler(Protocol, Generic[TSnapshot]):
    """Mirrors ``interface MemoryExtractionScheduler<TSnapshot>``."""

    def drain(self) -> Awaitable[None]: ...
    def get_cursor(self) -> Optional[MessageId]: ...
    def has_pending_work(self) -> bool: ...
    def schedule(self, snapshot: Union[TSnapshot, Awaitable[TSnapshot]]) -> None: ...
    def shutdown(self) -> None: ...


def build_memory_extraction_prompt(input: dict[str, Any]) -> str:
    """Mirrors ``buildMemoryExtractionPrompt(input)``."""
    manifest: Sequence[MemoryManifestEntry] = input["manifest"]
    existing_memories = (
        f"\n\n## Existing memory files\n\n{format_memory_manifest(manifest)}\n\n"
        "Check this list before writing — update an existing file rather than creating a duplicate."
        if len(manifest) > 0
        else ""
    )

    return "\n".join(
        [
            f"You are now acting as the memory extraction subagent. Analyze the most recent ~{input['messageCount']} messages above and use them to update your persistent memory systems.",
            "",
            "Available tools: Read, Grep, Glob, read-only Bash (ls/find/cat/stat/wc/head/tail and similar), and Edit/Write for paths inside the memory directory only, and Bash rm with paths inside the memory directory only. All other tools — MCP, Agent, write-capable Bash, etc — will be denied.",
            "",
            "You have a limited turn budget. Edit requires a prior Read of the same file, so the efficient strategy is: turn 1 — issue all Read calls in parallel for every file you might update; turn 2 — issue all Write/Edit calls in parallel. Do not interleave reads and writes across multiple turns.",
            "",
            f"You MUST only use content from the last ~{input['messageCount']} messages to update your persistent memories. Do not waste any turns attempting to investigate or verify that content further — no grepping source files, no reading code to confirm a pattern exists, no git commands.{existing_memories}",
            "",
            "If nothing is worth saving, output only 'Nothing to save.' Do not explain why.",
            "",
            "If the user explicitly asks you to remember something, save it immediately as whichever type fits best. If they ask you to forget something, find and remove the relevant entry.",
            "",
            "Apply the memory types, what-not-to-save criteria, and frontmatter format from the Memory section of your system prompt — it is already in your context above.",
        ]
    )


def evaluate_memory_extraction(
    snapshot: MemoryExtractionSnapshot, cursor: Optional[MessageId]
) -> MemoryExtractionDecision:
    """Mirrors ``evaluateMemoryExtraction(snapshot, cursor)``."""
    message_count = count_messages_after_cursor(snapshot.durable_messages, cursor)

    if contains_direct_memory_write(snapshot, cursor):
        return MemoryExtractionDecision(
            decision="skip", message_count=message_count, reason="direct-memory-write"
        )

    if not contains_eligible_user_prose(snapshot.durable_messages, cursor):
        return MemoryExtractionDecision(
            decision="skip", message_count=message_count, reason="no-user-prose"
        )

    return MemoryExtractionDecision(decision="run", message_count=message_count)


def create_memory_extraction_scheduler(
    execute: Callable[
        [MemoryExtractionExecutionInput], Awaitable[MemoryExtractionExecutionStatus]
    ]
) -> "MemoryExtractionScheduler[MemoryExtractionSnapshot]":
    """Mirrors ``createMemoryExtractionScheduler(execute)``."""
    return _MemoryExtractionScheduler(execute)


class _MemoryExtractionScheduler(Generic[TSnapshot]):
    """Mirrors the closure returned by ``createMemoryExtractionScheduler``."""

    def __init__(self, execute):
        self._execute = execute
        self._cursor: Optional[MessageId] = None
        self._latest_pending: Optional[Awaitable[Any]] = None
        self._running: Optional[asyncio.Task] = None
        self._shutting_down = False
        self._shutdown_controller = AbortController()

    async def _process_snapshot(self, snapshot: TSnapshot) -> None:
        decision = evaluate_memory_extraction(snapshot, self._cursor)
        snapshot_end = snapshot.boundary_message_id

        if decision.decision == "skip":
            if snapshot_end is not None:
                self._cursor = snapshot_end
            return

        status: Optional[MemoryExtractionExecutionStatus]
        try:
            status = await self._execute(
                MemoryExtractionExecutionInput(
                    abort_signal=self._shutdown_controller.signal,
                    message_count=decision.message_count,
                    snapshot=snapshot,
                )
            )
        except Exception:  # noqa: BLE001
            return

        if (
            not self._shutting_down
            and status in ("success", "no-op")
            and snapshot_end is not None
        ):
            self._cursor = snapshot_end

    async def _run(self, first: Awaitable[Any]) -> None:
        try:
            current: Optional[Awaitable[Any]] = first
            while current is not None and not self._shutting_down:
                acquisition = await _wait_for_snapshot_acquisition_or_shutdown(
                    current, self._shutdown_controller.signal
                )
                if acquisition.get("status") == "shutdown" or self._shutting_down:
                    break
                if acquisition.get("status") == "acquired":
                    try:
                        await self._process_snapshot(acquisition["snapshot"])
                    except Exception:  # noqa: BLE001
                        # 本次 error 不推进 cursor；latest pending 仍按既有 coalescing 语义继续。
                        pass
                current = None if self._shutting_down else self._latest_pending
                self._latest_pending = None
        finally:
            if self._shutting_down:
                self._latest_pending = None
            self._running = None

    async def drain(self) -> None:
        while self._running is not None:
            await self._running

    def get_cursor(self) -> Optional[MessageId]:
        return self._cursor

    def has_pending_work(self) -> bool:
        return self._running is not None or self._latest_pending is not None

    def schedule(self, snapshot: Union[TSnapshot, Awaitable[TSnapshot]]) -> None:
        if self._shutting_down:
            return
        acquisition = _acquire_snapshot(snapshot)
        if self._running is not None:
            self._latest_pending = acquisition
            return
        # PORT: 调度循环需在事件循环中创建；与 TS ``run(acquisition)`` 的 Promise 语义一致。
        self._running = asyncio.ensure_future(self._run(acquisition))

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        # ZCode 关闭单个 session 后进程仍继续运行；旧 scheduler 只让调用方放弃等待，
        # running/pending Extraction 仍可能继续请求模型和写 Memory。
        self._shutting_down = True
        self._latest_pending = None
        self._shutdown_controller.abort()


async def _acquire_snapshot(snapshot: Union[T, Awaitable[T]]) -> dict[str, Any]:
    """Mirrors ``acquireSnapshot(snapshot)``。

    # PORT: 原 ``Promise.resolve(snapshot).then(...)``；Python 侧把 awaitable 或值都归一为
    # ``{ status: "acquired", snapshot }``，异常归为 ``{ status: "error" }``。
    """
    try:
        value = await snapshot if isinstance(snapshot, Awaitable) else snapshot
        return {"status": "acquired", "snapshot": value}
    except Exception:  # noqa: BLE001
        return {"status": "error"}


async def _wait_for_snapshot_acquisition_or_shutdown(
    acquisition: Awaitable[Any], signal: AbortSignal
) -> dict[str, Any]:
    """Mirrors ``waitForSnapshotAcquisitionOrShutdown(acquisition, signal)``。

    # PORT: 原 Node 用 ``signal.addEventListener("abort", onAbort, { once: true })`` 与
    # ``acquisition.then`` 竞速；Python 用 ``asyncio.wait`` 在 acquisition 与 ``signal.wait()``
    # 之间取先完成者；使用本包 ``AbortSignal.wait()``（见 core/abort.py）。
    """
    if signal.aborted:
        return {"status": "shutdown"}

    acquisition_task = asyncio.ensure_future(acquisition)
    abort_task = asyncio.ensure_future(signal.wait())
    done, _ = await asyncio.wait(
        {acquisition_task, abort_task}, return_when=asyncio.FIRST_COMPLETED
    )
    if acquisition_task in done:
        abort_task.cancel()
        try:
            await abort_task
        except asyncio.CancelledError:
            pass
        return await acquisition_task
    # shutdown 优先。
    acquisition_task.cancel()
    try:
        await acquisition_task
    except asyncio.CancelledError:
        pass
    return {"status": "shutdown"}


def count_messages_after_cursor(
    messages: Sequence[MessageWithParts], cursor: Optional[MessageId]
) -> int:
    """Mirrors ``countMessagesAfterCursor(messages, cursor)``."""
    if cursor is None:
        return len(messages)
    cursor_index = next(
        (i for i, message in enumerate(messages) if message.info.id == cursor), -1
    )
    return len(messages) if cursor_index < 0 else len(messages) - cursor_index - 1


def contains_direct_memory_write(
    snapshot: MemoryExtractionSnapshot, cursor: Optional[MessageId]
) -> bool:
    """Mirrors ``containsDirectMemoryWrite(snapshot, cursor)``."""
    messages = messages_after_found_cursor(snapshot.durable_messages, cursor)
    if messages is None:
        return False

    for message in messages:
        if message.info.role != "assistant":
            continue
        for part in message.parts:
            if not is_memory_mutation_tool_part(part):
                continue
            # PORT: 原 ``part.state.input.file_path``（字典入参）。
            file_path = part.state.input.get("file_path") if isinstance(part.state.input, dict) else None
            if not isinstance(file_path, str) or len(file_path) == 0:
                continue
            if (
                resolve_contained_memory_file_path(
                    {
                        "filePath": file_path,
                        "rootDir": snapshot.memory_root,
                        "workingDirectory": snapshot.working_directory,
                        "workspaceRoot": snapshot.workspace_root,
                    }
                )
                is not None
            ):
                return True

    return False


def contains_eligible_user_prose(
    messages: Sequence[MessageWithParts], cursor: Optional[MessageId]
) -> bool:
    """Mirrors ``containsEligibleUserProse(messages, cursor)``."""
    messages_after_cursor = messages_after_found_cursor(messages, cursor) or messages
    for message in messages_after_cursor:
        if not is_non_meta_user_message(message):
            continue
        for part in message.parts:
            if (
                part.type == "text"
                and not part.ignored
                and not part.synthetic
                and count_words(part.text) >= MINIMUM_USER_WORDS
            ):
                return True
    return False


def messages_after_found_cursor(
    messages: Sequence[MessageWithParts], cursor: Optional[MessageId]
) -> Optional[Sequence[MessageWithParts]]:
    """Mirrors ``messagesAfterFoundCursor(messages, cursor)``."""
    if cursor is None:
        return messages
    cursor_index = next(
        (i for i, message in enumerate(messages) if message.info.id == cursor), -1
    )
    return None if cursor_index < 0 else messages[cursor_index + 1 :]


def is_non_meta_user_message(message: MessageWithParts) -> bool:
    """Mirrors ``isNonMetaUserMessage(message)``."""
    return (
        message.info.role == "user"
        and not message.info.synthetic
        and message.info.visibility != "model-only"
    )


def is_memory_mutation_tool_part(part: MessagePart) -> bool:
    """Mirrors ``isMemoryMutationToolPart(part)`` (TS 类型守卫)。"""
    return part.type == "tool" and part.tool in ("Write", "Edit")


def count_words(text: str) -> int:
    """Mirrors ``countWords(text)``。"""
    return len([word for word in re.split(r"\s+", text) if word])
