"""Memory Origin Session — stamps memory files with the origin session id.

Ported from: packages/core/src/memory/origin-session.ts
Original package: @zcode/core (memory)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from .memory_file_path import memory_file_relative_path

# PORT: 原 TS 用 npm ``yaml`` 的 ``parseDocument`` / ``isMap`` 直接做 YAML AST 改写，
# 此处用 PyYAML（``yaml``）做等价 dict 往返。运行环境需安装 PyYAML；缺失时按解析失败同分支原样返回。
try:  # pragma: no cover - 由运行环境决定
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

MEMORY_FRONTMATTER_PATTERN = re.compile(
    # PORT: 原 ``/^(\uFEFF?---(?:\r\n|\n))([\s\S]*?)((?:\r\n|\n)---)(?=(?:\r\n|\n)|$)/u``。
    r"^(\ufeff?---(?:\r\n|\n))([\s\S]*?)((?:\r\n|\n)---)(?=(?:\r\n|\n)|$)",
    re.DOTALL,
)


@dataclass(slots=True)
class StampMemoryOriginSessionIdInput:
    """Mirrors the inline argument object of ``stampMemoryOriginSessionId``."""

    content: str
    file_path: str
    memory_root: Optional[str]
    session_id: str


def stamp_memory_origin_session_id(input: StampMemoryOriginSessionIdInput) -> str:
    """Mirrors ``stampMemoryOriginSessionId(input)``."""
    if (
        input.memory_root is None
        or not input.file_path.endswith(".md")
        or memory_file_relative_path(input.memory_root, input.file_path) is None
    ):
        return input.content

    match = MEMORY_FRONTMATTER_PATTERN.search(input.content)
    if match is None:
        return input.content

    opening = match.group(1)
    frontmatter = match.group(2)
    closing = match.group(3)

    if yaml is None:
        # PORT: PyYAML 缺失时无法安全改写 frontmatter；与原解析失败同分支原样返回。
        return input.content

    try:
        parsed = yaml.safe_load(frontmatter)
    except Exception:  # noqa: BLE001
        return input.content

    if not isinstance(parsed, dict):
        return input.content

    metadata = parsed.get("metadata")
    if not isinstance(metadata, dict):
        return input.content

    existing_origin = metadata.get("originSessionId")
    if isinstance(existing_origin, str) and len(existing_origin) > 0:
        return input.content

    # 基线只在缺少 origin 时序列化 frontmatter，并在同一次写入补齐 node_type。
    metadata.pop("node_type", None)
    new_metadata: dict[str, Any] = {"node_type": "memory"}
    for key, value in metadata.items():
        if key == "node_type":
            continue
        new_metadata[key] = value
    new_metadata["originSessionId"] = input.session_id
    parsed["metadata"] = new_metadata

    line_ending = "\r\n" if opening.endswith("\r\n") else "\n"
    # PORT: 原 ``document.toString().replace(/\n$/, "").replace(/\n/gu, lineEnding)``。
    dumped = yaml.safe_dump(
        parsed, default_flow_style=False, sort_keys=False, allow_unicode=True
    )
    stamped_frontmatter = dumped.rstrip("\n").replace("\n", line_ending)
    return f"{opening}{stamped_frontmatter}{closing}{input.content[match.end():]}"
