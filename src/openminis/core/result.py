"""Result type mirroring Kotlin's ``Result<T>`` / ``runCatching``.

Ported from: ``kotlin.Result`` usage across ``com.openminis.app``.

The Android code leans heavily on ``runCatching { }.getOrElse { }``,
``.getOrNull()``, ``.onSuccess`` / ``.onFailure`` chains for network calls,
JSON parsing and sandbox shell work. Python's exception story is different, so
we keep *both* idioms available:

* :func:`Result.of` — wraps a callable, never raises (``runCatching``).
* :meth:`Result.unwrap` — raises the captured error (``getOrThrow``).
* :meth:`Result.ok_or_none` — ``getOrNull()``.

Do **not** convert existing ``try/except`` blocks into ``Result`` — port them
as they are. Use ``Result`` only where the original used ``Result``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any, Generic, TypeVar

T = TypeVar("T")
U = TypeVar("U")
E = TypeVar("E", bound=BaseException)

__all__ = ["Result", "Ok", "Err"]


class Result(Generic[T]):
    """Immutable success/failure container."""

    __slots__ = ("_value", "_error")

    def __init__(self, value: T | None = None, error: BaseException | None = None) -> None:
        self._value = value
        self._error = error

    # --- constructors ---------------------------------------------------
    @staticmethod
    def of(fn: Callable[[], T], *catch: type[BaseException]) -> Result[T]:
        """Kotlin ``runCatching { fn() }``.

        ``catch`` defaults to ``Exception`` (Kotlin's ``runCatching`` catches
        ``Throwable``, but we deliberately let ``BaseException`` —
        ``KeyboardInterrupt`` / ``SystemExit`` / ``anyio`` cancellation —
        propagate, matching the original's treatment of ``CancellationException``.
        """
        try:
            return Ok(fn())
        except (catch or (Exception,)) as e:  # type: ignore[misc]
            return Err(e)

    # --- inspection -----------------------------------------------------
    @property
    def is_ok(self) -> bool:
        return self._error is None

    @property
    def is_err(self) -> bool:
        return self._error is not None

    @property
    def error(self) -> BaseException | None:
        """Kotlin: ``exceptionOrNull()``."""
        return self._error

    # --- extraction -----------------------------------------------------
    def unwrap(self) -> T:
        """Kotlin: ``getOrThrow()``."""
        if self._error is not None:
            raise self._error
        return self._value  # type: ignore[return-value]

    def ok_or_none(self) -> T | None:
        """Kotlin: ``getOrNull()``."""
        return None if self._error is not None else self._value  # type: ignore[return-value]

    def get_or_else(self, default: U) -> T | U:
        """Kotlin: ``getOrElse { default }``."""
        return default if self._error is not None else self._value  # type: ignore[return-value]

    def get_or_else_with(self, fn: Callable[[BaseException], U]) -> T | U:
        """Kotlin: ``getOrElse { e -> ... }`` with the error in scope."""
        if self._error is None:
            return self._value  # type: ignore[return-value]
        return fn(self._error)

    def expect(self, message: str) -> T:
        """Kotlin: ``getOrThrow()`` with a custom message (Rust-style ``expect``)."""
        if self._error is not None:
            raise RuntimeError(f"{message}: {self._error}") from self._error
        return self._value  # type: ignore[return-value]

    # --- combinators ----------------------------------------------------
    def map(self, fn: Callable[[T], U]) -> Result[U]:
        """Kotlin: ``map { }``."""
        if self._error is not None:
            return Err(self._error)  # type: ignore[return-value]
        return Result.of(lambda: fn(self._value))  # type: ignore[arg-type,return-value]

    def flat_map(self, fn: Callable[[T], Result[U]]) -> Result[U]:
        if self._error is not None:
            return Err(self._error)  # type: ignore[return-value]
        return fn(self._value)  # type: ignore[arg-type]

    def map_error(self, fn: Callable[[BaseException], BaseException]) -> Result[T]:
        if self._error is None:
            return self
        return Err(fn(self._error))

    def on_success(self, fn: Callable[[T], Any]) -> Result[T]:
        """Kotlin: ``onSuccess { }`` — pass-through."""
        if self._error is None:
            fn(self._value)  # type: ignore[arg-type]
        return self

    def on_failure(self, fn: Callable[[BaseException], Any]) -> Result[T]:
        """Kotlin: ``onFailure { }`` — pass-through."""
        if self._error is not None:
            fn(self._error)
        return self

    def recover(self, fn: Callable[[BaseException], U]) -> Result[T | U]:
        """Kotlin: ``recover { }``."""
        if self._error is None:
            return self
        return Ok(fn(self._error))

    # --- pythonic extras ------------------------------------------------
    def __iter__(self) -> Iterator[Any]:
        """Allows ``ok, value = result`` destructuring in tests."""
        return iter((self._error is None, self._value if self._error is None else self._error))

    def __bool__(self) -> bool:
        return self._error is None

    def __repr__(self) -> str:
        if self._error is None:
            return f"Ok({self._value!r})"
        return f"Err({type(self._error).__name__}: {self._error})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Result):
            return NotImplemented
        return self._value == other._value and self._error == other._error

    def __hash__(self) -> int:  # pragma: no cover - rarely hashed
        return hash((self._value, self._error))


class Ok(Result[T]):
    """Kotlin: ``Result.success(value)``."""

    def __init__(self, value: T) -> None:
        super().__init__(value=value, error=None)


class Err(Result[T]):
    """Kotlin: ``Result.failure(e)``.

    Also accepts a plain string for ergonomics; it becomes a ``RuntimeError``
    so ``.error`` is always a real exception.
    """

    def __init__(self, error: BaseException | str) -> None:
        if isinstance(error, str):
            error = RuntimeError(error)
        super().__init__(value=None, error=error)
