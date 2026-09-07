"""Settings: model providers, role identities and tool matching.

New in the Python port — gives the Web/TUI/CLI surfaces a place to configure
which model provider to talk through and which identity (role) drives the
agent's persona + available tools/skills. Kotlin spreads this across
``config``, ``data/model/ProviderConfig.kt`` and ``agent/SoulStore.kt``; here
it is one cohesive package with a JSON-backed store.
"""

from .catalog import BUILTIN_IDENTITIES, PROVIDER_TYPES, TOOL_CATALOG
from .store import SettingsError, SettingsStore

__all__ = [
    "BUILTIN_IDENTITIES",
    "PROVIDER_TYPES",
    "TOOL_CATALOG",
    "SettingsError",
    "SettingsStore",
]
