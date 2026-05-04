"""Concurrency / race-condition tests for ``main_routers/game_router``.

These cover the audit findings B1, B2, B3, B5, B6, B8 (B4 is a defensive
guard verified in ``test_game_context_organizer.py``; B7 was landed in
PR #1125), plus two PR #1127 follow-up tests:

- ``test_route_start_serializes_supersede_across_game_types_for_same_lanlan``
  guards CodeRabbit's per-lanlan supersede-lock finding.
- ``test_game_session_create_lock_evicted_with_session`` guards codex's
  ``_game_session_create_locks`` memory-leak finding.
"""
from __future__ import annotations

import asyncio

import pytest

from main_routers import game_router
from utils import game_route_state as grs

from .game_route_test_helpers import reset_game_route_state


class _FakeOmniSession:
    """Stand-in for ``OmniOfflineClient`` that records lifecycle calls."""

    def __init__(self, *, name: str = "fake"):
        self.name = name
        self.connect_calls = 0
        self.close_calls = 0
        self.stream_calls = 0
        self._closed = False

    async def connect(self, *, instructions: str = ""):
        self.connect_calls += 1

    async def close(self):
        self.close_calls += 1
        self._closed = True

    async def stream_text(self, text: str):
        if self._closed:
            raise RuntimeError("stream_text on closed session")
        self.stream_calls += 1

    async def update_session(self, config):
        pass


def _activate_route(lanlan: str, game_type: str, session_id: str) -> dict:
    state = {
        "game_route_active": True,
        "game_type": game_type,
        "session_id": session_id,
        "lanlan_name": lanlan,
        "created_at": game_router.time.time(),
        "last_heartbeat_at": game_router.time.time(),
        "last_activity": game_router.time.time(),
        "heartbeat_enabled": True,
        "pending_outputs": [],
        "game_dialog_log": [],
        "_external_voice_seen_request_ids": None,
    }
    game_router._game_route_states[grs._route_state_key(lanlan, game_type)] = state
    return state


def _stub_archive_calls(monkeypatch):
    """Replace heavy archive helpers with no-ops so finalize tests focus
    on the close_game_session decision rather than the AI archive flow.
    """
    async def _no_op(*_args, **_kwargs):
        return None

    async def _ok_submit(_archive):
        return {"status": "ok"}

    monkeypatch.setattr(
        game_router, "_settle_game_context_organizer_before_archive", _no_op,
    )
    monkeypatch.setattr(
        game_router, "_cancel_game_context_organizer_before_disabled_archive", _no_op,
    )
    monkeypatch.setattr(
        game_router, "_submit_game_archive_to_memory", _ok_submit,
    )
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_b5_finalize_or_merges_close_session_request(monkeypatch):
    """B5: two finalize callers with conflicting close_game_session args
    must produce exactly one close, and the True caller wins via OR-merge.
    """
    _stub_archive_calls(monkeypatch)
    with reset_game_route_state():
        state = _activate_route("Lan", "soccer", "match_b5")
        fake_session = _FakeOmniSession(name="b5")
        key = game_router._game_session_key("Lan", "soccer", "match_b5")
        game_router._game_sessions[key] = {
            "session": fake_session,
            "reply_chunks": [],
            "lanlan_name": "Lan",
            "lanlan_prompt": "",
            "source": {},
            "last_activity": 0,
            "lock": asyncio.Lock(),
            "instructions": "",
        }

        # Caller A: close_game_session=False; Caller B: True.
        results = await asyncio.gather(
            game_router._finalize_game_route_state(
                state, reason="A_no_close", close_game_session=False,
            ),
            game_router._finalize_game_route_state(
                state, reason="B_close", close_game_session=True,
            ),
        )

        # Both callers see the same finalized result (the shared task).
        assert results[0]["game_session_closed"] is True
        assert results[1]["game_session_closed"] is True

        # The session was closed exactly once and removed from the cache.
        assert fake_session.close_calls == 1
        assert key not in game_router._game_sessions


@pytest.mark.unit
@pytest.mark.asyncio
async def test_b1_chat_short_circuits_when_route_exit_started():
    """B1/B2: ``_run_game_chat`` must short-circuit if the route flipped to
    ``_exit_flow_started`` before the call lands. Otherwise it would issue
    a ``stream_text`` against a session that finalize is about to close.
    """
    with reset_game_route_state():
        state = _activate_route("Lan", "soccer", "match_b1")
        state["_exit_flow_started"] = True
        # No need to populate _game_sessions — the pre-create short-circuit
        # in _run_game_chat must trip *before* ever calling
        # _get_or_create_session.
        result = await game_router._run_game_chat(
            "soccer", "match_b1", {"kind": "free-ball", "lanlan_name": "Lan"},
        )
        assert result.get("skipped") == "route_inactive"
        assert result.get("line") == ""


