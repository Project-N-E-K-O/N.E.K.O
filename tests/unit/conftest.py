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

`_reset_steamworks_handle` covers the same class of leak for the process-global
Steamworks handle, which used to live in that very `_state` dict before #1270
moved it down to `utils.steam_state`. See that fixture's own docstring.
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
def _reset_steamworks_handle():
    """Restore the process-global Steamworks handle around every unit test.

    ``utils.steam_state`` owns the process-singleton Steamworks handle plus the
    lazy-init callback that ``main_routers.shared_state`` re-exports, and
    ``app/main_server/__init__.py`` keeps a module-global mirror of the same
    handle (``on_startup`` reads that mirror when it seeds shared state).

    Any test that drives an endpoint calling ``ensure_steamworks()`` — e.g. a
    ``TestClient`` GET on ``/api/config/steam_language`` — makes the registered
    initializer run for real. On a developer machine with Steam installed that
    call succeeds and installs a live ``STEAMWORKS`` object into *both* globals.
    Nothing tore it down, so a later test that expects a pristine ``None`` handle
    failed depending on execution order. CI never saw it: with no Steam client
    the initializer returns ``None``, so the leak is invisible there.

    Before #1270 the handle lived in ``main_routers.shared_state._state`` and the
    ``_reset_shared_state`` fixture above covered it. Moving the singleton down to
    the L1 ``utils`` layer silently dropped it out of that snapshot; this fixture
    restores the lost coverage on the state's new home.

    ``utils.steam_state``'s whole module namespace is snapshotted rather than a
    hand-listed set of names, so a global added there later is covered without
    anyone remembering to extend a checklist.

    Restoring deliberately drops any handle a test installed instead of calling
    ``STEAMWORKS.unload()`` on it, and that is not an oversight:

    * ``unload()`` runs ``SteamAPI_Shutdown``, which is *process*-global — it
      does not shut down "that instance". Unloading a handle a test created
      would therefore also invalidate the snapshot handle this fixture is in the
      middle of restoring, turning a dropped reference into a live-but-dead API.
    * Production keeps the matching rule: ``SteamCloudBundleBridge.close()``
      (utils/steam_cloud_bundle.py) unloads only ``_owned_steamworks``, the
      handle it constructed itself, and never one that was passed in. A fixture
      restoring a snapshot is by definition not the owner.

    Dropping the reference is also strictly better than the status quo it
    replaces: with the fixture in place the unit suite performs *zero* real
    ``STEAMWORKS()`` initializations (measured; it was two without it), because
    restoring ``_steamworks_initializer`` closes the lazy-init path as well.
    """
    # Importing here (not lazily via sys.modules) removes the "module not yet
    # imported" branch entirely; the module is a few globals and a lock, with no
    # import side effects.
    from utils import steam_state

    steam_state_snapshot = dict(vars(steam_state))
    # The mirror's definition-time value is None, so a main_server imported
    # *during* the test restores to the value it was born with.
    main_server_steamworks = getattr(sys.modules.get("app.main_server"), "steamworks", None)

    try:
        yield
    finally:
        # Same lock the module's own setters take, in case a test leaked a
        # background thread that is mid-``ensure_steamworks()``.
        with steam_state_snapshot["_steamworks_lock"]:
            for name in [n for n in vars(steam_state) if n not in steam_state_snapshot]:
                delattr(steam_state, name)
            for name, value in steam_state_snapshot.items():
                setattr(steam_state, name, value)

        main_server = sys.modules.get("app.main_server")
        if main_server is not None:
            main_server.steamworks = main_server_steamworks


@pytest.fixture(autouse=True)
def _reset_sys_path():
    """Restore ``sys.path`` around every unit test.

    ``sys.path`` is process-global, and several product entry points prepend to
    it as a deliberate, permanent side effect. ``_start_embedded_user_plugin_server``
    (app/agent_server/plugin_host.py) inserts ``<repo>/plugin`` at index 1 so the
    embedded server can import ``plugin.server.http_app``. In a real agent process
    that entry is meant to stay for the process lifetime; in a unit test that calls
    the function directly it stays for the rest of the session.

    The leaked entry is not inert. ``<repo>/plugin`` ships its own ``config``
    package, and under pytest the repo root is not ``sys.path[0]`` — the rootdir
    insertions for ``tests`` and ``tests/unit/asr_client`` sit in front of it, so
    a hardcoded index 1 lands *ahead* of the repo root. Every fresh resolution of
    ``config`` then finds ``plugin/config``, and that includes ``importlib.reload``,
    which re-runs the finder and rebinds the module object in place. So
    ``launcher_core.runtime._reload_runtime_config_from_env`` — whose entire job is
    to refresh the negotiated ``NEKO_*`` ports — reloaded ``plugin/config`` into the
    root ``config`` module and left every port at its pre-negotiation value. That
    is how ``test_runtime_config_reload_preserves_negotiated_fallback_ports``
    turned red under seed 31337 while passing on its own.

    The whole list is snapshotted rather than a named entry removed, so path leaks
    nobody has thought of yet are covered without extending a checklist. Restoring
    in place (``sys.path[:] =``) keeps the list identity that importers may hold.
    """
    snapshot = list(sys.path)
    try:
        yield
    finally:
        if sys.path != snapshot:
            sys.path[:] = snapshot


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
