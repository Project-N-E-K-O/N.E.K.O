"""Rust-like Result primitives for SDK v2.

This module provides a lightweight `Result` model for dual usage styles:
- explicit pattern matching / branching (Ok / Err)
- exception-style flow via `must()` / `unwrap()`
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, Literal, TypeAlias, TypeVar

T = TypeVar("T")
U = TypeVar("U")
E = TypeVar("E")
F = TypeVar("F")


class ResultError(RuntimeError):
    """Raised when unwrapping an `Err` result."""

    def __init__(self, error: object, message: str | None = None):
        self.error = error
        self.code = None
        self.details = None

        msg = message
        if msg is None:
            if isinstance(error, dict):
                maybe_error = error.get("error")
                if isinstance(maybe_error, dict):
                    self.code = maybe_error.get("code")
                    self.details = maybe_error.get("details")
                    msg = str(maybe_error.get("message") or maybe_error.get("code") or "Result error")
                else:
                    self.code = error.get("code")
                    self.details = error.get("details")
                    msg = str(error.get("message") or error.get("error") or error)
            else:
                msg = str(error)
        super().__init__(msg)


@dataclass(frozen=True, slots=True)
class Ok(Generic[T]):
    """Successful result value."""

    __match_args__ = ("value",)

    value: T

    def is_ok(self) -> Literal[True]:
        return True

    def is_err(self) -> Literal[False]:
        return False

    def ok(self) -> T:
        return self.value

    def err(self) -> None:
        return None

    def map(self, fn: Callable[[T], U]) -> Ok[U]:
        return Ok(fn(self.value))

    def map_err(self, _fn: Callable[[object], F]) -> Ok[T]:
        return self

    def bind(self, fn: Callable[[T], Result[U, F]]) -> Result[U, F]:
        return fn(self.value)

    def unwrap(self) -> T:
        return self.value

    def unwrap_or(self, _default: U) -> T:
        return self.value

    def raise_for_err(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class Err(Generic[E]):
    """Failed result value."""

    __match_args__ = ("error",)

    error: E

    def is_ok(self) -> Literal[False]:
        return False

    def is_err(self) -> Literal[True]:
        return True

    def ok(self) -> None:
        return None

    def err(self) -> E:
        return self.error

    def map(self, _fn: Callable[[object], U]) -> Err[E]:
        return self

    def map_err(self, fn: Callable[[E], F]) -> Err[F]:
        return Err(fn(self.error))

    def bind(self, _fn: Callable[[object], Result[U, F]]) -> Result[U, E | F]:
        return self

    def unwrap(self) -> object:
        if isinstance(self.error, Exception):
            raise self.error
        raise ResultError(self.error)

    def unwrap_or(self, default: U) -> U:
        return default

    def raise_for_err(self) -> None:
        if isinstance(self.error, Exception):
            raise self.error
        raise ResultError(self.error)


Result: TypeAlias = Ok[T] | Err[E]


def is_ok(result: Result[T, E]) -> bool:
    return isinstance(result, Ok)


def is_err(result: Result[T, E]) -> bool:
    return isinstance(result, Err)


def map_result(result: Result[T, E], fn: Callable[[T], U]) -> Result[U, E]:
    if isinstance(result, Ok):
        return Ok(fn(result.value))
    return result


def map_err_result(result: Result[T, E], fn: Callable[[E], F]) -> Result[T, F]:
    if isinstance(result, Err):
        return Err(fn(result.error))
    return result


def bind_result(result: Result[T, E], fn: Callable[[T], Result[U, F]]) -> Result[U, E | F]:
    if isinstance(result, Ok):
        return fn(result.value)
    return result


def unwrap(result: Result[T, E]) -> T:
    if isinstance(result, Ok):
        return result.value
    if isinstance(result.error, Exception):
        raise result.error
    raise ResultError(result.error)


def unwrap_or(result: Result[T, E], default: U) -> T | U:
    if isinstance(result, Ok):
        return result.value
    return default


def raise_for_err(result: Result[T, E]) -> None:
    if isinstance(result, Ok):
        return None
    if isinstance(result.error, Exception):
        raise result.error
    raise ResultError(result.error)


def must(result: Result[T, E]) -> T:
    """Rust-like `?` helper: return value or raise `ResultError`."""

    return unwrap(result)


def match_result(
    result: Result[T, E],
    on_ok: Callable[[T], U],
    on_err: Callable[[E], U],
) -> U:
    if isinstance(result, Ok):
        return on_ok(result.value)
    return on_err(result.error)


def capture(fn: Callable[[], T]) -> Result[T, Exception]:
    """Execute `fn` and convert exceptions into `Err(Exception)`."""
    try:
        return Ok(fn())
    except Exception as e:  # pragma: no cover - simple wrapper
        return Err(e)
