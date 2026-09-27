"""Tool layer — registry, executor, handlers, path/read-state policies.

Ported from: packages/core/src/tool/**
Original package: @zcode/core (tool)

本包**刻意不做**再导出。子模块之间是双向依赖的：``handlers`` 依赖 ``types`` / ``registry`` /
``path_policy``，而 ``registry`` 只依赖 ``types``，``executor`` 又依赖 ``handlers``。若在
包级 ``__init__`` 里汇总，``from ..types import ToolEntry`` 会先触发本文件 → 再次导入
``handlers`` → 撞上半初始化的 ``types``，形成导入环。调用方一律写显式模块路径：

    from ..types import ToolEntry
    from ..handlers import built_in_tools, register_built_in_tools
    from ..handlers.bash import bash_tool_entry

装配入口是 ``handlers.register_built_in_tools``（见 ``handlers/__init__.py``，其中记录了
按 ZCORE_PORTING.md 边界裁剪掉的办公/编排类工具）。
"""
