"""Choose a shell-execution timeout based on the command prefix.

Ported from: src/android/app/src/main/java/com/openminis/app/sandbox/ShellTimeoutPolicy.kt
Original package: com.openminis.app.sandbox

Matching the tiered strategy iOS uses in ISHExecutionCoordinator. Agent calls
tend to cluster around a few command families with wildly different expected
durations.
"""

from __future__ import annotations

__all__ = ["ShellTimeoutPolicy"]

# PORT: Kotlin `object ShellTimeoutPolicy` -> Python class with static methods.
class ShellTimeoutPolicy:
    """Tiered shell-execution timeout policy keyed on command prefix."""

    # Default (baseline) timeout matching the existing call-site default.
    DEFAULT_TIMEOUT_MS = 600_000  # 10 min

    # Quick tier — shell builtins, pure text utilities, metadata queries.
    QUICK_TIMEOUT_MS = 60_000  # 1 min

    # Interactive / networked queries (ping, dig, curl one-shots).
    NETWORK_TIMEOUT_MS = 180_000  # 3 min

    # Package installs — apk / pip / npm / gem / cargo install.
    INSTALL_TIMEOUT_MS = 600_000  # 10 min

    # Build / compile — make, cargo build, go build, gradle, ninja.
    BUILD_TIMEOUT_MS = 1_200_000  # 20 min

    # Long-running services / daemons — always allowed the full budget.
    LONG_RUNNING_TIMEOUT_MS = 1_800_000  # 30 min

    _quick_prefixes = set(
        "ls cat head tail wc grep rg egrep fgrep "
        "sed awk tr cut sort uniq find "
        "echo printf pwd which whoami id env date "
        "basename dirname file stat du df "
        "mkdir rmdir touch chmod chown cp mv rm ln".split()
    )

    _network_prefixes = set(
        "ping dig nslookup host traceroute "
        "curl wget http "
        "ssh scp rsync".split()
    )

    # Install commands — any of these (possibly with subcommand) triggers.
    _install_tokens = set(
        "apk apt apt-get yum dnf pacman brew "
        "pip pip3 pipx "
        "npm pnpm yarn bun "
        "gem bundle "
        "go get go install".split()
    )

    # Build / compile drivers.
    _build_prefixes = set(
        "make gmake ninja cmake "
        "cargo rustc "
        "go build go test "
        "gradle gradlew mvn "
        "webpack vite rollup esbuild "
        "tsc swc babel".split()
    )

    # Long-running / daemon drivers.
    _long_running_prefixes = set(
        "watch "
        "node --watch npm run dev npm run start npm start "
        "python -m http.server "
        "serve http-server".split()
    )

    @staticmethod
    def for_command(command: str) -> int:
        """Return a recommended timeout (ms) for ``command``."""
        trimmed = command.strip()
        if trimmed == "":
            return ShellTimeoutPolicy.DEFAULT_TIMEOUT_MS
        lower = trimmed.lower()

        # Pipelines / shell-chains — take the first sub-command for classification.
        first_segment = (
            lower.split("&&", 1)[0].split("||", 1)[0].split(";", 1)[0].strip()
        )
        first_token = first_segment.split(" ", 1)[0]

        # Check longest-match phrases first (go build vs go).
        if any(first_segment.startswith(p) for p in ShellTimeoutPolicy._long_running_prefixes):
            return ShellTimeoutPolicy.LONG_RUNNING_TIMEOUT_MS
        if any(first_segment.startswith(p) for p in ShellTimeoutPolicy._build_prefixes):
            return ShellTimeoutPolicy.BUILD_TIMEOUT_MS
        if any(
            first_segment.startswith(it)
            and ShellTimeoutPolicy._contains_install_subcommand(first_segment)
            for it in ShellTimeoutPolicy._install_tokens
        ):
            return ShellTimeoutPolicy.INSTALL_TIMEOUT_MS

        if first_token in ShellTimeoutPolicy._network_prefixes:
            return ShellTimeoutPolicy.NETWORK_TIMEOUT_MS
        if first_token in ShellTimeoutPolicy._quick_prefixes:
            return ShellTimeoutPolicy.QUICK_TIMEOUT_MS

        return ShellTimeoutPolicy.DEFAULT_TIMEOUT_MS

    @staticmethod
    def _contains_install_subcommand(segment: str) -> bool:
        """Heuristic: treat bare `apk`, `pip` etc. without an install subcommand as neutral."""
        install_verbs = ["install", "add", "get", "update", "upgrade"]
        return any(f" {v}" in segment for v in install_verbs) or segment.startswith(
            "apk "
        ) or segment.startswith("pip install")
