from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

F = TypeVar("F", bound=Callable[..., object])
UI_CONTEXT_META_ATTR = "__neko_ui_context__"
UI_ACTION_META_ATTR = "__neko_ui_action__"


def context(*, id: str = "main", title: str | None = None) -> Callable[[F], F]:
    """Declare a lightweight UI context provider for hosted surfaces."""

    def decorator(fn: F) -> F:
        setattr(fn, UI_CONTEXT_META_ATTR, {
            "id": str(id or "main"),
            "title": title,
        })
        return fn

    return decorator


def action(
    *,
    id: str | None = None,
    label: str | None = None,
    icon: str | None = None,
    tone: str = "default",
    group: str | None = None,
    order: int = 0,
    confirm: bool | str = False,
    refresh_context: bool = True,
) -> Callable[[F], F]:
    """Attach UI metadata to an existing plugin entry."""

    def decorator(fn: F) -> F:
        setattr(fn, UI_ACTION_META_ATTR, {
            "id": id,
            "label": label,
            "icon": icon,
            "tone": tone,
            "group": group,
            "order": int(order),
            "confirm": confirm,
            "refresh_context": bool(refresh_context),
        })
        return fn

    return decorator


__all__ = ["UI_CONTEXT_META_ATTR", "UI_ACTION_META_ATTR", "context", "action"]
