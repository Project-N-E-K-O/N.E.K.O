from __future__ import annotations

import threading

_lock = threading.Lock()
_state: dict[str, tuple[str, bool]] = {}
_delivery_state: dict[str, tuple[str, bool]] = {}


def _set_generation_permission(
    state: dict[str, tuple[str, bool]],
    source: str,
    generation: str,
    allowed: bool,
) -> bool:
    if not source or not generation:
        return False
    with _lock:
        current = state.get(source)
        if not allowed and current is not None and current[0] != generation:
            return False
        state[source] = (generation, allowed)
    return True


def clear_live_frame_permissions() -> None:
    with _lock:
        _state.clear()


def clear_plugin_delivery_permissions() -> None:
    with _lock:
        _delivery_state.clear()


def revoke_plugin_permissions(source_name: str) -> dict[str, object]:
    source = str(source_name or "").strip()
    live_frame_revoked = False
    delivery_revoked = False
    if source:
        with _lock:
            live_frame_revoked = _state.pop(source, None) is not None
            delivery_revoked = _delivery_state.pop(source, None) is not None
    return {
        "ok": True,
        "source_name": source,
        "live_frame_revoked": live_frame_revoked,
        "delivery_revoked": delivery_revoked,
    }


def set_live_frame_permission(
    source_name: str,
    token: str,
    *,
    enabled: bool,
) -> dict[str, object]:
    source = str(source_name or "").strip()
    generation = str(token or "").strip()
    allowed = bool(enabled)
    applied = _set_generation_permission(_state, source, generation, allowed)
    return {
        "ok": True,
        "source_name": source,
        "token": generation,
        "enabled": allowed,
        "applied": applied,
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


def set_plugin_delivery_permission(
    source_name: str,
    token: str,
    *,
    enabled: bool,
) -> dict[str, object]:
    source = str(source_name or "").strip()
    generation = str(token or "").strip()
    allowed = bool(enabled)
    applied = _set_generation_permission(
        _delivery_state, source, generation, allowed
    )
    return {
        "ok": True,
        "source_name": source,
        "token": generation,
        "enabled": allowed,
        "applied": applied,
    }


def allows_plugin_delivery(source_name: str, token: str) -> bool:
    """Whether a stamped plugin cue may still be spoken.

    Call-outs that never opted in (empty token) stay deliverable, so existing
    plugins are unchanged. A stamped generation is fail-closed: unknown,
    replaced, or disabled tokens are dropped at the delivery point.
    """
    generation = str(token or "").strip()
    if not generation:
        return True
    source = str(source_name or "").strip()
    if not source:
        return False
    with _lock:
        current = _delivery_state.get(source)
    if current is None:
        return False
    current_token, allowed = current
    return allowed and current_token == generation
