from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

F = TypeVar("F", bound=Callable[..., object])
UI_CONTEXT_META_ATTR = "__neko_ui_context__"


def context(*, id: str = "main", title: str | None = None) -> Callable[[F], F]:
    """Declare a lightweight UI context provider for hosted surfaces."""

    def decorator(fn: F) -> F:
        setattr(fn, UI_CONTEXT_META_ATTR, {
            "id": str(id or "main"),
            "title": title,
        })
        return fn

    return decorator


__all__ = ["UI_CONTEXT_META_ATTR", "context"]
