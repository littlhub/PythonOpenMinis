"""ZCode 内核 ↔ OpenMinis 宿主：端口适配器。

内核（``agent/zcode/**``）所有 IO 都走端口注入（``FileSystemPort`` /
``ExecutionPort`` / ``SkillPort`` / ``SessionStorePort`` …），本模块提供**本机宿主**
的具体实现，让移植进来的 ``Read`` / ``Write`` / ``Edit`` / ``Bash`` / ``Glob`` /
``Grep`` / ``Todo*`` / ``Skill`` / ``WebFetch`` 能真正跑起来。

设计约定
--------
- **请求形态双读**：内核调用点有的传 camelCase ``dict``（如 ``edit.py``），有的传
  ``FileSystem*Request`` dataclass（如 ``glob.py``）。所有适配方法统一走 ``_req()``
  双读，两种形态都接受 —— 不要假设某一种。
- **错误码对齐**：内核按 ``FileSystemPortError.code`` 分支（``"not_found"`` /
  ``"too_large"`` …），所以本机 ``OSError`` 必须翻译成契约错误，不能直接冒泡。
- **换行归一**：契约要求 ``read_text_file`` 返回 LF 归一化文本并用 ``line_endings``
  回报原风格；``write_text_file`` 按 ``line_endings`` 还原。这条不对称是**有意的**
  （内核的 Edit 靠它做等值替换），照做。
- **执行走应用的持久 shell**：``AppExecutionPort`` 复用
  ``openminis.sandbox.ExecutionCoordinator``，与 ``shell_execute`` 同一条链路 ——
  同一会话里 ZCode 的 ``Bash`` 和应用自己的 ``shell_execute`` 共享 shell 状态与
  per-session cwd，不会出现两个工作目录。
"""

from __future__ import annotations

import asyncio
import glob as _glob
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ....core.logging import get_logger
from ..contracts.execution import (
    BackgroundExecutionStartResult,
    BackgroundExecutionSnapshot,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStreamResult,
)
from ..contracts.file_system import (
    FileSystemCreateDirectoryResult,
    FileSystemErrorDetails,
    FileSystemListDirectoryEntry,
    FileSystemListDirectoryResult,
    FileSystemPortError,
    FileSystemReadBytesResult,
    FileSystemReadTextRangeResult,
    FileSystemReadTextResult,
    FileSystemRemoveFileResult,
    FileSystemRevision,
    FileSystemSearchFilesResult,
    FileSystemSearchTextEntry,
    FileSystemSearchTextResult,
    FileSystemStatResult,
    FileSystemWriteTextResult,
)
from ..core.abort import AbortController

__all__ = [
    "AbortFlag",
    "LocalFileSystemPort",
    "AppExecutionPort",
    "InMemorySessionStorePort",
    "SimpleHttpClientPort",
    "HttpClientPortError",
    "read_field",
]

#: 一个文件最多读进内存做文本搜索/读取的字节数（超出直接给 ``too_large``）。
DEFAULT_MAX_READ_BYTES = 32 * 1024 * 1024

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 请求双读
# ---------------------------------------------------------------------------
def read_field(source: Any, snake_name: str, camel_name: Optional[str] = None) -> Any:
    """从 dict（camelCase/snake_case）或 dataclass 里读一个字段。

    内核调用点两种形态混用（见模块 docstring），所以适配层不能只认一种。
    """
    if source is None:
        return None
    if isinstance(source, dict):
        if snake_name in source:
            return source[snake_name]
        if camel_name and camel_name in source:
            return source[camel_name]
        return None
    if hasattr(source, snake_name):
        return getattr(source, snake_name)
    if camel_name and hasattr(source, camel_name):
        return getattr(source, camel_name)
    return None


class AbortFlag:
    """最小 ``AbortSignal`` 替身：只要 ``aborted`` 一个性质。

    内核只读 ``signal.aborted``；web 会话取消时宿主把 ``aborted`` 置真即可，
    不必把整个 abort 控制器搬过来。
    """

    __slots__ = ("_aborted",)

    def __init__(self) -> None:
        self._aborted = False

    @property
    def aborted(self) -> bool:
        return self._aborted

    def abort(self) -> None:
        self._aborted = True

    def throw_if_aborted(self) -> None:
        if self._aborted:
            raise asyncio.CancelledError("aborted")


def _new_abort_signal():
    """优先用内核自己的 ``AbortSignal``（它有 ``wait()`` 等完整语义）。"""
    try:
        return AbortController().signal
    except Exception:  # pragma: no cover - 内核实现变动时的兜底
        return AbortFlag()


# ---------------------------------------------------------------------------
# FileSystemPort —— 本机文件系统
# ---------------------------------------------------------------------------
def _now_ms() -> float:
    return time.time() * 1000.0


def _revision_of(path: str, stat_result: os.stat_result) -> FileSystemRevision:
    return FileSystemRevision(
        id=f"{int(stat_result.st_mtime_ns)}:{stat_result.st_size}",
        mtime_ms=stat_result.st_mtime * 1000.0,
        size_bytes=stat_result.st_size,
    )


def _kind_of(path: str, st: os.stat_result) -> str:
    if os.path.islink(path):
        return "symlink"
    if os.path.isdir(path):
        return "directory"
    if os.path.isfile(path):
        return "file"
    return "other"