@pytest.mark.unit
def test_b2_append_dialog_drops_after_exit_started():
    """B2: late writes after finalize starts must not mutate state."""
    with reset_game_route_state():
        state = _activate_route("Lan", "soccer", "match_b2")
        state["_exit_flow_started"] = True
        before_log_len = len(state["game_dialog_log"])
        before_outputs_len = len(state["pending_outputs"])

        game_router._append_game_dialog(state, {"type": "user", "text": "late"})
        game_router._append_game_output(state, {"type": "game_event"})

        assert len(state["game_dialog_log"]) == before_log_len
        assert len(state["pending_outputs"]) == before_outputs_len


@pytest.mark.unit
@pytest.mark.asyncio
async def test_b3_external_transcript_short_circuits_when_route_exiting():
    """B3: dispatcher path must bail out cleanly if the route flipped to
    exiting between the active-check and the transcript-route call.
    """
    with reset_game_route_state():
        state = _activate_route("Lan", "soccer", "match_b3")
        state["_exit_flow_started"] = True

        handled = await game_router._route_external_transcript_to_game(
            "Lan", state, "hello",
            source="external_voice_route",
            mode="voice",
            kind="user-voice",
            request_id="req-b3",
        )
        # Returns True (handled) so caller doesn't double-route through
        # ordinary chat — the route was active at the dispatcher gate.
        assert handled is True
        # No game_dialog_log mutation, no game_chat call, no
        # pending_outputs.
        assert state["game_dialog_log"] == []
        assert state["pending_outputs"] == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_b6_session_create_lock_serializes_concurrent_misses(monkeypatch):
    """B6: two concurrent ``_get_or_create_session`` calls for the same key
    must produce exactly one ``OmniOfflineClient`` + one ``connect`` call,
    and exactly one entry in ``_game_sessions``.
    """
    with reset_game_route_state():
        # Drop any leftover create lock for this key from a prior run.
        key = game_router._game_session_key("Lan", "soccer", "match_b6")
        game_router._game_session_create_locks.pop(key, None)

        construct_count = {"n": 0}
        connect_count = {"n": 0}

        class _StubOmni:
            def __init__(self, **kwargs):
                construct_count["n"] += 1
                self._closed = False

            async def connect(self, *, instructions: str = ""):
                # Yield to let the peer reach the cache-recheck under lock.
                await asyncio.sleep(0)
                connect_count["n"] += 1

            async def close(self):
                self._closed = True

        import main_logic.omni_offline_client as omni_module
        monkeypatch.setattr(omni_module, "OmniOfflineClient", _StubOmni)

        def _stub_char_info(name):
            return {
                "lanlan_name": "Lan",
                "lanlan_prompt": "",
                "model": "stub",
                "base_url": "https://stub.example.com",
                "api_key": "stub",
                "master_name": "Master",
                "user_language": "en",
                "api_type": "openai",
            }

        monkeypatch.setattr(game_router, "_get_character_info", _stub_char_info)
        monkeypatch.setattr(
            game_router,
            "_build_game_prompt",
            lambda *args, **kwargs: "stub_prompt",
        )

        results = await asyncio.gather(
            game_router._get_or_create_session("soccer", "match_b6", "Lan"),
            game_router._get_or_create_session("soccer", "match_b6", "Lan"),
        )

        assert construct_count["n"] == 1
        assert connect_count["n"] == 1
        # Both calls return the same entry (cache hit on the second).
        assert results[0] is results[1]
        # Cache contains exactly one entry for this key.
        assert key in game_router._game_sessions


@pytest.mark.unit
@pytest.mark.asyncio
async def test_b8_finalize_routes_for_character_closes_old_routes(monkeypatch):
    """B8: switching characters must finalize all active routes for the
    outgoing character, releasing the SessionManager takeover and
    closing the LLM session.
    """
    _stub_archive_calls(monkeypatch)
    with reset_game_route_state():
        state = _activate_route("Lan", "soccer", "match_b8")
        fake_session = _FakeOmniSession(name="b8")
        key = game_router._game_session_key("Lan", "soccer", "match_b8")
        game_router._game_sessions[key] = {
            "session": fake_session,
            "reply_chunks": [],
            "lanlan_name": "Lan",
            "lanlan_prompt": "",
            "source": {},
            "last_activity": 0,
            "lock": asyncio.Lock(),
            "instructions": "",
        }

        n = await game_router.finalize_game_routes_for_character("Lan")

        assert n == 1
        assert state.get("_exit_flow_started") is True
        assert state.get("game_route_active") is False
        assert fake_session.close_calls == 1
        # Calling again is a no-op (route already inactive).
        n_again = await game_router.finalize_game_routes_for_character("Lan")
        assert n_again == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_b1_supersede_then_chat_does_not_revive_closed_route(monkeypatch):
    """B1: after a ``/route/start`` supersede finalizes a prior route,
    a stale chat for that prior route must not silently re-create an
    ``OmniOfflineClient`` for the now-inactive route slot.
    """
    with reset_game_route_state():
        # Activate state for old session, then mark it as if route_start
        # superseded it.
        state = _activate_route("Lan", "soccer", "match_old")
        state["_exit_flow_started"] = True
        state["game_route_active"] = False

        construct_count = {"n": 0}

        class _StubOmni:
            def __init__(self, **kwargs):
                construct_count["n"] += 1

            async def connect(self, *, instructions: str = ""):
                pass

            async def close(self):
                pass

        import main_logic.omni_offline_client as omni_module
        monkeypatch.setattr(omni_module, "OmniOfflineClient", _StubOmni)

        result = await game_router._run_game_chat(
            "soccer", "match_old", {"kind": "free-ball", "lanlan_name": "Lan"},
        )

        # Pre-create short-circuit must trip before any client is built.
        assert construct_count["n"] == 0
        assert result.get("skipped") == "route_inactive"


