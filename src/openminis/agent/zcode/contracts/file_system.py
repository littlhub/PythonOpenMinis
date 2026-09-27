"""FileSystem Port — file I/O boundary contracts.

Ported from: packages/contracts/src/interfaces/file-system.port.ts
Original package: @zcode/contracts (interfaces)

# PORT: 搬运规则见 ZCORE_PORTING.md §1——纯数据 ``interface`` → ``@dataclass(slots=True)``；
# 字符串 union → ``Literal``（保留原字符串字面量）；``Promise<T>`` → ``async def -> T``；
# ``Uint8Array`` → ``bytes``；``unknown`` → ``Any``。
#
# ``AbortSignal`` 用 ``Any`` 承载：本包 ``core.abort.AbortSignal`` 与 ``contracts`` 存在反向
# 依赖，契约层只声明形状（与 ``contracts/execution.py`` 的处理一致）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional, Protocol

from .tracing import ExecutionContext, TraceContext

__all__ = [
    "FileSystemErrorCode",
    "FileSystemErrorDetails",
    "FileSystemPortError",
    "create_file_system_error",
    "is_file_system_port_error",
    "FileSystemNodeKind",
    "FileSystemRevision",
    "FileSystemLineEndings",
    "FileSystemTextEncoding",
    "FileSystemStatRequest",
    "FileSystemStatResult",
    "FileSystemCreateDirectoryRequest",
    "FileSystemCreateDirectoryResult",
    "FileSystemReadTextRequest",
    "FileSystemReadTextResult",
    "FileSystemReadBytesRequest",
    "FileSystemReadBytesResult",
    "FileSystemReadTextRangeRequest",
    "FileSystemReadTextRangeResult",
    "FileSystemWriteTextRequest",
    "FileSystemWriteTextResult",
    "FileSystemRemoveFileRequest",
    "FileSystemRemoveFileResult",
    "FileSystemListDirectoryRequest",
    "FileSystemListDirectoryEntry",
    "FileSystemListDirectoryResult",
    "FileSystemSearchFilesRequest",
    "FileSystemSearchFilesResult",
    "FileSystemTextSearchOutputMode",
    "FileSystemSearchTextRequest",
    "FileSystemSearchTextEntry",
    "FileSystemSearchTextResult",
    "FileSystemOperationOptions",
    "FileSystemPort",
]


# ============================================================
# FileSystem Port - file I/O boundary
# ============================================================

FileSystemErrorCode = Literal[
    "not_found",
    "permission_denied",
    "is_directory",
    "not_file",
    "too_large",
    "stale_write",
    "invalid_path",
    "invalid_pattern",
    "unsupported",
    "cancelled",
    "io_error",
]


@dataclass(slots=True)
class FileSystemErrorDetails:
    """Mirrors ``interface FileSystemErrorDetails``."""

    code: FileSystemErrorCode
    message: str
    path: Optional[str] = None
    cause: Any = None


class FileSystemPortError(Exception):
    """Mirrors ``class FileSystemPortError extends Error``."""

    code: FileSystemErrorCode
    path: Optional[str]
    cause: Any

    def __init__(self, details: FileSystemErrorDetails) -> None:
        super().__init__(details.message)
        self.name = "FileSystemPortError"
        self.code = details.code
        self.path = details.path
        self.cause = details.cause


def create_file_system_error(details: FileSystemErrorDetails) -> FileSystemPortError:
    """Mirrors ``createFileSystemError(details)``."""
    return FileSystemPortError(details)


def is_file_system_port_error(error: object) -> bool:
    """Mirrors ``isFileSystemPortError(error): error is FileSystemPortError``."""
    return isinstance(error, FileSystemPortError)


FileSystemNodeKind = Literal["file", "directory", "symlink", "other", "missing"]


@dataclass(slots=True)
class FileSystemRevision:
    """Mirrors ``interface FileSystemRevision``."""

    id: str
    mtime_ms: Optional[float] = None
    size_bytes: Optional[int] = None
    hash: Optional[str] = None


FileSystemLineEndings = Literal["LF", "CRLF"]

# PORT: TS ``BufferEncoding | "gb2312" | "gbk" | "gb18030"``。``BufferEncoding`` 是 node 的
# 大型字符串 union，本包无 node 类型来源，故放宽为 ``str`` 并保留上述额外字面量语义。
FileSystemTextEncoding = str


@dataclass(slots=True)
class FileSystemStatRequest:
    """Mirrors ``interface FileSystemStatRequest``."""

    # 归一化后的绝对路径。相对路径由工具层解析。
    path: str
    trace: Optional[TraceContext] = None


@dataclass(slots=True)
class FileSystemStatResult:
    """Mirrors ``interface FileSystemStatResult``."""

    path: str
    kind: FileSystemNodeKind
    size_bytes: int
    mtime_ms: Optional[float] = None
    revision: Optional[FileSystemRevision] = None


@dataclass(slots=True)
class FileSystemCreateDirectoryRequest:
    """Mirrors ``interface FileSystemCreateDirectoryRequest``."""

    # 归一化后的绝对路径。父目录会被递归创建。
    path: str
    trace: Optional[TraceContext] = None


@dataclass(slots=True)
class FileSystemCreateDirectoryResult:
    """Mirrors ``interface FileSystemCreateDirectoryResult``."""

    path: str


@dataclass(slots=True)
class FileSystemReadTextRequest:
    """Mirrors ``interface FileSystemReadTextRequest``."""

    # 归一化后的绝对路径。相对路径由工具层解析。
    path: str
    encoding: Optional[FileSystemTextEncoding] = None
    max_bytes: Optional[int] = None
    trace: Optional[TraceContext] = None


@dataclass(slots=True)
class FileSystemReadTextResult:
    """Mirrors ``interface FileSystemReadTextResult``."""

    path: str
    # 为 tool/core 消费归一化为 LF 换行的文本内容；原文件主导换行风格经
    # ``line_endings`` 上报。
    content: str
    encoding: FileSystemTextEncoding
    bytes_read: int
    size_bytes: int
    truncated: bool
    # 归一化为 LF 之前观察到的主导换行风格。
    line_endings: Optional[FileSystemLineEndings] = None
    revision: Optional[FileSystemRevision] = None


@dataclass(slots=True)
class FileSystemReadBytesRequest:
    """Mirrors ``interface FileSystemReadBytesRequest``."""

    # 归一化后的绝对路径。相对路径由工具层解析。
    path: str
    # 可选的文件总大小护栏。超过即抛 ``too_large``，不做截断。
    max_bytes: Optional[int] = None
    trace: Optional[TraceContext] = None


@dataclass(slots=True)
class FileSystemReadBytesResult:
    """Mirrors ``interface FileSystemReadBytesResult``."""

    path: str
    # PORT: TS ``Uint8Array`` → ``bytes``。
    content: bytes
    bytes_read: int
    size_bytes: int
    revision: Optional[FileSystemRevision] = None


@dataclass(slots=True)
class FileSystemReadTextRangeRequest:
    """Mirrors ``interface FileSystemReadTextRangeRequest``."""

    # 归一化后的绝对路径。相对路径由工具层解析。
    path: str
    encoding: Optional[FileSystemTextEncoding] = None
    # 零基的逻辑行偏移。面向工具的 1 基偏移在进入本端口之前已完成翻译。
    offset_line: Optional[int] = None
    # 要返回的逻辑行数。省略表示从 ``offset_line`` 读到 EOF。
    limit_lines: Optional[int] = None
    # 可选的文件总大小护栏。超过即抛 ``too_large``，不做截断。
    max_bytes: Optional[int] = None
    trace: Optional[TraceContext] = None


@dataclass(slots=True)
class FileSystemReadTextRangeResult:
    """Mirrors ``interface FileSystemReadTextRangeResult``."""

    path: str
    # 为 tool/core 消费归一化为 LF 换行的文本内容；原文件主导换行风格经
    # ``line_endings`` 上报。
    content: str
    encoding: FileSystemTextEncoding
    # 适配器为产出本区间消耗的字节数。
    bytes_read: int
    # 文件总字节数。
    size_bytes: int
    # 仅对会主动截断 range 读取的适配器为 True；node 适配器的 range 读取按失败处理。
    truncated: bool
    # 首个返回/请求行的 1 基行号。
    start_line: int
    # ``content`` 中返回的行数。
    line_count: int
    # 文件的总逻辑行数。
    total_lines: int
    # 归一化为 LF 之前观察到的主导换行风格。
    line_endings: Optional[FileSystemLineEndings] = None
    revision: Optional[FileSystemRevision] = None


@dataclass(slots=True)
class FileSystemWriteTextRequest:
    """Mirrors ``interface FileSystemWriteTextRequest``."""

    # 归一化后的绝对路径。相对路径由工具层解析。
    path: str
    content: str
    encoding: Optional[FileSystemTextEncoding] = None
    # 写入逻辑 LF 归一化文本时恢复该换行风格。
    line_endings: Optional[FileSystemLineEndings] = None
    create_parents: Optional[bool] = None
    atomic: Optional[bool] = None
    expected_revision: Optional[FileSystemRevision] = None
    trace: Optional[TraceContext] = None


@dataclass(slots=True)
class FileSystemWriteTextResult:
    """Mirrors ``interface FileSystemWriteTextResult``."""

    path: str
    bytes_written: int
    revision: Optional[FileSystemRevision] = None


@dataclass(slots=True)
class FileSystemRemoveFileRequest:
    """Mirrors ``interface FileSystemRemoveFileRequest``."""

    # 归一化后的绝对路径。相对路径由工具层解析。
    path: str
    # 把「文件不存在」当作已完成的空操作。
    missing_ok: Optional[bool] = None
    trace: Optional[TraceContext] = None


@dataclass(slots=True)
class FileSystemRemoveFileResult:
    """Mirrors ``interface FileSystemRemoveFileResult``."""

    path: str
    removed: bool


@dataclass(slots=True)
class FileSystemListDirectoryRequest:
    """Mirrors ``interface FileSystemListDirectoryRequest``."""

    # 归一化后的绝对目录路径。相对路径由调用方解析。
    path: str
    trace: Optional[TraceContext] = None


@dataclass(slots=True)
class FileSystemListDirectoryEntry:
    """Mirrors ``interface FileSystemListDirectoryEntry``."""

    kind: FileSystemNodeKind
    name: str
    path: str


@dataclass(slots=True)
class FileSystemListDirectoryResult:
    """Mirrors ``interface FileSystemListDirectoryResult``."""

    duration_ms: float
    entries: list[FileSystemListDirectoryEntry]
    num_entries: int
    path: str


@dataclass(slots=True)
class FileSystemSearchFilesRequest:
    """Mirrors ``interface FileSystemSearchFilesRequest``."""

    # 归一化后的绝对目录路径。相对路径由工具层解析。
    path: str
    # 相对 ``path`` 的 glob 模式，除非适配器文档声明了额外支持。
    pattern: str
    max_results: Optional[int] = None
    offset: Optional[int] = None
    trace: Optional[TraceContext] = None


@dataclass(slots=True)
class FileSystemSearchFilesResult:
    """Mirrors ``interface FileSystemSearchFilesResult``."""

    path: str
    pattern: str
    duration_ms: float
    files: list[str]
    num_files: int
    truncated: bool


FileSystemTextSearchOutputMode = Literal["content", "files_with_matches", "count"]


@dataclass(slots=True)
class FileSystemSearchTextRequest:
    """Mirrors ``interface FileSystemSearchTextRequest``."""

    # 归一化后的绝对文件或目录路径。相对路径由工具层解析。
    path: str
    pattern: str
    glob: Optional[str] = None
    output_mode: Optional[FileSystemTextSearchOutputMode] = None
    before_context: Optional[int] = None
    after_context: Optional[int] = None
    context: Optional[int] = None
    show_line_numbers: Optional[bool] = None
    only_matching: Optional[bool] = None
    ignore_case: Optional[bool] = None
    type: Optional[str] = None
    head_limit: Optional[int] = None
    offset: Optional[int] = None
    multiline: Optional[bool] = None
    trace: Optional[TraceContext] = None


# PORT: 上游 ``GrepFileEntry`` 就是本类型（grep.ts 直接导入 ``FileSystemSearchTextEntry``）。
# 落成 ``@dataclass``（§1：纯数据 interface → dataclass），使 ``entry.path`` /
# ``entry.line_number`` 这类属性访问成立——原 TS 就是属性访问。
@dataclass(slots=True)
class FileSystemSearchTextEntry:
    """Mirrors ``interface FileSystemSearchTextEntry``."""

    path: str
    line_number: Optional[int] = None
    text: Optional[str] = None
    count: Optional[int] = None
    matched: Optional[bool] = None


@dataclass(slots=True)
class FileSystemSearchTextResult:
    """Mirrors ``interface FileSystemSearchTextResult``."""

    path: str
    pattern: str
    mode: FileSystemTextSearchOutputMode
    duration_ms: float
    files: list[str]
    entries: list[FileSystemSearchTextEntry]
    num_matches: int
    truncated: bool
    applied_limit: Optional[int] = None
    applied_offset: Optional[int] = None


@dataclass(slots=True)
class FileSystemOperationOptions:
    """Mirrors ``interface FileSystemOperationOptions``."""

    # PORT: TS ``AbortSignal`` → 本包 ``core.abort.AbortSignal``；用 ``Any`` 承载，
    # 避免 contracts → core 的反向依赖。
    signal: Any = None
    context: Optional[ExecutionContext] = None


class FileSystemPort(Protocol):
    """Mirrors ``interface FileSystemPort``.

    # PORT: 各方法在原 TS 里接收**对象字面量**请求体；本仓 handler 层按既有先例以
    # ``dict`` 传参（见 ``tool/handlers/edit.py`` / ``glob.py`` 的调用点），故请求位
    # 形参放宽为 ``Any``：契约类型 ``FileSystem*Request`` 描述的是该 dict 的字段形状。
    """

    async def create_directory(
        self,
        request: Any,
        options: Optional[FileSystemOperationOptions] = None,
    ) -> FileSystemCreateDirectoryResult: ...

    async def stat(
        self,
        request: Any,
        options: Optional[FileSystemOperationOptions] = None,
    ) -> FileSystemStatResult: ...

    async def read_text_file(
        self,
        request: Any,
        options: Optional[FileSystemOperationOptions] = None,
    ) -> FileSystemReadTextResult: ...

    async def read_binary_file(
        self,
        request: Any,
        options: Optional[FileSystemOperationOptions] = None,
    ) -> FileSystemReadBytesResult: ...

    async def read_text_file_range(
        self,
        request: Any,
        options: Optional[FileSystemOperationOptions] = None,
    ) -> FileSystemReadTextRangeResult: ...

    async def write_text_file(
        self,
        request: Any,
        options: Optional[FileSystemOperationOptions] = None,
    ) -> FileSystemWriteTextResult: ...

    async def remove_file(
        self,
        request: Any,
        options: Optional[FileSystemOperationOptions] = None,
    ) -> FileSystemRemoveFileResult: ...

    async def list_directory(
        self,
        request: Any,
        options: Optional[FileSystemOperationOptions] = None,
    ) -> FileSystemListDirectoryResult: ...

    async def search_files(
        self,
        request: Any,
        options: Optional[FileSystemOperationOptions] = None,
    ) -> FileSystemSearchFilesResult: ...

    async def search_text(
        self,
        request: Any,
        options: Optional[FileSystemOperationOptions] = None,
    ) -> FileSystemSearchTextResult: ...
