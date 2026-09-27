"""定位工程根 —— 这套体检/迁移脚本共用的路径引导。

背景：这些脚本原先散落在 ``PythonOpenMinis-main/`` 根目录，与 ``src/`` 同级，
所以都写成 ``Path(__file__).parent / "src"``。后来它们被统一收进
``testZcode/``，那个假设就不成立了 —— 拼出的是 ``testZcode/src``（不存在），
**每个脚本都会 ModuleNotFoundError 或扫到 0 个文件**。那是最糟的一种失效：
体检脚本自己崩了/空转，看起来却像「没问题」。

所以路径一律从这里取：从 ``__file__`` 一路向上找「含 ``src/openminis`` 的那一层」，
脚本放在哪、从哪个目录调用都不影响。

用法（脚本开头，必须早于 ``import openminis``）::

    from _root import PROJECT_ROOT, SRC_ROOT, ensure_src_on_path

    ensure_src_on_path()
"""

from __future__ import annotations

import sys
from pathlib import Path

__all__ = ["PROJECT_ROOT", "SRC_ROOT", "ensure_src_on_path"]


def _find_project_root() -> Path:
    here = Path(__file__).resolve()
    for candidate in (here.parent, *here.parents):
        if (candidate / "src" / "openminis").is_dir():
            return candidate
    # 兜底：找不到就退回脚本所在目录（上层会以清晰的报错暴露问题）。
    return here.parent


#: 含 ``src/openminis`` 的那一层（即 ``PythonOpenMinis-main/``）。
PROJECT_ROOT: Path = _find_project_root()

#: ``PROJECT_ROOT/src``。
SRC_ROOT: Path = PROJECT_ROOT / "src"


def ensure_src_on_path() -> str:
    """把 ``<root>/src`` 放到 ``sys.path`` 最前，返回该路径。"""
    text = str(SRC_ROOT)
    if text in sys.path:
        sys.path.remove(text)
    sys.path.insert(0, text)
    return text
