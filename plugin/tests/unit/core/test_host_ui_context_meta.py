from __future__ import annotations

import functools

from plugin.sdk.plugin import ui


def _find_ui_context_meta_like_host(member):
    from plugin.sdk.plugin.ui import UI_CONTEXT_META_ATTR

    candidates = [member]
    if hasattr(member, "__func__"):
        candidates.append(member.__func__)
    current = getattr(member, "__wrapped__", None)
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        candidates.append(current)
        if hasattr(current, "__func__"):
            candidates.append(current.__func__)
        current = getattr(current, "__wrapped__", None)
    for candidate in candidates:
        meta = getattr(candidate, UI_CONTEXT_META_ATTR, None)
        if isinstance(meta, dict):
            return dict(meta)
    return None


def test_ui_context_meta_survives_outer_wrapped_decorator() -> None:
    def outer(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        return wrapper

    class Demo:
        @outer
        @ui.context(id="dashboard")
        def dashboard(self):
            return {}

    meta = _find_ui_context_meta_like_host(Demo().dashboard)

    assert meta is not None
    assert meta["id"] == "dashboard"