#: 内核 ``path_policy`` 在调用宿主端口**之前**已经 ``normpath`` 过一遍。Windows 上
#: 这会把模型给的 ``/var/minis/workspace/x`` 变成 ``\var\minis\workspace\x``，而且
#: **常带盘符**（``C:\var\minis\x`` / ``G:\var\minis\x`` —— 取的是进程当前盘）。
#: 只按正斜杠去找 ``/var/minis`` 会漏掉这种形态，于是前缀换不回去，文件落到盘根，
#: ``os.makedirs`` 在盘根建目录被 Windows 拒绝 → ``permission_denied``。
#:
#: 匹配「行首（可选的 ``C:`` 盘符）后面紧跟 ``/var/minis``」，后面的段照原样交回
#: ``path_utils``。刻意要求 ``/var/minis`` 出现在**开头**：正文里偶然提到
#: ``.../var/minis/...`` 的普通路径不该被改写。
_SANDBOX_ENTRY = re.compile(r"^(?:[A-Za-z]:)?(?=/+var/minis(?:/|$))", re.IGNORECASE)

#: 「容器家目录」形态的幻觉根：``/home/<user>`` / ``/root``（Docker 镜像的标配 HOME）。
#:
#: 系统提示原先没告诉模型工作目录是什么（section 静默降级成空串，见
#: ``integration/agent.py`` 的 ``_SECTION_MODULES``），模型就按训练数据里最常见的
#: Linux 容器惯例自己编了一个 —— ``{"filePath": "/home/user/calculator.html"}``。
#: 内核 ``os.path.isabs()`` 认它是绝对路径，``normpath`` 之后到达端口的就是
#: ``\\home\\user\\calculator.html``（可能还带盘符），于是 ``os.makedirs`` 在**盘根**
#: 建目录：``C:\\home\\user\\`` 被拒（无权限），``G:\\home\\user\\`` 则建出来但用户
#: 永远找不到。
#:
#: 这两个前缀在 Windows 上几乎不可能是真实路径（不像 ``C:\\Users``、``C:\\Program
#: Files`` 那样有系统惯例），所以见到就整段丢掉、剩下的当**工作区根相对**，与裸
#: ``/var/minis`` 同待遇。
#:
#: ``/app`` 也是常见的容器工作目录，但 ``C:\\app`` 在 Windows 上**可能真的存在**
#: （用户自己建的目录），故只在**无盘符**形态下才认（``_CONTAINER_APP_ENTRY``），
#: 带盘符时宁可不动。
#:
#: 这里全部**消费**掉幻觉根（不像 ``_SANDBOX_ENTRY`` 那样用零宽断言），因为后面要
#: 从 ``match.end()`` 之后接管剩余段。
_CONTAINER_HOME_ENTRY = re.compile(
    r"^(?:[A-Za-z]:)?(?:/+(?:home/[^/\\]+|root))(?=/|$)", re.IGNORECASE
)
_CONTAINER_APP_ENTRY = re.compile(r"^/+(?:app)(?=/|$)", re.IGNORECASE)

#: 幻觉根锚到哪儿 —— 工作区根（与 ``path_utils`` 里裸 ``/var/minis`` 的规则一致）。
_SANDBOX_WORKSPACE_ROOT = "/var/minis/workspace"


def _host_path(raw: Any) -> str:
    """把内核给的路径落到宿主真实目录上（入站还原）。

    内核的 ``path_policy`` 是 **Android 语义** —— ``/var/minis/workspace/...`` 就是
    一个真实的 Linux 绝对路径，所以适配层原本只做绝对化、不碰内容。桌面端却把
    工作区挂在 ``~/openminis/workspace`` 下面，两者的世界观对不上：

    - 模型从主智能体那边带过来的沙箱路径（工具结果、系统提示都是这种写法），
      原样丢给 ``os.makedirs`` 会落到当前盘根的 ``\\var\\minis\\...``（往往还没
      权限），于是「写文件」这一步直接失败；
    - 在 Git Bash 里更绕，``/var/minis`` 会挂到 bash 安装目录下，拼出
      ``.../PortableGit/versions/*/var/minis/...`` → ``No such file``。

    这里跟 :func:`openminis.tools.path_utils.rewrite_sandbox_paths` 走同一套
    规则（也正是 ``shell_execute`` 自己每次都在做的还原），保证 ZCode 的
    ``Read``/``Write``/``Edit``/``Bash`` 与主智能体的工具落在同一个目录。

    两种形态都要认：模型给的**原始写法**（正斜杠 ``/var/minis/...``），以及内核
    ``normpath`` 之后的 **Windows 写法**（反斜杠、可能带盘符 ``C:\\var\\minis\\...``）。
    后者才是实际到达端口的形态 —— 只认前者等于没修。

    第三种是**容器幻觉根**（``/home/user`` / ``/root`` / ``/app``）：模型没被告知
    工作目录时自己编出来的，见 ``_CONTAINER_HOME_ENTRY`` 的注释。
    """
    text = str(raw or "")
    if not text:
        return text
    probe = text.replace("\\", "/")
    match = _SANDBOX_ENTRY.match(probe)
    if match is not None:
        # 只把沙箱那一段交回 path_utils；前面残留的盘符/根（``C:``、``G:``）必须丢掉，
        # 否则拼出来的仍是盘根路径。``_SANDBOX_ENTRY`` 只吃盘符，所以 ``match.end()``
        # 正好停在 ``/var/minis`` 前面。
        tail = "/" + probe[match.end() :].lstrip("/")
    else:
        container = _CONTAINER_HOME_ENTRY.match(probe) or _CONTAINER_APP_ENTRY.match(probe)
        if container is None:
            return text
        # 幻觉根整段丢掉，剩下的接在工作区根后面，交回同一套规则还原。
        tail = _SANDBOX_WORKSPACE_ROOT + "/" + probe[container.end() :].lstrip("/")
    try:
        from ....tools.path_utils import rewrite_sandbox_paths

        rewritten = rewrite_sandbox_paths(tail)
    except Exception:  # pragma: no cover - 路径工具缺席时保持原样，不做二次伤害
        return text
    return rewritten if rewritten != tail else text


