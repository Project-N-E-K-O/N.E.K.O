from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., object])
UI_CONTEXT_META_ATTR = "__neko_ui_context__"
UI_ACTION_META_ATTR = "__neko_ui_action__"


def _sync_ui_meta_to_wrapped(fn: Callable[..., object], attr: str, meta: dict[str, Any]) -> None:
    """Attach UI metadata to a callable and its wrapper chain."""
    current: Any = fn
    seen: set[int] = set()
    while callable(current) and id(current) not in seen:
        seen.add(id(current))
        try:
            setattr(current, attr, dict(meta))
        except Exception:
            pass
        current = getattr(current, "__wrapped__", None)


def context(*, id: str = "main", title: str | None = None) -> Callable[[F], F]:
    """Declare a lightweight UI context provider for hosted surfaces."""

    def decorator(fn: F) -> F:
        meta = {
            "id": str(id or "main"),
            "title": title,
        }
        _sync_ui_meta_to_wrapped(fn, UI_CONTEXT_META_ATTR, meta)
        return fn

    return decorator


def action(
    *,
    id: str | None = None,
    label: object | None = None,
    icon: str | None = None,
    tone: str = "default",
    group: str | None = None,
    order: int = 0,
    confirm: bool | str | Mapping[str, object] = False,
    refresh_context: bool = True,
) -> Callable[[F], F]:
    """Attach UI metadata to an existing plugin entry."""

    def decorator(fn: F) -> F:
        meta = {
            "id": id,
            "label": label,
            "icon": icon,
            "tone": tone,
            "group": group,
            "order": int(order),
            "confirm": confirm,
            "refresh_context": bool(refresh_context),
        }
        _sync_ui_meta_to_wrapped(fn, UI_ACTION_META_ATTR, meta)
        return fn

    return decorator


__all__ = ["UI_CONTEXT_META_ATTR", "UI_ACTION_META_ATTR", "context", "action"]
