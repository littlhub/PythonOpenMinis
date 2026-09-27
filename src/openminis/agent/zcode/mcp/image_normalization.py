"""Image normalization — official CUA frame authority check.

Ported from: packages/core/src/mcp/image-normalization.ts
Original package: @zcode/core (mcp/image-normalization)

# PORT-STATUS: partial
The original ``hasOfficialCuaFrameAuthority`` inspects CUA (Computer Use Agent)
frame metadata to decide whether an image carries an authoritative frame. In the
coding-only port there is no CUA frame subsystem, so this fails closed
(returns ``False``).
"""

from __future__ import annotations

from typing import Any


def has_official_cua_frame_authority(image: Any) -> bool:  # noqa: D401, ANN401
    """Return whether *image* carries an official CUA frame authority.

    PORT: mirrors ``hasOfficialCuaFrameAuthority``. Returns ``False`` in the
    coding-only port (no CUA frame concept).
    """
    return False
