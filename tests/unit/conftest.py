"""Unit-test-scoped fixtures.

Why this file exists: `main_routers.shared_state._state` and the Steamworks
handle in `utils.steam_state` are process-global state. Unit tests mutate both
without a teardown hook. Tests that run later can otherwise observe a dangling
`ConfigManager` or a real Steamworks object left by an earlier test.

The `_reset_shared_state` fixture below snapshots these globals before each
unit test and restores them after, so cross-test pollution cannot happen.
Introduced in response to CodeRabbit review on PR #681 — flagged for
tests/unit/test_character_memory_regression.py and
tests/unit/test_cloudsave_autocloud_router.py, but applied globally
because the same leak pattern exists in every cloudsave/character test.
"""
from __future__ import annotations

import sys

import pytest


def _needs_game_route_reset(request) -> bool:
    """Apply game-route isolation to test_game_* modules or explicit marker users."""
    module_name = getattr(request.module, "__name__", "").split(".")[-1]
    return module_name.startswith("test_game_") or request.node.get_closest_marker("game_route") is not None


def _needs_icebreaker_route_reset(request) -> bool:
    module_name = getattr(request.module, "__name__", "").split(".")[-1]
    return module_name.startswith("test_icebreaker_") or request.node.get_closest_marker("icebreaker_route") is not None


@pytest.fixture(autouse=True)
def _reset_shared_state():
    shared_state = sys.modules.get("main_routers.shared_state")
    had_shared_state = shared_state is not None
    snapshot = dict(shared_state._state) if had_shared_state else {}

    steam_state = sys.modules.get("utils.steam_state")
    had_steam_state = steam_state is not None
    steam_snapshot = (
        (
            steam_state._steamworks,
            steam_state._steamworks_initializer,
            steam_state._last_init_attempt_monotonic,
        )
        if had_steam_state
        else (None, None, 0.0)
    )

    main_server = sys.modules.get("app.main_server")
    had_main_server = main_server is not None
    main_server_steamworks = (
        getattr(main_server, "steamworks", None) if had_main_server else None
    )

    try:
        yield
    finally:
        shared_state = sys.modules.get("main_routers.shared_state")
        if shared_state is not None:
            shared_state._state.clear()
            if had_shared_state:
                shared_state._state.update(snapshot)

        steam_state = sys.modules.get("utils.steam_state")
        if steam_state is not None:
            with steam_state._steamworks_lock:
                (
                    steam_state._steamworks,
                    steam_state._steamworks_initializer,
                    steam_state._last_init_attempt_monotonic,
                ) = steam_snapshot

        main_server = sys.modules.get("app.main_server")
        if main_server is not None:
            main_server.steamworks = (
                main_server_steamworks if had_main_server else None
            )


@pytest.fixture(autouse=True)
def _reset_game_sessions(request):
    if not _needs_game_route_reset(request):
        yield
        return

    from .game_route_test_helpers import reset_game_route_state

    with reset_game_route_state():
        yield


@pytest.fixture(autouse=True)
def _reset_icebreaker_routes(request):
    if not _needs_icebreaker_route_reset(request):
        yield
        return

    from utils import icebreaker_route_state

    states_snapshot = dict(icebreaker_route_state._icebreaker_route_states)
    locks_snapshot = dict(icebreaker_route_state._icebreaker_route_locks)
    try:
        icebreaker_route_state._icebreaker_route_states.clear()
        icebreaker_route_state._icebreaker_route_locks.clear()
        yield
    finally:
        icebreaker_route_state._icebreaker_route_states.clear()
        icebreaker_route_state._icebreaker_route_states.update(states_snapshot)
        icebreaker_route_state._icebreaker_route_locks.clear()
        icebreaker_route_state._icebreaker_route_locks.update(locks_snapshot)
