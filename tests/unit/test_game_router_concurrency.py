"""Concurrency / race-condition tests for ``main_routers/game_router``.

These cover the audit findings B1, B2, B3, B5, B6, B8 (B4 is a defensive
guard verified in ``test_game_context_organizer.py``; B7 was landed in
PR #1125), plus three PR #1127 follow-up tests:

- ``test_route_start_serializes_supersede_across_game_types_for_same_lanlan``
  guards CodeRabbit's per-lanlan supersede-lock finding.
- ``test_game_session_create_lock_evicted_with_session`` guards codex's
  ``_game_session_create_locks`` memory-leak finding.
- ``test_character_switch_finalize_blocks_concurrent_route_start_for_same_lanlan``
  guards codex's ``finalize_game_routes_for_character`` snapshot-without-
  supersede-lock finding (race lets a concurrent ``/route/start`` activate
  a NEW route AFTER the snapshot and escape character-switch cleanup).
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
        results = await asyncio.wait_for(
            asyncio.gather(
                game_router._finalize_game_route_state(
                    state, reason="A_no_close", close_game_session=False,
                ),
                game_router._finalize_game_route_state(
                    state, reason="B_close", close_game_session=True,
                ),
            ),
            timeout=5.0,
        )

        # Both callers see the same finalized result (the shared task).
        assert results[0]["game_session_closed"] is True
        assert results[1]["game_session_closed"] is True

        # The session was closed exactly once and removed from the cache.
        assert fake_session.close_calls == 1
        assert key not in game_router._game_sessions


@pytest.mark.unit
@pytest.mark.asyncio
async def test_b5_finalize_late_close_request_after_inner_done(monkeypatch):
    """codex P2 follow-up (PR #1127): a ``close_game_session=True`` caller
    arriving AFTER the inner finalize already passed its close-site check
    must still close the session.

    Pre-fix race: Caller A spawns inner with ``close=False``; inner reads
    ``_exit_close_session_request=False`` at the close site and returns
    without closing. Caller B then arrives with ``close=True``; the
    dispatcher ORs the flag but only awaits the cached (done) task and
    returns its no-close result. Session leaks until heartbeat sweep.

    Post-fix: dispatcher re-checks the OR-merge flag against the cached
    result on the existing-task path and performs the close itself.
    """
    _stub_archive_calls(monkeypatch)
    with reset_game_route_state():
        state = _activate_route("Lan", "soccer", "match_b5_late")
        fake_session = _FakeOmniSession(name="b5_late")
        key = game_router._game_session_key("Lan", "soccer", "match_b5_late")
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

        # Caller A: close=False. Run to completion BEFORE B arrives so the
        # inner task has fully passed its close-site check and returned.
        result_a = await asyncio.wait_for(
            game_router._finalize_game_route_state(
                state, reason="A_no_close", close_game_session=False,
            ),
            timeout=5.0,
        )
        assert result_a["game_session_closed"] is False
        assert fake_session.close_calls == 0
        assert key in game_router._game_sessions

        # Caller B arrives late with close=True. The cached task is already
        # done; dispatcher must still honor B's close request.
        result_b = await asyncio.wait_for(
            game_router._finalize_game_route_state(
                state, reason="B_late_close", close_game_session=True,
            ),
            timeout=5.0,
        )
        assert result_b["game_session_closed"] is True
        assert fake_session.close_calls == 1
        assert key not in game_router._game_sessions


@pytest.mark.unit
@pytest.mark.asyncio
async def test_b5_finalize_late_close_request_during_inner_archive(monkeypatch):
    """codex P2 follow-up (PR #1127): late ``close=True`` caller variant
    where the inner is parked on archive submission when B arrives.

    This exercises a finer-grained race: even if scheduling lets the inner
    resume past its close-site check before observing B's flag set
    (because B has not actually been scheduled yet at the resume point),
    the dispatcher recheck on B's existing-task path still performs the
    close. ``_close_and_remove_session`` is idempotent, so concurrent
    close paths between inner and dispatcher cannot double-close.
    """
    _stub_archive_calls(monkeypatch)
    barrier = asyncio.Event()
    archive_started = asyncio.Event()

    async def _blocked_submit(_archive):
        archive_started.set()
        await barrier.wait()
        return {"status": "ok"}

    monkeypatch.setattr(game_router, "_submit_game_archive_to_memory", _blocked_submit)

    with reset_game_route_state():
        state = _activate_route("Lan", "soccer", "match_b5_mid")
        # Force the inner to actually enter ``_submit_game_archive_to_memory``
        # so the barrier in ``_blocked_submit`` becomes a real park point.
        # Without these flags ``_game_archive_memory_skip_reason`` returns
        # "game_not_started" / "soccer_game_memory_archive_disabled" and
        # the submit call is bypassed entirely.
        state["game_started"] = True
        state["game_started_at"] = game_router.time.time() - 30
        state["soccer_game_memory_enabled"] = True
        state["soccer_game_memory_archive_enabled"] = True
        fake_session = _FakeOmniSession(name="b5_mid")
        key = game_router._game_session_key("Lan", "soccer", "match_b5_mid")
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

        # Caller A spawns inner with close=False; inner parks on the barrier
        # at archive submission, BEFORE reaching the close-site check.
        task_a = asyncio.create_task(
            game_router._finalize_game_route_state(
                state, reason="A_no_close", close_game_session=False,
            )
        )
        # Pin the timing: wait until the inner has actually entered
        # ``_blocked_submit`` and is parked on ``barrier``. Explicit event
        # sync — robust against future reordering of ``state["archive"]``
        # assignment around the archive submit call.
        await asyncio.wait_for(archive_started.wait(), timeout=5.0)

        # Caller B: close=True. Dispatcher ORs the flag and awaits the
        # in-flight task.
        task_b = asyncio.create_task(
            game_router._finalize_game_route_state(
                state, reason="B_close", close_game_session=True,
            )
        )
        await asyncio.sleep(0)

        # Release the barrier so inner reaches its close-site check with
        # the OR-merged flag set to True.
        barrier.set()
        result_a, result_b = await asyncio.wait_for(
            asyncio.gather(task_a, task_b), timeout=5.0,
        )

        assert result_a["game_session_closed"] is True
        assert result_b["game_session_closed"] is True
        # Exactly one close, despite the dispatcher recheck path being
        # eligible to fire.
        assert fake_session.close_calls == 1
        assert key not in game_router._game_sessions


@pytest.mark.unit
@pytest.mark.asyncio
async def test_b1_chat_short_circuits_when_route_exit_started(monkeypatch):
    """B1/B2: ``_run_game_chat`` must short-circuit if the route flipped to
    ``_exit_flow_started`` before the call lands. Otherwise it would issue
    a ``stream_text`` against a session that finalize is about to close.

    CodeRabbit Minor follow-up (PR #1127): pin the assertion to the
    session-creation path. A pure return-value check would still pass if
    the implementation regressed to "first build a session, then notice
    the route is inactive, then return ``skipped``" — leaking the freshly
    built ``OmniOfflineClient``. Monkeypatch ``_get_or_create_session`` to
    raise on call AND assert ``_game_sessions`` / ``_game_session_create_locks``
    were never touched, so any future regression that takes the slow path
    fails this test loudly.
    """
    with reset_game_route_state():
        state = _activate_route("Lan", "soccer", "match_b1")
        state["_exit_flow_started"] = True

        async def _explode(*_args, **_kwargs):
            raise AssertionError(
                "B1/B2 short-circuit must trip BEFORE _get_or_create_session; "
                "the route is inactive and creating a session would leak."
            )

        monkeypatch.setattr(game_router, "_get_or_create_session", _explode)

        # Snapshot cache state to verify nothing was inserted.
        sessions_snapshot = dict(game_router._game_sessions)
        create_locks_snapshot = dict(game_router._game_session_create_locks)

        result = await game_router._run_game_chat(
            "soccer", "match_b1", {"kind": "free-ball", "lanlan_name": "Lan"},
        )
        assert result.get("skipped") == "route_inactive"
        assert result.get("line") == ""

        # Cache invariants: no new entries materialized via the slow path.
        assert game_router._game_sessions == sessions_snapshot
        assert game_router._game_session_create_locks == create_locks_snapshot


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

        results = await asyncio.wait_for(
            asyncio.gather(
                game_router._get_or_create_session("soccer", "match_b6", "Lan"),
                game_router._get_or_create_session("soccer", "match_b6", "Lan"),
            ),
            timeout=5.0,
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

    CodeRabbit Minor follow-up (PR #1127): seed at least TWO active
    routes for the same ``lanlan_name`` (different ``game_type``) and
    assert ``n == 2``. With a single route, an implementation that only
    closes the first match — leaving sibling routes for the same
    character alive after a character switch — would still pass. Two
    routes guards against that regression directly.
    """
    _stub_archive_calls(monkeypatch)
    with reset_game_route_state():
        state_soccer = _activate_route("Lan", "soccer", "match_b8")
        state_chess = _activate_route("Lan", "chess", "match_b8_chess")
        fake_session_soccer = _FakeOmniSession(name="b8_soccer")
        fake_session_chess = _FakeOmniSession(name="b8_chess")
        soccer_key = game_router._game_session_key("Lan", "soccer", "match_b8")
        chess_key = game_router._game_session_key("Lan", "chess", "match_b8_chess")
        for key, fake in (
            (soccer_key, fake_session_soccer),
            (chess_key, fake_session_chess),
        ):
            game_router._game_sessions[key] = {
                "session": fake,
                "reply_chunks": [],
                "lanlan_name": "Lan",
                "lanlan_prompt": "",
                "source": {},
                "last_activity": 0,
                "lock": asyncio.Lock(),
                "instructions": "",
            }

        n = await game_router.finalize_game_routes_for_character("Lan")

        assert n == 2
        # Both routes for ``Lan`` are now inactive and their sessions closed.
        for state in (state_soccer, state_chess):
            assert state.get("_exit_flow_started") is True
            assert state.get("game_route_active") is False
        assert fake_session_soccer.close_calls == 1
        assert fake_session_chess.close_calls == 1
        # Calling again is a no-op (both routes already inactive).
        n_again = await game_router.finalize_game_routes_for_character("Lan")
        assert n_again == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_postgame_bypass_runs_chat_after_finalize(monkeypatch):
    """codex P1 follow-up (PR #1127 thread r3182095129): postgame text
    generation runs AFTER ``_finalize_game_route_state`` flips the route
    to inactive, on purpose. The B1/B2 short-circuits in ``_run_game_chat``
    must therefore be bypass-able for this designed teardown step,
    otherwise non-Realtime sessions silently lose the postgame bubble.

    With ``allow_postgame=True`` the route-active gates are skipped and a
    fresh chat call lands successfully. The test pins the contract:
    given an inactive route state, the chat returns a non-empty line and
    no ``skipped`` marker.
    """
    with reset_game_route_state():
        state = _activate_route("Lan", "soccer", "match_postgame_bypass")
        # Mark the route as already finalized — this is exactly the
        # state postgame observes when ``_complete_game_end_from_payload``
        # runs ``_deliver_game_postgame`` after finalize.
        state["_exit_flow_started"] = True
        state["game_route_active"] = False

        captured_callback = {"fn": None}

        class _StubOmni:
            def __init__(self, **kwargs):
                captured_callback["fn"] = kwargs.get("on_text_delta")
                self._closed = False

            async def connect(self, *, instructions: str = ""):
                pass

            async def close(self):
                self._closed = True

            async def stream_text(self, text: str):
                cb = captured_callback["fn"]
                if cb is not None:
                    await cb("postgame line", True)

            async def update_session(self, config):
                pass

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

        # Without allow_postgame: short-circuits to ``route_inactive``.
        baseline = await game_router._run_game_chat(
            "soccer",
            "match_postgame_bypass",
            {"kind": "postgame", "lanlan_name": "Lan"},
        )
        assert baseline.get("skipped") == "route_inactive"
        assert baseline.get("line") == ""

        # With allow_postgame=True: bypass trips, chat runs to completion.
        result = await game_router._run_game_chat(
            "soccer",
            "match_postgame_bypass",
            {"kind": "postgame", "lanlan_name": "Lan"},
            allow_postgame=True,
        )
        assert result.get("skipped") is None, result
        assert result.get("line") == "postgame line"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_postgame_text_bubble_closes_session_after_finalize(monkeypatch):
    """codex P1 follow-up (PR #1127 thread r3182095129): the postgame
    bypass must NOT leak the freshly-built ``OmniOfflineClient``.

    ``_deliver_postgame_text_bubble`` runs after finalize has already
    evicted the prior session. The bypass lets us build a NEW one to
    generate the postgame line; nothing else in the lifecycle would
    close it (which is exactly why the original B1/B2 guards existed),
    so the bubble itself owns cleanup. Regressing the cleanup means
    long-running processes accumulate one open ``OmniOfflineClient``
    per finished game over uptime.
    """
    _stub_archive_calls(monkeypatch)
    with reset_game_route_state():
        state = _activate_route("Lan", "soccer", "match_postgame_leak")
        # Simulate post-finalize state — the prior session is already
        # gone (finalize closed it before postgame ran).
        state["_exit_flow_started"] = True
        state["game_route_active"] = False

        captured_callback = {"fn": None}
        constructed = []

        class _StubOmni:
            def __init__(self, **kwargs):
                captured_callback["fn"] = kwargs.get("on_text_delta")
                self.closed = False
                constructed.append(self)

            async def connect(self, *, instructions: str = ""):
                pass

            async def close(self):
                self.closed = True

            async def stream_text(self, text: str):
                cb = captured_callback["fn"]
                if cb is not None:
                    await cb("postgame bubble line", True)

            async def update_session(self, config):
                pass

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

        # Minimal stub SessionManager mirroring the prepare/finish/feed_tts
        # contract that ``_deliver_postgame_text_bubble`` calls.
        class _StubManager:
            is_active = False
            session = None
            current_speech_id = "speech-postgame"
            state = None

            def __init__(self):
                self.delivered = None

            async def prepare_proactive_delivery(self, *, min_idle_secs=0.0):
                return True

            async def finish_proactive_delivery(self, line, *, expected_speech_id=None):
                self.delivered = line
                return True

            async def feed_tts_chunk(self, line, *, expected_speech_id=None):
                pass

        mgr = _StubManager()
        archive = {
            "lanlan_name": "Lan",
            "summary": "",
            "last_full_dialogues": [],
            "last_state": {},
            "finalScore": {},
            "preGameContext": {},
        }
        options = {"enabled": True, "max_chars": 60, "include_last_dialogues": 0}

        # Sanity: cache is empty before postgame runs.
        key = game_router._game_session_key("Lan", "soccer", "match_postgame_leak")
        assert key not in game_router._game_sessions

        result = await game_router._deliver_postgame_text_bubble(
            "soccer", "match_postgame_leak", mgr, archive, options,
        )

        # Bubble committed: postgame line was generated AND finished.
        assert result.get("ok") is True, result
        assert result.get("action") == "chat", result
        assert result.get("line") == "postgame bubble line"
        assert mgr.delivered == "postgame bubble line"

        # Lifecycle invariant: exactly one client built, and it was
        # closed + evicted from ``_game_sessions`` after the bubble.
        assert len(constructed) == 1
        assert constructed[0].closed is True
        assert key not in game_router._game_sessions
        assert key not in game_router._game_session_create_locks


@pytest.mark.unit
@pytest.mark.asyncio
async def test_postgame_text_bubble_identity_gates_close(monkeypatch):
    """codex P1 / CR Major (PR #1127 r3182247827 / r3182218166): postgame
    cleanup must close ONLY the entry it built, never the cache slot
    blindly. After ``_complete_game_end_from_payload`` releases
    ``end_route_lock``, a fresh ``/route/start`` for the same
    ``(lanlan, game_type, session_id)`` can replace the cache entry
    before postgame's ``finally`` runs. A key-based close would tear
    down the new route's freshly-built ``OmniOfflineClient``.

    Invariant: between ``_run_game_chat(..., allow_postgame=True)``
    returning and the bubble's ``finally`` firing, swap the cache slot
    with a peer-owned entry. The peer's session must remain intact and
    cached after the bubble completes.
    """
    _stub_archive_calls(monkeypatch)
    with reset_game_route_state():
        state = _activate_route("Lan", "soccer", "match_postgame_replace")
        state["_exit_flow_started"] = True
        state["game_route_active"] = False

        captured_callback = {"fn": None}
        constructed: list = []

        class _StubOmni:
            def __init__(self, **kwargs):
                captured_callback["fn"] = kwargs.get("on_text_delta")
                self.closed = False
                constructed.append(self)

            async def connect(self, *, instructions: str = ""):
                pass

            async def close(self):
                self.closed = True

            async def stream_text(self, text: str):
                cb = captured_callback["fn"]
                if cb is not None:
                    await cb("postgame bubble line", True)

            async def update_session(self, config):
                pass

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

        peer_session = _FakeOmniSession(name="peer_after_finalize")
        peer_entry = {
            "session": peer_session,
            "reply_chunks": [],
            "lanlan_name": "Lan",
            "lanlan_prompt": "",
            "source": {},
            "last_activity": 0,
            "lock": asyncio.Lock(),
            "instructions": "",
        }
        peer_lock = asyncio.Lock()
        key = game_router._game_session_key(
            "Lan", "soccer", "match_postgame_replace",
        )

        # Hook ``finish_proactive_delivery`` — it runs AFTER chat finishes
        # but BEFORE the bubble's finally — to simulate a racing
        # ``/route/start`` whose first ``/game_chat`` built a fresh entry
        # and replaced postgame's cache slot.
        class _StubManager:
            is_active = False
            session = None
            current_speech_id = "speech-postgame"
            state = None

            def __init__(self):
                self.delivered = None

            async def prepare_proactive_delivery(self, *, min_idle_secs=0.0):
                return True

            async def finish_proactive_delivery(self, line, *, expected_speech_id=None):
                # Simulate the race: a peer ``/route/start`` activated
                # for the same key and its first ``/game_chat`` built a
                # fresh entry, replacing postgame's cache slot.
                game_router._game_sessions[key] = peer_entry
                game_router._game_session_create_locks[key] = peer_lock
                self.delivered = line
                return True

            async def feed_tts_chunk(self, line, *, expected_speech_id=None):
                pass

        mgr = _StubManager()
        archive = {
            "lanlan_name": "Lan",
            "summary": "",
            "last_full_dialogues": [],
            "last_state": {},
            "finalScore": {},
            "preGameContext": {},
        }
        options = {"enabled": True, "max_chars": 60, "include_last_dialogues": 0}

        result = await game_router._deliver_postgame_text_bubble(
            "soccer", "match_postgame_replace", mgr, archive, options,
        )

        assert result.get("ok") is True, result
        assert result.get("action") == "chat", result
        assert result.get("line") == "postgame bubble line"

        # Postgame built exactly one ``OmniOfflineClient`` and closed it.
        assert len(constructed) == 1
        assert constructed[0].closed is True

        # CRITICAL: the peer's entry survived. The bubble must not have
        # touched the cache slot once identity diverged from its own
        # entry, and must NOT have closed the peer's session.
        assert peer_session.close_calls == 0, (
            "postgame finally closed the peer route's session — "
            "key-based close regressed"
        )
        assert game_router._game_sessions.get(key) is peer_entry, (
            "postgame finally evicted the peer route's cache entry — "
            "key-based eviction regressed"
        )
        assert game_router._game_session_create_locks.get(key) is peer_lock, (
            "postgame finally evicted the peer route's create lock — "
            "key-based eviction regressed"
        )


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

        results = await asyncio.wait_for(
            asyncio.gather(
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
            ),
            timeout=5.0,
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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_or_create_session_canonical_key_mismatch_leaves_no_orphan_lock(
    monkeypatch,
):
    """CodeRabbit follow-up (PR #1127): when caller passes an empty
    ``lanlan_name`` and ``_get_character_info`` canonicalizes it to a
    non-empty name, the raw key (``""+game_type+session_id``) must NOT
    leave an orphan entry in ``_game_session_create_locks``.

    Pre-fix shape acquired the create lock under the raw key, then
    canonicalized inside the lock and acquired a SECOND lock under the
    canonical key. The session was stored under the canonical key, so
    ``_close_and_remove_session`` only evicted
    ``_game_session_create_locks[canonical_key]`` — the raw-key entry
    accumulated over uptime as an unreachable, never-cleaned ``Lock``.

    Post-fix shape resolves the canonical key BEFORE locking, so only
    one lock is ever taken and the dict only ever contains the
    canonical-key entry (which the existing eviction path already
    handles).
    """
    with reset_game_route_state():
        raw_key = game_router._game_session_key("", "soccer", "match_canon")
        canonical_key = game_router._game_session_key("Lan", "soccer", "match_canon")
        assert raw_key != canonical_key  # sanity: keys actually differ.

        # Drop any leftovers from earlier runs.
        game_router._game_session_create_locks.pop(raw_key, None)
        game_router._game_session_create_locks.pop(canonical_key, None)

        class _StubOmni:
            def __init__(self, **kwargs):
                self._closed = False

            async def connect(self, *, instructions: str = ""):
                pass

            async def close(self):
                self._closed = True

        import main_logic.omni_offline_client as omni_module
        monkeypatch.setattr(omni_module, "OmniOfflineClient", _StubOmni)

        def _stub_char_info(name):
            # Caller-supplied ``name`` is empty; canonicalize to "Lan".
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

        # Caller passes empty lanlan_name → triggers canonical_key != key.
        entry = await game_router._get_or_create_session(
            "soccer", "match_canon", "",
        )

        # Session is stored under the canonical key, not the raw key.
        assert canonical_key in game_router._game_sessions
        assert raw_key not in game_router._game_sessions
        assert game_router._game_sessions[canonical_key] is entry

        # Critical invariant: no orphan lock under the raw key. Only the
        # canonical key may have a lock entry — and that one will be
        # evicted by ``_close_and_remove_session`` when the session ends.
        assert raw_key not in game_router._game_session_create_locks, (
            "Raw-key create lock leaked when canonical_key != key; "
            "_close_and_remove_session would never evict it."
        )

        # Round-trip: closing the session via the canonical lookup must
        # still clean up the (only) lock entry, leaving the dict free of
        # any per-session entries for this case.
        closed = await game_router._close_and_remove_session(
            "soccer", "match_canon", "Lan",
        )
        assert closed is True
        assert canonical_key not in game_router._game_session_create_locks
        assert raw_key not in game_router._game_session_create_locks


@pytest.mark.unit
@pytest.mark.asyncio
async def test_character_switch_finalize_blocks_concurrent_route_start_for_same_lanlan(
    monkeypatch,
):
    """codex P2 follow-up (PR #1127 thread 3182019570):
    ``finalize_game_routes_for_character`` must serialize with concurrent
    ``/route/start`` calls for the same ``lanlan_name`` via the per-lanlan
    supersede lock — otherwise a route activated AFTER finalize's snapshot
    escapes cleanup and the character switch completes with an old-character
    route still alive.

    Pre-fix shape: ``finalize_game_routes_for_character`` snapshots
    ``_game_route_states`` BEFORE acquiring any supersede lock. A
    concurrent ``/route/start`` for the same ``lanlan_name`` could:
      1. land its activation between the snapshot and the iterate step,
      2. miss being included in the snapshot,
      3. survive the character switch unfinalized.

    Post-fix shape: finalize takes ``_route_supersede_locks[lanlan_name]``
    (the OUTER lock per the lock-ordering rule documented in
    ``utils/game_route_state.py``) BEFORE snapshotting. Any concurrent
    ``/route/start`` for the same lanlan_name blocks on that lock until
    cleanup finishes; the new route either lands strictly before the
    snapshot (in which case finalize observes and cleans it) or strictly
    after (in which case finalize already returned).

    This test forces the race by externally holding the supersede lock
    while ``finalize_game_routes_for_character`` is in flight, and then
    firing a concurrent ``/route/start``. With the fix, finalize must
    block on the supersede lock until we release it; without the fix,
    finalize would complete immediately because it never tries to take
    the lock.
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
        # Drop any leftover supersede / route locks so this test starts
        # fresh — locks live across tests because they're a process-global
        # registry by design (see comment on ``_route_state_locks``).
        from utils import game_route_state as grs_mod
        grs_mod._route_supersede_locks.pop("Lan", None)
        grs_mod._route_state_locks.pop(grs_mod._route_state_key("Lan", "soccer"), None)

        # Existing active route for the OLD character: this is what
        # ``finalize_game_routes_for_character`` is supposed to clean up.
        old_state = _activate_route("Lan", "soccer", "match_old")
        fake_session = _FakeOmniSession(name="charswitch_old")
        old_key = game_router._game_session_key("Lan", "soccer", "match_old")
        game_router._game_sessions[old_key] = {
            "session": fake_session,
            "reply_chunks": [],
            "lanlan_name": "Lan",
            "lanlan_prompt": "",
            "source": {},
            "last_activity": 0,
            "lock": asyncio.Lock(),
            "instructions": "",
        }

        # Acquire the supersede lock externally to simulate a /route/start
        # already in progress. Post-fix: finalize must block on this lock.
        # Pre-fix: finalize would NOT take this lock and would proceed
        # without serialization.
        supersede_lock = grs_mod._get_supersede_lock("Lan")
        await supersede_lock.acquire()

        try:
            # Schedule finalize in the background. Post-fix, this awaits
            # the supersede lock and never returns until we release it.
            finalize_task = asyncio.create_task(
                game_router.finalize_game_routes_for_character("Lan")
            )

            # Yield enough times to let finalize_task run as far as it can.
            # Post-fix: it parks on supersede_lock.acquire().
            for _ in range(10):
                await asyncio.sleep(0)

            # Post-fix invariant: finalize is still running (blocked on
            # supersede lock). Pre-fix: finalize would already be done.
            assert not finalize_task.done(), (
                "finalize_game_routes_for_character must block on the "
                "per-lanlan supersede lock; it appears to have skipped "
                "the lock and snapshotted _game_route_states without "
                "serialization (codex P2 race window)."
            )
            # The old route is still active because finalize is parked
            # before it could snapshot.
            assert old_state.get("game_route_active") is True
        finally:
            # Release the lock so finalize can proceed and we don't leak
            # a stuck task.
            supersede_lock.release()

        # Finalize now proceeds: snapshot, iterate, cleanup.
        n = await finalize_task
        assert n == 1
        assert old_state.get("_exit_flow_started") is True
        assert old_state.get("game_route_active") is False
        assert fake_session.close_calls == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_character_switch_finalize_serializes_with_concurrent_route_start_via_gather(
    monkeypatch,
):
    """codex P2 follow-up (PR #1127 thread 3182019570) — gather variant.

    End-to-end: ``finalize_game_routes_for_character`` and a concurrent
    ``game_route_start`` for the same ``lanlan_name`` are launched via
    ``asyncio.gather``. After both complete, the per-lanlan supersede
    lock guarantees that the resulting state is one of the two stable
    interleavings (route_start-then-finalize, or finalize-then-
    route_start), never a half-state where finalize "missed" a route
    that was activated mid-iteration.

    The load-bearing assertion is in the FIRST test
    (``test_character_switch_finalize_blocks_concurrent_route_start_for_same_lanlan``)
    which directly verifies the supersede lock is taken; this test just
    smoke-checks that running both concurrently doesn't trip any
    deadlock or lock-ordering inversion. The lock order in
    ``finalize_game_routes_for_character`` is OUTER ``_route_supersede_locks``
    then INNER ``_route_state_locks``, identical to ``game_route_start`` —
    no deadlock window.
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
        from utils import game_route_state as grs_mod
        grs_mod._route_supersede_locks.pop("Lan", None)
        grs_mod._route_state_locks.pop(grs_mod._route_state_key("Lan", "soccer"), None)

        # Both calls fired concurrently. The supersede lock serializes
        # them; whichever lands inside the lock first runs to completion
        # before the other proceeds. No deadlock should occur.
        results = await asyncio.wait_for(
            asyncio.gather(
                game_router.game_route_start(
                    "soccer",
                    _FakeRouteStartRequest({
                        "lanlan_name": "Lan",
                        "session_id": "match_charswitch_race",
                    }),
                ),
                game_router.finalize_game_routes_for_character("Lan"),
            ),
            timeout=5.0,  # If serialization is broken, deadlock surfaces here.
        )

        route_start_result, _finalize_count = results
        assert route_start_result.get("ok"), route_start_result

        # Final state: AT MOST one active Lan route. Whichever
        # interleaving the supersede lock chose, the result is a
        # well-defined steady state — never two concurrently-active
        # routes for the same character.
        active_for_lan = [
            key
            for key, state in game_router._game_route_states.items()
            if key[0] == "Lan" and state.get("game_route_active")
        ]
        assert len(active_for_lan) <= 1, active_for_lan


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_or_create_session_evicts_create_lock_on_build_failure(monkeypatch):
    """codex P2 follow-up (PR #1127 r3182157092): when
    ``_build_and_register_game_session`` raises (e.g. ``session.connect``
    fails) the per-key entry in ``_game_session_create_locks`` must be
    evicted on the failure path. Without the eviction every failed
    creation leaks one ``asyncio.Lock`` per unique session_id over
    uptime — and crucially, ``_close_and_remove_session`` (the only
    other place that prunes the lock map) is never called for sessions
    that never registered.
    """
    with reset_game_route_state():
        canonical_key = game_router._game_session_key(
            "Lan", "soccer", "match_build_fail",
        )
        # Drop any leftover from prior runs.
        game_router._game_session_create_locks.pop(canonical_key, None)

        async def _explode(*_args, **_kwargs):
            raise RuntimeError("connect failed (simulated)")

        monkeypatch.setattr(
            game_router, "_build_and_register_game_session", _explode,
        )

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

        with pytest.raises(RuntimeError, match="connect failed"):
            await game_router._get_or_create_session(
                "soccer", "match_build_fail", "Lan",
            )

        # Critical invariant: the per-key create lock must NOT linger
        # after a failed build, because nothing else in the lifecycle
        # would prune it.
        assert canonical_key not in game_router._game_session_create_locks, (
            "Failed _build_and_register_game_session leaked its create lock; "
            "_close_and_remove_session never runs for unregistered sessions."
        )
        # Sanity: nothing was inserted into _game_sessions either.
        assert canonical_key not in game_router._game_sessions


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_or_create_session_keeps_create_lock_when_peer_waits(monkeypatch):
    """codex P2 follow-up — peer-waiter variant: if Thread A's build
    fails while Thread B is parked on the same per-key create lock, the
    eviction must NOT pop the lock from the registry mid-flight.
    Otherwise a third arrival would call ``_get_session_create_lock``,
    receive a fresh ``asyncio.Lock``, and run its own build concurrently
    with B — defeating the build serialization.

    Implementation invariant: when ``create_lock._waiters`` is
    non-empty on the failure path, leave the lock in place. We exercise
    that branch with a real concurrent waiter and assert that AT THE
    MOMENT A's failure unwinds, the registry still points at the
    original lock (and Thread B subsequently re-uses it rather than
    seeing a fresh one).
    """
    with reset_game_route_state():
        canonical_key = game_router._game_session_key(
            "Lan", "soccer", "match_peer_wait",
        )
        game_router._game_session_create_locks.pop(canonical_key, None)

        # The build helper must NOT auto-yield before A reaches the
        # async-with body, so A wins the lock acquisition. A then yields
        # while raising so B has time to park as a waiter.
        a_inside_build = asyncio.Event()
        b_can_unblock = asyncio.Event()
        captured: dict = {}

        async def _explode_a(*_args, **_kwargs):
            captured["lock_at_A_inside"] = (
                game_router._game_session_create_locks.get(canonical_key)
            )
            a_inside_build.set()
            # Park until B has had time to register as a waiter.
            await b_can_unblock.wait()
            # Confirm that B is in fact registered as a waiter on our
            # lock by the time we raise.
            lock = captured["lock_at_A_inside"]
            captured["waiters_seen_at_A_raise"] = list(
                getattr(lock, "_waiters", []) or ()
            )
            raise RuntimeError("A connect failed")

        async def _succeed_b(*args, **kwargs):
            # B's build runs after A fails; capture the lock instance
            # at this point so we can prove it was not swapped.
            captured["lock_at_B_build"] = (
                game_router._game_session_create_locks.get(canonical_key)
            )
            # Build and register a minimal entry so this path resembles
            # a successful retry.
            from asyncio import Lock as _Lock
            entry = {
                "session": _DummySession(),
                "reply_chunks": [],
                "lanlan_name": "Lan",
                "lanlan_prompt": "",
                "source": {},
                "last_activity": 0,
                "lock": _Lock(),
                "instructions": "",
            }
            game_router._game_sessions[canonical_key] = entry
            return entry

        class _DummySession:
            async def close(self):
                pass

        # First call (A) explodes; second call (B) succeeds — we swap
        # the dispatch after A's failure so the same monkeypatch slot
        # serves both peers.
        current_fn = {"fn": _explode_a}

        async def _dispatch(*args, **kwargs):
            return await current_fn["fn"](*args, **kwargs)

        monkeypatch.setattr(
            game_router, "_build_and_register_game_session", _dispatch,
        )

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

        async def _a_runner():
            try:
                await game_router._get_or_create_session(
                    "soccer", "match_peer_wait", "Lan",
                )
            except RuntimeError:
                pass

        async def _b_runner():
            # Wait until A is INSIDE the lock so we park as a real
            # waiter (not just sneak in before A acquires).
            await a_inside_build.wait()
            await game_router._get_or_create_session(
                "soccer", "match_peer_wait", "Lan",
            )

        async def _release_a_after_b_parks():
            await a_inside_build.wait()
            # Yield several times to let B reach the async-with on the
            # create lock and register as a waiter.
            for _ in range(20):
                await asyncio.sleep(0)
            # Swap the build dispatcher so B's build succeeds when it
            # eventually acquires the lock.
            current_fn["fn"] = _succeed_b
            b_can_unblock.set()

        await asyncio.wait_for(
            asyncio.gather(_a_runner(), _b_runner(), _release_a_after_b_parks()),
            timeout=5.0,
        )

        # Invariant 1: A saw a non-None waiter list at the moment of
        # raise. This is the precondition for the conditional-pop guard
        # to actually be exercised; if the test scaffold never produced
        # a real waiter the assertion below would be vacuous.
        assert captured["waiters_seen_at_A_raise"], (
            "Test scaffold failed to register Thread B as a waiter; "
            "the conditional-pop branch was not exercised."
        )

        # Invariant 2 (load-bearing): the lock B saw when its build ran
        # is the SAME lock A had. If A's failure had unconditionally
        # popped the registry, B's wake-up would have been on the old
        # lock object but the registry would point at a fresh one for
        # the next arrival — and `_succeed_b` here would observe a
        # different lock instance, mismatched with the one B awaited.
        assert captured["lock_at_B_build"] is captured["lock_at_A_inside"], (
            "Create lock was swapped while a peer was awaiting it; "
            "build serialization is broken."
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_partial_connect_race_does_not_orphan_session(monkeypatch):
    """CR Major (PR #1127 r3182158697): if a finalize runs to a no-op
    while ``_build_and_register_game_session`` is mid ``session.connect``
    (because the entry isn't registered yet), the freshly-built
    ``OmniOfflineClient`` would otherwise survive in ``_game_sessions``
    until the 30-min idle sweep. The post-lock route_inactive short-
    circuit in ``_run_game_chat`` must evict + close the orphan.

    Race interleaving exercised here:
      1. Thread A calls ``_run_game_chat`` → passes pre-create gate
         (route still active) → enters ``_get_or_create_session`` →
         awaits ``connect`` (we block this on a barrier).
      2. Thread B flips ``_exit_flow_started`` / ``game_route_active``
         on the route state, then calls ``_close_and_remove_session``
         — which is a no-op because the entry isn't registered yet.
      3. Barrier released → Thread A's build completes and registers
         the entry → Thread A acquires ``entry['lock']`` → post-lock
         route-active gate trips → fix path closes + evicts.

    Pre-fix: Thread A's session lingers in ``_game_sessions`` and is
    never closed (``close_calls == 0``).
    Post-fix: Thread A evicts itself and awaits ``session.close()``
    once before returning ``skipped=route_inactive``.
    """
    with reset_game_route_state():
        state = _activate_route("Lan", "soccer", "match_partial_connect")
        canonical_key = game_router._game_session_key(
            "Lan", "soccer", "match_partial_connect",
        )
        game_router._game_session_create_locks.pop(canonical_key, None)

        connect_barrier = asyncio.Event()
        connect_started = asyncio.Event()
        constructed: list = []

        class _BlockingOmni:
            def __init__(self, **kwargs):
                self.connect_calls = 0
                self.close_calls = 0
                self.stream_calls = 0
                self._closed = False
                constructed.append(self)

            async def connect(self, *, instructions: str = ""):
                self.connect_calls += 1
                connect_started.set()
                # Park here until Thread B has flipped the route state
                # and run its no-op _close_and_remove_session.
                await connect_barrier.wait()

            async def close(self):
                self.close_calls += 1
                self._closed = True

            async def stream_text(self, text: str):
                if self._closed:
                    raise RuntimeError("stream_text on closed session")
                self.stream_calls += 1

            async def update_session(self, config):
                pass

        import main_logic.omni_offline_client as omni_module
        monkeypatch.setattr(omni_module, "OmniOfflineClient", _BlockingOmni)

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

        # Thread A: drive _run_game_chat. It will block on connect()
        # until we release the barrier.
        chat_task = asyncio.create_task(
            game_router._run_game_chat(
                "soccer",
                "match_partial_connect",
                {"kind": "free-ball", "lanlan_name": "Lan"},
            )
        )

        # Wait until Thread A is parked inside connect(). Past this
        # point the route's pre-create gate has been crossed but the
        # entry isn't registered in _game_sessions yet.
        await asyncio.wait_for(connect_started.wait(), timeout=2.0)
        assert canonical_key not in game_router._game_sessions

        # Thread B: simulate finalize racing in. Flip the route state
        # to inactive, then call _close_and_remove_session — which is a
        # no-op because Thread A's entry hasn't been inserted yet.
        state["_exit_flow_started"] = True
        state["game_route_active"] = False
        no_op_close = await game_router._close_and_remove_session(
            "soccer", "match_partial_connect", "Lan",
        )
        # Sanity: finalize's close was indeed a no-op.
        assert no_op_close is False
        assert canonical_key not in game_router._game_sessions

        # Now release the barrier so Thread A's connect returns. The
        # build then registers the entry, _run_game_chat acquires
        # entry['lock'], hits the post-lock route_inactive gate, and
        # (post-fix) evicts + closes its own orphan.
        connect_barrier.set()
        result = await asyncio.wait_for(chat_task, timeout=2.0)

        assert result.get("skipped") == "route_inactive"
        assert result.get("line") == ""

        # Lifecycle invariants — these are what the fix guarantees:
        # exactly one client built, closed exactly once, no orphan
        # left behind in either the session cache or the create-lock
        # map.
        assert len(constructed) == 1
        assert constructed[0].close_calls == 1, (
            "Orphan OmniOfflineClient was not closed; partial-connect "
            "race leaked a session past finalize."
        )
        assert canonical_key not in game_router._game_sessions, (
            "Orphan entry survived in _game_sessions after the "
            "route_inactive short-circuit."
        )
        assert canonical_key not in game_router._game_session_create_locks
        # Stream was never invoked — short-circuit fired before
        # stream_text.
        assert constructed[0].stream_calls == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_session_cancellation_closes_half_open_client(monkeypatch):
    """CR Major (PR #1127 r3182318081): if ``_build_and_register_game_session``
    is cancelled mid ``await session.connect(...)``, the half-open client
    must still be closed. ``asyncio.CancelledError`` does not inherit from
    ``Exception`` in Python 3.8+, so a bare ``except Exception`` would
    swallow nothing here and leak the freshly built client.
    """
    with reset_game_route_state():
        canonical_key = game_router._game_session_key(
            "Lan", "soccer", "match_cancel_during_connect",
        )
        game_router._game_session_create_locks.pop(canonical_key, None)

        connect_started = asyncio.Event()
        # Connect parks on this event forever — the test cancels the
        # build task while it is awaiting here.
        connect_release = asyncio.Event()
        constructed: list = []

        class _StubOmni:
            def __init__(self, **kwargs):
                self.close_calls = 0
                constructed.append(self)

            async def connect(self, *, instructions: str = ""):
                connect_started.set()
                await connect_release.wait()

            async def close(self):
                self.close_calls += 1

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

        build_task = asyncio.create_task(
            game_router._get_or_create_session(
                "soccer", "match_cancel_during_connect", "Lan",
            )
        )
        await asyncio.wait_for(connect_started.wait(), timeout=2.0)

        build_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await build_task

        # Invariant: the half-open client must have been closed exactly
        # once on the cancellation path. Pre-fix, this assertion fails
        # because ``except Exception`` does not catch CancelledError.
        assert len(constructed) == 1
        assert constructed[0].close_calls == 1, (
            "Cancelled session.connect leaked a half-open OmniOfflineClient; "
            "CancelledError must trigger the same cleanup as Exception."
        )
        # Cache state is clean: nothing was registered, lock evicted.
        assert canonical_key not in game_router._game_sessions
        assert canonical_key not in game_router._game_session_create_locks


@pytest.mark.unit
@pytest.mark.asyncio
async def test_close_and_remove_session_identity_gates_pop_after_lock_wait():
    """codex P1 (PR #1127 r3182582714): ``_close_and_remove_session``
    must identity-gate ``_game_sessions.pop`` against the captured
    ``entry`` so a peer that rotated the cache to ``entry_NEW`` while we
    waited on ``entry['lock']`` does NOT lose its live session.

    Race window:
      1. Caller A reads entry_A from the cache, awaits entry_A['lock'].
      2. A peer closer (also queued on entry_A['lock']) wins the lock
         first, pops entry_A, closes it, releases the lock.
      3. ``/route/start`` reuses the same (lanlan, game_type, session_id)
         and inserts entry_NEW at the same key with a fresh
         ``OmniOfflineClient``.
      4. Caller A finally acquires entry_A['lock'] and proceeds to its
         pop — pre-fix this evicts entry_NEW (NOT entry_A) from the
         cache and closes entry_NEW's live session. Post-fix the pop is
         identity-gated on ``cache.get(key) is entry`` so Caller A
         leaves entry_NEW alone and only closes its OWN entry_A
         session.

    The two ``_FakeOmniSession`` instances are distinct objects, so the
    test asserts on the per-object ``close_calls`` to verify which side
    was actually closed.
    """
    with reset_game_route_state():
        key = game_router._game_session_key("Lan", "soccer", "match_close_idgate")
        game_router._game_sessions.pop(key, None)
        game_router._game_session_create_locks.pop(key, None)

        session_a = _FakeOmniSession(name="entry_A")
        lock_a = asyncio.Lock()
        entry_a = {
            "session": session_a,
            "reply_chunks": [],
            "lanlan_name": "Lan",
            "lanlan_prompt": "",
            "source": {},
            "last_activity": 0,
            "lock": lock_a,
            "instructions": "",
        }
        game_router._game_sessions[key] = entry_a

        # A peer task holds entry_A's lock — stand-in for "another caller
        # already won the lock acquisition race". Caller A will queue
        # behind it.
        peer_release = asyncio.Event()

        async def _hold_entry_a_lock():
            async with lock_a:
                await peer_release.wait()

        peer_task = asyncio.create_task(_hold_entry_a_lock())
        # Yield until the peer has actually acquired lock_a.
        for _ in range(50):
            if lock_a.locked():
                break
            await asyncio.sleep(0)
        assert lock_a.locked(), "peer task failed to acquire entry_A's lock"

        # Caller A: enters _close_and_remove_session, captures entry_a,
        # then awaits lock_a (parked behind the peer).
        caller_a_task = asyncio.create_task(
            game_router._close_and_remove_session("soccer", "match_close_idgate", "Lan")
        )
        # Spin until Caller A is waiting on the lock (i.e., it has read
        # entry and is queued — lock has waiters).
        for _ in range(200):
            waiters = getattr(lock_a, "_waiters", None)
            if waiters and len(waiters) > 0:
                break
            await asyncio.sleep(0)
        waiters = getattr(lock_a, "_waiters", None)
        assert waiters and len(waiters) > 0, (
            "Caller A failed to queue on entry_A['lock']; "
            "the test cannot reproduce the race without that queueing."
        )

        # Simulate the peer-rotation: the lock holder pops entry_A,
        # closes it, and a fresh /route/start inserts entry_NEW at the
        # same key with a NEW lock + NEW session BEFORE we release
        # lock_a. We perform these synchronously while Caller A is
        # still parked.
        evicted = game_router._game_sessions.pop(key, None)
        assert evicted is entry_a
        game_router._game_session_create_locks.pop(key, None)

        session_b = _FakeOmniSession(name="entry_NEW")
        entry_new = {
            "session": session_b,
            "reply_chunks": [],
            "lanlan_name": "Lan",
            "lanlan_prompt": "",
            "source": {},
            "last_activity": 0,
            "lock": asyncio.Lock(),
            "instructions": "",
        }
        game_router._game_sessions[key] = entry_new
        # Stand-in create lock for entry_NEW so the assertion below can
        # distinguish "evicted by us" from "never present".
        entry_new_create_lock = game_router._get_session_create_lock(key)
        assert key in game_router._game_session_create_locks

        # Release the peer's hold on lock_a so Caller A wakes up.
        peer_release.set()
        await peer_task
        result = await asyncio.wait_for(caller_a_task, timeout=5.0)

        # Caller A reports success — it closed its own entry's session.
        assert result is True

        # Identity gate held: entry_NEW is still in the cache, untouched.
        assert game_router._game_sessions.get(key) is entry_new, (
            "Identity gate failed: Caller A's pop evicted entry_NEW. "
            "This is the codex P1 bug — a fresh /route/start's session "
            "would now be orphaned and its session closed by us."
        )
        assert session_b.close_calls == 0, (
            "entry_NEW's session was closed by Caller A; identity gate "
            "must protect it because Caller A captured entry_A, not "
            "entry_NEW."
        )
        # entry_NEW's create lock must also still be present.
        assert game_router._game_session_create_locks.get(key) is entry_new_create_lock

        # entry_A's session was closed (Caller A always owns its captured
        # entry's lifecycle, regardless of cache state).
        assert session_a.close_calls == 1, (
            "entry_A's session must be closed by Caller A even when the "
            "cache rotated; the captured entry is our responsibility."
        )

        # Cleanup so we leave the registry clean for sibling tests.
        game_router._game_sessions.pop(key, None)
        game_router._game_session_create_locks.pop(key, None)