def _rewrite_sandbox_in_text(text: str) -> str:
    """**自由文本**（shell 命令行）里的沙箱路径还原 —— 任意位置、所有出现。

    与 :func:`_host_path` 的分工：那个针对「单个路径参数」，只认开头的沙箱前缀；
    命令行是另一回事 —— ``cat /var/minis/workspace/a > /var/minis/data/b`` 里
    路径在**中段**，且一条命令里可能有好几处，全都要替。
    """
    if not text or "/var/minis" not in text:
        return text
    try:
        from ....tools.path_utils import rewrite_sandbox_paths

        return rewrite_sandbox_paths(text)
    except Exception:  # pragma: no cover - 路径工具缺席时保持原样，不做二次伤害
        return text


def _fs_error(code: str, message: str, path: Optional[str] = None, cause: Any = None):
    return FileSystemPortError(
        FileSystemErrorDetails(code=code, message=message, path=path, cause=cause)
    )


def _translate_oserror(error: OSError, path: str):
    import errno

    if isinstance(error, FileNotFoundError) or error.errno == errno.ENOENT:
        return _fs_error("not_found", f"Path not found: {path}", path, error)
    if isinstance(error, IsADirectoryError) or error.errno == errno.EISDIR:
        return _fs_error("is_directory", f"Path is a directory: {path}", path, error)
    if isinstance(error, NotADirectoryError) or error.errno == errno.ENOTDIR:
        return _fs_error("not_file", f"Path is not a file: {path}", path, error)
    if isinstance(error, PermissionError) or error.errno in (errno.EACCES, errno.EPERM):
        return _fs_error("permission_denied", f"Permission denied: {path}", path, error)
    return _fs_error("io_error", f"{type(error).__name__}: {error}", path, error)


# ---------------------------------------------------------------------------
# 落盘护栏 —— ZCode 的一切读写都限制在工作区内
# ---------------------------------------------------------------------------
#
# 内核的 ``path_policy`` 刻意**不做** containment（原版注释：*"Current release
# intentionally does not hard-block paths outside workspaceRoot"*），上游靠**权限
# 状态机**管（``agent/turn_machine.py`` 的 ``request_permission`` /
# ``resolve_permission``，``write.py`` 的 ToolEntry 里声明 ``permission="edit"``）。
# 而本适配层的 ``_run_turns`` 是自实现的循环、**直接调 handler** —— 那道门从来没接。
#
# 结果是落盘完全没有约束（实测）：``zcode_write`` 能写到 ``C:\Users\loo\…`` 主目录，
# 也能用 ``../../`` 跳出工作区，三条全成功。主智能体那边不是这样 —— 文件工具走
# :func:`openminis.tools.path_utils.resolve_workspace_path`（越界返回 ``None``）、
# shell 走 :func:`openminis.sandbox.guard.check_shell_command`（越界/删除/敏感信息
# 三道规则）。同一台机器上「换个循环就能绕开沙盒」不能接受，所以这层必须补。


def _app_workspace_root() -> Optional[Path]:
    """主智能体的工作区根（``external_files_dir``）；上下文缺席时返回 ``None``。"""
    try:
        from ....core.context import app_context

        return Path(app_context().external_files_dir)
    except Exception:  # pragma: no cover - 由调用方按 fail-closed 处置
        return None


def _guard_roots(workspace_root: str, *, writable: bool) -> list[Path]:
    """落盘护栏允许的根。

    * **写**：ZCode 自己的工作区（端口构造时注入的 ``workspace_root``）+ 主智能体的
      工作区根。后者是必需的 —— 模型按主智能体的世界观写
      ``/var/minis/workspace/x`` 时，:func:`_host_path` 把它还原到**工作区根**
      （不是会话目录），只认前者会把正常写入也拒掉；
    * **读**：额外放行 ``path_utils`` 的只读根（技能库），与
      ``resolve_workspace_path`` 同语义 —— 少一处例外就少一次「模型只能瞎猜路径」。
    """
    candidates: list[Path] = []
    if workspace_root:
        candidates.append(Path(workspace_root))
    app_root = _app_workspace_root()
    if app_root is not None:
        candidates.append(app_root)
    if not writable:
        try:
            from ....tools.path_utils import readonly_roots

            candidates.extend(readonly_roots())
        except Exception:  # pragma: no cover - 技能模块损坏不该放开护栏
            pass

    roots: list[Path] = []
    for candidate in candidates:
        try:
            resolved = Path(candidate).resolve()
        except Exception:  # pragma: no cover
            continue
        if resolved not in roots:
            roots.append(resolved)
    return roots


