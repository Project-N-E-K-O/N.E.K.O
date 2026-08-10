"""Session-scoped target ownership for live output."""

from __future__ import annotations

from typing import Any


def capture_live_target(runtime: Any) -> str:
    """Bind live output to the character selected when this session starts."""

    current = str(getattr(runtime, "live_target_lanlan", "") or "").strip()
    if current:
        return current
    resolver = getattr(getattr(runtime, "dispatcher", None), "current_target_lanlan", None)
    if not callable(resolver):
        return ""
    try:
        target = resolver()
    except Exception:
        target = ""
    runtime.live_target_lanlan = str(target or "").strip()[:120]
    return runtime.live_target_lanlan


def release_live_target(runtime: Any) -> None:
    runtime.live_target_lanlan = ""


def release_live_target_if_scene_restored(runtime: Any) -> bool:
    """Keep the old owner available when its scene clear can still be retried."""

    if bool(getattr(runtime, "instructions_injected", False)):
        return False
    release_live_target(runtime)
    return True
