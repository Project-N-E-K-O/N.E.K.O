from __future__ import annotations

from contextlib import contextmanager

from main_routers import game_router


@contextmanager
def reset_game_route_state():
    sessions_snapshot = dict(game_router._game_sessions)
    routes_snapshot = dict(game_router._game_route_states)
    game_router._game_sessions.clear()
    game_router._game_route_states.clear()
    try:
        yield
    finally:
        game_router._game_sessions.clear()
        game_router._game_sessions.update(sessions_snapshot)
        game_router._game_route_states.clear()
        game_router._game_route_states.update(routes_snapshot)


def mark_game_started(state, elapsed_ms=12_000):
    state["game_started"] = True
    state["game_started_elapsed_ms"] = elapsed_ms
    state["game_started_at"] = game_router.time.time() - (elapsed_ms / 1000.0)
    return state


def set_soccer_game_memory_policy(
    state,
    enabled=True,
    *,
    player_interaction=None,
    event_reply=None,
    archive=None,
    postgame_context=None,
):
    state["soccer_game_memory_enabled"] = enabled
    state["soccer_game_memory_player_interaction_enabled"] = enabled if player_interaction is None else player_interaction
    state["soccer_game_memory_event_reply_enabled"] = enabled if event_reply is None else event_reply
    state["soccer_game_memory_archive_enabled"] = enabled if archive is None else archive
    state["soccer_game_memory_postgame_context_enabled"] = enabled if postgame_context is None else postgame_context
    state["game_memory_enabled"] = enabled
    return state
