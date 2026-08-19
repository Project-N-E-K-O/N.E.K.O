from __future__ import annotations

import threading

_lock = threading.Lock()
_state: dict[str, tuple[str, bool]] = {}


def clear_live_frame_permissions() -> None:
    with _lock:
        _state.clear()


def set_live_frame_permission(
    source_name: str,
    token: str,
    *,
    enabled: bool,
) -> dict[str, object]:
    source = str(source_name or "").strip()
    generation = str(token or "").strip()
    allowed = bool(enabled)
    if source and generation:
        with _lock:
            _state[source] = (generation, allowed)
    return {
        "ok": True,
        "source_name": source,
        "token": generation,
        "enabled": allowed,
    }


def allows_live_frame(source_name: str, token: str) -> bool:
    source = str(source_name or "").strip()
    generation = str(token or "").strip()
    if not source or not generation:
        return False
    with _lock:
        current = _state.get(source)
    if current is None:
        return False
    current_token, allowed = current
    return allowed and current_token == generation
