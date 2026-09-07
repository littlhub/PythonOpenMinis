"""Decide when a proot child's death looks like a seccomp fast-path miscompile.

Ported from: src/android/app/src/main/java/com/openminis/app/sandbox/SeccompFallbackPolicy.kt
Original package: com.openminis.app.sandbox

[T-android-seccomp-selfheal / GH#186] Decide when a proot child's death looks
like the host kernel's seccomp fast path miscompiling our syscall filter, and
should be retried once with ``PROOT_NO_SECCOMP=1``.
"""

from __future__ import annotations

__all__ = ["SeccompFallbackPolicy"]

# PORT: Kotlin `object SeccompFallbackPolicy` -> Python class with static methods.
class SeccompFallbackPolicy:
    """Retry-once policy for suspected host-kernel seccomp miscompiles."""

    # Env var proot reads to skip installing its seccomp filter.
    NO_SECCOMP_ENV = "PROOT_NO_SECCOMP"
    NO_SECCOMP_VALUE = "1"

    # Upper bound on "died during startup". Generous on purpose.
    EARLY_DEATH_MS = 1_500

    # Shell/waitpid convention: a process killed by signal N reports 128+N.
    #   135 = 128+7  SIGBUS
    #   139 = 128+11 SIGSEGV
    #   132 = 128+4  SIGILL
    #   159 = 128+31 SIGSYS
    RETRYABLE_EXIT_CODES = frozenset({132, 135, 139, 159})

    @staticmethod
    def should_retry_without_seccomp(
        exit_code: int | None,
        duration_ms: int,
        produced_output: bool,
        already_retried: bool,
    ) -> bool:
        if already_retried:
            return False
        if produced_output:
            return False
        if exit_code is None or exit_code not in SeccompFallbackPolicy.RETRYABLE_EXIT_CODES:
            return False
        return duration_ms < SeccompFallbackPolicy.EARLY_DEATH_MS

    @staticmethod
    def signal_name(exit_code: int | None) -> str | None:
        return {
            132: "SIGILL",
            135: "SIGBUS",
            139: "SIGSEGV",
            159: "SIGSYS",
        }.get(exit_code)

    @staticmethod
    def retry_log_line(exit_code: int | None, duration_ms: int, what: str) -> str:
        sig = SeccompFallbackPolicy.signal_name(exit_code) or "signal"
        return (
            f"[proot-retry] detected early {sig} (exit={exit_code}) after {duration_ms}ms "
            f"in {what} — retrying once with "
            f"{SeccompFallbackPolicy.NO_SECCOMP_ENV}={SeccompFallbackPolicy.NO_SECCOMP_VALUE} (GH#186)"
        )
