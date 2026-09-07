"""Bridges the user-facing MountedFoldersStore to read-only-mount enforcement.

Ported from: src/android/app/src/main/java/com/openminis/app/sandbox/MountedFolderCoordinator.kt
Original package: com.openminis.app.sandbox
"""

from __future__ import annotations

from typing import Any

__all__ = ["MountedFolderCoordinator", "ReadOnlyMountException"]

# Linux prefix under which all bind-mounted external folders live.
_MOUNTS_PREFIX = "/var/minis/mounts/"


# PORT: Kotlin `object MountedFolderCoordinator` -> Python class with static
# methods. `store` is the Android `MountedFoldersStore` (data layer); in the
# Python port it is any object exposing `store.entries.value` -> list of
# entries with `.name`, `.effectiveWritable`, `.resolvedHostPath` (duck-typed;
# the real `openminis.data` store provides these).
class MountedFolderCoordinator:
    """Read-only-mount enforcement + bind-mount spec snapshot."""

    @staticmethod
    def require_writable(linux_path: str, store: Any) -> None:
        if MountedFolderCoordinator.is_linux_path_under_read_only_mount(linux_path, store):
            raise ReadOnlyMountException(linux_path)

    @staticmethod
    def is_linux_path_under_read_only_mount(linux_path: str, store: Any) -> bool:
        if not linux_path.startswith(_MOUNTS_PREFIX):
            return False
        tail = linux_path[len(_MOUNTS_PREFIX):]
        # Match `<name>` or `<name>/...` exactly — don't treat
        # `/var/minis/mounts/foobar` as inside a mount named `foo`.
        name = tail.split("/", 1)[0]
        if name == "":
            return False
        # PORT: store.entries is a StateFlow; `.value` yields the current list.
        entries = store.entries.value
        entry = next((e for e in entries if e.name == name), None)
        if entry is None:
            return False
        return not entry.effective_writable

    @staticmethod
    def bind_mount_specs(store: Any) -> list[tuple[str, str]]:
        """Snapshot of bind-mount specs PRoot should inject.

        Returns ``(linuxDir, hostPath)`` pairs. Only entries with a resolved
        POSIX path are returned.
        """
        result: list[tuple[str, str]] = []
        entries = store.entries.value
        for e in entries:
            host = e.resolved_host_path
            if host is None:
                continue
            result.append((f"{_MOUNTS_PREFIX}{e.name}", host))
        return result


class ReadOnlyMountException(Exception):
    """Thrown when a write tool is asked to modify a path inside a locked mount."""

    def __init__(self, linux_path: str) -> None:
        super().__init__(
            f"{linux_path} is inside a read-only mounted folder and cannot be modified. "
            "Toggle writability in Settings → Mount External Folders if this is a mistake."
        )
        self.linux_path = linux_path
