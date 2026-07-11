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

"""Unit tests for ``LLMSessionManager._should_suppress_activity_narration``.

The activity_guess emotion-tier narration only feeds proactive Phase 2. The
tracker heartbeat outlives a session (so the rule-based break-reminder /
context-prompt logic keeps ticking), so without a "no consumer" gate the loop
would keep paying for the LLM call after the user closed the page. This
predicate is that gate; it must suppress when EITHER goodbye-silence is in
effect OR no client WebSocket is connected.

Construction of the real ``LLMSessionManager`` is heavy, so — following
``tests/unit/test_focus_indicator_bridge.py`` — we bind the unbound methods onto
a tiny ``SimpleNamespace`` stub that exposes only the attributes the predicate
touches.
"""

from __future__ import annotations

import types

from main_logic.core import LLMSessionManager


def _mgr(*, goodbye_silent: bool, connected: bool):
    """Stub manager exposing only what the suppress predicate reads."""
    stub = types.SimpleNamespace()
    stub.goodbye_silent = goodbye_silent

    # ``_has_connected_websocket`` compares ``websocket.client_state`` against
    # ``client_state.CONNECTED``. Make the fake client_state equal to its own
    # ``.CONNECTED`` iff we want "connected".
    cs = types.SimpleNamespace()
    cs.CONNECTED = cs if connected else object()
    stub.websocket = types.SimpleNamespace(client_state=cs)

    for name in (
        'is_goodbye_silent',
        '_has_connected_websocket',
        '_should_suppress_activity_narration',
    ):
        setattr(
            stub, name,
            getattr(LLMSessionManager, name).__get__(stub, LLMSessionManager),
        )
    return stub


def test_suppress_when_goodbye_silent():
    # Even with a live connection, goodbye-silence means Phase 2 bails → suppress.
    assert _mgr(goodbye_silent=True, connected=True)._should_suppress_activity_narration() is True


def test_suppress_when_no_connected_websocket():
    # The headline fix (A): a plain disconnect with NO goodbye_silent must still
    # suppress — otherwise the heartbeat burns the LLM at the backoff cap all
    # night after the user closes the page.
    assert _mgr(goodbye_silent=False, connected=False)._should_suppress_activity_narration() is True


def test_not_suppressed_when_connected_and_not_goodbye():
    # The only case with a real consumer: live connection, not silenced.
    assert _mgr(goodbye_silent=False, connected=True)._should_suppress_activity_narration() is False


def test_suppress_when_both_conditions_hold():
    assert _mgr(goodbye_silent=True, connected=False)._should_suppress_activity_narration() is True


def test_suppress_when_websocket_is_none():
    stub = types.SimpleNamespace()
    stub.goodbye_silent = False
    stub.websocket = None
    for name in (
        'is_goodbye_silent',
        '_has_connected_websocket',
        '_should_suppress_activity_narration',
    ):
        setattr(
            stub, name,
            getattr(LLMSessionManager, name).__get__(stub, LLMSessionManager),
        )
    assert stub._should_suppress_activity_narration() is True
