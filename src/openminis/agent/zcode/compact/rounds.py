"""Rounds — group items by assistant-started rounds.

Ported from: packages/core/src/compact/rounds.ts
Original package: @zcode/core (compact)
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence, TypeVar

__all__ = ["group_by_assistant_started_rounds"]

# PORT: TS ``<T>`` 泛型 → ``TypeVar``（无约束）。
T = TypeVar("T")


def group_by_assistant_started_rounds(
    items: Sequence[T],
    role_of: Callable[[T], Optional[str]],
    assistant_id_of: Optional[Callable[[T], Optional[str]]] = None,
) -> list[list[T]]:
    """按「assistant 起头」切分轮次。

    Mirrors ``groupByAssistantStartedRounds``.

    # PORT: ``assistantIdOf?.(item)`` 可选调用 → ``assistant_id_of is not None`` 判空后调用。
    # 未提供 ``assistant_id_of`` 时 TS 得到 ``undefined``，Python 侧同样得到 ``None``。
    """
    groups: list[list[T]] = []
    current: list[T] = []
    last_assistant_id: Optional[str] = None

    for item in items:
        role = role_of(item)
        assistant_id = (
            assistant_id_of(item) if role == "assistant" and assistant_id_of is not None else None
        )
        starts_new_assistant_round = (
            role == "assistant"
            and len(current) > 0
            and (assistant_id is None or assistant_id != last_assistant_id)
        )

        if starts_new_assistant_round:
            groups.append(current)
            current = [item]
        else:
            current.append(item)

        if role == "assistant":
            last_assistant_id = assistant_id

    if len(current) > 0:
        groups.append(current)

    return groups
