"""Host-side endpoint of the proot ``native_offload`` extension.

Ported from: src/android/app/src/main/java/com/openminis/app/sandbox/NativeOffload.kt
Original package: com.openminis.app.sandbox

The guest issues ``execve("<handler-name>", argv, envp)``; the proot extension
sends argv/env/cwd over an abstract unix socket to this server. The server
dispatches to the registered ``NativeOffloadHandler``, writes the handler's
combined output into a tmpfile inside the guest's ``/tmp``, and replies with
``(exit_code, guest_tmpfile_path)``.

PORT: the original uses Android's ``LocalServerSocket`` (an *abstract* unix
socket). On Python we use ``socket.AF_UNIX`` with a *filesystem* socket path
under the rootfs tmp dir (abstract sockets are unavailable on Windows and
behave differently on macOS). The wire protocol (little-endian magic + ints +
length-prefixed strings) is preserved byte-for-byte.
"""

from __future__ import annotations

import os
import struct
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openminis.core.logging import Log

__all__ = [
    "NativeOffloadRequest",
    "NativeOffloadResult",
    "NativeOffloadHandler",
    "NativeOffloadServer",
]

_MAGIC_REQ = 0x46464F4E  # 'N' 'O' 'F' 'F' little-endian
_MAGIC_RSP = 0x52464F4E  # 'N' 'O' 'F' 'R'
_VERSION = 1


@dataclass
class NativeOffloadRequest:
    pid: int
    argv: list[str]
    env: dict[str, str]
    cwd: str
    # T340: chat session id forwarded by the agent shell via the
    # `MINIS_CHAT_SESSION_ID` env var.
    session_id: str | None = None


@dataclass
class NativeOffloadResult:
    exit_code: int
    output: str


# PORT: Kotlin `fun interface NativeOffloadHandler` -> Python callable protocol.
NativeOffloadHandler = Callable[[NativeOffloadRequest], NativeOffloadResult]


@dataclass
class _Registered:
    name: str
    handler: NativeOffloadHandler


