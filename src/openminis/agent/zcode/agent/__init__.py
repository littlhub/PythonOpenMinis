"""Agent exports.

Ported from: packages/core/src/agent/index.ts
Original package: @zcode/core (agent)

# PORT-STATUS: partial — ``session-history-hydrator.ts`` has no Python counterpart
# yet. Upstream its symbols are re-exported unconditionally; here the re-export is
# guarded so importing this package keeps working until
# ``agent/session_history_hydrator.py`` lands.
"""

from __future__ import annotations

from .message_history import CacheStats, MessageHistory, MessageHistoryImpl, ModelInputMessage
from .message_history import ToolCallInput, create_message_history

# ``export * from "./turn-state.js"`` / ``"./turn-machine.js"`` — both modules declare
# ``__all__``, so the star re-export mirrors the upstream ``export *``.
from .turn_state import *  # noqa: F401,F403
from .turn_machine import *  # noqa: F401,F403

# PORT: ``activeSessionMessages`` / ``hydrateMessageHistoryFromSession`` /
# ``SessionHistoryHydrationResult`` come from ``./session-history-hydrator.js``, which
# is not ported yet. Guarded import keeps the package importable in the meantime.
try:  # pragma: no cover
    from .session_history_hydrator import (
        SessionHistoryHydrationResult,
        active_session_messages,
        hydrate_message_history_from_session,
    )
except ImportError:  # pragma: no cover
    pass
