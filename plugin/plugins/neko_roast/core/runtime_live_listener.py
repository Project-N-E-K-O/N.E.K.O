"""No-op live listener reconciliation for early runtime-config slices."""

from __future__ import annotations

from typing import Any


async def start_live_listener(runtime: Any, room_ref: Any) -> bool:
    _ = runtime, room_ref
    return False


async def stop_live_listener(runtime: Any, *, mark_disabled: bool = True) -> None:
    _ = runtime, mark_disabled


async def reconcile_live_listener_after_config(
    runtime: Any,
    clean: dict[str, Any],
    *,
    old_room_id: int,
    old_platform: str,
    old_room_ref: str,
    was_listening: bool,
) -> None:
    _ = runtime, clean, old_room_id, old_platform, old_room_ref, was_listening
