"""Logging setup, replacing ``android.util.Log``.

Ported from: ``android.util.Log`` calls (``Log.d/w/e/i/v``) across
``com.openminis.app``, plus the file-sink behaviour in
``logging/`` and ``crash/CrashFileReporter.kt``.

Mapping:
* ``Log.v/d/i/w/e(TAG, msg)`` → ``logger.debug/info/warning/error``
* ``Log.e(TAG, msg, throwable)`` → ``logger.exception(...)``
* Logcat ring buffer → rotating file handler under ``AppContext.cache_dir``
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from .context import app_context

__all__ = ["get_logger", "setup_logging", "LogLevel", "MINIS_LOGGER_NAME"]

MINIS_LOGGER_NAME = "openminis"

# Kotlin Log.* priorities, kept for parity with logcat filtering.
LogLevel = int  # logging.DEBUG / INFO / WARNING / ERROR

_CONFIGURED = False


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a logger under the ``openminis`` namespace.

    Usage in ported files::

        from openminis.core.logging import get_logger
        logger = get_logger(__name__)

    replacing ``companion object { const val TAG = "..." }`` + ``Log.d``.
    """
    if name and not name.startswith(MINIS_LOGGER_NAME):
        name = f"{MINIS_LOGGER_NAME}.{name}" if name else MINIS_LOGGER_NAME
    return logging.getLogger(name or MINIS_LOGGER_NAME)


def setup_logging(
    level: int = logging.INFO,
    *,
    log_file: Path | None = None,
    max_bytes: int = 8 * 1024 * 1024,
    backup_count: int = 4,
    rich_console: bool = True,
) -> None:
    """Configure the ``openminis`` logger tree.

    Idempotent; safe to call from CLI, TUI and server entrypoints.
    """
    global _CONFIGURED
    root = logging.getLogger(MINIS_LOGGER_NAME)
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)

    # --- console ---------------------------------------------------------
    if rich_console:
        try:
            from rich.logging import RichHandler

            console_handler: logging.Handler = RichHandler(
                rich_tracebacks=True, show_path=False, markup=False
            )
        except Exception:  # pragma: no cover - rich optional at runtime
            console_handler = logging.StreamHandler(sys.stderr)
            console_handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
            )
    else:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
    console_handler.setLevel(level)
    root.addHandler(console_handler)

    # --- rotating file (logcat persistence parity) -----------------------
    path = log_file or (app_context().cache_dir / "logs" / "minis.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s [%(threadName)s]: %(message)s"
        )
    )
    file_handler.setLevel(logging.DEBUG)
    root.addHandler(file_handler)

    root.propagate = False
    _CONFIGURED = True


def is_configured() -> bool:
    return _CONFIGURED


class Log:
    """Python stand-in for ``android.util.Log``.

    The ported Kotlin uses ``Log.d/w/e/i/v(TAG, msg)`` (and
    ``Log.e(TAG, msg, throwable)``). We route them to the ``openminis`` logger
    tree under a ``<tag>`` child so logcat-style tags survive the port.

    PORT: Kotlin's ``Log.e(TAG, msg, throwable)`` maps to ``error`` with
    ``exc_info`` so the stack is attached — callers that passed a throwable in
    the original keep that behaviour here.
    """

    @staticmethod
    def _logger(tag: str) -> logging.Logger:
        return get_logger(f"{tag}")

    @staticmethod
    def v(tag: str, msg: str) -> None:
        Log._logger(tag).debug(msg)

    @staticmethod
    def d(tag: str, msg: str) -> None:
        Log._logger(tag).debug(msg)

    @staticmethod
    def i(tag: str, msg: str) -> None:
        Log._logger(tag).info(msg)

    @staticmethod
    def w(tag: str, msg: str) -> None:
        Log._logger(tag).warning(msg)

    @staticmethod
    def e(tag: str, msg: str, throwable: BaseException | None = None) -> None:
        Log._logger(tag).error(msg, exc_info=throwable)


class AppLogger:
    """Python stand-in for ``com.openminis.app.logging.AppLogger``.

    PORT: the original exposes ``AppLogger.info/warn/warning/error(tag, msg)``
    (the Android app's own wrapper over ``android.util.Log``). We forward to the
    ``openminis`` logger under ``openminis.<tag>``. Tag == logger name keeps the
    file-log routing identical to the Kotlin side.
    """

    @staticmethod
    def _logger(tag: str) -> logging.Logger:
        return get_logger(f"{tag}")

    @staticmethod
    def debug(tag: str, msg: str) -> None:
        AppLogger._logger(tag).debug(msg)

    @staticmethod
    def info(tag: str, msg: str) -> None:
        AppLogger._logger(tag).info(msg)

    @staticmethod
    def warn(tag: str, msg: str) -> None:
        AppLogger._logger(tag).warning(msg)

    @staticmethod
    def warning(tag: str, msg: str) -> None:
        # Kotlin side uses AppLogger.warning; alias kept for parity.
        AppLogger._logger(tag).warning(msg)

    @staticmethod
    def error(tag: str, msg: str, throwable: BaseException | None = None) -> None:
        AppLogger._logger(tag).error(msg, exc_info=throwable)
