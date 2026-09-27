"""CUA request-access contract — placeholder schema (Computer Use not shipped).

Ported from: packages/zcode-cua/request-access-contract.js (+ request-access-contract.d.ts)
Original package: @zcode/zcode-cua

Mirrors the upstream placeholder: ``safeParse`` always fails closed with
"Computer Use is not available in this build."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Union

CUA_REQUEST_ACCESS_STATUS_META_KEY = "zcode.cua/request-access-status-v1"


@dataclass(slots=True)
class CuaRequestAccessStatus:
    schema_version: int
    platform: str
    grant_owner: str
    accessibility: str
    screen_recording: str


@dataclass(slots=True)
class CuaRequestAccessStatusParseSuccess:
    success: bool
    data: CuaRequestAccessStatus


@dataclass(slots=True)
class CuaRequestAccessStatusParseFailure:
    success: bool
    error: Exception


CuaRequestAccessStatusParseResult = Union[
    CuaRequestAccessStatusParseSuccess, CuaRequestAccessStatusParseFailure
]


class CuaRequestAccessStatusSchema:
    def safe_parse(self, _input: Any) -> CuaRequestAccessStatusParseResult:
        return CuaRequestAccessStatusParseFailure(
            success=False,
            error=RuntimeError("Computer Use is not available in this build."),
        )


cua_request_access_status_schema = CuaRequestAccessStatusSchema()
