"""CUA frame contract — API-compatible placeholder (this build ships without Computer Use).

Ported from: packages/zcode-cua/frame-contract.js (+ frame-contract.d.ts)
Original package: @zcode/zcode-cua

Computer Use is **not** part of the programming-agent kernel. This module is a
1:1 port of the upstream *placeholder* build, whose README states: predicates
about official CUA frames are always ``false`` and frame results pass through
untouched. Only the two contracts consumed by the tool executor are ported.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

OFFICIAL_CUA_FRAME_INTEGRITY_META_KEY = "zcode.cua/official-frame-integrity-v1"

OFFICIAL_CUA_FRAME_MODEL_CONTENT_PROTECTION = "official_cua_frame_v1"

OFFICIAL_CUA_IMAGE_INLINE_BASE64_BYTES = 200 * 1024


def is_official_cua_image_ref_text(_text: str) -> bool:
    return False


def contains_official_cua_image_ref_credential_text(_text: str) -> bool:
    return False


def contains_image_ref_authority(_text: str) -> bool:
    return False


def parse_official_cua_image_ref(_text: str) -> Optional[dict[str, Any]]:
    return None


def read_raster_envelope_identity(_input: Any) -> Optional[dict[str, Any]]:
    return None


async def preserve_official_cua_frame_result(result: Any, options: Any = None) -> Any:
    return result


@dataclass(slots=True)
class OfficialCuaFrameAttestation:
    kind: str


def attest_official_cua_frame_content(
    _content: Any, _expected_kind: Optional[str] = None
) -> Optional[OfficialCuaFrameAttestation]:
    return None


@dataclass(slots=True)
class OfficialCuaFrameContentPair:
    image: Any
    image_ref: Any
    image_ref_index: int
    image_index: int


def find_official_cua_frame_content_pair(
    _content: Any,
) -> Optional[OfficialCuaFrameContentPair]:
    return None
