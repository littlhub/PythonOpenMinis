"""Permission responder race — concurrent hook chain vs interaction broker.

Ported from: packages/core/src/tool/executor/permission-responder-race.ts
Original package: @zcode/core (tool/executor)

# PORT-STATUS: partial
# 旧路径在 emitPermissionRequested 之后串行 await hook 链，broker 的应答 deferred 要等 hook 返回才
# 注册。同步 hook（把权限请求转投外部 UI 并阻塞等待外部点击的桥接程序）挂起期间，确认窗已对用户
# 可见，但所有点击都被 resolveInteraction 按「未命中即幂等成功」丢弃——确认窗永久死亡，turn 永久挂起。
# 修法：两个应答方并发启动、先到的决定生效、败者立即 abort 且不被等待。
#
# ``AbortController`` / ``linkAbortSignal`` 来自同包 ``timeout``（已移植）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from ...core.abort import AbortController
from .timeout import link_abort_signal

__all__ = ["race_permission_responders"]


async def race_permission_responders(input: dict[str, Any]) -> dict[str, Any]:
    """``input`` mirrors ``PermissionResponderRaceInput``:

    - ``runHooks(signal) -> PermissionBrokerResult | None``
    - ``requestBroker(signal, claimResponse) -> PermissionBrokerResult``
    - ``signal?`` 外层 turn 取消信号
    - ``onHookFailure?(error)``
    """
    hook_controller = AbortController()
    broker_controller = AbortController()
    unlink_hook = link_abort_signal(input.get("signal"), hook_controller)
    unlink_broker = link_abort_signal(input.get("signal"), broker_controller)

    settled = False
    broker_claimed = False
    loop = asyncio.get_event_loop()

    outcome: "asyncio.Future[dict[str, Any]]" = loop.create_future()

    def settle_resolve(result: Any, source: str) -> None:
        nonlocal settled
        if settled:
            return
        settled = True
        # 先兑现结果再 abort 败者：败者的收尾（子进程终止、反向 RPC 取消）绝不能
        # 反过来阻塞胜者——挂死的就是败者本身。
        if source == "broker":
            hook_controller.abort()
        else:
            broker_controller.abort()
        if not outcome.done():
            outcome.set_result({"result": result, "source": source})

    def settle_reject(error: BaseException) -> None:
        nonlocal settled
        if settled:
            return
        settled = True
        hook_controller.abort()
        if not outcome.done():
            outcome.set_exception(error)

    async def run_broker() -> None:
        # broker 先启动：requestPermission 内同步注册应答 deferred，保证确认窗一可见
        # 用户点击就有归宿，不给 hook 留任何独占窗口期。
        def claim_response() -> bool:
            if settled or broker_controller.signal.aborted:
                return False
            nonlocal broker_claimed
            broker_claimed = True
            hook_controller.abort()
            return True

        try:
            result = await input["requestBroker"](broker_controller.signal, claim_response)
        except Exception as error:  # noqa: BLE001
            # hook 胜出后我们主动 abort broker 产生的拒绝是自己的取消，吞掉；
            # 竞速未收口时的拒绝（超时/外层取消/fail-closed）是真实错误，原样上抛。
            if not settled:
                settle_reject(error)
            return
        if not settled:
            settle_resolve(result, "broker")

    async def run_hooks() -> None:
        try:
            decision = await input["runHooks"](hook_controller.signal)
        except Exception as error:  # noqa: BLE001
            # hook 链故障只令其退赛：辅助应答方的基础设施故障不应替用户做拒绝决定，
            # 确认窗继续等待 broker 应答。
            if not settled:
                on_hook_failure = input.get("onHookFailure")
                if on_hook_failure is not None:
                    on_hook_failure(error)
            return
        # 无决定 = 退赛：不迁移竞速状态，broker 继续单边等待。
        if decision is None or broker_claimed:
            return
        if not settled:
            settle_resolve(decision, "hook")

    broker_task = asyncio.ensure_future(run_broker())
    hook_task = asyncio.ensure_future(run_hooks())
    try:
        return await outcome
    finally:
        # 让两侧协程收尾（它们都检查 settled / abort），避免未 awaited 警告。
        await asyncio.gather(broker_task, hook_task, return_exceptions=True)
        unlink_hook()
        unlink_broker()
