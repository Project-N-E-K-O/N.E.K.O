# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Guard the ``_reset_steamworks_handle`` fixture in ``tests/unit/conftest.py``.

The process-global Steamworks handle lives in ``utils.steam_state`` and is
mirrored by ``app.main_server.steamworks``. A test that reaches an endpoint
calling ``ensure_steamworks()`` initializes the real SDK on a machine with Steam
installed and used to leave that live handle installed for every later test.

Each guard below asserts the globals carry no marker from *this* module, then
installs one. Whichever of the two pytest happens to run second fails if the
fixture stopped restoring — so the guard holds under any shuffle order, which
matters because ``pytest-randomly`` reorders within the module (see pytest.ini).

The assertions deliberately look for this module's own marker instead of
``is None``: the point is "no test leaks its handle onward", not "the handle is
always None", and the latter would break the day a session-scoped fixture
legitimately installs one.
"""
from __future__ import annotations

import sys

import pytest

from utils import steam_state


class _MarkerSteamworks:
    """Stand-in handle installed only by the guards in this module."""


_MARKER_LAST_ATTEMPT = 424242.0


def _marker_initializer():
    return _MarkerSteamworks()


def _assert_no_marker_leaked_in() -> None:
    assert not isinstance(steam_state._steamworks, _MarkerSteamworks), (
        "utils.steam_state._steamworks leaked from another test — the "
        "_reset_steamworks_handle fixture is no longer restoring it"
    )
    assert steam_state._steamworks_initializer is not _marker_initializer, (
        "utils.steam_state._steamworks_initializer leaked from another test"
    )
    assert steam_state._last_init_attempt_monotonic != _MARKER_LAST_ATTEMPT, (
        "utils.steam_state._last_init_attempt_monotonic leaked from another test"
    )

    main_server = sys.modules.get("app.main_server")
    if main_server is not None:
        assert not isinstance(main_server.steamworks, _MarkerSteamworks), (
            "app.main_server.steamworks mirror leaked from another test — "
            "on_startup seeds shared state from this global"
        )


def _install_marker_handle() -> None:
    """Dirty every global the fixture is responsible for restoring."""
    from app import main_server

    steam_state._steamworks = _MarkerSteamworks()
    steam_state._steamworks_initializer = _marker_initializer
    steam_state._last_init_attempt_monotonic = _MARKER_LAST_ATTEMPT
    main_server.steamworks = steam_state._steamworks


@pytest.mark.unit
def test_steamworks_globals_are_not_inherited_from_a_previous_test():
    _assert_no_marker_leaked_in()
    _install_marker_handle()


@pytest.mark.unit
def test_steamworks_globals_are_restored_between_tests():
    _assert_no_marker_leaked_in()
    _install_marker_handle()


@pytest.mark.unit
def test_ensure_steamworks_lazy_init_does_not_outlive_the_test():
    """The concrete leak path: a lazy ``ensure_steamworks()`` from a request handler.

    ``main_routers/config_router/language.py`` calls ``ensure_steamworks()``,
    which runs whatever initializer ``main_server.on_startup`` registered. Here
    the initializer is a stub so the guard runs identically with or without a
    Steam client, which is what makes this reproduce on CI and not just on a
    developer machine.
    """
    from main_routers.shared_state import ensure_steamworks

    steam_state._steamworks = None
    steam_state._steamworks_initializer = _marker_initializer
    steam_state._last_init_attempt_monotonic = _MARKER_LAST_ATTEMPT

    handle = ensure_steamworks(force=True)

    assert isinstance(handle, _MarkerSteamworks)
    assert steam_state._steamworks is handle
    # Teardown must undo all of this; the two guards above are what observe it.
