"""[T-channels-plugins] 通道驱动注册表。

清单里的 ``driver`` 字段指向这里的实现 —— 导入第三方插件包时也校验这一项：
驱动必须在引擎里存在，包才能被接受（包本身只带配置与元信息，不执行代码）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .base import ChannelAdapter

#: 驱动 id → 类。加平台就在这里加一行（并在 builtin/ 放一份插件清单）。
_DRIVERS: dict[str, str] = {
    "qq": "openminis.plugins.drivers.qq:QQAdapter",
}

#: 驱动 id → 给人看的名字（通道页选平台时用）。
DRIVER_LABELS: dict[str, str] = {
    "qq": "QQ（官方开放平台 Bot）",
}


def driver_ids() -> list[str]:
    return sorted(_DRIVERS)


def has_driver(driver: str) -> bool:
    return str(driver or "") in _DRIVERS


def load_adapter(driver: str) -> type["ChannelAdapter"]:
    """按需 import 驱动（没装依赖的平台不该拖垮启动）。"""
    target = _DRIVERS.get(str(driver or ""))
    if target is None:
        raise KeyError(f"没有这个通道驱动：{driver}")
    module_name, _, attr = target.partition(":")
    from importlib import import_module

    module = import_module(module_name)
    return getattr(module, attr)


__all__ = ["driver_ids", "has_driver", "load_adapter", "DRIVER_LABELS"]
