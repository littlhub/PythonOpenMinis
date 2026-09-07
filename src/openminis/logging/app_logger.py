"""Logging helpers mirroring two Android sources:

* ``com.openminis.app.logging.AppLogger``  → :class:`AppLogger` (info/warning/error/debug)
* ``android.util.Log``                     → :class:`Log`        (d/i/w/e)

Ported from: src/android/app/src/main/java/com/openminis/app/logging/AppLogger.kt
and the platform ``android.util.Log`` calls scattered through ``com.openminis.app``.

Both delegate to :func:`openminis.core.logging.get_logger`. They exist so the
ported files can keep their original ``AppLogger.info(TAG, msg)`` / ``Log.e(TAG,
msg, t)`` call shapes instead of a noisy grep-replace.
"""

from __future__ import annotations

import logging
from typing import Any

from openminis.core.logging import get_logger

__all__ = ["AppLogger", "Log"]


class AppLogger:
    """Mirror of ``com.openminis.app.logging.AppLogger``.

    Static methods only — the original is a stateless object too.
    """

    @staticmethod
    def info(tag: str, msg: str) -> None:
        get_logger(tag).info(msg)

    @staticmethod
    def warning(tag: str, msg: str) -> None:
        get_logger(tag).warning(msg)

    @staticmethod
    def error(tag: str, msg: str) -> None:
        get_logger(tag).error(msg)

    @staticmethod
    def debug(tag: str, msg: str) -> None:
        get_logger(tag).debug(msg)


class Log:
    """Mirror of ``android.util.Log`` (d/i/w/e/v)."""

    @staticmethod
    def d(tag: str, msg: str) -> None:
        get_logger(tag).debug(msg)

    @staticmethod
    def i(tag: str, msg: str) -> None:
        get_logger(tag).info(msg)

    @staticmethod
    def w(tag: str, msg: str) -> None:
        get_logger(tag).warning(msg)

    @staticmethod
    def e(tag: str, msg: str, tr: Any | None = None) -> None:
        if tr is None:
            get_logger(tag).error(msg)
        else:
            get_logger(tag).exception(f"{msg}: {tr}")

    @staticmethod
    def v(tag: str, msg: str) -> None:
        get_logger(tag).debug(msg)
