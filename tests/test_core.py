"""Tests for the coroutine-primitive replacements in openminis.core."""

from __future__ import annotations

import asyncio

import pytest

from openminis.core.flow import MutableSharedFlow, MutableStateFlow, collect_latest, flow_of
from openminis.core.result import Err, Ok, Result


class TestStateFlow:
    def test_current_value_reads_without_suspending(self) -> None:
        flow = MutableStateFlow(1)
        assert flow.value == 1
        flow.value = 2
        assert flow.value == 2

    def test_deduplicates_equal_values(self) -> None:
        """Kotlin StateFlow drops emissions equal to the current value."""
        seen: list[int] = []

        async def run() -> None:
            flow = MutableStateFlow(1)
            task = asyncio.create_task(_drain(flow, seen, 99))
            await asyncio.sleep(0.01)
            flow.value = 1  # equal — should not emit
            flow.value = 2
            await asyncio.sleep(0.05)
            task.cancel()

        asyncio.run(run())
        # Collector replays the current value (1) once, then only the distinct
        # emissions follow — the equal assignment above must not appear twice.
        assert seen == [1, 2]

    def test_new_collector_sees_current_value(self) -> None:
        async def run() -> int:
            flow = MutableStateFlow(7)
            return await anext(flow.collect())

        assert asyncio.run(run()) == 7


async def _drain(flow: MutableStateFlow[int], sink: list[int], count: int) -> None:
    async for value in flow.collect():
        sink.append(value)
        if len(sink) >= count:
            break


class TestSharedFlow:
    def test_no_replay_by_default(self) -> None:
        async def run() -> list[int]:
            flow = MutableSharedFlow[int](replay=0)
            await flow.emit(1)
            collected: list[int] = []
            task = asyncio.create_task(_drain_shared(flow, collected))
            await asyncio.sleep(0.01)
            await flow.emit(2)
            await asyncio.sleep(0.05)
            task.cancel()
            return collected

        assert asyncio.run(run()) == [2]


async def _drain_shared(flow: MutableSharedFlow[int], sink: list[int]) -> None:
    async for value in flow.collect():
        sink.append(value)


class TestResult:
    def test_of_captures_success(self) -> None:
        result = Result.of(lambda: 1 + 1)
        assert result.is_ok
        assert result.unwrap() == 2

    def test_of_captures_failure(self) -> None:
        def boom() -> int:
            raise ValueError("nope")

        result = Result.of(boom)
        assert result.is_err
        assert result.ok_or_none() is None
        assert isinstance(result.error, ValueError)

    def test_unwrap_raises(self) -> None:
        with pytest.raises(RuntimeError):
            Err("bad").unwrap()

    def test_map_and_chain(self) -> None:
        result = Ok(2).map(lambda v: v * 3)
        assert result.unwrap() == 6

        touched: list[str] = []
        Ok(1).on_success(lambda v: touched.append(f"ok{v}"))
        Err("x").on_failure(lambda e: touched.append(f"err{e}"))
        assert touched == ["ok1", "errx"]

    def test_flow_of(self) -> None:
        async def run() -> list[int]:
            return [v async for v in flow_of(1, 2, 3)]

        assert asyncio.run(run()) == [1, 2, 3]

    def test_collect_latest(self) -> None:
        """Kotlin collectLatest cancels the previous action on a new value."""
        seen: list[int] = []

        async def action(value: int) -> None:
            await asyncio.sleep(0.05)
            seen.append(value)

        async def run() -> None:
            flow = MutableSharedFlow[int](replay=0)
            task = asyncio.create_task(collect_latest(flow, action))
            await asyncio.sleep(0.01)
            await flow.emit(1)
            await asyncio.sleep(0.01)
            await flow.emit(2)
            await asyncio.sleep(0.1)
            task.cancel()

        asyncio.run(run())
        assert seen == [2]
