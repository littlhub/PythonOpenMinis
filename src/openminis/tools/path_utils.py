"""Shared workspace-path resolution for the first-agent style tools
(``ls`` / ``search_files``).

These tools operate on the desktop workspace (``app_context().external_files_dir``
— the same root the per-session ``file_*`` tools are scoped under). A helper
lives here rather than in each module so every tool applies the identical
prefix stripping and escape guard.

Accepted path forms:

- ``""`` / ``"."``                     → the workspace root itself
- ``/var/minis/workspace/...``         → workspace-root-relative (OpenMinis sandbox notation)
- ``/var/minis/...`` or ``/workspace/...`` → alias of the workspace root
- ``~/...``                            → workspace-root-relative convenience alias
- ``relative/path``                    → workspace-root-relative

Anything that resolves outside the workspace root is refused (returns ``None``),
mirroring the escape guard in :mod:`openminis.tools.file_read_tool`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from ..core.context import app_context
from ..core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "workspace_root",
    "resolve_workspace_path",
    "readonly_roots",
    "PREFIXES",
    "SANDBOX_WORKSPACE",
    "SANDBOX_DATA",
    "SANDBOX_HOME",
    "split_sandbox_prefix",
    "split_sandbox_root",
    "to_sandbox_path",
    "to_host_path",
    "scrub_machine_paths",
    "unscrub_sandbox_paths",
]


def readonly_roots() -> tuple[Path, ...]:
    """只读放行的附加根（当前只有技能库目录）。

    技能目录不在 workspace 下面，默认会被路径护栏拒掉 —— 模型只能瞎猜路径。
    放行后 ls / search_files / file_read 可以浏览技能目录（装什么技能、
    SKILL.md 写了什么），写入类工具有自己独立的会话根校验，不受影响。
    """
    try:
        from ..skills.store import configured_skills_root

        return (configured_skills_root(),)
    except Exception:  # pragma: no cover - 技能模块损坏不该拖垮路径解析
        return ()


#: Path prefixes that are transparently mapped onto the workspace root.
PREFIXES = ("/var/minis/workspace", "/workspace", "/var/minis")

#: 出站（发给模型）时的沙箱写法。用户要求模型眼里**不出现** ``C:/Users/loo/…``
#: 这种机器路径，只留工作区的相对形态 —— 少泄露本机信息，模型也不用去猜
#: 「这个盘符和用户名换台机器还在不在」。
SANDBOX_WORKSPACE = "/var/minis/workspace"
SANDBOX_DATA = "/var/minis/data"
SANDBOX_HOME = "/var/minis/home"


def split_sandbox_prefix(path: str) -> str | None:
    """``/var/minis/workspace/a/b`` → ``a/b``；不是沙箱写法返回 ``None``。

    「工作区根」这个语义必须被工具端认出来 —— 否则模型把出站看到的
    ``/var/minis/workspace/uploads/x.jpg`` 回填给 ``read_image``，会被当成
    **会话相对**路径去 ``workspace/<sid>/uploads/`` 里找，当然找不到。
    """
    candidate = (path or "").strip()
    if not candidate:
        return None
    for prefix in PREFIXES:
        if candidate == prefix:
            return ""
        if candidate.startswith(prefix + "/"):
            return candidate[len(prefix) + 1 :]
    return None


#: 前缀后面跟到哪儿为止：遇到空白或明显的分隔符就停。
#:
#: 含空格的路径只能替掉前缀、剩下保持原样 —— 宁可少替一点，也不要把正文一起
#: 当路径吃进去（把正常句子咬掉半截比路径没替干净糟得多）。
_PATH_TAIL = r"((?:[\\/]+[^\s)\]\"'`,;：:]*)?)"


def _sep_pattern(prefix: str) -> str:
    """一段路径 → 「两种分隔符都认」的正则片段（Windows 上 \\ 与 / 混着来）。"""
    parts = [re.escape(p) for p in re.split(r"[\\/]+", prefix.strip("\\/")) if p]
    return r"[\\/]+".join(parts)


def _sandbox_rules() -> list[tuple[re.Pattern[str], str]]:
    """按**从具体到宽泛**排好的替换规则（workspace → data → home）。

    顺序不能反：``data_dir`` 在 ``home`` 下面，先替 home 会把 workspace 一起吃掉，
    拼出 ``/var/minis/home/openminis/workspace/…`` 这种四不像。
    """
    ctx = app_context()
    pairs: list[tuple[Path, str]] = [
        (ctx.external_files_dir, SANDBOX_WORKSPACE),
        (ctx.data_dir, SANDBOX_DATA),
        (Path.home(), SANDBOX_HOME),
    ]
    seen: set[str] = set()
    rules: list[tuple[re.Pattern[str], str]] = []
    for path, replacement in pairs:
        text = str(path).replace("\\", "/").rstrip("/")
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        # 前缀不能从半个路径中间咬一口（前面紧挨着路径字符就不算）
        rules.append((
            re.compile(rf"(?<![\w.]){_sep_pattern(text)}{_PATH_TAIL}", re.IGNORECASE),
            replacement,
        ))
    return rules


def _sandbox_join(replacement: str, tail: str) -> str:
    cleaned = (tail or "").lstrip("\\/").replace("\\", "/")
    return f"{replacement}/{cleaned}".rstrip("/") if cleaned else replacement


def to_sandbox_path(path: str | Path) -> str:
    """单条路径 → 沙箱写法；不在已知根下面（或本来就是沙箱写法）则原样返回。"""
    raw = str(path or "")
    if not raw or raw.startswith("/var/minis"):
        return raw
    norm = raw.replace("\\", "/")
    for pattern, replacement in _sandbox_rules():
        m = pattern.match(norm)
        if m:
            return _sandbox_join(replacement, m.group(1) or "")
    return raw


#: 沙箱写法 → 真实目录。**顺序从具体到宽泛**，否则 ``/var/minis/workspace``
#: 会被更宽的前缀先吃掉。
_SANDBOX_ROOTS: tuple[tuple[str, Any], ...] = (
    (SANDBOX_WORKSPACE, lambda: app_context().external_files_dir),
    (SANDBOX_DATA, lambda: app_context().data_dir),
    (SANDBOX_HOME, lambda: Path.home()),
)


def split_sandbox_root(raw: str | Path) -> tuple[Path, str] | None:
    """``/var/minis/data/a/b`` → ``(data_dir, "a/b")``；不是沙箱写法返回 ``None``。

    同时给出「根部」与「余额」：调用方既要知道最终路径，也要知道**该用哪个根
    做围栏**（工作区里的东西和工作区外的数据目录规则不一样）。
    """
    text = str(raw or "").strip().strip('"').strip("'")
    if not text:
        return None
    norm = text.replace("\\", "/")
    for prefix, resolve in _SANDBOX_ROOTS:
        if norm == prefix:
            return Path(resolve()), ""
        if norm.startswith(prefix + "/"):
            rest = norm[len(prefix) + 1 :]
            return Path(resolve()), rest
    return None


def to_host_path(raw: str | Path) -> Path | None:
    """沙箱写法还原成本机路径；不是沙箱写法返回 ``None``。

    与 :func:`to_sandbox_path` 是一对：出站会把机器路径换成沙箱写法，模型回填
    时必须还原得回来，否则「看得见、用不了」。
    """
    split = split_sandbox_root(raw)
    if split is None:
        return None
    root, rest = split
    return Path(root, *[p for p in rest.split("/") if p]) if rest else root


def unscrub_sandbox_paths(text: str) -> str:
    """把文本里的沙箱写法还原成本机路径（给 shell 命令用）。

    Windows 上不存在 ``/var/minis``，所以模型照着出站看到的路径拼出来的命令
    （``cd /var/minis/data/skills/xxx``）必然 "No such file or directory"。
    替换成真实路径后，bash 与 cmd 都认。
    """
    if not text or "/var/minis" not in text:
        return text
    out = text
    for prefix, resolve in _SANDBOX_ROOTS:
        try:
            target = str(resolve()).replace("\\", "/").rstrip("/")
        except Exception:  # pragma: no cover - 上下文没起来就原样留着
            continue
        if not target:
            continue
        # 前缀后面不能紧跟路径字符，避免从半个目录名中间咬一口
        out = re.sub(
            re.escape(prefix) + r"(?![A-Za-z0-9_])",
            lambda _m, t=target: t,
            out,
        )
    return out


def scrub_machine_paths(text: str) -> str:
    """把文本里的本机绝对路径前缀换成沙箱写法。

    只动「工作区 / 数据目录 / 用户主目录」这三处的**目录前缀**，其余内容原样；
    替换后剩下的分隔符统一成正斜杠（JSON、markdown 引用、Windows API 都认）。
    """
    if not text:
        return text
    out = text
    for pattern, replacement in _sandbox_rules():
        out = pattern.sub(
            lambda m, r=replacement: _sandbox_join(r, m.group(1) or ""),  # type: ignore[misc]
            out,
        )
    return out


def workspace_root() -> Path:
    """The single directory these tools read from / write under."""
    return app_context().external_files_dir


def resolve_workspace_path(path: str | None) -> Path | None:
    """Resolve ``path`` against the workspace root, or ``None`` if refused."""
    root = workspace_root().resolve()
    candidate = (path or "").strip()
    if not candidate or candidate in {".", "./"}:
        return root
    if candidate == "~":
        candidate = ""
    elif candidate.startswith("~/"):
        candidate = candidate[2:]
    for prefix in PREFIXES:
        if candidate == prefix:
            candidate = ""
            break
        if candidate.startswith(prefix + "/"):
            candidate = candidate[len(prefix) + 1 :]
            break
    candidate = candidate.replace("\\", "/").lstrip("/")

    resolved = (root / candidate).resolve() if candidate else root
    if resolved != root and root not in resolved.parents:
        # 技能库等只读根：解析结果落在里面就放行。
        for extra in readonly_roots():
            er = extra.resolve()
            if resolved == er or er in resolved.parents:
                return resolved
        logger.warning("workspace tool rejected path outside root: %s", path)
        return None
    return resolved