class _FakeRouteStartRequest:
    """Minimal stand-in for ``fastapi.Request`` exposing only ``json()``."""

    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_start_serializes_supersede_across_game_types_for_same_lanlan(monkeypatch):
    """CodeRabbit follow-up: two concurrent /route/start calls for the same
    ``lanlan_name`` but DIFFERENT ``game_type`` must be serialized so only
    one active route remains for that character.

    Without the per-lanlan supersede lock, each call holds a different
    per-(lanlan, game_type) lock, both supersede scans miss the other's
    pending activation, and both call ``_activate_game_route(...)`` —
    leaving two active routes for the same character.
    """
    _stub_archive_calls(monkeypatch)

    async def _fake_pregame(**_kwargs):
        return (
            game_router._default_soccer_pregame_context(initial_difficulty="lv2"),
            "fallback",
            "",
        )

    monkeypatch.setattr(game_router, "_build_soccer_pregame_context", _fake_pregame)

    with reset_game_route_state():
        # Drop any leftover supersede / route locks so this test starts fresh.
        from utils import game_route_state as grs_mod
        grs_mod._route_supersede_locks.pop("Lan", None)
        grs_mod._route_state_locks.pop(grs_mod._route_state_key("Lan", "soccer"), None)
        grs_mod._route_state_locks.pop(grs_mod._route_state_key("Lan", "chess"), None)

        results = await asyncio.gather(
            game_router.game_route_start(
                "soccer",
                _FakeRouteStartRequest({
                    "lanlan_name": "Lan",
                    "session_id": "soccer_match",
                }),
            ),
            game_router.game_route_start(
                "chess",
                _FakeRouteStartRequest({
                    "lanlan_name": "Lan",
                    "session_id": "chess_match",
                }),
            ),
        )

        assert all(r.get("ok") for r in results), results

        # Exactly one active route survives for ``Lan``. The second
        # /route/start to acquire the supersede lock finalized the first.
        active_for_lan = [
            (key, state)
            for key, state in game_router._game_route_states.items()
            if key[0] == "Lan" and state.get("game_route_active")
        ]
        assert len(active_for_lan) == 1, active_for_lan


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_session_create_lock_evicted_with_session():
    """codex P2 follow-up: ``_game_session_create_locks`` must drop its
    per-key entry when the session is closed via ``_close_and_remove_session``.

    Otherwise the dict accumulates one ``asyncio.Lock`` per ever-seen
    session_id over uptime — a memory leak in long-running processes.
    """
    with reset_game_route_state():
        key = game_router._game_session_key("Lan", "soccer", "match_evict")
        # Drop any leftover from prior runs.
        game_router._game_session_create_locks.pop(key, None)

        # Lazily create the lock as ``_get_or_create_session`` would.
        create_lock = game_router._get_session_create_lock(key)
        assert key in game_router._game_session_create_locks
        assert create_lock is game_router._game_session_create_locks[key]

        # Register a session with a per-entry lock so close() goes through
        # the locked branch (mirrors the production cache shape).
        class _Closer:
            def __init__(self):
                self.closed = False

            async def close(self):
                self.closed = True

        session = _Closer()
        game_router._game_sessions[key] = {
            "session": session,
            "reply_chunks": [],
            "lanlan_name": "Lan",
            "lanlan_prompt": "",
            "source": {},
            "last_activity": 0,
            "lock": asyncio.Lock(),
            "instructions": "",
        }

        closed = await game_router._close_and_remove_session(
            "soccer", "match_evict", "Lan",
        )

        assert closed is True
        assert key not in game_router._game_sessions
        # The create lock for this evicted session must also be gone.
        assert key not in game_router._game_session_create_locks
        assert session.closed is True
