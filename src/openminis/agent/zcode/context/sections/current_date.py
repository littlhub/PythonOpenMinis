"""Current Date section builder — injects today's date as a meta user attachment.

Ported from: packages/core/src/context/sections/current-date.ts
Original package: @zcode/core (context/sections)
"""

from __future__ import annotations

from typing import Optional

from ..types import ContextSection
from ..utils import estimate_tokens


def build_current_date_section(current_date: Optional[str]) -> Optional[ContextSection]:
    if not current_date:
        return None

    content = f"# currentDate\nToday's date is {current_date}."

    return ContextSection(
        name="Current Date",
        source="current_date",
        injection_target="meta_user",
        cache_hint="dynamic",
        chars=len(content),
        tokens=estimate_tokens(content),
        content=content,
        preview=content[:100],
    )
