"""Small, dependency-free helpers for the EmbeddingService singleton."""
from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar


T = TypeVar("T")


def get_or_create(current: T | None, factory: Callable[[], T]) -> T:
    """Return ``current`` or lazily construct the process service."""
    return current if current is not None else factory()


def reset_for_tests() -> None:
    """Return the empty singleton state used by the public test hook."""
    return None
