"""[T-plugins-manager] 插件体系：装什么、谁在跑、怎么接到引擎。

对外只暴露三件事（``server/plugins_api.py`` 与界面都走这里）：

- :mod:`openminis.plugins.store` —— 安装 / 导入 / 卸载 / 配置
- :mod:`openminis.plugins.registry` —— 起停 / 状态 / 日志
- :mod:`openminis.plugins.bridge` —— IM 会话 ↔ 引擎会话（通道插件用）
- :mod:`openminis.plugins.tools` —— 插件声明的工具 → agent 可调用工具

插件**不只是通道**：``runtime`` 说本体怎么跑（引擎内驱动 / 外部程序 / 不起连接），
``tools`` 段说它给 agent 加什么工具。装一个插件既能接通一个 IM，也能多一项能力。
"""

from __future__ import annotations

from . import store
from .manifest import (
    CATEGORY_CHANNEL,
    CATEGORY_TOOL,
    RUNTIME_ENGINE,
    RUNTIME_MANUAL,
    RUNTIME_PROCESS,
    ChannelManifest,
    ManifestError,
    PluginToolSpec,
)
from .registry import PluginRuntime, get_runtime, runtime, shutdown

__all__ = [
    "store",
    "ChannelManifest",
    "ManifestError",
    "PluginToolSpec",
    "PluginRuntime",
    "get_runtime",
    "runtime",
    "shutdown",
    "RUNTIME_ENGINE",
    "RUNTIME_PROCESS",
    "RUNTIME_MANUAL",
    "CATEGORY_CHANNEL",
    "CATEGORY_TOOL",
]