def _outside_workspace(path: str, workspace_root: str, *, writable: bool) -> bool:
    """``path`` 是否落在允许的根**之外**。

    ``resolve()`` 这一步不能省：它规范化 ``..``、展开软链接 —— 少了它，
    ``../../OUTSIDE/x`` 和「工作区里的软链指向外面」都能绕过去。
    """
    roots = _guard_roots(workspace_root, writable=writable)
    if not roots:
        # 拿不到任何根 = 无法判定。**fail-closed**：宁可拒绝（用户看得见、模型能
        # 自己纠正），也不要静默放开整个文件系统 —— 后者正是这次要修的问题。
        return True
    try:
        target = Path(path).resolve()
    except Exception:  # pragma: no cover
        return True
    return not any(target == root or root in target.parents for root in roots)


def _deny_outside(path: str, *, writable: bool) -> Any:
    action = "write to" if writable else "access"
    return _fs_error(
        "permission_denied",
        f"Refusing to {action} {path}: it is outside the workspace. "
        "Use a relative path, or the /var/minis/workspace/... notation.",
        path,
    )


class LocalFileSystemPort:
    """``FileSystemPort`` 的本机实现。

    # PORT-STATUS: partial —— 上游 node 适配器支持 ``gb2312``/``gbk`` 等编码与
    # 符号链接策略；本实现按 UTF-8 读写（失败回退 latin-1）并拒绝跟随链接写目标
    # 之外的路径。编码面够用即可，不追求 node 全量编码表。
    """

    def __init__(self, *, workspace_root: str = "", follow_symlinks: bool = True) -> None:
        # 内核的 ``path_policy`` 只做绝对化/归一化、**不做** containment（见上面
        # 「落盘护栏」一节）；越界拦截由本层负责 —— 与主智能体同一语义，不另立规则。
        self._workspace_root = str(workspace_root or "")
        self._follow_symlinks = follow_symlinks

    def _guard(self, path: str, *, writable: bool) -> str:
        """跑落盘护栏；越界即抛 ``permission_denied``（模型看得见、能自己纠正）。"""
        if _outside_workspace(path, self._workspace_root, writable=writable):
            raise _deny_outside(path, writable=writable)
        return path

    # -- 目录 ---------------------------------------------------------------
    async def create_directory(self, request: Any, options: Any = None) -> FileSystemCreateDirectoryResult:
        path = self._guard(_host_path(read_field(request, "path")), writable=True)
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as error:
            raise _translate_oserror(error, path) from None
        return FileSystemCreateDirectoryResult(path=path)

    async def list_directory(self, request: Any, options: Any = None) -> FileSystemListDirectoryResult:
        path = self._guard(_host_path(read_field(request, "path")), writable=False)
        started = _now_ms()
        try:
            names = sorted(os.listdir(path))
        except OSError as error:
            raise _translate_oserror(error, path) from None
        entries: list[FileSystemListDirectoryEntry] = []
        for name in names:
            child = os.path.join(path, name)
            try:
                st = os.lstat(child)
            except OSError:
                continue
            entries.append(
                FileSystemListDirectoryEntry(kind=_kind_of(child, st), name=name, path=child)
            )
        return FileSystemListDirectoryResult(
            duration_ms=_now_ms() - started,
            entries=entries,
            num_entries=len(entries),
            path=path,
        )

    # -- stat ---------------------------------------------------------------
    async def stat(self, request: Any, options: Any = None) -> FileSystemStatResult:
        path = self._guard(_host_path(read_field(request, "path")), writable=False)
        try:
            st = os.lstat(path)
        except OSError as error:
            raise _translate_oserror(error, path) from None
        kind = _kind_of(path, st)
        size = 0 if kind == "directory" else int(st.st_size)
        return FileSystemStatResult(
            path=path,
            kind=kind,  # type: ignore[arg-type]
            size_bytes=size,
            mtime_ms=st.st_mtime * 1000.0,
            revision=_revision_of(path, st),
        )

    # -- 读 -----------------------------------------------------------------
    @staticmethod
    def _decode(raw: bytes) -> tuple[str, str]:
        try:
            return raw.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            # PORT: node 适配器按配置的编码回退；本机默认 UTF-8，失败则 latin-1
            # （永不抛错，保证「能读出来」优先于「读得准」）。
            return raw.decode("latin-1"), "latin-1"

    @staticmethod
    def _normalize_and_detect(raw_text: str) -> tuple[str, str]:
        crlf = raw_text.count("\r\n")
        lf_only = raw_text.count("\n") - crlf
        line_endings = "CRLF" if crlf > lf_only else "LF"
        return raw_text.replace("\r\n", "\n").replace("\r", "\n"), line_endings

    async def read_text_file(self, request: Any, options: Any = None) -> FileSystemReadTextResult:
        path = self._guard(_host_path(read_field(request, "path")), writable=False)
        max_bytes = read_field(request, "max_bytes", "maxBytes") or DEFAULT_MAX_READ_BYTES
        try:
            st = os.stat(path)
        except OSError as error:
            raise _translate_oserror(error, path) from None
        if os.path.isdir(path):
            raise _fs_error("is_directory", f"Path is a directory: {path}", path)
        if st.st_size > int(max_bytes):
            raise _fs_error(
                "too_large",
                f"File is larger than the allowed limit ({st.st_size} > {int(max_bytes)} bytes)",
                path,
            )
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
        except OSError as error:
            raise _translate_oserror(error, path) from None
        text, encoding = self._decode(raw)
        content, line_endings = self._normalize_and_detect(text)
        return FileSystemReadTextResult(
            path=path,
            content=content,
            encoding=encoding,
            bytes_read=len(raw),
            size_bytes=st.st_size,
            truncated=False,
            line_endings=line_endings,  # type: ignore[arg-type]
            revision=_revision_of(path, st),
        )

    async def read_binary_file(self, request: Any, options: Any = None) -> FileSystemReadBytesResult:
        path = self._guard(_host_path(read_field(request, "path")), writable=False)
        max_bytes = read_field(request, "max_bytes", "maxBytes") or DEFAULT_MAX_READ_BYTES
        try:
            st = os.stat(path)
            if st.st_size > int(max_bytes):
                raise _fs_error(
                    "too_large",
                    f"File is larger than the allowed limit ({st.st_size} > {int(max_bytes)} bytes)",
                    path,
                )
            with open(path, "rb") as handle:
                raw = handle.read()
        except FileSystemPortError:
            raise
        except OSError as error:
            raise _translate_oserror(error, path) from None
        return FileSystemReadBytesResult(
            path=path,
            content=raw,
            bytes_read=len(raw),
            size_bytes=st.st_size,
            revision=_revision_of(path, st),
        )

    async def read_text_file_range(
        self, request: Any, options: Any = None
    ) -> FileSystemReadTextRangeResult:
        """按逻辑行区间读取（零基 ``offset_line``）。

        # PORT: 上游 node 适配器把 range 读取按失败处理；本实现**支持**它 ——
        内核的 Read 工具带 ``offset``/``limit`` 时走这条路，退化成读全文再切片
        会让大文件读取失去意义。
        """
        path = self._guard(_host_path(read_field(request, "path")), writable=False)
        full = await self.read_text_file({"path": path, "maxBytes": read_field(request, "max_bytes", "maxBytes")})
        lines = full.content.split("\n")
        offset = read_field(request, "offset_line", "offsetLine") or 0
        limit = read_field(request, "limit_lines", "limitLines")
        offset = max(0, int(offset))
        selected = lines[offset:] if limit is None else lines[offset : offset + int(limit)]
        content = "\n".join(selected)
        return FileSystemReadTextRangeResult(
            path=path,
            content=content,
            encoding=full.encoding,
            bytes_read=full.bytes_read,
            size_bytes=full.size_bytes,
            truncated=False,
            start_line=offset + 1,
            line_count=len(selected),
            total_lines=len(lines),
            line_endings=full.line_endings,
            revision=full.revision,
        )

    # -- 写 -----------------------------------------------------------------
    async def write_text_file(self, request: Any, options: Any = None) -> FileSystemWriteTextResult:
        path = self._guard(_host_path(read_field(request, "path")), writable=True)
        content = read_field(request, "content")
        if content is None:
            content = ""
        content = str(content)
        line_endings = read_field(request, "line_endings", "lineEndings")
        create_parents = read_field(request, "create_parents", "createParents")
        atomic = read_field(request, "atomic")
        expected = read_field(request, "expected_revision", "expectedRevision")
        encoding = read_field(request, "encoding") or "utf-8"

        if expected is not None and os.path.exists(path):
            # 乐观并发：内核 Edit 把「读到的 revision」带回来，不一致说明文件被
            # 别的进程改过，必须报 stale_write 而不是覆盖（否则丢改动）。
            try:
                st = os.stat(path)
            except OSError as error:
                raise _translate_oserror(error, path) from None
            expected_id = read_field(expected, "id")
            if expected_id and expected_id != _revision_of(path, st).id:
                raise _fs_error("stale_write", f"File changed since it was read: {path}", path)

        payload = content
        if line_endings == "CRLF":
            payload = content.replace("\n", "\r\n")
        raw = payload.encode(encoding if encoding != "latin-1" else "latin-1", errors="surrogateescape")

        try:
            parent = os.path.dirname(path)
            if create_parents and parent:
                os.makedirs(parent, exist_ok=True)
            if atomic:
                tmp = f"{path}.zcode-tmp-{os.getpid()}-{int(time.time() * 1000)}"
                with open(tmp, "wb") as handle:
                    handle.write(raw)
                os.replace(tmp, path)
            else:
                with open(path, "wb") as handle:
                    handle.write(raw)
        except OSError as error:
            raise _translate_oserror(error, path) from None
        st = os.stat(path)
        return FileSystemWriteTextResult(
            path=path, bytes_written=len(raw), revision=_revision_of(path, st)
        )

    async def remove_file(self, request: Any, options: Any = None) -> FileSystemRemoveFileResult:
        path = self._guard(_host_path(read_field(request, "path")), writable=True)
        missing_ok = bool(read_field(request, "missing_ok", "missingOk"))
        if not os.path.exists(path):
            if missing_ok:
                return FileSystemRemoveFileResult(path=path, removed=False)
            raise _fs_error("not_found", f"Path not found: {path}", path)
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except OSError as error:
            raise _translate_oserror(error, path) from None
        return FileSystemRemoveFileResult(path=path, removed=True)

    # -- 搜索 ---------------------------------------------------------------
    async def search_files(self, request: Any, options: Any = None) -> FileSystemSearchFilesResult:
        path = self._guard(_host_path(read_field(request, "path")), writable=False)
        pattern = str(read_field(request, "pattern") or "")
        max_results = read_field(request, "max_results", "maxResults")
        offset = int(read_field(request, "offset") or 0)
        started = _now_ms()

        # PORT: 上游是 ripgrep --files -g；本机用 stdlib glob。语义差异：
        # ``**`` 的递归深度、``.gitignore`` 不参与过滤（ripgrep 会过滤）。
        # 结果按路径排序保证稳定，便于模型 diff。
        matches: list[str] = []
        try:
            joined = os.path.join(path, pattern)
            for hit in _glob.glob(joined, recursive=True):
                if os.path.isfile(hit):
                    matches.append(os.path.abspath(hit))
        except re.error as error:
            raise _fs_error("invalid_pattern", f"Invalid glob pattern: {pattern}", path, error) from None
        matches = sorted(set(matches))
        window = matches[offset:] if not max_results else matches[offset : offset + int(max_results)]
        return FileSystemSearchFilesResult(
            path=path,
            pattern=pattern,
            duration_ms=_now_ms() - started,
            files=window,
            num_files=len(window),
            truncated=len(window) < len(matches),
        )

    async def search_text(self, request: Any, options: Any = None) -> FileSystemSearchTextResult:
        path = self._guard(_host_path(read_field(request, "path")), writable=False)
        pattern = str(read_field(request, "pattern") or "")
        glob_filter = read_field(request, "glob")
        mode = read_field(request, "output_mode", "outputMode") or "files_with_matches"
        ignore_case = read_field(request, "ignore_case", "ignoreCase")
        show_line_numbers = read_field(request, "show_line_numbers", "showLineNumbers")
        only_matching = read_field(request, "only_matching", "onlyMatching")
        before = int(read_field(request, "before_context", "beforeContext") or 0)
        after = int(read_field(request, "after_context", "afterContext") or 0)
        ctx = read_field(request, "context")
        if ctx is not None:
            before = after = int(ctx)
        head_limit = read_field(request, "head_limit", "headLimit")
        offset = int(read_field(request, "offset") or 0)
        started = _now_ms()

        try:
            matcher = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as error:
            raise _fs_error("invalid_pattern", f"Invalid regex pattern: {pattern}", path, error) from None

        if os.path.isfile(path):
            candidates = [path]
        elif os.path.isdir(path):
            walk_pattern = os.path.join(path, glob_filter or "**/*")
            candidates = sorted(
                hit for hit in _glob.glob(walk_pattern, recursive=True) if os.path.isfile(hit)
            )
        else:
            raise _fs_error("not_found", f"Path not found: {path}", path)

        files: list[str] = []
        entries: list[FileSystemSearchTextEntry] = []
        num_matches = 0
        for candidate in candidates:
            try:
                st = os.stat(candidate)
                if st.st_size > DEFAULT_MAX_READ_BYTES or st.st_size == 0:
                    continue
                with open(candidate, "rb") as handle:
                    raw = handle.read()
            except OSError:
                continue
            if b"\x00" in raw[:8192]:
                continue  # 二进制文件跳过（ripgrep 默认行为）
            text, _encoding = self._decode(raw)
            lines = text.split("\n")
            file_hit = False
            file_count = 0
            for index, line in enumerate(lines):
                if not matcher.search(line):
                    continue
                file_hit = True
                file_count += 1
                num_matches += 1
                if mode == "content":
                    if before or after:
                        lo = max(0, index - before)
                        hi = min(len(lines), index + after + 1)
                        for ctx_index in range(lo, hi):
                            entries.append(
                                FileSystemSearchTextEntry(
                                    path=candidate,
                                    line_number=ctx_index + 1 if show_line_numbers is not False else None,
                                    text=(matcher.search(lines[ctx_index]).group(0) if only_matching and matcher.search(lines[ctx_index]) else lines[ctx_index]),
                                    matched=ctx_index == index,
                                )
                            )
                    else:
                        text_value = line
                        if only_matching:
                            found = matcher.search(line)
                            text_value = found.group(0) if found else ""
                        entries.append(
                            FileSystemSearchTextEntry(
                                path=candidate,
                                line_number=index + 1 if show_line_numbers is not False else None,
                                text=text_value,
                                matched=True,
                            )
                        )
            if mode == "count" and file_hit:
                entries.append(
                    FileSystemSearchTextEntry(path=candidate, count=file_count, matched=True)
                )
            if file_hit:
                files.append(candidate)

        if head_limit:
            if mode == "content":
                entries = entries[offset : offset + int(head_limit)]
            else:
                files = files[offset : offset + int(head_limit)]
        elif offset:
            if mode == "content":
                entries = entries[offset:]
            else:
                files = files[offset:]

        truncated = False
        if mode == "content" and head_limit:
            truncated = len(files) > len({entry.path for entry in entries})
        return FileSystemSearchTextResult(
            path=path,
            pattern=pattern,
            mode=mode,  # type: ignore[arg-type]
            duration_ms=_now_ms() - started,
            files=files,
            entries=entries,
            num_matches=num_matches,
            truncated=truncated,
            applied_limit=int(head_limit) if head_limit else None,
            applied_offset=offset or None,
        )