class NativeOffloadServer:
    """Host-side native_offload dispatcher (singleton)."""

    _TAG = "NativeOffloadServer"
    _SOCKET_NAME = "native-offload"
    # [T-android-offload-tmp-leak] Filename prefix of a handler reply file.
    _REPLY_PREFIX = ".native-offload-"
    # How long a reply file may live before the in-session sweep may remove it.
    _REPLY_TTL_MS = 10 * 60 * 1000
    # Run the opportunistic sweep every N replies, not on every single one.
    _SWEEP_EVERY_N_REPLIES = 50

    socket_name: str = _SOCKET_NAME

    def __init__(self) -> None:
        self._handlers: dict[str, NativeOffloadHandler] = {}
        self._counter = 0
        self._lock = threading.Lock()
        self._server_socket: Any | None = None
        self._accept_thread: threading.Thread | None = None
        self._rootfs_tmp_dir: Path | None = None
        self._sock_path: Path | None = None

    @property
    def registered_handlers(self) -> set[str]:
        with self._lock:
            return set(self._handlers.keys())

    def register(self, name: str, handler: NativeOffloadHandler) -> None:
        if name == "":
            raise ValueError("handler name must not be empty")
        with self._lock:
            self._handlers[name] = handler
        Log.d(self._TAG, f"register '{name}' (total={len(self._handlers)})")

    def start(self, rootfs_dir: Path) -> None:
        """Bind the unix socket and start the accept loop."""
        self._rootfs_tmp_dir = Path(rootfs_dir) / "tmp"
        with self._lock:
            if self._server_socket is not None:
                return

            # T287-followup: bind with bounded retry. Linux abstract sockets are
            # freed by the kernel only after the owning process is fully reaped;
            # we map to a filesystem socket, so a stale file from a previous run
            # is the equivalent hazard — unlink before bind.
            sock = self._bind_with_retry()
            if sock is None:
                raise OSError(
                    f"failed to bind unix socket '{self._SOCKET_NAME}' after retries — "
                    "previous process holding the socket?"
                )
            self._server_socket = sock
            self._accept_thread = threading.Thread(
                target=self._run_accept_loop, name="native-offload-accept", daemon=True
            )
            self._accept_thread.start()
        Log.i(
            self._TAG,
            f"listening on unix socket '{self._SOCKET_NAME}' "
            f"handlers={sorted(self._handlers.keys())} tmpDir={self._rootfs_tmp_dir}",
        )
        # [T-android-offload-tmp-leak] Sweep reply files orphaned by earlier runs.
        self._sweep_stale_replies(all_=True)

    def _bind_with_retry(self) -> Any | None:
        import socket

        # Filesystem socket path (abstract-namespace equivalent on this host).
        self._sock_path = self._rootfs_tmp_dir / f".{self._SOCKET_NAME}.sock"
        delays = [0, 0.05, 0.1, 0.2, 0.4, 0.8]
        for attempt, delay in enumerate(delays):
            if delay > 0:
                time_sleep(delay)
            try:
                if self._sock_path.exists():
                    self._sock_path.unlink()
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.bind(str(self._sock_path))
                s.listen(8)
                return s
            except OSError as e:
                Log.w(self._TAG, f"bind attempt {attempt + 1}/{len(delays)} failed: {e}")
        return None

    def stop(self) -> None:
        with self._lock:
            try:
                if self._server_socket is not None:
                    self._server_socket.close()
            except Exception:
                pass
            self._server_socket = None
            self._accept_thread = None
            if self._sock_path is not None and self._sock_path.exists():
                try:
                    self._sock_path.unlink()
                except OSError:
                    pass

    def _run_accept_loop(self) -> None:
        import socket

        while True:
            assert self._server_socket is not None
            try:
                client, _ = self._server_socket.accept()
            except OSError as e:
                Log.i(self._TAG, f"accept loop terminated: {e}")
                return
            Log.d(self._TAG, "accepted client from proot extension")
            t = threading.Thread(
                target=self._handle_client, args=(client,), name="native-offload-worker",
                daemon=True,
            )
            t.start()

    def _handle_client(self, client: Any) -> None:
        try:
            with client:
                reader = _LEReader(client.makefile("rwb"))
                magic = reader.read_le_int()
                if magic != _MAGIC_REQ:
                    Log.w(self._TAG, f"bad magic: 0x{magic & 0xFFFFFFFF:X}")
                    return
                version = reader.read_le_int()
                if version != _VERSION:
                    Log.w(self._TAG, f"unsupported version {version}")
                    return

                pid = reader.read_le_int()
                argc = reader.read_le_int()
                if argc < 0 or argc > 256:
                    raise IllegalStateException(f"bad argc={argc}")
                argv: list[str] = [reader.read_le_string() for _ in range(argc)]

                envc = reader.read_le_int()
                if envc < 0 or envc > 4096:
                    raise IllegalStateException(f"bad envc={envc}")
                env: dict[str, str] = {}
                for _ in range(envc):
                    s = reader.read_le_string()
                    eq = s.find("=")
                    if eq >= 0:
                        env[s[:eq]] = s[eq + 1:]
                    else:
                        env[s] = ""

                cwd = reader.read_le_string()

                name = (argv[0] if argv else "").rsplit("/", 1)[-1]
                Log.d(
                    self._TAG,
                    f"recv pid={pid} name='{name}' argc={argc} argv={argv} "
                    f"cwd={cwd} envc={envc}",
                )

                import time
                t0 = time.monotonic_ns()

                handler = self._handlers.get(name)
                if handler is None:
                    Log.w(self._TAG, f"no handler registered for '{name}' (known={list(self._handlers)})")
                    result = NativeOffloadResult(
                        exit_code=127, output=f"native_offload: no handler for '{name}'\n"
                    )
                else:
                    try:
                        result = handler(
                            NativeOffloadRequest(
                                pid=pid,
                                argv=argv,
                                env=env,
                                cwd=cwd,
                                session_id=env.get("MINIS_CHAT_SESSION_ID") or None,
                            )
                        )
                    except Exception as e:  # noqa: BLE001
                        Log.w(self._TAG, f"handler '{name}' threw: {e}", e)
                        result = NativeOffloadResult(
                            exit_code=1, output=f"native_offload: {e}\n"
                        )
                elapsed_ms = (time.monotonic_ns() - t0) // 1_000_000

                tmp_dir = self._rootfs_tmp_dir
                if tmp_dir is None:
                    raise IllegalStateException("server not started")
                tmp_dir.mkdir(parents=True, exist_ok=True)
                self._counter += 1
                seq = self._counter
                tmp_host = tmp_dir / f"{self._REPLY_PREFIX}{pid}-{seq}"
                tmp_host.write_text(result.output, encoding="utf-8")
                tmp_guest = f"/tmp/{tmp_host.name}"

                # [T-android-offload-tmp-leak] Bound growth WITHIN a process.
                if seq % self._SWEEP_EVERY_N_REPLIES == 0:
                    self._sweep_stale_replies(all_=False)

                Log.d(
                    self._TAG,
                    f"reply name='{name}' exit={result.exit_code} "
                    f"outBytes={len(result.output)} tmpGuest={tmp_guest} elapsed={elapsed_ms}ms",
                )

                writer = _LEWriter(client.makefile("rwb"))
                writer.write_le_int(_MAGIC_RSP)
                writer.write_le_int(result.exit_code)
                writer.write_le_string(tmp_guest)
        except Exception as e:  # noqa: BLE001
            Log.w(self._TAG, f"worker error: {e}", e)

    def _sweep_stale_replies(self, all_: bool) -> None:
        import time

        tmp_dir = self._rootfs_tmp_dir
        if tmp_dir is None or not tmp_dir.exists():
            return
        try:
            cutoff = time.time() * 1000 - self._REPLY_TTL_MS
            removed = 0
            freed = 0
            for f in tmp_dir.iterdir():
                if not f.is_file() or not f.name.startswith(self._REPLY_PREFIX):
                    continue
                if not all_ and f.stat().st_mtime * 1000 > cutoff:
                    continue
                try:
                    size = f.stat().st_size
                    f.unlink()
                    removed += 1
                    freed += size
                except OSError:
                    pass
            if removed > 0:
                Log.i(
                    self._TAG,
                    f"swept {removed} stale reply file(s), freed {freed // 1024}KB (all={all_})",
                )
        except Exception as e:  # noqa: BLE001
            Log.w(self._TAG, f"sweepStaleReplies failed: {e}")


