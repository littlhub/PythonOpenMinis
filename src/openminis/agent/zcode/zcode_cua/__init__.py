"""@zcode/zcode-cua — API-compatible placeholder package (Python port).

Ported from: packages/zcode-cua/
Original package: @zcode/zcode-cua

This build ships without Computer Use: every runtime surface reports
**unavailable** and fails closed. Only the two contracts consumed by the
programming-agent kernel are ported here:

- ``frame_contract``            (packages/zcode-cua/frame-contract.js)
- ``request_access_contract``   (packages/zcode-cua/request-access-contract.js)

# PORT: 其余 CUA 运行时未移植（scope: cua-runtime，非编程内核）：
#   broker-server / broker / broker-ports / broker-helper-* / broker-socket-path /
#   pip-session / pip-session-node / host-display-contract
"""

from .frame_contract import (
    OFFICIAL_CUA_FRAME_INTEGRITY_META_KEY,
    OFFICIAL_CUA_FRAME_MODEL_CONTENT_PROTECTION,
    OFFICIAL_CUA_IMAGE_INLINE_BASE64_BYTES,
    OfficialCuaFrameAttestation,
    OfficialCuaFrameContentPair,
    attest_official_cua_frame_content,
    contains_image_ref_authority,
    contains_official_cua_image_ref_credential_text,
    find_official_cua_frame_content_pair,
    is_official_cua_image_ref_text,
    parse_official_cua_image_ref,
    preserve_official_cua_frame_result,
    read_raster_envelope_identity,
)
from .request_access_contract import (
    CUA_REQUEST_ACCESS_STATUS_META_KEY,
    CuaRequestAccessStatus,
    CuaRequestAccessStatusParseFailure,
    CuaRequestAccessStatusParseResult,
    CuaRequestAccessStatusParseSuccess,
    CuaRequestAccessStatusSchema,
    cua_request_access_status_schema,
)

__all__ = [
    "OFFICIAL_CUA_FRAME_INTEGRITY_META_KEY",
    "OFFICIAL_CUA_FRAME_MODEL_CONTENT_PROTECTION",
    "OFFICIAL_CUA_IMAGE_INLINE_BASE64_BYTES",
    "OfficialCuaFrameAttestation",
    "OfficialCuaFrameContentPair",
    "attest_official_cua_frame_content",
    "contains_image_ref_authority",
    "contains_official_cua_image_ref_credential_text",
    "find_official_cua_frame_content_pair",
    "is_official_cua_image_ref_text",
    "parse_official_cua_image_ref",
    "preserve_official_cua_frame_result",
    "read_raster_envelope_identity",
    "CUA_REQUEST_ACCESS_STATUS_META_KEY",
    "CuaRequestAccessStatus",
    "CuaRequestAccessStatusParseFailure",
    "CuaRequestAccessStatusParseResult",
    "CuaRequestAccessStatusParseSuccess",
    "CuaRequestAccessStatusSchema",
    "cua_request_access_status_schema",
]