# ---------------------------------------------------------------------------
# ExecutionPort —— 复用应用的持久 shell
# ---------------------------------------------------------------------------
_EXIT_SUFFIX_RE = re.compile(r"\n\(exit code: (\d+)\)$")


class AppExecutionPort:
    """``ExecutionPort`` 的应用实现（持久 shell + per-session cwd）。

    只实现 ``run``：``start`` / ``get_background_task`` 等后台能力**故意缺席** ——
    内核用 ``supports_bash_background_lifecycle(port)`` 探测，缺席时 ``Bash`` 会明确
    拒绝 ``run_in_background``，而不是静默降级成前台（静默降级会让模型以为命令在
    后台跑着）。应用自己的后台执行走 ``shell_execute``。
    """

    def __init__(self, session_id: str, *, extra_env: Optional[dict[str, str]] = None) -> None:
        self._session_id = session_id
        self._extra_env = dict(extra_env or {})
        self._closed = False

    @property
    def session_id(self) -> str:
        return self._session_id

    def _coordinator(self):
        from ....tools.shell_execute_tool import get_coordinator

        return get_coordinator()

    @staticmethod
    def _command_text(request: ExecutionRequest) -> str:
        command = read_field(request, "command")
        # ``ExecutionCommand`` 是 dataclass（mode/shell_profile/…）；真实命令行在
        # ``command`` 字段里。两种形态都接受。
        text = read_field(command, "command")
        if text is None:
            text = read_field(command, "text")
        if text is None and isinstance(command, str):
            text = command
        # 命令行里的沙箱路径同样要还原 —— 模型会把 ``/var/minis/workspace/x``
        # 直接写进 ``cat`` / ``cd`` / 重定向里，而且**出现在命令中段**（不是开头）。
        #
        # 这里刻意不走 :func:`_host_path`：那个是给**单个路径参数**用的，只认
        # 「开头（可带盘符）的沙箱前缀」，免得把 ``C:\Users\me\var\minis\x`` 这种
        # 普通路径误改。命令行要的是「整条里所有出现都替掉」，正是
        # ``rewrite_sandbox_paths`` 的语义（``shell_execute`` 用的也是它）。
        return _rewrite_sandbox_in_text(str(text or ""))

    async def run(self, request: ExecutionRequest, options: Any = None) -> ExecutionResult:
        command_text = self._command_text(request)
        cwd = _host_path(read_field(request, "cwd"))
        timeout_ms = read_field(request, "timeout_ms", "timeoutMs")
        capture_cwd = bool(read_field(request, "capture_cwd_after_success", "captureCwdAfterSuccess"))
        env_overlay = read_field(request, "env")
        env_vars: dict[str, str] = dict(self._extra_env)
        # ExecutionEnvOverlay 用 set/unset 两个集合表达增量，不是 KV 直传。
        for name in (read_field(env_overlay, "unset") or []):
            env_vars.pop(str(name), None)
        for name, value in dict(read_field(env_overlay, "set") or {}).items():
            env_vars[str(name)] = str(value)

        coordinator = self._coordinator()
        if cwd:
            # 只在 shell 还没建起来时生效（持久 shell 的 cd 是粘性的，与上游一致）。
            coordinator.set_session_cwd(self._session_id, Path(str(cwd)))

        # 沙箱守卫：与主智能体的 ``shell_execute`` 走**同一道门** ——
        # :func:`openminis.sandbox.guard.check_shell_command`（目录越界 / 异常删除 /
        # 敏感信息三条规则）。拦下时不执行命令，把提示当输出回给模型（它看得见，
        # 能自己改成工作区内的相对路径），同时在「沙箱」页记一条事件、可手动放行。
        #
        # 没接这道门之前 ZCode 的 Bash 是**裸奔**的：``echo x > C:\Users\...`` 与
        # ``rm -rf`` 都不受拦，而主智能体那边同样的命令会被拦 —— 换个循环就能
        # 绕开沙盒，这在同一台机器上是不能接受的。
        try:
            from ....sandbox.guard import check_shell_command

            blocked = check_shell_command(
                command_text,
                session_id=self._session_id,
                cwd=str(coordinator.cwd_for(self._session_id) or ""),
                tool="zcode_bash",
            )
        except Exception:  # pragma: no cover - 守卫故障不该让 shell 整体失效
            logger.exception("zcode sandbox guard failed")
            blocked = None
        if blocked is not None:
            now = datetime.now(timezone.utc)
            return self._result(
                status="failed",
                stdout=blocked,
                stderr="",
                exit_code=1,
                duration_ms=0,
                started_at=now,
                completed_at=now,
                timed_out=False,
                resolved_cwd=None,
            )

        started_at = datetime.now(timezone.utc)
        timeout_s = (int(timeout_ms) / 1000.0) if timeout_ms else None
        try:
            result = await coordinator.execute(
                self._session_id,
                command_text,
                timeout=timeout_s if timeout_s else 600.0,
                env_vars=env_vars or None,
            )
        except asyncio.TimeoutError:
            completed_at = datetime.now(timezone.utc)
            return self._result(
                status="failed",
                stdout="",
                stderr="",
                exit_code=None,
                duration_ms=int((completed_at - started_at).total_seconds() * 1000),
                started_at=started_at,
                completed_at=completed_at,
                timed_out=True,
                resolved_cwd=coordinator.cwd_for(self._session_id) if capture_cwd else None,
            )

        completed_at = datetime.now(timezone.utc)
        raw_output = str(getattr(result, "output", "") or "")
        exit_code = getattr(result, "exit_code", None)
        # 协调器把退出码拼在正文尾部（``\n(exit code: N)``）；内核自己会按 exit_code
        # 渲染，若不剥掉会出现两处退出码。
        stripped = raw_output
        match = _EXIT_SUFFIX_RE.search(stripped)
        if match is not None:
            if exit_code is None:
                exit_code = int(match.group(1))
            stripped = stripped[: match.start()]

        timed_out = exit_code == 124
        status = "completed" if (exit_code in (0, None) and not timed_out) else "failed"
        if timed_out:
            status = "failed"
        return self._result(
            status=status,
            stdout=stripped,
            stderr="",
            exit_code=exit_code,
            duration_ms=int(getattr(result, "duration_ms", 0) or 0),
            started_at=started_at,
            completed_at=completed_at,
            timed_out=timed_out,
            resolved_cwd=coordinator.cwd_for(self._session_id) if capture_cwd else None,
        )

    @staticmethod
    def _result(
        *,
        status: str,
        stdout: str,
        stderr: str,
        exit_code: Optional[int],
        duration_ms: int,
        started_at: datetime,
        completed_at: datetime,
        timed_out: bool,
        resolved_cwd: Optional[str],
    ) -> ExecutionResult:
        return ExecutionResult(
            status=status,
            stdout=ExecutionStreamResult(
                text=stdout, bytes=len(stdout.encode("utf-8", "surrogateescape")), truncated=False
            ),
            stderr=ExecutionStreamResult(
                text=stderr, bytes=len(stderr.encode("utf-8", "surrogateescape")), truncated=False
            ),
            duration_ms=duration_ms,
            timed_out=timed_out,
            cancelled=False,
            started_at=started_at,
            completed_at=completed_at,
            exit_code=exit_code,
            resolved_cwd=resolved_cwd,
        )

    async def close(self) -> None:
        # 会话 shell 由应用自己管理生命周期（``stop_session``）；这里不杀，
        # 否则 ZCode 的一次工具调用会连带掐掉同一会话里 ``shell_execute`` 的 shell。
        self._closed = True


