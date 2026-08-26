from __future__ import annotations

import threading

_lock = threading.Lock()
_INTERNAL_HOST_GENERATION = "__internal__"
_state: dict[str, tuple[str, str, bool]] = {}
_delivery_state: dict[str, tuple[str, str, bool]] = {}
_host_generations: dict[str, str] = {}
_delivery_host_generations: dict[str, str] = {}
_revoked_host_generations: dict[str, set[str]] = {}
_revoked_delivery_host_generations: dict[str, set[str]] = {}


def _set_generation_permission(
    state: dict[str, tuple[str, str, bool]],
    host_generations: dict[str, str],
    revoked_host_generations: dict[str, set[str]],
    source: str,
    host_generation: str,
    generation: str,
    allowed: bool,
) -> bool:
    if not source or not host_generation or not generation:
        return False
    with _lock:
        if host_generation in revoked_host_generations.get(source, set()):
            return False
        current_host_generation = host_generations.get(source)
        if current_host_generation is None:
            host_generations[source] = host_generation
        elif current_host_generation != host_generation:
            return False
        current = state.get(source)
        if not allowed and current is not None and current[1] != generation:
            return False
        state[source] = (host_generation, generation, allowed)
    return True


def _revoke_generation_permission(
    state: dict[str, tuple[str, str, bool]],
    host_generations: dict[str, str],
    revoked_host_generations: dict[str, set[str]],
    source: str,
    host_generation: str,
) -> bool:
    current_host_generation = host_generations.get(source)
    if host_generation:
        revoked_generations = revoked_host_generations.setdefault(source, set())
        already_revoked = host_generation in revoked_generations
        revoked_generations.add(host_generation)
        if (
            current_host_generation is not None
            and current_host_generation != host_generation
        ):
            return False
        if current_host_generation is None and already_revoked:
            return False
    else:
        generations = revoked_host_generations.setdefault(source, set())
        if current_host_generation:
            generations.add(current_host_generation)
        current_permission = state.get(source)
        if current_permission is not None:
            generations.add(current_permission[0])

    host_generations.pop(source, None)
    removed = state.pop(source, None) is not None
    return True if host_generation else removed


def clear_live_frame_permissions() -> None:
    with _lock:
        _state.clear()
        _host_generations.clear()
        _revoked_host_generations.clear()


def clear_plugin_delivery_permissions() -> None:
    with _lock:
        _delivery_state.clear()
        _delivery_host_generations.clear()
        _revoked_delivery_host_generations.clear()


def revoke_plugin_permissions(
    source_name: str,
    host_generation: str = "",
) -> dict[str, object]:
    source = str(source_name or "").strip()
    normalized_host_generation = str(host_generation or "").strip()
    live_frame_revoked = False
    delivery_revoked = False
    if source:
        with _lock:
            live_frame_revoked = _revoke_generation_permission(
                _state,
                _host_generations,
                _revoked_host_generations,
                source,
                normalized_host_generation,
            )
            delivery_revoked = _revoke_generation_permission(
                _delivery_state,
                _delivery_host_generations,
                _revoked_delivery_host_generations,
                source,
                normalized_host_generation,
            )
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
    host_generation: str | None = None,
) -> dict[str, object]:
    source = str(source_name or "").strip()
    generation = str(token or "").strip()
    normalized_host_generation = str(
        host_generation
        if host_generation is not None
        else _INTERNAL_HOST_GENERATION
    ).strip()
    allowed = bool(enabled)
    applied = _set_generation_permission(
        _state,
        _host_generations,
        _revoked_host_generations,
        source,
        normalized_host_generation,
        generation,
        allowed,
    )
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
        current_host_generation, current_token, allowed = current
        if _host_generations.get(source) != current_host_generation:
            return False
    return allowed and current_token == generation


def set_plugin_delivery_permission(
    source_name: str,
    token: str,
    *,
    enabled: bool,
    host_generation: str | None = None,
) -> dict[str, object]:
    source = str(source_name or "").strip()
    generation = str(token or "").strip()
    normalized_host_generation = str(
        host_generation
        if host_generation is not None
        else _INTERNAL_HOST_GENERATION
    ).strip()
    allowed = bool(enabled)
    applied = _set_generation_permission(
        _delivery_state,
        _delivery_host_generations,
        _revoked_delivery_host_generations,
        source,
        normalized_host_generation,
        generation,
        allowed,
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
        current_host_generation, current_token, allowed = current
        if _delivery_host_generations.get(source) != current_host_generation:
            return False
    return allowed and current_token == generation
