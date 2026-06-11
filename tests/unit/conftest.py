"""Unit-test-scoped fixtures.

Why this file exists: `main_routers.shared_state._state` is a module-level
dict that `init_shared_state()` mutates in place (no teardown hook). Unit
tests call `init_shared_state(role_state={}, config_manager=cm, ...)`
where `cm` is a `ConfigManager` pointing at a `TemporaryDirectory`. When
that temp dir is torn down, `_state['config_manager']` keeps holding the
dangling reference, and the next test that reads shared state (without
re-initializing) can grab a `config_manager` whose disk paths are gone.

The `_reset_shared_state` fixture below snapshots `_state` before each
unit test and restores it after, so cross-test pollution cannot happen.
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


@pytest.fixture(autouse=True)
def _reset_shared_state():
    shared_state = sys.modules.get("main_routers.shared_state")
    had_shared_state = shared_state is not None
    snapshot = dict(shared_state._state) if had_shared_state else {}

    try:
        yield
    finally:
        shared_state = sys.modules.get("main_routers.shared_state")
        if shared_state is None:
            return
        if not had_shared_state:
            shared_state._state.clear()
            return

        shared_state._state.clear()
        shared_state._state.update(snapshot)


@pytest.fixture(autouse=True)
def _reset_game_sessions(request):
    if not _needs_game_route_reset(request):
        yield
        return

    from .game_route_test_helpers import reset_game_route_state

    with reset_game_route_state():
        yield
