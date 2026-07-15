from __future__ import annotations

from contextlib import contextmanager

from main_routers import game_router
from main_routers.game_router import badminton_scores as gr_scores
from main_routers.game_router import runtime as gr_runtime


def gr_patch_all(monkeypatch, name, value, raising=True):
    """Patch the same object onto every submodule that holds the binding.

    Restores pre-split semantics: with monolithic game_router a single
    setattr hit the one namespace all flows resolved against; after the
    package split, from-import snapshots live in several submodules'
    globals, so patch them all with the same object."""
    from main_routers.game_router import (
        _shared, char_info, logs, memory_policy, game_context, pregame,
        visible_events, balance, badminton_scores, archive,
        route_lifecycle, session_pool, postgame, runtime,
    )
    hit = False
    for _m in (_shared, char_info, logs, memory_policy, game_context, pregame,
               visible_events, balance, badminton_scores, archive,
               route_lifecycle, session_pool, postgame, runtime):
        if hasattr(_m, name):
            monkeypatch.setattr(_m, name, value)
            hit = True
    if not hit and raising:
        raise AttributeError("no game_router submodule has %r" % name)


@contextmanager
def reset_game_route_state():
    sessions_snapshot = dict(gr_runtime._game_sessions)
    routes_snapshot = dict(gr_runtime._game_route_states)
    badminton_score_sessions_snapshot = dict(gr_scores._badminton_recent_score_sessions)
    gr_runtime._game_sessions.clear()
    gr_runtime._game_route_states.clear()
    gr_scores._badminton_recent_score_sessions.clear()
    try:
        yield
    finally:
        gr_runtime._game_sessions.clear()
        gr_runtime._game_sessions.update(sessions_snapshot)
        gr_runtime._game_route_states.clear()
        gr_runtime._game_route_states.update(routes_snapshot)
        gr_scores._badminton_recent_score_sessions.clear()
        gr_scores._badminton_recent_score_sessions.update(badminton_score_sessions_snapshot)


def mark_game_started(state, elapsed_ms=12_000):
    state["game_started"] = True
    state["game_started_elapsed_ms"] = elapsed_ms
    state["game_started_at"] = gr_runtime.time.time() - (elapsed_ms / 1000.0)
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
