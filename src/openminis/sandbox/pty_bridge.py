"""PTY bridge to a real TTY for the spawned proot/shell process.

Ported from: src/android/app/src/main/java/com/openminis/app/sandbox/PtyBridge.kt
Original package: com.openminis.app.sandbox

JNI bridge to bionic libc's forkpty() + termios + waitpid on the Android side.
PORT: the original is an ``external fun`` JNI binding to a native ``libpty_bridge``.
On Python we re-implement the same surface with the stdlib ``pty``/``os``/
``signal``/``fcntl`` primitives. The child is forked with ``os.openpty`` +
``os.fork`` and execve'd into ``cmd``, giving a real TTY so readline, PS1,
arrow keys and Ctrl+C (SIGINT) all work — mirroring iOS ISHKernel behaviour.

Windows note: ``pty``, ``fcntl`` and ``os.fork`` do not exist on Win32. The
module imports cleanly (the native imports are guarded), but the bridge raises
``RuntimeError`` at call time there. A production Windows build would swap this
for a ConPTY-based backend; that is tracked separately and out of scope here.
"""

from __future__ import annotations

import os
import signal
import sys

__all__ = ["PtyBridge", "PtyBridgeError"]

try:  # pragma: no cover - platform dependent
    import fcntl
    import pty
    import termios
    _HAS_PTY = True
except Exception:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]
    pty = None  # type: ignore[assignment]
    termios = None  # type: ignore[assignment]
    _HAS_PTY = False


class PtyBridgeError(RuntimeError):
    """Raised when the PTY backend is unavailable (e.g. Windows) or misbehaves."""


def _require_native() -> None:
    if not _HAS_PTY:
        raise PtyBridgeError(
            "PtyBridge requires POSIX pty/fork (available on Linux/macOS); "
            "this platform (Windows) has no os.fork/pty module."
        )


class PtyBridge:
    """Equivalent of the Kotlin ``object PtyBridge`` JNI singleton."""

    # PORT: Kotlin System.loadLibrary("pty_bridge") ran in the object's init
    # block. On Python the backend is the stdlib; nothing to preload.
    init_done = _HAS_PTY

    @staticmethod
    def fork_exec(
        cmd: str,
        argv: list[str],
        envp: list[str],
        cwd: str | None,
        cols: int,
        rows: int,
        out_pid: list[int],
    ) -> int:
        """Fork a child process attached to a new PTY and execve() into ``cmd``.

        Returns the PTY master fd on success, or ``-errno`` on failure.
        ``out_pid`` (single-element list) receives the child pid.
        """
        _require_native()
        assert pty is not None and fcntl is not None and termios is not None
        try:
            master_fd, slave_fd = os.openpty()
            # Disable the master side's blocking-on-empty so reads return 0/EOF
            # promptly when the child exits.
            pid = os.fork()
        except OSError as e:
            return -e.errno if e.errno else -1

        if pid == 0:
            # ---- child ----
            try:
                os.close(master_fd)
                os.setsid()
                os.dup2(slave_fd, 0)
                os.dup2(slave_fd, 1)
                os.dup2(slave_fd, 2)
                if slave_fd > 2:
                    os.close(slave_fd)
                if cwd:
                    os.chdir(cwd)
                # Best-effort: adopt the requested window size on the slave.
                try:
                    _set_winsize_fd(slave_fd, cols, rows)
                except OSError:
                    pass
                env = dict(item.split("=", 1) for item in envp)
                os.execve(argv[0], argv, env)
            except Exception:
                os._exit(127)
        # ---- parent ----
        os.close(slave_fd)
        try:
            _set_winsize_fd(master_fd, cols, rows)
        except OSError:
            pass
        out_pid[0] = pid
        return master_fd

    @staticmethod
    def read_bytes(fd: int, buf: bytearray, off: int, length: int) -> int:
        """Read from PTY master into ``buf[off:off+length]``.

        Returns bytes read, 0 on EOF, or ``-errno``.
        """
        _require_native()
        try:
            data = os.read(fd, length)
        except OSError as e:
            if e.errno == 0:
                return 0
            return -e.errno if e.errno else -1
        if not data:
            return 0
        buf[off:off + len(data)] = data
        return len(data)

    @staticmethod
    def write_bytes(fd: int, buf: bytes, off: int, length: int) -> int:
        """Write to PTY master. Returns bytes written, or ``-errno``.

        Blocks until all ``length`` bytes are written (matches the JNI contract).
        """
        _require_native()
        view = buf[off:off + length]
        total = 0
        while total < len(view):
            try:
                n = os.write(fd, view[total:])
            except OSError as e:
                if total > 0:
                    return total
                return -e.errno if e.errno else -1
            if n <= 0:
                return total
            total += n
        return total

    @staticmethod
    def set_window_size(fd: int, cols: int, rows: int) -> int:
        """ioctl(TIOCSWINSZ). Returns 0 or ``-errno``."""
        _require_native()
        try:
            _set_winsize_fd(fd, cols, rows)
            return 0
        except OSError as e:
            return -e.errno if e.errno else -1

    @staticmethod
    def close_fd(fd: int) -> int:
        """Close the PTY master fd. Returns 0 or ``-errno``."""
        try:
            os.close(fd)
            return 0
        except OSError as e:
            return -e.errno if e.errno else -1

    @staticmethod
    def send_signal(pid: int, sig: int) -> int:
        """kill(pid, sig). Returns 0 or ``-errno``."""
        try:
            os.kill(pid, sig)
            return 0
        except OSError as e:
            return -e.errno if e.errno else -1

    @staticmethod
    def wait_for(pid: int) -> int:
        """waitpid(pid, &status, 0). Blocks.

        Returns WEXITSTATUS on normal exit, ``-(128+sig)`` on signal,
        ``-errno`` on failure.
        """
        import errno

        try:
            _, status = os.waitpid(pid, 0)
        except OSError as e:
            return -e.errno if e.errno else -1
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return -(128 + os.WTERMSIG(status))
        return -errno.EINVAL


def _set_winsize_fd(fd: int, cols: int, rows: int) -> None:
    if termios is None or fcntl is None:
        return
    import struct

    # TIOCSWINSZ = 0x5414 on Linux; termios provides it via fcntl on some
    # platforms, but we set the winsize struct directly for portability.
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
    except (OSError, AttributeError):
        # Windows / unsupported platform — best effort only.
        pass