# Module-level singleton mirroring the Kotlin `object NativeOffloadServer`.
NativeOffloadServer_instance = NativeOffloadServer()


class IllegalStateException(Exception):
    """PORT: Kotlin IllegalStateException, surfaced by the protocol checks."""


# ---- little-endian helpers ------------------------------------------------

def time_sleep(seconds: float) -> None:
    import time
    time.sleep(seconds)


class _LEReader:
    def __init__(self, stream: Any) -> None:
        self._s = stream

    def read_le_int(self) -> int:
        buf = self._s.read(4)
        if len(buf) < 4:
            raise EOFError("short read for LE int")
        return struct.unpack("<i", buf)[0]

    def read_le_string(self) -> str:
        length = self.read_le_int()
        if length < 0 or length > (1 << 20):
            raise IllegalStateException(f"bad string len {length}")
        if length == 0:
            return ""
        buf = b""
        while len(buf) < length:
            chunk = self._s.read(length - len(buf))
            if not chunk:
                raise EOFError("short read for LE string")
            buf += chunk
        return buf.decode("utf-8", errors="replace")


class _LEWriter:
    def __init__(self, stream: Any) -> None:
        self._s = stream

    def write_le_int(self, v: int) -> None:
        self._s.write(struct.pack("<i", v))

    def write_le_string(self, s: str) -> None:
        data = s.encode("utf-8")
        self.write_le_int(len(data))
        if data:
            self._s.write(data)