# ---------------------------------------------------------------------------
# SessionStorePort —— todo 列表
# ---------------------------------------------------------------------------
class InMemorySessionStorePort:
    """``SessionStorePort`` 的最小实现：按会话存 todo 列表。

    # PORT-STATUS: partial —— 上游还有 read_state / update_state（会话级 KV）。
    # 那块能力应用侧有自己的会话存储，未接线；todo 是当前唯一被 ``TodoRead`` /
    # ``TodoWrite`` 消费的部分，先把它做对。
    """

    def __init__(self) -> None:
        self._todos: dict[str, list[dict[str, Any]]] = {}

    async def read_todos(self, request: Any) -> list[dict[str, Any]]:
        session_id = str(read_field(request, "session_id", "sessionId") or "")
        return list(self._todos.get(session_id, []))

    async def update_todos(self, request: Any) -> None:
        session_id = str(read_field(request, "session_id", "sessionId") or "")
        todos = read_field(request, "todos") or []
        self._todos[session_id] = [dict(item) for item in todos]

    async def read_state(self, request: Any) -> Any:  # pragma: no cover - 未接线
        return None

    async def update_state(self, request: Any) -> None:  # pragma: no cover - 未接线
        return None


# ---------------------------------------------------------------------------
# HttpClientPort —— WebFetch
# ---------------------------------------------------------------------------
class HttpClientPortError(Exception):
    """带 ``code`` 的 HTTP 端口错误（内核按 ``code`` 分支，如 ``egress_blocked``）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SimpleHttpClientPort:
    """``HttpClientPort`` 的实现：``urllib`` + 线程池（不引第三方依赖）。"""

    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        self._timeout = timeout_seconds

    async def fetch(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[dict[str, str]] = None,
        body: Any = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self._fetch_sync, url, method, headers or {}, body)

    def _fetch_sync(
        self, url: str, method: str, headers: dict[str, str], body: Any
    ) -> dict[str, Any]:
        import urllib.error
        import urllib.request

        started = _now_ms()
        payload = None
        if body is not None:
            payload = body.encode("utf-8") if isinstance(body, str) else body
        request = urllib.request.Request(url, data=payload, method=method.upper())
        for name, value in headers.items():
            request.add_header(name, value)
        if not any(key.lower() == "user-agent" for key in headers):
            request.add_header("User-Agent", "ZCode/1.0 (+openminis)")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
                text, _encoding = LocalFileSystemPort._decode(raw)
                return {
                    "status": int(getattr(response, "status", 200)),
                    "statusText": str(getattr(response, "reason", "") or ""),
                    "headers": {k.lower(): v for k, v in response.headers.items()},
                    "body": text,
                    "bytes": len(raw),
                    "url": str(getattr(response, "url", url)),
                    "durationMs": int(_now_ms() - started),
                }
        except urllib.error.HTTPError as error:
            raw = error.read() if hasattr(error, "read") else b""
            text, _encoding = LocalFileSystemPort._decode(raw)
            # HTTP 错误是**正常响应**（404/403 的内核分支靠它），不当异常抛。
            return {
                "status": int(error.code),
                "statusText": str(error.reason or ""),
                "headers": {k.lower(): v for k, v in (error.headers or {}).items()},
                "body": text,
                "bytes": len(raw),
                "url": url,
                "durationMs": int(_now_ms() - started),
            }
        except OSError as error:
            raise HttpClientPortError("network_error", f"{type(error).__name__}: {error}") from None


#: ``ExecutionPort`` 的可选后台能力：本实现不提供，内核对缺席的探测点。
def _supports_background(port: Any) -> bool:
    return isinstance(port, AppExecutionPort) and hasattr(port, "start")


# PORT: 保留导入以便类型检查器识别契约来源（后台执行结果类型仍在契约里）。
_ = (BackgroundExecutionStartResult, BackgroundExecutionSnapshot, _new_abort_signal, _supports_background)
