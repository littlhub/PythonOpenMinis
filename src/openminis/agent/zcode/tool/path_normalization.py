"""Tool path normalization helpers.

Ported from: packages/core/src/tool/path-normalization.ts
Original package: @zcode/core (tool)
"""

from __future__ import annotations

import ntpath
import os
import posixpath
import re
import sys
import unicodedata
from typing import Protocol

__all__ = [
    "ToolPathPlatform",
    "ToolPathApi",
    "get_tool_path_api",
    "normalize_tool_path_for_comparison",
]

# PORT: ``NodeJS.Platform`` — Python 侧的 ``sys.platform`` 取值与之对齐
# （Windows → "win32"，Linux → "linux"，macOS → "darwin"）。
ToolPathPlatform = str


class ToolPathApi(Protocol):
    """Mirrors ``type ToolPathApi = typeof path.posix | typeof path.win32``.

    # PORT: Node 的 ``path.posix`` / ``path.win32`` 是带 ``normalize`` /
    # ``relative`` / ``isAbsolute`` / ``sep`` 的对象（属性名 camelCase）。
    # Python 的 ``posixpath`` / ``ntpath`` 用的是 ``normpath`` / ``relpath`` /
    # ``isabs``，故用一个薄适配器对象暴露与 TS 同名的方法，避免调用点改写。
    """

    # 路径分隔符（``path.posix.sep`` / ``path.win32.sep``）。
    sep: str

    def normalize(self, path: str) -> str: ...

    def relative(self, from_path: str, to_path: str) -> str: ...

    def is_absolute(self, path: str) -> bool: ...

    def join(self, *paths: str) -> str: ...


class _ModulePathApi:
    """Node ``path.posix`` / ``path.win32`` 的等价适配器。"""

    __slots__ = ("_module", "sep")

    def __init__(self, module: object, sep: str) -> None:
        self._module = module
        self.sep = sep

    def normalize(self, path: str) -> str:
        return self._module.normpath(path)  # type: ignore[attr-defined]

    def relative(self, from_path: str, to_path: str) -> str:
        # PORT: JS ``path.relative(from, to)`` 的参数顺序与 ``os.path.relpath(path, start)`` 相反。
        return self._module.relpath(to_path, from_path)  # type: ignore[attr-defined]

    def is_absolute(self, path: str) -> bool:
        return self._module.isabs(path)  # type: ignore[attr-defined]

    def join(self, *paths: str) -> str:
        return self._module.join(*paths)  # type: ignore[attr-defined]


_POSIX_PATH_API = _ModulePathApi(posixpath, posixpath.sep)
_WIN32_PATH_API = _ModulePathApi(ntpath, ntpath.sep)


def get_tool_path_api(platform: ToolPathPlatform) -> ToolPathApi:
    """Mirrors ``getToolPathApi(platform)``."""
    return _WIN32_PATH_API if platform == "win32" else _POSIX_PATH_API


def normalize_tool_path_for_comparison(
    file_path: str,
    platform: ToolPathPlatform = sys.platform,
) -> str:
    """Mirrors ``normalizeToolPathForComparison(filePath, platform?)``."""
    # PORT: TS 非 win32 分支用的是 host ``normalize``（跨平台运行 Node 时随宿主变化），
    # 这里保持同一语义，故用 ``os.path.normpath`` 而非固定 posix。
    normalized = (
        _normalize_windows_tool_path(file_path) if platform == "win32" else os.path.normpath(file_path)
    )
    return unicodedata.normalize("NFC", normalized)


def _normalize_windows_tool_path(file_path: str) -> str:
    drive_alias_normalized = _normalize_windows_drive_alias(file_path)
    prefix_stripped = _strip_windows_extended_path_prefix(ntpath.normpath(drive_alias_normalized))
    return _canonicalize_windows_drive_letter(prefix_stripped)


_DRIVE_ALIAS_RE = re.compile(r"^/([A-Za-z])/")


def _normalize_windows_drive_alias(file_path: str) -> str:
    # Read/Edit/Write 和 Bash cwd 都会收到 Git Bash 风格的 /c/... 路径；
    # 统一转换到 drive-letter 形式，避免各工具重复实现 Windows path 兼容逻辑。
    drive_alias_match = _DRIVE_ALIAS_RE.match(file_path)
    if not drive_alias_match:
        return file_path

    drive = drive_alias_match.group(1).upper()
    rest = file_path[2:]
    return f"{drive}:{rest}".replace("/", "\\")


_DRIVE_LETTER_RE = re.compile(r"^([a-zA-Z]):")


def _canonicalize_windows_drive_letter(file_path: str) -> str:
    return _DRIVE_LETTER_RE.sub(lambda match: f"{match.group(1).upper()}:", file_path)


_EXTENDED_UNC_PREFIX = "\\\\?\\UNC\\"
_EXTENDED_PATH_PREFIX = "\\\\?\\"
# PORT: 反斜杠不能出现在 f-string 的表达式部分（Python < 3.12 直接 SyntaxError），
# 而本仓要求 ``requires-python >= 3.11`` —— 故前缀一律提到模块常量、拼接用 ``+``。
_EXTENDED_UNC_REPLACEMENT = "\\\\"


def _strip_windows_extended_path_prefix(file_path: str) -> str:
    # 只剥离 extended UNC 和 drive path，保留 Volume/GLOBALROOT 等设备命名空间。
    if file_path.startswith(_EXTENDED_UNC_PREFIX):
        return _EXTENDED_UNC_REPLACEMENT + file_path[len(_EXTENDED_UNC_PREFIX):]
    if (
        file_path.startswith(_EXTENDED_PATH_PREFIX)
        and len(file_path) >= 7
        and file_path[5] == ":"
    ):
        return file_path[len(_EXTENDED_PATH_PREFIX):]
    return file_path
