"""Contract modules for the ported ZCode core.

Ported from: packages/contracts/src/**（逐模块 1:1 搬运）
Original package: @zcode/contracts

本包**刻意不做**再导出：上游 ``@zcode/contracts`` 有一个 ``index.ts`` 汇总面，但本包每个
子模块之间已经存在细粒度相对导入（``from .tracing import …``），若此处在包级再导出全部
子模块，会引入「包初始化 → 子模块 → 包属性」的循环风险，且把契约的真实来源藏在了一行
进口里。调用方一律写显式模块路径，例如：

    from ...contracts.errors import CoreErrorType, create_core_error
    from ...contracts.file_system import FileSystemStatResult
    from ...contracts.execution import ExecutionResult

已移植的子模块：``errors`` / ``events`` / ``execution`` / ``file_system`` / ``hooks`` /
``ids`` / ``model`` / ``tools`` / ``tracing``。未移植的上游模块（``capabilities`` /
``commands`` / ``compact`` / ``config`` / ``logging`` / ``network`` / ``path`` /
``plugins`` / ``rewind`` / ``skills`` / ``telemetry`` / ``time`` / ``workflow`` 以及
``interfaces/*`` 中除 file-system 之外的端口）见 ``ZCORE_PORTING_MAP.md``。
"""
