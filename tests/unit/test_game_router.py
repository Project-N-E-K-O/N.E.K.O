import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from starlette.responses import JSONResponse

from .game_route_test_helpers import (
    gr_patch_all as _gr_patch_all,
    mark_game_started as _mark_game_started,
    reset_game_route_state,
    set_soccer_game_memory_policy as _set_soccer_game_memory_policy,
)
from main_routers import game_router
from main_routers.game_router import archive as gr_archive
from main_routers.game_router import badminton_scores as gr_scores
from main_routers.game_router import balance as gr_balance
from main_routers.game_router import char_info as gr_char_info
from main_routers.game_router import game_context as gr_game_context
from main_routers.game_router import logs as gr_logs
from main_routers.game_router import postgame as gr_postgame
from main_routers.game_router import pregame as gr_pregame
from main_routers.game_router import runtime as gr_runtime
from main_routers.game_router import route_lifecycle as gr_route_lifecycle
from main_routers.game_router import visible_events as gr_visible_events
from main_routers.system_router import AUTOSTART_CSRF_TOKEN
from main_logic.core import LLMSessionManager
from tests.fake_clock import patch_module_clock
from utils import game_log
from utils.llm_client import AIMessage, HumanMessage


class _FakeRequest:
    def __init__(self, payload, *, mutation_headers=True, path="/api/game/test"):
        self._payload = payload
        self.base_url = "http://127.0.0.1:8000/"
        self.url = SimpleNamespace(path=path)
        self.method = "POST"
        self.headers = {}
        if mutation_headers:
            self.headers = {
                "origin": "http://127.0.0.1:8000",
                "X-CSRF-Token": AUTOSTART_CSRF_TOKEN,
            }

    async def json(self):
        return self._payload


class _LocaleTrackingManager:
    def __init__(self, language="en", *, explicit=False, render_language="en"):
        self.user_language = language
        self._user_language_explicit = explicit
        self._conversation_render_language = render_language
        self.language_updates = []

    def set_user_language(self, language):
        self.language_updates.append(language)
        self.user_language = language
        self._user_language_explicit = True


def _assert_not_icebreaker_game_route_error(exc: HTTPException, expected_route: str) -> None:
    assert exc.status_code == 400
    assert exc.detail == {
        "ok": False,
        "reason": "not_a_game_route",
        "route": expected_route,
    }


def _put_game_session(lanlan_name, game_type, session_id, session):
    key = gr_runtime._game_session_key(lanlan_name, game_type, session_id)
    gr_runtime._game_sessions[key] = {
        "session": session,
        "reply_chunks": [],
        "lanlan_name": lanlan_name,
        "game_type": game_type,
        "session_id": session_id,
        "last_activity": 0,
        "lock": None,
    }
    return key


def _allow_badminton_score_session(lanlan_name, session_id, mode="duel"):
    state = {
        "game_type": "badminton",
        "session_id": session_id,
        "lanlan_name": lanlan_name,
        "game_route_active": False,
        "mode": mode,
    }
    _mark_game_started(state)
    gr_runtime._game_route_states[gr_runtime._route_state_key(lanlan_name, "badminton")] = state
    gr_scores._remember_badminton_score_session(lanlan_name, session_id, mode)
    return state


@pytest.fixture(autouse=True)
def _clear_game_session_debug_logs():
    game_log._game_session_debug_logs.clear()
    yield
    game_log._game_session_debug_logs.clear()


@pytest.mark.unit
def test_badminton_removed_modes_are_not_public_or_scored():
    assert gr_scores._normalize_badminton_mode("shooter") == "spectator"
    assert gr_scores._normalize_badminton_mode("SHOOTER") == "spectator"
    assert gr_scores._is_badminton_scoring_mode("shooter") is False
    assert gr_scores._normalize_badminton_mode("horse") == "spectator"
    assert gr_scores._normalize_badminton_mode("HORSE") == "spectator"
    assert gr_scores._is_badminton_scoring_mode("horse") is False
    assert gr_scores._normalize_badminton_mode("timed") == "spectator"
    assert gr_scores._normalize_badminton_mode("TIMED") == "spectator"
    assert gr_scores._is_badminton_scoring_mode("timed") is False


@pytest.mark.unit
def test_game_prompt_locale_preserves_session_zh_tw(monkeypatch):
    manager = SimpleNamespace(user_language="zh-TW")
    monkeypatch.setattr(
        gr_char_info,
        "get_session_manager",
        lambda: {"Lan": manager},
    )
    monkeypatch.setattr(
        gr_char_info,
        "get_global_language_full",
        lambda: "zh-CN",
    )

    assert gr_char_info._resolve_game_prompt_locale("Lan") == "zh-TW"
    assert gr_char_info._resolve_game_prompt_language("Lan") == "zh"


@pytest.mark.unit
def test_game_request_marks_matching_seeded_locale_explicit(monkeypatch):
    manager = SimpleNamespace(
        user_language="en",
        _user_language_explicit=False,
        set_user_language=MagicMock(),
    )
    monkeypatch.setattr(
        gr_char_info,
        "get_session_manager",
        lambda: {"Lan": manager},
    )

    assert gr_char_info._absorb_request_language({"language": "en"}, "Lan") == "en"
    manager.set_user_language.assert_called_once_with("en")


@pytest.mark.unit
def test_game_render_language_selects_template_without_marking_session_explicit(monkeypatch):
    manager = SimpleNamespace(
        user_language="en",
        _user_language_explicit=False,
        set_user_language=MagicMock(),
    )
    monkeypatch.setattr(
        gr_char_info,
        "get_session_manager",
        lambda: {"Lan": manager},
    )

    payload = {"render_language": "ja"}
    assert gr_char_info._resolve_game_prompt_locale("Lan", payload) == "ja"
    assert gr_char_info._resolve_game_prompt_language("Lan", payload) == "ja"
    assert gr_char_info._absorb_request_language(payload, "Lan") is None
    manager.set_user_language.assert_not_called()


@pytest.mark.unit
def test_game_archive_does_not_persist_render_only_language():
    archive = {
        "lanlan_name": "Lan",
        "user_language": "ja",
        "user_language_source": "render",
    }
    assert gr_archive._archive_memory_language(archive) is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_route_start_accepts_direct_debug_session(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    async def fake_pregame_context(**kwargs):
        assert kwargs["neko_initiated"] is False
        assert kwargs["prompt_locale"] == "zh-TW"
        return gr_pregame._default_badminton_pregame_context(mode="duel"), "lightweight", ""

    _gr_patch_all(monkeypatch, "_build_badminton_pregame_context", fake_pregame_context)

    with reset_game_route_state():
        result = await gr_runtime.game_route_start(
            "badminton",
            _FakeRequest({
                "lanlan_name": "Lan",
                "session_id": "debug-badminton",
                "mode": "duel",
                "i18n_language": "zh-TW",
            }),
        )

        assert result["ok"] is True
        assert result["state"]["game_type"] == "badminton"
        assert result["state"]["session_id"] == "debug-badminton"
        assert result["state"]["mode"] == "duel"
        assert gr_runtime._route_state_key("Lan", "badminton") in gr_runtime._game_route_states
        debug_log = await gr_logs.game_logs(session_id="debug-badminton", game_type="badminton")
        assert debug_log["ok"] is True
        assert debug_log["missing"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_soccer_route_start_auto_enables_session_debug_log(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    async def fake_pregame_context(**kwargs):
        assert kwargs["game_type"] == "soccer"
        assert kwargs["prompt_locale"] == "zh-TW"
        return gr_pregame._default_soccer_pregame_context(initial_difficulty="lv2"), "lightweight", ""

    _gr_patch_all(monkeypatch, "_build_soccer_pregame_context", fake_pregame_context)

    with reset_game_route_state():
        result = await gr_runtime.game_route_start(
            "soccer",
            _FakeRequest({
                "lanlan_name": "Lan",
                "session_id": "soccer-auto-log",
                "i18n_language": "zh-TW",
            }),
        )

    assert result["ok"] is True
    debug_log = await gr_logs.game_logs(session_id="soccer-auto-log", game_type="soccer")
    assert debug_log["ok"] is True
    assert debug_log["log"]["status"] == "active"
    assert [item["event"] for item in debug_log["log"]["entries"]] == [
        "session_active",
        "route_start_requested",
        "route_start_completed",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_soccer_route_start_enables_session_debug_log_under_route_locks(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    async def fake_pregame_context(**kwargs):
        assert kwargs["game_type"] == "soccer"
        return gr_pregame._default_soccer_pregame_context(initial_difficulty="lv2"), "lightweight", ""

    lock_observation = {}
    original_enable = gr_runtime._enable_game_session_debug_log

    def observed_enable(game_type, session_id, *, lanlan_name=""):
        lock_observation["supersede_locked"] = gr_runtime._get_supersede_lock(lanlan_name).locked()
        lock_observation["route_locked"] = gr_runtime._get_route_lock(lanlan_name, game_type).locked()
        return original_enable(game_type, session_id, lanlan_name=lanlan_name)

    _gr_patch_all(monkeypatch, "_build_soccer_pregame_context", fake_pregame_context)
    _gr_patch_all(monkeypatch, "_enable_game_session_debug_log", observed_enable)

    with reset_game_route_state():
        result = await gr_runtime.game_route_start(
            "soccer",
            _FakeRequest({"lanlan_name": "Lan", "session_id": "soccer-auto-log-locks"}),
        )

    assert result["ok"] is True
    assert lock_observation == {"supersede_locked": True, "route_locked": True}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_end_before_start_consumes_exact_session_without_activation(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    with reset_game_route_state():
        end_result = await gr_runtime._complete_game_end_from_payload(
            "soccer",
            {
                "lanlan_name": "Lan",
                "session_id": "page-exit-before-start",
                "reason": "pagehide",
            },
        )
        key = ("Lan", "soccer", "page-exit-before-start", "")
        assert end_result["ok"] is True
        assert key in gr_runtime._game_route_end_tombstones

        start_result = await gr_runtime.game_route_start(
            "soccer",
            _FakeRequest({
                "lanlan_name": "Lan",
                "session_id": "page-exit-before-start",
            }),
        )

        assert start_result == {
            "ok": True,
            "reason": "ended_before_start",
            "state": {"game_route_active": False},
        }
        assert key not in gr_runtime._game_route_end_tombstones
        assert gr_runtime._game_route_states == {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_repeated_route_end_does_not_cancel_same_session_restart(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    postgame_calls = []

    async def fake_submit(_archive):
        return {"ok": True, "status": "cached", "count": 1}

    async def fake_postgame(*args, **kwargs):
        postgame_calls.append((args, kwargs))
        return {"ok": True, "action": "sent"}

    async def fake_pregame_context(**kwargs):
        assert kwargs["game_type"] == "soccer"
        return gr_pregame._default_soccer_pregame_context(), "lightweight", ""

    _gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", fake_submit)
    _gr_patch_all(monkeypatch, "_deliver_game_postgame", fake_postgame)
    _gr_patch_all(monkeypatch, "_build_soccer_pregame_context", fake_pregame_context)

    with reset_game_route_state():
        state = gr_runtime._activate_game_route("soccer", "reused-session", "Lan")
        _mark_game_started(state)
        payload = {
            "lanlan_name": "Lan",
            "session_id": "reused-session",
            "reason": "pagehide",
        }

        first_end = await gr_runtime._complete_game_end_from_payload("soccer", payload)
        second_end = await gr_runtime._complete_game_end_from_payload("soccer", payload)

        tombstone_key = ("Lan", "soccer", "reused-session", "")
        assert first_end["route_closed"] is True
        assert second_end["archive"] == first_end["archive"]
        assert len(postgame_calls) == 1
        assert tombstone_key not in gr_runtime._game_route_end_tombstones

        restart = await gr_runtime.game_route_start(
            "soccer",
            _FakeRequest({
                "lanlan_name": "Lan",
                "session_id": "reused-session",
            }),
        )

        assert restart["ok"] is True
        assert restart.get("reason") != "ended_before_start"
        assert restart["state"]["game_route_active"] is True
        assert tombstone_key not in gr_runtime._game_route_end_tombstones


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_start_retires_the_generations_its_retry_left_behind(monkeypatch):
    """A delayed original start must not supersede the generation the SDK owns.

    When the first ``/route/start`` is lost (timeout / abort) the SDK cannot
    resolve that generation, so the retry ships it behind the new primary in
    ``sdk_route_instance_ids``. If the original request was in flight all along
    and reaches the handler after the retry, it activates a route the game has
    already given up on -- and takes the character's takeover with it. The retry
    retires the tail, mirroring what ``/route/end`` already does for the same
    candidate list.
    """
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    async def fake_pregame_context(**kwargs):
        return gr_pregame._default_soccer_pregame_context(), "lightweight", ""

    _gr_patch_all(monkeypatch, "_build_soccer_pregame_context", fake_pregame_context)

    with reset_game_route_state():
        retry = await gr_runtime.game_route_start(
            "soccer",
            _FakeRequest({
                "lanlan_name": "Lan",
                "session_id": "lost-start",
                "sdk_route_instance_id": "route-B",
                "sdk_route_instance_ids": ["route-B", "route-A"],
            }),
        )

        assert retry["ok"] is True
        assert retry["state"]["game_route_active"] is True
        assert ("Lan", "soccer", "lost-start", "route-A") in (
            gr_runtime._game_route_end_tombstones
        )
        # The primary is what the SDK now owns; retiring it would cancel the
        # very route this request just activated.
        assert ("Lan", "soccer", "lost-start", "route-B") not in (
            gr_runtime._game_route_end_tombstones
        )

        delayed_original = await gr_runtime.game_route_start(
            "soccer",
            _FakeRequest({
                "lanlan_name": "Lan",
                "session_id": "lost-start",
                "sdk_route_instance_id": "route-A",
            }),
        )

        assert delayed_original == {
            "ok": True,
            "reason": "ended_before_start",
            "state": {"game_route_active": False},
        }
        surviving = gr_runtime._get_active_game_route_state("Lan", "soccer")
        assert surviving is not None
        assert surviving["_sdk_route_instance_id"] == "route-B"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_start_absorbs_its_locale_only_after_it_wins(monkeypatch):
    """A start that gets retired must not have moved the session language.

    ``_resolve_game_prompt_locale`` writes ``mgr.user_language`` as a side
    effect, and it used to run before the tombstone check and before the
    supersede scan. A delayed original start carrying an older locale therefore
    re-rendered the live route in that locale on its way to being rejected --
    the same shape as the silent preload writing the shared render language.
    """
    mgr = _LocaleTrackingManager(language="en")
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})

    async def fake_pregame_context(**kwargs):
        return gr_pregame._default_soccer_pregame_context(), "lightweight", ""

    _gr_patch_all(monkeypatch, "_build_soccer_pregame_context", fake_pregame_context)

    with reset_game_route_state():
        gr_runtime._remember_game_route_end_before_start(
            "Lan", "soccer", "retired-session", "route-A",
        )
        retired = await gr_runtime.game_route_start(
            "soccer",
            _FakeRequest({
                "lanlan_name": "Lan",
                "session_id": "retired-session",
                "sdk_route_instance_id": "route-A",
                "i18n_language": "ja",
            }),
        )

        assert retired["reason"] == "ended_before_start"
        assert mgr.language_updates == []
        assert mgr.user_language == "en"

        # And the winning start still absorbs, so nothing regressed for the
        # normal path this side effect exists for.
        accepted = await gr_runtime.game_route_start(
            "soccer",
            _FakeRequest({
                "lanlan_name": "Lan",
                "session_id": "live-session",
                "i18n_language": "ja",
            }),
        )

        assert accepted["ok"] is True
        assert mgr.language_updates == ["ja"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_start_without_sdk_generations_leaves_no_tombstone(monkeypatch):
    """An ID-less legacy start must not tombstone its own session.

    ``/route/end`` normalises an empty candidate list to ``("",)``; copying that
    here would write the ID-less key and cancel the next soccer/badminton start
    that reuses the session id.
    """
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    async def fake_pregame_context(**kwargs):
        return gr_pregame._default_soccer_pregame_context(), "lightweight", ""

    _gr_patch_all(monkeypatch, "_build_soccer_pregame_context", fake_pregame_context)

    with reset_game_route_state():
        started = await gr_runtime.game_route_start(
            "soccer",
            _FakeRequest({"lanlan_name": "Lan", "session_id": "legacy-session"}),
        )

        assert started["ok"] is True
        assert gr_runtime._game_route_end_tombstones == {}


@pytest.mark.unit
def test_route_end_before_start_tombstones_expire_and_stay_bounded(monkeypatch):
    clock = {"now": 100.0}
    patch_module_clock(monkeypatch, gr_runtime, monotonic=lambda: clock["now"])

    with reset_game_route_state():
        gr_runtime._remember_game_route_end_before_start("Lan", "soccer", "expired")
        clock["now"] += gr_runtime._GAME_ROUTE_END_TOMBSTONE_TTL_SECONDS + 1
        assert gr_runtime._consume_game_route_end_before_start(
            "Lan", "soccer", "expired"
        ) is False

        for index in range(gr_runtime._GAME_ROUTE_END_TOMBSTONE_LIMIT + 1):
            gr_runtime._remember_game_route_end_before_start(
                "Lan",
                "soccer",
                f"bounded-{index}",
            )

        assert len(gr_runtime._game_route_end_tombstones) == (
            gr_runtime._GAME_ROUTE_END_TOMBSTONE_LIMIT
        )
        assert ("Lan", "soccer", "bounded-0", "") not in gr_runtime._game_route_end_tombstones


@pytest.mark.unit
def test_sdk_route_instance_candidates_are_deduplicated_and_bounded():
    assert gr_runtime._sdk_route_instance_ids({
        "sdk_route_instance_id": "primary",
        "sdk_route_instance_ids": [
            "primary",
            "second",
            "third",
            "fourth",
            "fifth",
            123,
            "x" * (gr_runtime._SDK_ROUTE_INSTANCE_ID_MAX_CHARS + 1),
        ],
    }) == ("primary", "second", "third", "fourth")


@pytest.mark.unit
def test_sdk_route_instance_binding_rejects_missing_and_stale_generations():
    legacy_state = {"game_route_active": True, "session_id": "legacy"}
    assert gr_runtime._sdk_route_instance_error(legacy_state, {}) is None

    identified_state = {
        "game_route_active": True,
        "session_id": "identified",
        "_sdk_route_instance_id": "route-B",
    }
    assert gr_runtime._sdk_route_instance_error(
        identified_state,
        {"sdk_route_instance_id": "route-B"},
    ) is None
    assert gr_runtime._sdk_route_instance_error(identified_state, {})["reason"] == (
        "route_instance_id_mismatch"
    )
    assert gr_runtime._sdk_route_instance_error(
        identified_state,
        {"sdk_route_instance_id": "route-A"},
    )["reason"] == "route_instance_id_mismatch"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_active_reconciliation_exposes_the_authoritative_generation(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    with reset_game_route_state():
        state = gr_runtime._activate_game_route("neutral-sdk-game", "session-1", "Lan")
        state["_sdk_route_instance_id"] = "route-instance-b"

        result = await gr_runtime.game_route_any_active("Lan")

    assert result == {
        "ok": True,
        "active": True,
        "game_type": "neutral-sdk-game",
        "session_id": "session-1",
        "lanlan_name": "Lan",
        "sdk_route_instance_id": "route-instance-b",
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_active_reconciliation_names_the_route_it_finalized(monkeypatch):
    """The inactive answer carries the identity the backend actually retired.

    The frontend uses this read to compensate for a ``closed`` websocket event
    it never received -- which is exactly when it has no record of the route it
    is about to clear, so a late STT gate can re-activate a dead route. It can
    only tombstone safely if the identity comes from here; guessing with its own
    would tombstone a live route whenever this read and the socket disagree.
    """
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    with reset_game_route_state():
        older = gr_runtime._activate_game_route("neutral-sdk-game", "session-1", "Lan")
        older["_sdk_route_instance_id"] = "route-instance-a"
        older["game_route_active"] = False
        older["_exit_flow_started"] = True
        older["exit_started_at"] = 100.0
        newer = gr_runtime._activate_game_route("other-game", "session-2", "Lan")
        newer["_sdk_route_instance_id"] = "route-instance-b"
        newer["game_route_active"] = False
        newer["_exit_flow_started"] = True
        newer["exit_started_at"] = 200.0
        other_character = gr_runtime._activate_game_route("neutral-sdk-game", "session-3", "Yui")
        other_character["game_route_active"] = False
        other_character["_exit_flow_started"] = True
        other_character["exit_started_at"] = 300.0

        result = await gr_runtime.game_route_any_active("Lan")

    assert result == {
        "ok": True,
        "active": False,
        "ended_route": {
            "game_type": "other-game",
            "session_id": "session-2",
            "sdk_route_instance_id": "route-instance-b",
        },
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_active_reconciliation_omits_a_route_that_never_finalized(monkeypatch):
    """A slot that was never entered into finalize is not a dead route.

    Tombstoning it would let the frontend reject the real gate of a route that
    is still coming up.
    """
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    with reset_game_route_state():
        stale = gr_runtime._activate_game_route("neutral-sdk-game", "session-1", "Lan")
        stale["game_route_active"] = False

        result = await gr_runtime.game_route_any_active("Lan")

    assert result == {"ok": True, "active": False}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_window_state_change_carries_the_route_generation():
    class ConnectedState:
        CONNECTED = "connected"

        def __eq__(self, other):
            return other == self.CONNECTED

    websocket = SimpleNamespace(
        client_state=ConnectedState(),
        send_json=AsyncMock(),
    )
    manager = SimpleNamespace(websocket=websocket)

    await gr_route_lifecycle._push_game_window_state_change(
        manager,
        action="opened",
        lanlan_name="Lan",
        game_type="neutral-sdk-game",
        session_id="session-1",
        route_instance_id="route-instance-b",
    )

    websocket.send_json.assert_awaited_once_with({
        "type": "game_window_state_change",
        "action": "opened",
        "lanlan_name": "Lan",
        "game_type": "neutral-sdk-game",
        "session_id": "session-1",
        "sdk_route_instance_id": "route-instance-b",
    })


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_window_state_change_push_has_a_hard_timeout(monkeypatch):
    send_cancelled = False

    class ConnectedState:
        CONNECTED = "connected"

        def __eq__(self, other):
            return other == self.CONNECTED

    async def stalled_send(_payload):
        nonlocal send_cancelled
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            send_cancelled = True
            raise

    websocket = SimpleNamespace(
        client_state=ConnectedState(),
        send_json=stalled_send,
    )
    manager = SimpleNamespace(websocket=websocket)
    monkeypatch.setattr(
        gr_route_lifecycle,
        "_GAME_WINDOW_STATE_CHANGE_PUSH_TIMEOUT_SECONDS",
        0.01,
    )

    await asyncio.wait_for(
        gr_route_lifecycle._push_game_window_state_change(
            manager,
            action="closed",
            lanlan_name="Lan",
            game_type="neutral-sdk-game",
            session_id="session-1",
            route_instance_id="route-instance-b",
        ),
        timeout=0.2,
    )

    assert send_cancelled is True


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_speech_cancel_carries_exact_route_and_correlation_identity():
    class ConnectedState:
        CONNECTED = "connected"

        def __eq__(self, other):
            return other == self.CONNECTED

    websocket = SimpleNamespace(
        client_state=ConnectedState(),
        send_json=AsyncMock(),
    )
    manager = SimpleNamespace(websocket=websocket)

    await gr_route_lifecycle._push_game_speech_cancel(
        manager,
        lanlan_name="Lan",
        game_type="neutral-sdk-game",
        session_id="session-1",
        route_instance_id="route-instance-b",
        speech_correlation_id="speech-correlation-b",
    )

    websocket.send_json.assert_awaited_once_with({
        "type": "game_route_speech_cancel",
        "lanlan_name": "Lan",
        "game_type": "neutral-sdk-game",
        "session_id": "session-1",
        "sdk_route_instance_id": "route-instance-b",
        "sdk_speech_correlation_id": "speech-correlation-b",
    })


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_speech_cancel_push_has_a_hard_timeout(monkeypatch):
    send_cancelled = False

    class ConnectedState:
        CONNECTED = "connected"

        def __eq__(self, other):
            return other == self.CONNECTED

    async def stalled_send(_payload):
        nonlocal send_cancelled
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            send_cancelled = True
            raise

    websocket = SimpleNamespace(
        client_state=ConnectedState(),
        send_json=stalled_send,
    )
    manager = SimpleNamespace(websocket=websocket)
    monkeypatch.setattr(
        gr_route_lifecycle,
        "_GAME_ROUTE_SPEECH_CANCEL_PUSH_TIMEOUT_SECONDS",
        0.01,
    )

    await asyncio.wait_for(
        gr_route_lifecycle._push_game_speech_cancel(
            manager,
            lanlan_name="Lan",
            game_type="neutral-sdk-game",
            session_id="session-1",
            route_instance_id="route-instance-b",
            speech_correlation_id="speech-correlation-b",
        ),
        timeout=0.2,
    )

    assert send_cancelled is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_speech_cleanup_releases_browser_audio_after_backend_task_done(
    monkeypatch,
):
    pushed = AsyncMock()
    monkeypatch.setattr(gr_postgame, "_push_game_speech_cancel", pushed)
    state = {
        "lanlan_name": "Lan",
        "game_type": "neutral-sdk-game",
        "session_id": "session-1",
        "_sdk_route_instance_id": "route-instance-b",
        # A cache hit resolves before its audio finishes playing, so an earlier
        # utterance can still be live in the browser when the next request
        # registers. Every outstanding correlation has to be cancelled, not just
        # the newest: the browser ignores a cancel that does not match the audio
        # it is currently playing, so a missed one keeps playing past route end.
        "_sdk_active_speech_correlation_ids": [
            "speech-correlation-a",
            "speech-correlation-b",
        ],
    }

    manager = SimpleNamespace()
    await gr_postgame._cancel_route_game_speech(state, manager)

    assert "_sdk_active_speech_correlation_ids" not in state
    cancelled = [
        call.kwargs["speech_correlation_id"] for call in pushed.await_args_list
    ]
    assert cancelled == ["speech-correlation-b", "speech-correlation-a"], (
        "route teardown did not cancel every outstanding speech correlation"
    )
    for call in pushed.await_args_list:
        assert call.args == (manager,)
        assert call.kwargs["lanlan_name"] == "Lan"
        assert call.kwargs["game_type"] == "neutral-sdk-game"
        assert call.kwargs["session_id"] == "session-1"
        assert call.kwargs["route_instance_id"] == "route-instance-b"
@pytest.mark.unit
@pytest.mark.asyncio
async def test_delayed_old_route_end_does_not_close_reused_session_generation(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    async def fake_submit(_archive):
        return {"ok": True, "status": "cached", "count": 1}

    async def fake_postgame(*_args, **_kwargs):
        return {"ok": True, "action": "sent"}

    async def fake_pregame_context(**_kwargs):
        return gr_pregame._default_soccer_pregame_context(), "lightweight", ""

    _gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", fake_submit)
    _gr_patch_all(monkeypatch, "_deliver_game_postgame", fake_postgame)
    _gr_patch_all(monkeypatch, "_build_soccer_pregame_context", fake_pregame_context)

    def request(instance_id):
        return _FakeRequest({
            "lanlan_name": "Lan",
            "session_id": "reused-generation-session",
            "sdk_route_instance_id": instance_id,
        })

    with reset_game_route_state():
        first_start = await gr_runtime.game_route_start("soccer", request("route-A"))
        first_state = gr_runtime._get_active_game_route_state("Lan", "soccer")
        assert first_state["_sdk_route_instance_id"] == "route-A"
        assert "sdk_route_instance_id" not in first_start["state"]
        _mark_game_started(first_state)
        await gr_runtime._complete_game_end_from_payload(
            "soccer",
            {
                "lanlan_name": "Lan",
                "session_id": "reused-generation-session",
                "sdk_route_instance_id": "route-A",
            },
        )

        second_start = await gr_runtime.game_route_start("soccer", request("route-B"))
        assert "sdk_route_instance_id" not in second_start["state"]

        delayed = await gr_runtime._complete_game_end_from_payload(
            "soccer",
            {
                "lanlan_name": "Lan",
                "session_id": "reused-generation-session",
                "sdk_route_instance_id": "route-A",
            },
        )
        active = gr_runtime._get_active_game_route_state("Lan", "soccer")
        assert delayed["reason"] == "stale_route_instance"
        assert active["game_route_active"] is True
        assert active["_sdk_route_instance_id"] == "route-B"
        assert (
            "Lan", "soccer", "reused-generation-session", "route-A"
        ) not in gr_runtime._game_route_end_tombstones


@pytest.mark.unit
@pytest.mark.asyncio
async def test_idless_end_does_not_close_identified_route(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    with reset_game_route_state():
        state = gr_runtime._activate_game_route("soccer", "identified-session", "Lan")
        state["_sdk_route_instance_id"] = "identified-route"

        result = await gr_runtime._complete_game_end_from_payload(
            "soccer",
            {
                "lanlan_name": "Lan",
                "session_id": "identified-session",
                "reason": "delayed-legacy-end",
            },
        )

        assert result["reason"] == "stale_route_instance"
        assert state["game_route_active"] is True
        assert ("Lan", "soccer", "identified-session", "") not in (
            gr_runtime._game_route_end_tombstones
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_identified_end_does_not_close_generationless_active_route(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    with reset_game_route_state():
        state = gr_runtime._activate_game_route(
            "neutral-sdk-game",
            "reused-session",
            "Lan",
        )

        result = await gr_runtime._complete_game_end_from_payload(
            "neutral-sdk-game",
            {
                "lanlan_name": "Lan",
                "session_id": "reused-session",
                "sdk_route_instance_id": "route-B",
                "reason": "pagehide-before-route-B-start",
            },
        )

        assert result["route_closed"] is False
        assert state["game_route_active"] is True
        assert (
            "Lan", "neutral-sdk-game", "reused-session", "route-B"
        ) in gr_runtime._game_route_end_tombstones


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_end_candidate_generations_close_active_and_cancel_unresolved(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    _gr_patch_all(
        monkeypatch,
        "_submit_game_archive_to_memory",
        AsyncMock(return_value={"ok": True, "status": "cached", "count": 1}),
    )
    _gr_patch_all(
        monkeypatch,
        "_deliver_game_postgame",
        AsyncMock(return_value={"ok": True, "action": "sent"}),
    )

    with reset_game_route_state():
        state = gr_runtime._activate_game_route("soccer", "retry-session", "Lan")
        state["_sdk_route_instance_id"] = "route-A"
        _mark_game_started(state)

        result = await gr_runtime._complete_game_end_from_payload(
            "soccer",
            {
                "lanlan_name": "Lan",
                "session_id": "retry-session",
                "sdk_route_instance_id": "route-B",
                "sdk_route_instance_ids": ["route-A", "route-B"],
                "reason": "pagehide",
            },
        )

        assert result["route_closed"] is True
        assert state["game_route_active"] is False
        assert (
            "Lan", "soccer", "retry-session", "route-B"
        ) in gr_runtime._game_route_end_tombstones
        assert (
            "Lan", "soccer", "retry-session", "route-A"
        ) not in gr_runtime._game_route_end_tombstones


@pytest.mark.unit
@pytest.mark.asyncio
async def test_icebreaker_is_rejected_from_game_route_start(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    with pytest.raises(HTTPException) as exc_info:
        await gr_runtime.game_route_start(
            "new_user_icebreaker",
            _FakeRequest({"lanlan_name": "Lan", "session_id": "icebreaker-day1"}),
        )

    _assert_not_icebreaker_game_route_error(exc_info.value, "/api/icebreaker/route/start")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_icebreaker_is_rejected_from_game_project_speak(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    with pytest.raises(HTTPException) as exc_info:
        await gr_runtime.game_project_speak(
            "new_user_icebreaker",
            _FakeRequest({"lanlan_name": "Lan", "session_id": "icebreaker-day1", "line": "hello"}),
        )

    _assert_not_icebreaker_game_route_error(exc_info.value, "/api/icebreaker/speak")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_icebreaker_is_rejected_from_game_route_end(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    with pytest.raises(HTTPException) as exc_info:
        await gr_runtime.game_route_end(
            "new_user_icebreaker",
            _FakeRequest({"lanlan_name": "Lan", "session_id": "icebreaker-day1"}),
        )

    _assert_not_icebreaker_game_route_error(exc_info.value, "/api/icebreaker/route/end")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_debug_log_ingest_and_query():
    enable_result = await gr_logs.game_log_enable(
        _FakeRequest({
            "session_id": "soccer-debug-1",
            "game_type": "soccer",
            "lanlan_name": "Lan",
            "source": "test",
            "reason": "unit",
        }, path="/api/game/logs/enable"),
    )
    assert enable_result["ok"] is True

    result = await gr_logs.game_log_ingest(
        _FakeRequest({
            "session_id": "soccer-debug-1",
            "game_type": "soccer",
            "lanlan_name": "Lan",
            "level": "error",
            "category": "frontend",
            "event": "window_error",
            "message": "boom",
            "details": {"filename": "soccer_demo.html", "line": 12},
        }),
    )

    assert result["ok"] is True
    assert result["seq"] == 2
    queried = await gr_logs.game_logs(session_id="soccer-debug-1", game_type="soccer")
    assert queried["ok"] is True
    assert queried["log"]["lanlan_name"] == "Lan"
    assert queried["log"]["entries"][0]["event"] == "session_log_enabled"
    assert queried["log"]["entries"][1]["event"] == "window_error"
    assert queried["log"]["entries"][1]["details"]["filename"] == "soccer_demo.html"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_debug_log_ingest_does_not_create_session_before_enable():
    result = await gr_logs.game_log_ingest(
        _FakeRequest({
            "session_id": "soccer-debug-disabled",
            "game_type": "soccer",
            "message": "ignored",
        }, path="/api/game/logs"),
    )

    assert result["ok"] is False
    assert result["seq"] is None
    assert game_log.find_game_session_debug_log("soccer-debug-disabled", "soccer") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_debug_log_enable_requires_local_mutation_csrf():
    result = await gr_logs.game_log_enable(
        _FakeRequest({
            "session_id": "soccer-debug-enable-csrf",
            "game_type": "soccer",
        }, mutation_headers=False, path="/api/game/logs/enable"),
    )

    assert isinstance(result, JSONResponse)
    assert result.status_code == 403
    assert b"csrf_validation_failed" in result.body
    assert game_log.find_game_session_debug_log("soccer-debug-enable-csrf", "soccer") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_debug_log_ingest_requires_local_mutation_csrf():
    result = await gr_logs.game_log_ingest(
        _FakeRequest({
            "session_id": "soccer-debug-csrf",
            "game_type": "soccer",
            "message": "blocked",
        }, mutation_headers=False, path="/api/game/logs"),
    )

    assert isinstance(result, JSONResponse)
    assert result.status_code == 403
    assert b"csrf_validation_failed" in result.body
    assert game_log.find_game_session_debug_log("soccer-debug-csrf", "soccer") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_debug_log_ingest_does_not_preserve_from_false_or_no_truncate():
    enable_result = await gr_logs.game_log_enable(
        _FakeRequest({
            "session_id": "soccer-debug-truncate",
            "game_type": "soccer",
        }, path="/api/game/logs/enable"),
    )
    assert enable_result["ok"] is True

    result = await gr_logs.game_log_ingest(
        _FakeRequest({
            "session_id": "soccer-debug-truncate",
            "game_type": "soccer",
            "message": "m" * 1500,
            "details": {"long": "d" * 2500},
            "preserve_message": "false",
            "preserve_details": "false",
            "no_truncate": True,
        }, path="/api/game/logs"),
    )

    assert result["ok"] is True
    queried = await gr_logs.game_logs(session_id="soccer-debug-truncate", game_type="soccer")
    entry = queried["log"]["entries"][1]
    assert len(entry["message"]) < 1500
    assert "<truncated" in entry["message"]
    assert len(entry["details"]["long"]) < 2500
    assert "<truncated" in entry["details"]["long"]


@pytest.mark.unit
def test_game_debug_logs_keep_latest_completed_session_until_next_active_session():
    for index in range(3):
        session_id = f"soccer-old-{index}"
        game_log.enable_game_session_debug_log("soccer", session_id, lanlan_name="Lan")
        game_log.mark_game_session_debug_log_ended("soccer", session_id, lanlan_name="Lan", reason="test")

    summaries_before_new_session = game_log.list_game_session_debug_log_summaries("soccer")
    assert {item["session_id"] for item in summaries_before_new_session} == {"soccer-old-2"}

    game_log.enable_game_session_debug_log("soccer", "soccer-new", lanlan_name="Lan")
    summaries = game_log.list_game_session_debug_log_summaries("soccer")

    session_ids = {item["session_id"] for item in summaries}
    assert session_ids == {"soccer-new"}


@pytest.mark.unit
def test_game_debug_logs_drop_ended_session_when_active_session_exists():
    game_log.enable_game_session_debug_log("soccer", "soccer-old", lanlan_name="Lan")
    game_log.enable_game_session_debug_log("soccer", "soccer-new", lanlan_name="Lan")
    game_log.mark_game_session_debug_log_ended("soccer", "soccer-old", lanlan_name="Lan", reason="superseded")

    summaries = game_log.list_game_session_debug_log_summaries("soccer")

    assert {item["session_id"] for item in summaries} == {"soccer-new"}


@pytest.mark.unit
def test_game_debug_logs_new_active_session_drops_old_active_session():
    game_log.enable_game_session_debug_log("soccer", "soccer-old-active", lanlan_name="LanA")

    assert game_log.find_game_session_debug_log("soccer-old-active", "soccer") is not None

    game_log.enable_game_session_debug_log("soccer", "soccer-new-active", lanlan_name="LanB")

    assert game_log.find_game_session_debug_log("soccer-old-active", "soccer") is None
    assert {item["session_id"] for item in game_log.list_game_session_debug_log_summaries()} == {"soccer-new-active"}


@pytest.mark.unit
def test_game_debug_logs_drop_idle_active_session_after_ttl():
    now = 1_000_000.0
    game_log.enable_game_session_debug_log("soccer", "soccer-idle-active", lanlan_name="Lan")
    entry = game_log.find_game_session_debug_log("soccer-idle-active", "soccer")
    assert entry is not None
    entry["updated_at"] = now - game_log.GAME_SESSION_DEBUG_ACTIVE_IDLE_TTL_SECONDS - 1

    game_log.cleanup_game_session_debug_logs(now)

    assert game_log.find_game_session_debug_log("soccer-idle-active", "soccer") is None


@pytest.mark.unit
def test_game_debug_logs_append_refreshes_active_idle_ttl():
    now = game_log.time.time()
    game_log.enable_game_session_debug_log("soccer", "soccer-active-refresh", lanlan_name="Lan")
    entry = game_log.find_game_session_debug_log("soccer-active-refresh", "soccer")
    assert entry is not None
    stale_updated_at = now - (game_log.GAME_SESSION_DEBUG_ACTIVE_IDLE_TTL_SECONDS / 2)
    entry["updated_at"] = stale_updated_at

    item = game_log.append_game_session_debug_log(
        "soccer",
        "soccer-active-refresh",
        lanlan_name="Lan",
        event="still_active",
        message="still active",
    )

    assert item is not None
    assert game_log.find_game_session_debug_log("soccer-active-refresh", "soccer") is not None
    assert entry["updated_at"] > stale_updated_at


@pytest.mark.unit
def test_game_debug_logs_reactivation_clears_ended_metadata():
    game_log.enable_game_session_debug_log("soccer", "soccer-reactivate", lanlan_name="Lan")
    game_log.mark_game_session_debug_log_ended("soccer", "soccer-reactivate", lanlan_name="Lan", reason="test")
    ended_entry = game_log.find_game_session_debug_log("soccer-reactivate", "soccer")
    assert ended_entry is not None
    assert ended_entry["ended_at"] is not None
    assert ended_entry["ended_time"]

    game_log.enable_game_session_debug_log("soccer", "soccer-reactivate", lanlan_name="Lan")
    reactivated_entry = game_log.find_game_session_debug_log("soccer-reactivate", "soccer")

    assert reactivated_entry is not None
    assert reactivated_entry["status"] == "active"
    assert reactivated_entry["ended_at"] is None
    assert reactivated_entry["ended_time"] is None


@pytest.mark.unit
def test_game_debug_logs_do_not_append_after_session_ended():
    game_log.enable_game_session_debug_log("soccer", "soccer-ended", lanlan_name="Lan")
    first_item = game_log.append_game_session_debug_log(
        "soccer",
        "soccer-ended",
        lanlan_name="Lan",
        event="before_end",
        message="before end",
    )
    assert first_item is not None

    game_log.mark_game_session_debug_log_ended("soccer", "soccer-ended", lanlan_name="Lan", reason="test")
    entry = game_log.find_game_session_debug_log("soccer-ended", "soccer")
    assert entry is not None
    entry_count_after_end = len(entry["entries"])

    late_item = game_log.append_game_session_debug_log(
        "soccer",
        "soccer-ended",
        lanlan_name="Lan",
        event="after_end",
        message="after end",
    )

    assert late_item is None
    assert entry["status"] == "ended"
    assert len(entry["entries"]) == entry_count_after_end
    assert [item["event"] for item in entry["entries"]] == [
        "before_end",
        "session_ended",
    ]


@pytest.mark.unit
def test_game_debug_logs_mark_ended_is_idempotent():
    game_log.enable_game_session_debug_log("soccer", "soccer-ended-idempotent", lanlan_name="Lan")
    game_log.mark_game_session_debug_log_ended("soccer", "soccer-ended-idempotent", lanlan_name="Lan", reason="first")
    entry = game_log.find_game_session_debug_log("soccer-ended-idempotent", "soccer")
    assert entry is not None
    first_ended_at = entry["ended_at"]
    first_ended_time = entry["ended_time"]
    first_events = [item["event"] for item in entry["entries"]]

    game_log.mark_game_session_debug_log_ended("soccer", "soccer-ended-idempotent", lanlan_name="Lan", reason="second")

    assert entry["status"] == "ended"
    assert entry["ended_at"] == first_ended_at
    assert entry["ended_time"] == first_ended_time
    assert [item["event"] for item in entry["entries"]] == first_events == ["session_ended"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_debug_logs_route_end_records_completed_before_session_ended(monkeypatch):
    async def fake_deliver_postgame(*_args, **_kwargs):
        return {"ok": True, "action": "skip", "reason": "test"}

    _gr_patch_all(monkeypatch, "_deliver_game_postgame", fake_deliver_postgame)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    with reset_game_route_state():
        state = gr_runtime._activate_game_route("soccer", "soccer-route-end", "Lan")
        _mark_game_started(state)
        game_log.enable_game_session_debug_log("soccer", "soccer-route-end", lanlan_name="Lan")

        result = await gr_runtime._complete_game_end_from_payload(
            "soccer",
            {
                "session_id": "soccer-route-end",
                "lanlan_name": "Lan",
                "gameStarted": True,
            },
            default_reason="route_end",
        )

    assert result["ok"] is True
    entry = game_log.find_game_session_debug_log("soccer-route-end", "soccer")
    assert entry is not None
    assert entry["status"] == "ended"
    assert [item["event"] for item in entry["entries"]] == [
        "route_end_requested",
        "route_end_completed",
        "session_ended",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_debug_logs_route_end_resets_defer_flag_when_postgame_fails(monkeypatch):
    async def fake_deliver_postgame(*_args, **_kwargs):
        raise RuntimeError("postgame failed")

    _gr_patch_all(monkeypatch, "_deliver_game_postgame", fake_deliver_postgame)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    with reset_game_route_state():
        state = gr_runtime._activate_game_route("soccer", "soccer-postgame-fails", "Lan")
        _mark_game_started(state)
        game_log.enable_game_session_debug_log("soccer", "soccer-postgame-fails", lanlan_name="Lan")

        with pytest.raises(RuntimeError, match="postgame failed"):
            await gr_runtime._complete_game_end_from_payload(
                "soccer",
                {
                    "session_id": "soccer-postgame-fails",
                    "lanlan_name": "Lan",
                    "gameStarted": True,
                },
                default_reason="route_end",
            )

        assert state.get("_exit_defer_debug_log_close") is False

    entry = game_log.find_game_session_debug_log("soccer-postgame-fails", "soccer")
    assert entry is not None
    assert entry["status"] == "active"
    assert [item["event"] for item in entry["entries"]] == ["route_end_requested"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_debug_logs_route_end_defers_concurrent_heartbeat_close(monkeypatch):
    release_finalize = asyncio.Event()

    async def fake_deliver_postgame(*_args, **_kwargs):
        return {"ok": True, "action": "skip", "reason": "test"}

    _gr_patch_all(monkeypatch, "_deliver_game_postgame", fake_deliver_postgame)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    with reset_game_route_state():
        state = gr_runtime._activate_game_route("soccer", "soccer-route-race", "Lan")
        _mark_game_started(state)
        game_log.enable_game_session_debug_log("soccer", "soccer-route-race", lanlan_name="Lan")

        async def existing_finalize_task():
            await release_finalize.wait()
            return {
                "archive": {
                    "game_type": "soccer",
                    "session_id": "soccer-route-race",
                    "lanlan_name": "Lan",
                    "game_started": True,
                },
                "archive_memory": {"ok": True, "status": "submitted"},
                "game_session_closed": False,
                "debug_log_ended": False,
                "exit_reason": "heartbeat_timeout",
                "postgame_context_snapshot": {},
            }

        heartbeat_task = asyncio.create_task(existing_finalize_task())
        state["_exit_task"] = heartbeat_task
        state["_exit_close_debug_log_request"] = True

        end_task = asyncio.create_task(gr_runtime._complete_game_end_from_payload(
            "soccer",
            {
                "session_id": "soccer-route-race",
                "lanlan_name": "Lan",
                "gameStarted": True,
            },
            default_reason="route_end",
        ))
        for _ in range(20):
            if state.get("_exit_defer_debug_log_close"):
                break
            await asyncio.sleep(0)
        assert state.get("_exit_defer_debug_log_close") is True

        release_finalize.set()
        heartbeat_result = await heartbeat_task
        result = await end_task

    assert heartbeat_result["debug_log_ended"] is True
    assert result["ok"] is True
    entry = game_log.find_game_session_debug_log("soccer-route-race", "soccer")
    assert entry is not None
    assert entry["status"] == "ended"
    assert [item["event"] for item in entry["entries"]] == [
        "route_end_requested",
        "route_end_completed",
        "session_ended",
    ]


@pytest.mark.unit
def test_game_debug_logs_retention_is_not_partitioned_by_type_or_lanlan():
    game_log.enable_game_session_debug_log("soccer", "soccer-old", lanlan_name="LanA")
    game_log.mark_game_session_debug_log_ended("soccer", "soccer-old", lanlan_name="LanA", reason="test")

    assert game_log.find_game_session_debug_log("soccer-old", "soccer") is not None

    game_log.enable_game_session_debug_log("badminton", "badminton-new", lanlan_name="LanB")

    assert game_log.find_game_session_debug_log("soccer-old", "soccer") is None
    assert {item["session_id"] for item in game_log.list_game_session_debug_log_summaries()} == {"badminton-new"}


@pytest.mark.unit
def test_game_debug_logs_drop_completed_session_after_retention_ttl():
    now = 1_000_000.0
    game_log.enable_game_session_debug_log("soccer", "soccer-old", lanlan_name="Lan")
    game_log.mark_game_session_debug_log_ended("soccer", "soccer-old", lanlan_name="Lan", reason="test")
    entry = game_log.find_game_session_debug_log("soccer-old", "soccer")
    assert entry is not None
    entry["ended_at"] = now - game_log.GAME_SESSION_DEBUG_RETAINED_SESSION_TTL_SECONDS - 1
    entry["updated_at"] = entry["ended_at"]

    game_log.cleanup_game_session_debug_logs(now)

    assert game_log.find_game_session_debug_log("soccer-old", "soccer") is None


@pytest.mark.unit
def test_parse_control_instructions_extracts_json_line():
    result = gr_runtime._parse_control_instructions(
        '这球我拿下了喵\n{"mood":"happy","difficulty":"lv2"}'
    )

    assert result == {
        "line": "这球我拿下了喵",
        "control": {"mood": "happy", "difficulty": "lv2"},
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llm_session_manager_appends_generic_context_to_session_history():
    class FakeSession:
        def __init__(self):
            self._conversation_history = []

    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.session = FakeSession()
    mgr.message_cache_for_new_session = []
    mgr.session_ready = True

    first = await mgr.append_context(source="icebreaker", role="assistant", text=" hi ")
    second = await mgr.append_context(source="icebreaker", role="user", text=" choice ")
    assert first.appended is True
    assert second.appended is True
    assert isinstance(mgr.session._conversation_history[0], AIMessage)
    assert mgr.session._conversation_history[0].content == "hi"
    assert isinstance(mgr.session._conversation_history[1], HumanMessage)
    assert mgr.session._conversation_history[1].content == "choice"


@pytest.mark.unit
def test_parse_control_instructions_sanitizes_visible_line_leaks():
    result = gr_runtime._parse_control_instructions(
        'glog_0040: 哼，那我认真一点咯。 (mood=angry, difficulty=lv2)\n'
        'reason="balance tuning"\n'
        '{"mood":"angry","difficulty":"lv2","reason":"压一压节奏"}'
    )

    assert result == {
        "line": "哼，那我认真一点咯。",
        "control": {"mood": "angry", "difficulty": "lv2", "reason": "压一压节奏"},
    }


@pytest.mark.unit
def test_parse_control_instructions_drops_internal_advice_lines_from_visible_line():
    result = gr_runtime._parse_control_instructions(
        '根据系统建议降低难度。\n'
        '看你追得这么急，我就稍微认真一点点。'
    )

    assert result == {
        "line": "看你追得这么急，我就稍微认真一点点。",
        "control": {},
    }


@pytest.mark.unit
def test_badminton_prompt_and_control_contract():
    prompt = gr_runtime._build_game_prompt(
        "badminton",
        "Lan",
        "傲娇但会认真看比赛。",
        language="zh",
    )

    assert "羽毛球小游戏" in prompt
    assert "有效区域或目标落点" in prompt
    assert "line_in、net_touch、zone_in、out、net" in prompt
    assert "expression" in prompt
    assert "intensity" in prompt
    assert "final_streak" in prompt
    assert ">=15" in prompt

    parsed = gr_runtime._parse_control_instructions(
        '破纪录了喵！\n{"mood":"surprised","expression":"hype","intensity":"high","difficulty":"max"}',
        game_type="badminton",
    )
    assert parsed == {
        "line": "破纪录了喵！",
        "control": {"mood": "surprised", "expression": "hype", "intensity": "high", "difficulty": "max"},
    }


@pytest.mark.unit
def test_badminton_duel_prompt_contract():
    prompt = gr_runtime._build_game_prompt(
        "badminton",
        "Lan",
        "傲娇但会认真看比赛。",
        language="zh",
        mode="duel",
    )

    assert "羽毛球对拉回合" in prompt
    assert "label / duel 字段" in prompt
    assert "player_duel_shot" in prompt
    assert "duel.player_score" in prompt
    assert "duel_outcome" in prompt


@pytest.mark.unit
@pytest.mark.parametrize("lang", ("zh", "en", "ja", "ko", "ru", "es", "pt"))
def test_badminton_duel_prompts_use_duel_outcome_for_winner(lang):
    prompt = gr_runtime.get_badminton_system_prompt(lang, mode="duel")

    assert "duel_outcome" in prompt
    assert "duel.active_shooter" in prompt


@pytest.mark.unit
def test_badminton_control_drops_invalid_values():
    parsed = gr_runtime._parse_control_instructions(
        '嗯？\n{"mood":"evil","expression":"explode","intensity":"extreme"}',
        game_type="badminton",
    )

    assert parsed == {"line": "嗯？", "control": {}}


@pytest.mark.unit
def test_badminton_event_sanitizer_keeps_current_state_and_drops_invalid_fields():
    event, error = gr_visible_events._sanitize_badminton_event({
        "kind": "shot_result",
        "result": "scored",
        "shot_type": "line_in",
        "streak": "7",
        "distance": "380",
        "currentState": {
            "game": "badminton",
            "streak": "7",
            "distance": "380",
            "record_distance": "520",
            "final_streak": "7",
            "final_distance": "380",
            "last_shot_type": "line_in",
            "score": {
                "score": "42",
                "best_streak": "7",
                "made_count": "9",
                "maxDistancePx": "380",
                "mode": "timed",
                "unsafe": "<tag>",
            },
            "unsafe": "<tag>",
        },
        "score": "42",
        "was_perfect": True,
        "badmintonGameMemoryEnabled": False,
        "gameMemoryEnabled": False,
        "debugBlob": "x" * 5000,
    })

    assert error == ""
    assert event["streak"] == 7
    assert event["distance"] == 380
    assert event["score"] == 42
    assert event["was_perfect"] is True
    assert event["badmintonGameMemoryEnabled"] is False
    assert event["badmintonGameMemoryEnabled"] is False
    assert event["gameMemoryEnabled"] is False
    assert "debugBlob" not in event
    assert event["shot_type"] == "line_in"
    assert event["currentState"] == {
        "game": "badminton",
        "last_shot_type": "line_in",
        "streak": 7,
        "distance": 380,
        "record_distance": 520,
        "final_streak": 7,
        "final_distance": 380,
        "score": {
            "score": 42,
            "best_streak": 7,
            "made_count": 9,
            "max_distance_px": 380.0,
            "mode": "spectator",
        },
    }

    invalid, invalid_error = gr_visible_events._sanitize_badminton_event({
        "kind": "bad_kind",
        "shot_type": "explode",
    })
    assert invalid is None
    assert invalid_error == "invalid kind"


@pytest.mark.unit
def test_badminton_event_sanitizer_keeps_duel_state_and_shot_missed():
    event, error = gr_visible_events._sanitize_badminton_event({
        "kind": "shot_missed",
        "mode": "duel",
        "duel_outcome": "player_win",
        "duel": {
            "playerScore": "2",
            "neko_score": "3",
            "playerMisses": "1",
            "neko_misses": "2",
            "maxMisses": "3",
            "round": "4",
            "activeShooter": "neko",
        },
        "currentState": {
            "game": "badminton",
            "mode": "duel",
            "duel": {
                "player_score": "2",
                "nekoScore": "3",
                "player_misses": "1",
                "nekoMisses": "2",
                "max_misses": "3",
                "round": "4",
                "active_shooter": "neko",
            },
        },
    })

    assert error == ""
    assert event["kind"] == "shot_missed"
    assert event["mode"] == "duel"
    assert event["duel_outcome"] == "player_win"
    assert event["duel"] == {
        "player_score": 2,
        "neko_score": 3,
        "player_misses": 1,
        "neko_misses": 2,
        "max_misses": 3,
        "round": 4,
        "active_shooter": "neko",
    }
    assert event["currentState"]["duel"] == {
        "player_score": 2,
        "neko_score": 3,
        "player_misses": 1,
        "neko_misses": 2,
        "max_misses": 3,
        "round": 4,
        "active_shooter": "neko",
    }

    event, error = gr_visible_events._sanitize_badminton_event({
        "kind": "shot_missed",
        "mode": "duel",
        "duel": {
            "playerMisses": "Infinity",
            "nekoMisses": "-Infinity",
            "maxMisses": "NaN",
            "playerScore": "5",
        },
    })

    assert error == ""
    assert event["duel"] == {"player_score": 5}


@pytest.mark.unit
def test_badminton_event_sanitizer_drops_removed_horse_state():
    event, error = gr_visible_events._sanitize_badminton_event({
        "kind": "shot_missed",
        "mode": "horse",
        "horse": {
            "word": "HORSE",
            "lettersPlayer": "2",
            "letters_neko": "1",
            "phase": "player_reply",
            "turnOwner": "player",
            "challenge": {
                "distance": "220",
                "angle": "58",
                "sweet": ["38", "44"],
                "owner": "neko",
                "unsafe": "<tag>",
            },
        },
        "currentState": {
            "game": "badminton",
            "mode": "horse",
            "horse": {
                "letters_player": 2,
                "lettersNeko": 1,
                "phase": "player_reply",
                "turn_owner": "player",
                "challenge": None,
            },
        },
    })

    assert error == ""
    assert event["mode"] == "spectator"
    assert "horse" not in event
    assert "horse" not in event["currentState"]


@pytest.mark.unit
def test_badminton_event_sanitizer_keeps_bounded_current_state_attempts():
    attempts = [
        {
            "shooter": "player",
            "shot_type": "line_in",
            "distance": str(100 + index),
            "distance_m": "3.5",
            "scored": index % 2 == 0,
            "score": "2",
            "round": str(index),
            "angle": "44.5",
            "power": "82.1",
            "unsafe": "<tag>",
        }
        for index in range(14)
    ]

    event, error = gr_visible_events._sanitize_badminton_event({
        "kind": "game_over",
        "mode": "duel",
        "currentState": {
            "game": "badminton",
            "mode": "duel",
            "attempts_results": attempts,
        },
    })

    assert error == ""
    sanitized_attempts = event["currentState"]["attempts_results"]
    assert len(sanitized_attempts) == 12
    assert sanitized_attempts[0]["round"] == 2
    assert sanitized_attempts[-1] == {
        "shooter": "player",
        "shot_type": "line_in",
        "scored": False,
        "score": 2,
        "round": 13,
        "distance": 113,
        "distance_m": 3.5,
        "angle": 44.5,
        "power": 82.1,
    }
    assert "unsafe" not in sanitized_attempts[-1]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_quick_lines_returns_fallback_on_llm_failure(monkeypatch):
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {
        "lanlan_name": "Lan",
        "lanlan_prompt": "傲娇。",
        "user_language": "zh",
        "model": "fake",
        "base_url": "http://fake",
        "api_key": "fake",
    })

    async def fail_llm_async(*_args, **_kwargs):
        raise RuntimeError("llm unavailable")

    import utils.llm_client as llm_client
    monkeypatch.setattr(llm_client, "create_chat_llm_async", fail_llm_async)

    result = await gr_runtime.game_quick_lines(
        "badminton",
        _FakeRequest({"lanlan_name": "Lan", "session_id": "bd-1"}),
    )

    assert result["ok"] is True
    assert result["fallback"] is True
    assert "line_in" in result["lines"]
    assert "shot_missed" in result["lines"]
    assert "game_over" in result["lines"]
    assert "close_to_record" in result["lines"]
    assert "streak_15" in result["lines"]
    assert "streak_20" in result["lines"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_quick_lines_fallback_uses_request_language(monkeypatch):
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {
        "lanlan_name": "Lan",
        "lanlan_prompt": "Tsundere but focused.",
        "user_language": "zh",
        "model": "fake",
        "base_url": "http://fake",
        "api_key": "fake",
    })

    async def fail_llm_async(*_args, **_kwargs):
        raise RuntimeError("llm unavailable")

    import utils.llm_client as llm_client
    monkeypatch.setattr(llm_client, "create_chat_llm_async", fail_llm_async)

    result = await gr_runtime.game_quick_lines(
        "badminton",
        _FakeRequest({"lanlan_name": "Lan", "session_id": "bd-1", "i18n_language": "en-US"}),
    )

    assert result["ok"] is True
    assert result["fallback"] is True
    assert result["lines"]["line_in"][0] == "On the line!"
    assert result["lines"]["shot_missed"][0] == "Still in it"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_quick_lines_fallback_supports_japanese_request_language(monkeypatch):
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {
        "lanlan_name": "Lan",
        "lanlan_prompt": "Tsundere but focused.",
        "user_language": "zh",
        "model": "fake",
        "base_url": "http://fake",
        "api_key": "fake",
    })

    async def fail_llm_async(*_args, **_kwargs):
        raise RuntimeError("llm unavailable")

    import utils.llm_client as llm_client
    monkeypatch.setattr(llm_client, "create_chat_llm_async", fail_llm_async)

    result = await gr_runtime.game_quick_lines(
        "badminton",
        _FakeRequest({"lanlan_name": "Lan", "session_id": "bd-ja-1", "i18n_language": "ja-JP"}),
    )

    assert result["ok"] is True
    assert result["fallback"] is True
    assert result["lines"]["line_in"][0] == "ラインぎりぎり！"
    assert result["lines"]["game_over"][1] == "この一本、覚えておくね"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_quick_lines_fallback_preserves_traditional_chinese_request_language(monkeypatch):
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {
        "lanlan_name": "Lan",
        "lanlan_prompt": "Tsundere but focused.",
        "user_language": "zh",
        "model": "fake",
        "base_url": "http://fake",
        "api_key": "fake",
    })

    async def fail_llm_async(*_args, **_kwargs):
        raise RuntimeError("llm unavailable")

    import utils.llm_client as llm_client
    monkeypatch.setattr(llm_client, "create_chat_llm_async", fail_llm_async)

    result = await gr_runtime.game_quick_lines(
        "badminton",
        _FakeRequest({"lanlan_name": "Lan", "session_id": "bd-zh-tw-1", "i18n_language": "zh-TW"}),
    )

    assert result["ok"] is True
    assert result["fallback"] is True
    assert result["lines"]["line_in"][0] == "壓線，算你準"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("language", "expected_line"),
    (
        ("zh-CN", "压线，算你准"),
        ("zh-TW", "壓線，算你準"),
        ("en", "On the line!"),
        ("ja", "ラインぎりぎり！"),
        ("ko", "라인에 걸쳤어!"),
        ("ru", "По линии!"),
        ("es", "¡En la línea!"),
        ("pt", "Na linha!"),
    ),
)
def test_badminton_quick_lines_fallback_supports_neko_core_languages(language, expected_line):
    lines = gr_runtime._get_badminton_quick_lines_fallback(language)

    assert lines["line_in"][0] == expected_line
    assert set(lines) == gr_runtime._BADMINTON_QUICK_LINE_KEYS


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_quick_lines_uses_requested_character(monkeypatch):
    gr_runtime._badminton_quick_lines_cache.clear()
    captured = {}

    def fake_character_info(lanlan_name=None):
        name = str(lanlan_name or "CurrentLan")
        return {
            "lanlan_name": name,
            "lanlan_prompt": "Requested persona." if name == "InviteLan" else "Current persona.",
            "user_language": "en",
            "model": "fake",
            "base_url": "http://fake",
            "api_key": "fake",
        }

    class _FakeResult:
        content = '{"line_in":["Nice arc"]}'

    class _FakeLLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def ainvoke(self, messages):
            captured["system"] = messages[0].content
            return _FakeResult()

    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: fake_character_info("CurrentLan"))
    _gr_patch_all(monkeypatch, "_get_character_info", fake_character_info)

    import utils.llm_client as llm_client
    monkeypatch.setattr(llm_client, "create_chat_llm", lambda *_args, **_kwargs: _FakeLLM())

    result = await gr_runtime.game_quick_lines(
        "badminton",
        _FakeRequest({"lanlan_name": "InviteLan", "session_id": "bd-1", "mode": "duel"}),
    )

    assert result["ok"] is True
    assert result["character"] == "InviteLan"
    assert result["lines"]["line_in"] == ["Nice arc"]
    assert "game_over" not in result["lines"]
    assert "Requested persona." in captured["system"]
    assert "Current persona." not in captured["system"]


@pytest.mark.unit
def test_badminton_template_contract():
    from pathlib import Path

    html = Path(__file__).resolve().parents[2].joinpath("templates/badminton_demo.html").read_text(encoding="utf-8")

    assert "/api/game/badminton/route/start" in html
    assert "/api/game/badminton/chat" in html
    assert "/api/game/badminton/quick-lines" in html
    assert "/api/game/badminton/speak" in html
    assert "/api/game/badminton/mirror-assistant" in html
    assert "/api/game/badminton/route/drain" in html
    assert "/api/game/badminton/route/heartbeat" in html
    assert "/api/game/badminton/route/end" in html
    assert "pageVisible: pageVisible" in html
    assert "visibilityState: document.visibilityState" in html
    assert "var drainSessionId = sessionId" in html
    assert "if (sessionId !== drainSessionId || currentMode !== drainMode) return" in html
    assert "/api/game/badminton/character" in html
    assert "/api/game/badminton/leaderboard" in html
    assert "initNekoAvatar" in html
    assert "activeAvatarType" in html
    assert "model_type" in html
    assert "live3d_sub_type" in html
    assert "async function initVRMAvatar(vrmPath)" in html
    assert "async function initMMDAvatar(mmdPath)" in html
    assert "var live2dPath = charData.live2d_path || '/static/yui-lolita/yui-lolita.model3.json';" in html
    assert "window.lanlan_config.model_type = 'live2d';" in html
    assert "window.lanlan_config.live3d_sub_type = '';" in html
    assert "await initLive2DAvatar(live2dPath);" in html
    assert "await initVRMAvatar(vrmPath);" not in html
    assert "initPIXI('neko-l2d-canvas', 'neko-l2d-container'" in html
    assert "loadModel(modelPath)" in html
    assert "var modelPath = live2dPath || '/static/yui-lolita/yui-lolita.model3.json'" in html
    assert "当前 Live2D 路径缺失" in html
    assert "角色接口不可用或未返回 model_type" not in html
    assert "modelType === 'live3d' && subType === 'vrm'" not in html
    assert "modelType === 'live3d' && subType === 'mmd'" not in html
    assert "MMD audience embed is waiting for a safe independent manager API" in html
    assert "not loading Live2D fallback for MMD" in html
    assert "var modelPath = '/static/mao_pro/mao_pro.model3.json'" not in html
    assert "focusController" in html
    assert "BADMINTON_COURT_METERS" in html
    assert "checkShuttleLanding" in html
    assert "drawNetPosts" in html
    assert "netTouched" in html
    assert "score-label" in html
    assert "leaderboard-panel" in html
    assert "leaderboard-button" in html
    assert "leaderboard-tabs" in html
    assert "leaderboard-body" in html
    assert "pxToMeters" in html
    assert "calcShotScore" in html
    assert 'id="aiming-canvas"' in html
    assert "var aimingCanvas = document.getElementById('aiming-canvas')" in html
    assert "function drawCourt()" in html
    assert "function drawNet()" in html
    assert "function drawAiming(now)" in html
    assert "drawDistanceMarkers" in html
    assert "drawFreeThrowLine" not in html
    assert "drawThreePointLine" not in html
    assert "bd_last_final_streak" in html
    assert "navigator.sendBeacon" in html
    assert ".textContent" in html
    assert ".innerHTML" not in html
    assert "ctx.lineTo(px + Math.cos(radians) * 54, py - Math.sin(radians) * 54);" not in html
    keydown = html[
        html.index("addBadmintonEventListener(window, 'keydown'"):
        html.index("if (bgmVolumeInput)", html.index("addBadmintonEventListener(window, 'keydown'"))
    ]
    assert "key === 'g'" not in keydown
    assert "key === 's'" not in keydown
    assert "key === 'm'" in keydown
    assert "key === ' ' || ev.code === 'Space'" in keydown
    assert "var baseX = getPlayerX() + 10;" not in html
    assert "var SWING_IMPACT_DELAY_MS = 120;" in html
    assert "var SHUTTLE_MASS_KG = 0.005;" in html
    assert "function buildSwingImpulse(angle, power, shooter, incomingBall)" in html
    assert "shuttle.massKg = SHUTTLE_MASS_KG;" in html
    assert "currentMode" in html
    assert "function isPracticeMode()" in html
    assert "var currentMode = 'duel';" in html
    assert "function isPracticeMode() {\n    return false;\n  }" in html
    assert "不限次数" in html
    assert "自由练习：不限挥拍次数，不记录排行榜分数" not in html
    assert "自由练习：不记录排行榜分数" not in html
    assert "if (!isPracticeMode()) game.attemptsRemaining" in html
    assert "if (!isPracticeMode()) game.totalScore += shotScore" in html
    assert "var newRecord = !isPracticeMode() && previousDistance > game.recordDistance" in html
    assert "async function initPlayerAvatar()" in html
    assert "playerSenseiLoading = true" in html
    assert "game.power = 0;" in html
    assert 'id="mode-switcher"' not in html
    assert 'data-mode="spectator"' not in html
    assert 'data-mode="shooter"' not in html
    assert 'data-mode="duel"' not in html
    assert "function updateModeSwitcher()" not in html
    assert "function switchBadmintonMode(nextMode)" not in html
    assert "url.searchParams.set('mode', mode)" not in html
    assert "queueNekoDuelTurnVoice" in html
    assert "voice_deadline_ms" in html
    assert "--neko-expression-y" in html
    assert "yui-neko-tease" in html
    assert "updateYuiPosition" in html
    assert "shouldCallLLMShooter" not in html
    assert "shouldCallLLMDuel" in html
    assert "function shouldCallLLMDuel(event) {\n    return false;" not in html
    assert "YUI_PASSIVE_LINES_SHOOTER" not in html
    assert "YUI_PASSIVE_LINES_DUEL" in html
    assert "mode: currentMode" in html
    assert "launchedFromInvite" not in html
    assert "badmintonInviteRequired" not in html
    assert "var currentMode = requestedMode === 'shooter' ? 'shooter' : 'spectator';" not in html
    assert "if (requestedMode === 'duel') currentMode = 'duel';" not in html
    assert "await initLive2DAvatar(live2dPath);" in html
    assert "aim_duration_seconds" in html
    assert "latestShooterRating" not in html
    assert "控拍评级" not in html
    assert "function getRequestLanguage()" in html
    assert "function getConversationLanguagePayload(renderLanguage)" in html
    assert "i18n_language: getRequestLanguage()" not in html
    assert "function applyCharacterIdentity(charData, languageRevision)" in html
    assert "function applyResolvedLanlanName(resolvedName)" in html
    assert "function applyRouteIdentity(state)" in html
    assert "lanlanName = resolvedName" in html
    assert "lanlan_name: queryLanlan || ''" in html
    assert "lanlan_name: queryLanlan || 'badminton_demo'" not in html
    assert "var lanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';" in html
    assert "var routeLanlanName = getRouteLanlanName();" in html
    assert "lanlan_name: routeLanlanName" in html
    assert "character_name: routeLanlanName" in html
    assert "applyRouteIdentity(res.state);" in html
    assert "lanlan_name: lanlanName, source: 'badminton_demo'" not in html
    assert "initNekoAvatar().finally(function () { startRoute(); })" not in html
    assert "var badmintonCharacterPromise = null;" in html
    assert "var badmintonCharacterLanguagePreferenceResolved = false;" in html
    assert "if (!badmintonCharacterLanguagePreferenceResolved)" in html
    assert "charData && charData.language_preference_resolved === true" in html
    assert "badmintonCharacterExplicitLanguage = normalizeBadmintonExplicitLanguage(" in html
    assert "badmintonCharacterLanguageRevision === languageRevision" in html
    assert html.count("badmintonCharacterLanguageRevision += 1;") == 4
    assert html.count("badmintonCharacterLanguagePreferenceResolved = true;") == 3
    assert "if (!currentCharacterName)" in html
    assert "payload.i18n_language = badmintonCharacterExplicitLanguage" in html
    assert "window.hydrateExplicitConversationLanguagePreference" not in html
    assert "voiceArbiter" in html
    assert "mirror_text: false" in html
    assert "post('/mirror-assistant'" in html
    assert "post('/speak'" in html
    assert "if (pending && pending.priority <= entry.priority) return" in html
    assert "if (voiceArbiter.pending.priority <= entry.priority) return" in html
    assert "label: shooter === 'neko' ? 'neko_duel_shot' : 'player_duel_shot'" in html


@pytest.mark.unit
def test_badminton_direct_open_language_clear_invalidates_character_response():
    from pathlib import Path

    html = Path(__file__).resolve().parents[2].joinpath(
        "templates/badminton_demo.html"
    ).read_text(encoding="utf-8")
    listener = html[html.index("function updateBadmintonCharacterExplicitLanguage"):
                    html.index("function api(path)")]
    apply_identity = html[html.index("function applyCharacterIdentity"):
                          html.index("function applyRouteIdentity")]

    assert "if (!currentCharacterName)" in listener
    assert "if (eventCharacterName) badmintonCharacterLanguageRevision += 1;" in listener
    assert "updateBadmintonCharacterExplicitLanguage(event, true);" in listener
    assert "badmintonCharacterLanguagePreferenceResolved = true;" not in listener.split(
        "if (!currentCharacterName)", 1
    )[1].split("return;", 1)[0]
    assert "badmintonCharacterLanguageRevision === languageRevision" in apply_identity


@pytest.mark.unit
def test_badminton_leaderboard_query_contract():
    from pathlib import Path

    source = "".join(q.read_text(encoding="utf-8") for q in sorted(Path(__file__).resolve().parents[2].joinpath("main_routers/game_router").glob("*.py")))

    assert "BEGIN IMMEDIATE" in source
    assert "LIMIT ? OFFSET ?" in source
    assert "WHERE lanlan_name = ?" in source
    assert "WHERE session_id = ?" in source
    assert "_badminton_score_order_clause" not in source


@pytest.mark.unit
def test_strip_ssml_like_tags_only_removes_known_ssml_tags():
    line = gr_runtime._strip_ssml_like_tags(
        'a < b > c &#160; <break time="200ms"/>'
        ' <prosody rate="slow">慢一点</prosody> <not-ssml>保留</not-ssml>'
    )

    assert "a < b > c" in line
    assert "&#160;" in line
    assert "慢一点" in line
    assert "<not-ssml>保留</not-ssml>" in line
    assert "<break" not in line
    assert "<prosody" not in line
    assert "</prosody>" not in line


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_leaderboard_post_and_get_sorting(tmp_path, monkeypatch):
    _gr_patch_all(monkeypatch, "_BADMINTON_SCORES_DB_PATH", tmp_path / "badminton_scores.db")

    with reset_game_route_state():
        _allow_badminton_score_session("Lan A", "s1", "duel")
        first = await gr_scores.game_badminton_leaderboard_submit("badminton", _FakeRequest({
            "session_id": "s1",
            "lanlan_name": "Lan A",
            "score": 15,
            "streak": 4,
            "max_distance_px": 200,
            "line_in_count": 1,
            "net_touch_count": 0,
            "zone_in_count": 0,
            "mode": "duel",
        }))
        _allow_badminton_score_session("Lan B", "s2", "duel")
        second = await gr_scores.game_badminton_leaderboard_submit("badminton", _FakeRequest({
            "session_id": "s2",
            "lanlan_name": "Lan B",
            "score": 20,
            "streak": 3,
            "max_distance_px": 300,
            "line_in_count": 0,
            "net_touch_count": 1,
            "zone_in_count": 0,
            "mode": "duel",
        }))
        _allow_badminton_score_session("Lan A", "s3", "duel")
        third = await gr_scores.game_badminton_leaderboard_submit("badminton", _FakeRequest({
            "session_id": "s3",
            "lanlan_name": "Lan A",
            "score": 20,
            "streak": 5,
            "max_distance_px": 250,
            "line_in_count": 2,
            "net_touch_count": 0,
            "zone_in_count": 1,
            "mode": "duel",
        }))

        assert first["ok"] is True
        assert second["ok"] is True
        assert third["ok"] is True
        assert third["rank"] == 1
        assert third["is_personal_best"] is True

        leaderboard = await gr_scores.game_badminton_leaderboard(
            "badminton",
            session_id="s3",
            lanlan_name="Lan A",
        )

        assert leaderboard["ok"] is True
        assert leaderboard["total_players"] == 2
        assert leaderboard["your_best"] == {"rank": 1, "score": 20}
        assert leaderboard["top"][0]["name"] == "Lan A"
        assert leaderboard["top"][0]["score"] == 20
        assert leaderboard["top"][0]["streak"] == 5
        assert leaderboard["top"][0]["max_distance_m"] == "6.3"
        assert leaderboard["top"][1]["name"] == "Lan B"
        assert leaderboard["top"][1]["score"] == 20
        assert leaderboard["top"][1]["streak"] == 3
        assert leaderboard["top"][1]["max_distance_m"] == "7.6"

        unsupported = await gr_scores.game_badminton_leaderboard("football")
        assert unsupported["ok"] is True
        assert unsupported["top"] == []


@pytest.mark.unit
def test_badminton_leaderboard_distance_uses_client_court_scale():
    assert gr_scores._BADMINTON_PX_PER_METER == pytest.approx(12 * 3.28084)
    assert gr_scores._format_badminton_distance_meters(300) == "7.6"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_leaderboard_migrates_legacy_table_without_new_columns(tmp_path, monkeypatch):
    db_path = tmp_path / "badminton_scores.db"
    _gr_patch_all(monkeypatch, "_BADMINTON_SCORES_DB_PATH", db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE badminton_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                lanlan_name TEXT NOT NULL DEFAULT '',
                score INTEGER NOT NULL,
                streak INTEGER NOT NULL,
                max_distance_px REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    with reset_game_route_state():
        _allow_badminton_score_session("Lan Legacy", "legacy-session", "duel")
        result = await gr_scores.game_badminton_leaderboard_submit("badminton", _FakeRequest({
            "session_id": "legacy-session",
            "lanlan_name": "Lan Legacy",
            "score": 24,
            "streak": 3,
            "max_distance_px": 300,
            "mode": "duel",
        }))

        assert result["ok"] is True
        leaderboard = await gr_scores.game_badminton_leaderboard("badminton")

    assert leaderboard["top"][0]["mode"] == "duel"
    with sqlite3.connect(str(db_path)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(badminton_scores)").fetchall()}
    assert {"mode", "line_in_count", "net_touch_count", "zone_in_count"} <= columns


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_leaderboard_get_paginates_results(tmp_path, monkeypatch):
    _gr_patch_all(monkeypatch, "_BADMINTON_SCORES_DB_PATH", tmp_path / "badminton_scores.db")

    with reset_game_route_state():
        for index in range(12):
            _allow_badminton_score_session(f"Lan {index}", f"s{index}", "duel")
            await gr_scores.game_badminton_leaderboard_submit("badminton", _FakeRequest({
                "session_id": f"s{index}",
                "lanlan_name": f"Lan {index}",
                "score": 120 - index,
                "streak": 1,
                "max_distance_px": 200,
                "line_in_count": 0,
                "net_touch_count": 0,
                "zone_in_count": 0,
                "mode": "duel",
            }))

        page = await gr_scores.game_badminton_leaderboard(
            "badminton",
            limit=5,
            offset=5,
        )

        assert page["limit"] == 5
        assert page["offset"] == 5
        assert page["total_scores"] == 12
        assert page["has_more"] is True
        assert [row["score"] for row in page["top"]] == [115, 114, 113, 112, 111]
        assert [row["rank"] for row in page["top"]] == [6, 7, 8, 9, 10]

        last_page = await gr_scores.game_badminton_leaderboard(
            "badminton",
            limit=5,
            offset=10,
        )

        assert last_page["has_more"] is False
        assert [row["score"] for row in last_page["top"]] == [110, 109]
        assert [row["rank"] for row in last_page["top"]] == [11, 12]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_leaderboard_sanitizes_inputs_and_normalizes_mode(tmp_path, monkeypatch):
    _gr_patch_all(monkeypatch, "_BADMINTON_SCORES_DB_PATH", tmp_path / "badminton_scores.db")

    with reset_game_route_state():
        _allow_badminton_score_session("Lan C", "session-9", "duel")
        result = await gr_scores.game_badminton_leaderboard_submit("badminton", _FakeRequest({
            "session_id": "  session-9  ",
            "lanlan_name": "  Lan C  ",
            "score": "-7",
            "streak": "4.9",
            "max_distance_px": "nan",
            "line_in_count": "-2",
            "net_touch_count": "2.8",
            "zone_in_count": "3.2",
            "mode": "duel",
        }))

        assert result["ok"] is True
        assert result["rank"] == 1
        assert result["total_players"] == 1
        assert result["is_personal_best"] is True

        leaderboard = await gr_scores.game_badminton_leaderboard(
            "badminton",
            session_id="session-9",
            lanlan_name="Lan C",
        )

        assert leaderboard["top"][0]["name"] == "Lan C"
        assert leaderboard["top"][0]["score"] == 0
        assert leaderboard["top"][0]["streak"] == 4
        assert leaderboard["top"][0]["mode"] == "duel"
        assert leaderboard["your_best"] == {"rank": 1, "score": 0}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_leaderboard_rejects_unknown_score_session(tmp_path, monkeypatch):
    _gr_patch_all(monkeypatch, "_BADMINTON_SCORES_DB_PATH", tmp_path / "badminton_scores.db")

    with reset_game_route_state():
        result = await gr_scores.game_badminton_leaderboard_submit("badminton", _FakeRequest({
            "session_id": "fake-session",
            "lanlan_name": "Lan Fake",
            "score": 999999,
            "mode": "duel",
        }))

        assert result == {"ok": False, "reason": "invalid_session"}
        leaderboard = await gr_scores.game_badminton_leaderboard("badminton")
        assert leaderboard["total_scores"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_leaderboard_rejects_spectator_score_session(tmp_path, monkeypatch):
    _gr_patch_all(monkeypatch, "_BADMINTON_SCORES_DB_PATH", tmp_path / "badminton_scores.db")

    with reset_game_route_state():
        gr_scores._remember_badminton_score_session("Lan Practice", "practice-session", "spectator")

        result = await gr_scores.game_badminton_leaderboard_submit("badminton", _FakeRequest({
            "session_id": "practice-session",
            "lanlan_name": "Lan Practice",
            "score": 999999,
            "mode": "spectator",
        }))

        assert result == {"ok": False, "reason": "invalid_session"}
        assert gr_scores._badminton_recent_score_sessions == {}
        leaderboard = await gr_scores.game_badminton_leaderboard("badminton")
        assert leaderboard["total_scores"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_route_end_uses_server_mode_for_score_session(monkeypatch):
    async def fake_deliver_postgame(*_args, **_kwargs):
        return {"ok": True, "action": "skip", "reason": "test"}

    _gr_patch_all(monkeypatch, "_deliver_game_postgame", fake_deliver_postgame)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    with reset_game_route_state():
        state = gr_runtime._activate_game_route("badminton", "practice-session", "Lan Practice")
        state["mode"] = "spectator"
        _mark_game_started(state)

        result = await gr_runtime._complete_game_end_from_payload(
            "badminton",
            {
                "session_id": "practice-session",
                "lanlan_name": "Lan Practice",
                "mode": "duel",
                "gameStarted": True,
                "finalScore": {"mode": "duel", "score": 999999},
            },
            default_reason="route_end",
        )

        assert result["ok"] is True
        assert result["route_closed"] is True
        assert gr_scores._badminton_recent_score_sessions == {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_route_end_requires_completed_round_for_score_session(monkeypatch):
    async def fake_deliver_postgame(*_args, **_kwargs):
        return {"ok": True, "action": "skip", "reason": "test"}

    _gr_patch_all(monkeypatch, "_deliver_game_postgame", fake_deliver_postgame)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    with reset_game_route_state():
        state = gr_runtime._activate_game_route("badminton", "early-exit-session", "Lan Early")
        state["mode"] = "duel"
        _mark_game_started(state)

        result = await gr_runtime._complete_game_end_from_payload(
            "badminton",
            {
                "session_id": "early-exit-session",
                "lanlan_name": "Lan Early",
                "mode": "duel",
                "gameStarted": True,
                "finalScore": {"mode": "duel", "score": 999999},
            },
            default_reason="route_end",
        )

        assert result["ok"] is True
        assert result["route_closed"] is True
        assert gr_scores._badminton_recent_score_sessions == {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_route_end_remembers_completed_round_score_session(monkeypatch):
    async def fake_deliver_postgame(*_args, **_kwargs):
        return {"ok": True, "action": "skip", "reason": "test"}

    _gr_patch_all(monkeypatch, "_deliver_game_postgame", fake_deliver_postgame)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    with reset_game_route_state():
        state = gr_runtime._activate_game_route("badminton", "completed-session", "Lan Done")
        state["mode"] = "duel"
        _mark_game_started(state)

        result = await gr_runtime._complete_game_end_from_payload(
            "badminton",
            {
                "session_id": "completed-session",
                "lanlan_name": "Lan Done",
                "mode": "duel",
                "gameStarted": True,
                "round_completed": True,
                "finalScore": {"mode": "duel", "score": 12, "best_streak": 4, "max_distance_px": 240},
            },
            default_reason="route_end",
        )

        assert result["ok"] is True
        assert result["route_closed"] is True
        assert result["state"]["lanlan_name"] == "Lan Done"
        score_session = gr_scores._badminton_recent_score_sessions[("Lan Done", "completed-session")]
        assert score_session["mode"] == "duel"
        assert score_session["score_totals"] == {"score": 12, "streak": 4, "max_distance_px": 240.0}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_leaderboard_rejects_score_mismatched_from_route_end(tmp_path, monkeypatch):
    async def fake_deliver_postgame(*_args, **_kwargs):
        return {"ok": True, "action": "skip", "reason": "test"}

    _gr_patch_all(monkeypatch, "_BADMINTON_SCORES_DB_PATH", tmp_path / "badminton_scores.db")
    _gr_patch_all(monkeypatch, "_deliver_game_postgame", fake_deliver_postgame)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    with reset_game_route_state():
        state = gr_runtime._activate_game_route("badminton", "bound-session", "Lan Bound")
        state["mode"] = "duel"
        _mark_game_started(state)

        await gr_runtime._complete_game_end_from_payload(
            "badminton",
            {
                "session_id": "bound-session",
                "lanlan_name": "Lan Bound",
                "mode": "duel",
                "gameStarted": True,
                "round_completed": True,
                "finalScore": {"mode": "duel", "score": 12, "best_streak": 3, "max_distance_px": 240},
            },
            default_reason="route_end",
        )

        tampered = await gr_scores.game_badminton_leaderboard_submit("badminton", _FakeRequest({
            "session_id": "bound-session",
            "lanlan_name": "Lan Bound",
            "score": 999999,
            "streak": 3,
            "max_distance_px": 240,
            "mode": "duel",
        }))

        assert tampered == {"ok": False, "reason": "invalid_session"}
        assert "reserved" not in gr_scores._badminton_recent_score_sessions[("Lan Bound", "bound-session")]

        accepted = await gr_scores.game_badminton_leaderboard_submit("badminton", _FakeRequest({
            "session_id": "bound-session",
            "lanlan_name": "Lan Bound",
            "score": 12,
            "streak": 3,
            "max_distance_px": 240,
            "mode": "duel",
        }))

        assert accepted["ok"] is True
        leaderboard = await gr_scores.game_badminton_leaderboard("badminton")
        assert leaderboard["total_scores"] == 1
        assert leaderboard["top"][0]["score"] == 12
        assert leaderboard["top"][0]["streak"] == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_leaderboard_rejects_live_active_route_score(tmp_path, monkeypatch):
    _gr_patch_all(monkeypatch, "_BADMINTON_SCORES_DB_PATH", tmp_path / "badminton_scores.db")

    with reset_game_route_state():
        state = {
            "game_type": "badminton",
            "session_id": "live-session",
            "lanlan_name": "Lan Live",
            "game_route_active": True,
            "mode": "duel",
        }
        _mark_game_started(state)
        gr_runtime._game_route_states[gr_runtime._route_state_key("Lan Live", "badminton")] = state

        result = await gr_scores.game_badminton_leaderboard_submit("badminton", _FakeRequest({
            "session_id": "live-session",
            "lanlan_name": "Lan Live",
            "score": 999999,
            "mode": "duel",
        }))

        assert result == {"ok": False, "reason": "invalid_session"}
        leaderboard = await gr_scores.game_badminton_leaderboard("badminton")
        assert leaderboard["total_scores"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_leaderboard_allows_recently_ended_route_score(tmp_path, monkeypatch):
    _gr_patch_all(monkeypatch, "_BADMINTON_SCORES_DB_PATH", tmp_path / "badminton_scores.db")

    with reset_game_route_state():
        state = _allow_badminton_score_session("Lan Ended", "ended-session", "duel")
        state["game_route_active"] = False
        gr_scores._remember_badminton_score_session("Lan Ended", "ended-session", "duel")

        result = await gr_scores.game_badminton_leaderboard_submit("badminton", _FakeRequest({
            "session_id": "ended-session",
            "lanlan_name": "Lan Ended",
            "score": 42,
            "streak": 2,
            "max_distance_px": 180,
            "mode": "duel",
        }))

        assert result["ok"] is True
        assert result["rank"] == 1

        duplicate = await gr_scores.game_badminton_leaderboard_submit("badminton", _FakeRequest({
            "session_id": "ended-session",
            "lanlan_name": "Lan Ended",
            "score": 99,
            "streak": 9,
            "max_distance_px": 500,
            "mode": "duel",
        }))

        assert duplicate == {"ok": False, "reason": "invalid_session"}
        leaderboard = await gr_scores.game_badminton_leaderboard("badminton")
        assert leaderboard["total_scores"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_leaderboard_rejects_removed_horse_mode_score(tmp_path, monkeypatch):
    _gr_patch_all(monkeypatch, "_BADMINTON_SCORES_DB_PATH", tmp_path / "badminton_scores.db")

    with reset_game_route_state():
        _allow_badminton_score_session("Lan Horse", "horse-session", "horse")

        result = await gr_scores.game_badminton_leaderboard_submit("badminton", _FakeRequest({
            "session_id": "horse-session",
            "lanlan_name": "Lan Horse",
            "score": 42,
            "streak": 2,
            "max_distance_px": 180,
            "mode": "horse",
        }))

        assert result == {"ok": False, "reason": "invalid_session"}
        leaderboard = await gr_scores.game_badminton_leaderboard("badminton")
        assert leaderboard["total_scores"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_leaderboard_rejects_removed_timed_mode_score(tmp_path, monkeypatch):
    _gr_patch_all(monkeypatch, "_BADMINTON_SCORES_DB_PATH", tmp_path / "badminton_scores.db")

    with reset_game_route_state():
        _allow_badminton_score_session("Lan Timed", "timed-session", "timed")

        result = await gr_scores.game_badminton_leaderboard_submit("badminton", _FakeRequest({
            "session_id": "timed-session",
            "lanlan_name": "Lan Timed",
            "score": 42,
            "streak": 2,
            "max_distance_px": 180,
            "mode": "timed",
        }))

        assert result == {"ok": False, "reason": "invalid_session"}
        leaderboard = await gr_scores.game_badminton_leaderboard("badminton")
        assert leaderboard["total_scores"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_leaderboard_keeps_score_session_when_insert_fails(monkeypatch):
    calls = 0

    def flaky_insert(_data, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("database is locked")
        return 1, 1, True

    _gr_patch_all(monkeypatch, "_badminton_insert_score", flaky_insert)

    with reset_game_route_state():
        _allow_badminton_score_session("Lan Retry", "retry-session", "duel")
        payload = {
            "session_id": "retry-session",
            "lanlan_name": "Lan Retry",
            "score": 42,
            "streak": 2,
            "max_distance_px": 180,
            "mode": "duel",
        }

        with pytest.raises(sqlite3.OperationalError):
            await gr_scores.game_badminton_leaderboard_submit("badminton", _FakeRequest(payload))

        assert gr_scores._badminton_recent_score_sessions[("Lan Retry", "retry-session")]["mode"] == "duel"
        assert "reserved" not in gr_scores._badminton_recent_score_sessions[("Lan Retry", "retry-session")]

        retry = await gr_scores.game_badminton_leaderboard_submit("badminton", _FakeRequest(payload))

        assert retry == {"ok": True, "rank": 1, "total_players": 1, "is_personal_best": True}
        assert ("Lan Retry", "retry-session") not in gr_scores._badminton_recent_score_sessions
        assert calls == 2


@pytest.mark.unit
def test_badminton_scores_default_path_uses_runtime_state_dir(tmp_path, monkeypatch):
    fake_config = type("FakeConfig", (), {"app_docs_dir": tmp_path / "runtime"})()
    _gr_patch_all(monkeypatch, "_BADMINTON_SCORES_DB_PATH", None)
    _gr_patch_all(monkeypatch, "get_config_manager", lambda: fake_config)

    path = gr_scores._get_badminton_scores_db_path()

    assert path == tmp_path / "runtime" / "state" / "game_scores" / "badminton_scores.db"
    assert "main_routers" not in str(path)


@pytest.mark.unit
def test_badminton_scores_default_path_uses_separate_runtime_db(tmp_path, monkeypatch):
    fake_config = type("FakeConfig", (), {"app_docs_dir": tmp_path / "runtime"})()
    _gr_patch_all(monkeypatch, "_BADMINTON_SCORES_DB_PATH", None)
    _gr_patch_all(monkeypatch, "get_config_manager", lambda: fake_config)

    path = gr_scores._get_badminton_scores_db_path("badminton")

    assert path == tmp_path / "runtime" / "state" / "game_scores" / "badminton_scores.db"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_leaderboard_uses_separate_scores_db(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    fake_config = type("FakeConfig", (), {"app_docs_dir": runtime_root})()
    _gr_patch_all(monkeypatch, "_BADMINTON_SCORES_DB_PATH", None)
    if hasattr(gr_scores._prepare_badminton_scores_db_path, "_migration_attempted"):
        monkeypatch.delattr(gr_scores._prepare_badminton_scores_db_path, "_migration_attempted")
    _gr_patch_all(monkeypatch, "get_config_manager", lambda: fake_config)

    with reset_game_route_state():
        _allow_badminton_score_session("Lan Badminton", "bd-session", "duel")
        submitted = await gr_scores.game_badminton_leaderboard_submit("badminton", _FakeRequest({
            "session_id": "bd-session",
            "lanlan_name": "Lan Badminton",
            "score": 12,
            "streak": 3,
            "max_distance_px": 180,
            "mode": "duel",
        }))
        leaderboard = await gr_scores.game_badminton_leaderboard(
            "badminton",
            session_id="bd-session",
            lanlan_name="Lan Badminton",
        )

    scores_dir = runtime_root / "state" / "game_scores"
    assert submitted == {"ok": True, "rank": 1, "total_players": 1, "is_personal_best": True}
    assert leaderboard["top"][0]["name"] == "Lan Badminton"
    assert (scores_dir / "badminton_scores.db").exists()


@pytest.mark.unit
def test_badminton_scores_legacy_db_migrates_to_runtime_path(tmp_path, monkeypatch):
    legacy_path = tmp_path / "legacy" / "badminton_scores.db"
    runtime_path = tmp_path / "runtime" / "state" / "game_scores" / "badminton_scores.db"
    legacy_path.parent.mkdir(parents=True)
    with sqlite3.connect(str(legacy_path)) as conn:
        conn.execute("CREATE TABLE marker (value TEXT)")
        conn.execute("INSERT INTO marker (value) VALUES ('legacy-score')")

    fake_config = type("FakeConfig", (), {"app_docs_dir": tmp_path / "runtime"})()
    _gr_patch_all(monkeypatch, "_BADMINTON_SCORES_DB_PATH", None)
    _gr_patch_all(monkeypatch, "_BADMINTON_LEGACY_SCORES_DB_PATH", legacy_path)
    if hasattr(gr_scores._prepare_badminton_scores_db_path, "_migration_attempted"):
        monkeypatch.delattr(gr_scores._prepare_badminton_scores_db_path, "_migration_attempted")
    _gr_patch_all(monkeypatch, "get_config_manager", lambda: fake_config)

    prepared = gr_scores._prepare_badminton_scores_db_path()

    assert prepared == runtime_path
    assert runtime_path.exists()
    with sqlite3.connect(str(runtime_path)) as conn:
        row = conn.execute("SELECT value FROM marker").fetchone()
    assert row[0] == "legacy-score"


class _FakeConfigManager:
    def __init__(self, characters, *, project_root=None, vrm_dir=None):
        self._characters = characters
        self.project_root = project_root
        self.vrm_dir = vrm_dir

    def load_characters(self):
        return self._characters


def _characters_with_avatar(name, avatar):
    return {
        "当前猫娘": name,
        "猫娘": {
            name: {
                "_reserved": {
                    "avatar": avatar,
                },
            },
        },
    }


@pytest.fixture
def game_character_locale_loader(monkeypatch):
    loader = AsyncMock(return_value=("", True))
    monkeypatch.setattr(gr_runtime, "_load_game_character_prompt_locale", loader)
    return loader


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("loaded_locale", "expected_language", "expected_resolved"),
    [
        (("ja", True), "ja", True),
        (("", True), "", True),
        (("", False), "", False),
    ],
)
async def test_game_character_returns_live2d_path(
    monkeypatch,
    game_character_locale_loader,
    loaded_locale,
    expected_language,
    expected_resolved,
):
    import main_routers.characters_router as characters_router

    game_character_locale_loader.return_value = loaded_locale
    _gr_patch_all(monkeypatch, "get_config_manager", lambda: _FakeConfigManager(
        _characters_with_avatar("Lan", {
            "model_type": "live2d",
            "live2d": {"model_path": "/user_live2d/Lan/model.model3.json"},
        })
    ))

    async def fake_current_live2d_model(name):
        assert name == "Lan"
        return JSONResponse({"model_info": {"path": "/user_live2d/Lan/model.model3.json"}})

    monkeypatch.setattr(characters_router, "get_current_live2d_model", fake_current_live2d_model)

    result = await gr_runtime.game_character("badminton")

    assert result["lanlan_name"] == "Lan"
    assert result["language"] == expected_language
    assert result["language_preference_resolved"] is expected_resolved
    assert result["model_type"] == "live2d"
    assert result["live2d_path"] == "/user_live2d/Lan/model.model3.json"
    game_character_locale_loader.assert_awaited_once_with("Lan")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_character_uses_canonical_live2d_fallback_when_saved_path_is_empty(
    monkeypatch,
    game_character_locale_loader,
):
    import main_routers.characters_router as characters_router

    _gr_patch_all(monkeypatch, "get_config_manager", lambda: _FakeConfigManager(
        _characters_with_avatar("FallbackLan", {
            "model_type": "live2d",
            "live2d": {"model_path": ""},
        })
    ))

    resolver_names = []

    async def fake_current_live2d_model(name):
        resolver_names.append(name)
        return JSONResponse({
            "model_info": {
                "path": "/static/yui-lolita/yui-lolita.model3.json",
                "is_fallback": True,
            },
        })

    monkeypatch.setattr(characters_router, "get_current_live2d_model", fake_current_live2d_model)

    result = await gr_runtime.game_character("soccer")

    assert resolver_names == ["FallbackLan"]
    assert result["live2d_path"] == "/static/yui-lolita/yui-lolita.model3.json"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_character_keeps_public_metadata_when_live2d_resolution_fails(
    monkeypatch,
    game_character_locale_loader,
):
    import main_routers.characters_router as characters_router

    _gr_patch_all(monkeypatch, "get_config_manager", lambda: _FakeConfigManager(
        _characters_with_avatar("Lan", {
            "model_type": "live2d",
            "live2d": {"model_path": "/broken/model.model3.json"},
        })
    ))

    async def fail_current_live2d_model(_name):
        raise RuntimeError("invalid live2d metadata")

    monkeypatch.setattr(characters_router, "get_current_live2d_model", fail_current_live2d_model)

    result = await gr_runtime.game_character("soccer")

    assert result["lanlan_name"] == "Lan"
    assert result["model_type"] == "live2d"
    assert result["live2d_path"] == ""
    assert "error" not in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_character_returns_vrm_path_for_live3d_vrm(
    monkeypatch,
    tmp_path,
    game_character_locale_loader,
):
    static_vrm = tmp_path / "static" / "vrm" / "hero.vrm"
    static_vrm.parent.mkdir(parents=True)
    static_vrm.write_text("vrm", encoding="utf-8")

    _gr_patch_all(monkeypatch, "get_config_manager", lambda: _FakeConfigManager(
        _characters_with_avatar("VrmLan", {
            "model_type": "live3d",
            "live3d_sub_type": "vrm",
            "vrm": {"model_path": "hero.vrm"},
        }),
        project_root=tmp_path,
        vrm_dir=tmp_path / "user_vrm",
    ))

    result = await gr_runtime.game_character("badminton")

    assert result["lanlan_name"] == "VrmLan"
    assert result["model_type"] == "live3d"
    assert result["live3d_sub_type"] == "vrm"
    assert result["vrm_path"] == "/static/vrm/hero.vrm"
    assert result["mmd_path"] == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_character_returns_mmd_path_for_live3d_mmd(
    monkeypatch,
    tmp_path,
    game_character_locale_loader,
):
    user_vrm = tmp_path / "user_vrm" / "ignored-but-direct.vrm"
    user_vrm.parent.mkdir(parents=True)
    user_vrm.write_text("vrm", encoding="utf-8")

    _gr_patch_all(monkeypatch, "get_config_manager", lambda: _FakeConfigManager(
        _characters_with_avatar("MmdLan", {
            "model_type": "live3d",
            "live3d_sub_type": "mmd",
            "mmd": {"model_path": "Miku/Miku.pmx"},
            "vrm": {"model_path": "/user_vrm/ignored-but-direct.vrm"},
        }),
        project_root=tmp_path,
        vrm_dir=tmp_path / "user_vrm",
    ))

    result = await gr_runtime.game_character("badminton")

    assert result["lanlan_name"] == "MmdLan"
    assert result["model_type"] == "live3d"
    assert result["live3d_sub_type"] == "mmd"
    assert result["mmd_path"] == "/static/mmd/Miku/Miku.pmx"
    assert result["vrm_path"] == "/user_vrm/ignored-but-direct.vrm"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response_or_error", "expected"),
    [
        (
            {"success": True, "language": "zh-tw", "effective_language": "en"},
            ("zh-TW", True),
        ),
        ({"success": True, "language": None, "effective_language": "ja"}, ("", True)),
        ({"success": True, "language": "", "effective_language": "ja"}, ("", True)),
        ({"success": False, "language": "ko", "effective_language": "ko"}, ("", False)),
        ({"success": True, "effective_language": "ko"}, ("", False)),
        ({"success": True, "language": "unsupported"}, ("", False)),
        (OSError("offline"), ("", False)),
    ],
)
async def test_game_character_prompt_locale_uses_only_durable_language(
    monkeypatch,
    response_or_error,
    expected,
):
    from utils import internal_http_client

    if isinstance(response_or_error, Exception):
        get = AsyncMock(side_effect=response_or_error)
    else:
        get = AsyncMock(return_value=SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: response_or_error,
        ))
    client = SimpleNamespace(get=get)
    monkeypatch.setattr(internal_http_client, "get_internal_http_client", lambda: client)

    assert await gr_runtime._load_game_character_prompt_locale("Mimi") == expected
    assert get.await_args.args[0].endswith("/prompt-locale/Mimi")
    assert get.await_args.kwargs["timeout"] == 2.5


@pytest.mark.unit
def test_soccer_prompt_marks_game_event_text_as_not_user_speech():
    assert "textRaw 只是游戏事件原文或你这边的内建气泡，不是玩家说的话" in __import__('importlib').import_module('config.prompts.prompts_soccer').SOCCER_SYSTEM_PROMPT
    assert "goal-conceded=玩家进球/你丢球" in __import__('importlib').import_module('config.prompts.prompts_soccer').SOCCER_SYSTEM_PROMPT


@pytest.mark.unit
def test_neutral_pregame_context_falls_back_to_lv2_default():
    context, invalid = gr_pregame._normalize_soccer_pregame_context({
        "gameStance": "neutral_play",
        "initialDifficulty": "max",
        "initialMood": "calm",
    })

    assert invalid is True
    assert context["gameStance"] == "neutral_play"
    assert context["initialDifficulty"] == "lv2"


@pytest.mark.unit
def test_special_pregame_context_can_keep_max_difficulty():
    context, invalid = gr_pregame._normalize_soccer_pregame_context({
        "gameStance": "punishing",
        "initialDifficulty": "max",
        "initialMood": "angry",
        "emotionIntensity": 0.9,
        "emotionInertia": "high",
    })

    assert invalid is False
    assert context["gameStance"] == "punishing"
    assert context["initialDifficulty"] == "max"
    assert context["initialMood"] == "angry"


@pytest.mark.unit
def test_soccer_anger_pressure_cap_applies_only_to_punishing_anger_context():
    state = {
        "preGameContext": {
            "gameStance": "punishing",
            "nekoEmotion": "angry",
            "initialMood": "angry",
            "launchIntent": "punishment_session",
        },
    }
    event = {
        "score": {"player": 5, "ai": 26},
        "scoreDiff": 21,
        "difficulty": "max",
        "mood": "angry",
        "requestControlReason": True,
    }

    cap = gr_balance._build_soccer_anger_pressure_cap(event, state)

    assert cap["applicable"] is True
    assert cap["reached"] is True
    assert cap["capGoals"] == 25
    assert cap["recommendedDifficulty"] == "lv4"
    assert cap["reason"] == "狂怒压制已到体力上限，改为降强度继续处理情绪"

    neutral = {
        "preGameContext": {
            "gameStance": "competitive",
            "nekoEmotion": "happy",
            "initialMood": "happy",
        },
    }
    assert gr_balance._build_soccer_anger_pressure_cap(event, neutral) == {}


@pytest.mark.unit
def test_soccer_anger_pressure_cap_uses_persona_stamina_bounds():
    event = {
        "score": {"player": 1, "ai": 9},
        "scoreDiff": 8,
        "difficulty": "max",
        "mood": "angry",
    }
    state = {
        "preGameContext": {
            "gameStance": "punishing",
            "nekoEmotion": "angry",
            "initialMood": "angry",
        },
    }

    weak_cap = gr_balance._build_soccer_anger_pressure_cap(
        event,
        state,
        lanlan_prompt="体力弱，不擅长运动，跑一会儿就容易累。",
    )
    strong_cap = gr_balance._build_soccer_anger_pressure_cap(
        event,
        state,
        lanlan_prompt="擅长运动，体力强，运动神经很好。",
    )

    assert weak_cap["capGoals"] == 8
    assert weak_cap["reached"] is True
    assert strong_cap["capGoals"] == 50
    assert strong_cap["reached"] is False


@pytest.mark.unit
def test_soccer_anger_pressure_cap_clamps_max_control_after_limit():
    event = {
        "score": {"player": 4, "ai": 26},
        "scoreDiff": 22,
        "difficulty": "max",
        "mood": "angry",
        "requestControlReason": True,
        "angerPressureCap": {
            "applicable": True,
            "reached": True,
            "capGoals": 25,
            "aiGoals": 26,
            "playerGoals": 4,
            "scoreDiff": 22,
            "recommendedDifficulty": "lv4",
        },
    }
    result = {
        "line": "还没完。",
        "control": {
            "mood": "angry",
            "difficulty": "max",
            "reason": "继续惩罚玩家",
        },
    }

    adjusted = gr_balance._apply_soccer_anger_pressure_cap(result, event)

    assert adjusted["control"]["difficulty"] == "lv4"
    assert "继续惩罚玩家" in adjusted["control"]["reason"]
    assert "体力上限" in adjusted["control"]["reason"]
    assert adjusted["anger_pressure_cap"]["adjusted"] is True


@pytest.mark.unit
def test_soccer_anger_pressure_cap_forces_difficulty_when_llm_omits_control():
    event = {
        "score": {"player": 4, "ai": 26},
        "scoreDiff": 22,
        "difficulty": "max",
        "mood": "angry",
        "requestControlReason": True,
        "angerPressureCap": {
            "applicable": True,
            "reached": True,
            "capGoals": 25,
            "aiGoals": 26,
            "playerGoals": 4,
            "scoreDiff": 22,
            "recommendedDifficulty": "lv4",
        },
    }
    result = {"line": "呼……先停一下。", "control": {}}

    adjusted = gr_balance._apply_soccer_anger_pressure_cap(result, event)

    assert adjusted["control"]["difficulty"] == "lv4"
    assert adjusted["control"]["reason"] == "狂怒压制已到体力上限，改为降强度继续处理情绪"
    assert adjusted["anger_pressure_cap"]["adjusted"] is True


@pytest.mark.unit
def test_soccer_anger_pressure_cap_reason_uses_requested_locale():
    state = {
        "preGameContext": {
            "gameStance": "punishing",
            "nekoEmotion": "angry",
            "initialMood": "angry",
        },
    }
    event = {
        "score": {"player": 4, "ai": 26},
        "scoreDiff": 22,
        "difficulty": "max",
        "mood": "angry",
        "requestControlReason": True,
    }

    cap = gr_balance._build_soccer_anger_pressure_cap(event, state, language="en")
    adjusted = gr_balance._apply_soccer_anger_pressure_cap(
        {"line": "Fine.", "control": {}},
        {**event, "angerPressureCap": cap},
    )

    assert "stamina cap" in cap["reason"]
    assert adjusted["control"]["reason"] == cap["reason"]


@pytest.mark.unit
def test_pregame_opening_line_is_short_and_does_not_repeat_invite():
    context, invalid = gr_pregame._normalize_soccer_pregame_context({
        "gameStance": "soft_teasing",
        "initialDifficulty": "lv2",
        "openingLine": "那我认真了",
    })
    assert invalid is False
    assert context["openingLine"] == "那我认真了"

    too_long, too_long_invalid = gr_pregame._normalize_soccer_pregame_context({
        "gameStance": "soft_teasing",
        "initialDifficulty": "lv2",
        "openingLine": "这次要认真看着我踢球哦玩家不许走神",
    })
    assert too_long_invalid is True
    assert too_long["openingLine"] == ""

    repeated, _ = gr_pregame._normalize_soccer_pregame_context(
        {
            "gameStance": "competitive",
            "initialDifficulty": "lv2",
            "openingLine": "来踢球吧，玩家。",
        },
        neko_invite_text="来踢球吧，玩家。",
    )
    assert repeated["openingLine"] == ""


@pytest.mark.unit
def test_game_prompt_includes_pregame_context():
    prompt = gr_runtime._build_game_prompt(
        "soccer",
        "Lan",
        "喜欢陪玩家玩。",
        {"gameStance": "withdrawn", "tonePolicy": "低声回应。"},
    )

    assert "开局上下文" in prompt
    assert '"gameStance":"withdrawn"' in prompt
    assert "不要把 neutral_play 强行解释成哄开心或关系修复" in prompt


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_pregame_context_uses_empty_history_fallback(monkeypatch):
    monkeypatch.setattr("random.choice", lambda seq: "lv2")
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {
        "lanlan_name": "Lan",
        "master_name": "玩家",
        "lanlan_prompt": "喜欢踢球。",
        "model": "fake",
        "base_url": "http://fake",
        "api_type": "local",
        "api_key": "key",
        "user_language": "zh",
        "user_language_full": "zh-TW",
    })

    async def fake_fetch(_lanlan_name, *, language=None):
        assert language == "zh-TW"
        return "", "recent_history_failed"

    async def fake_ai(**kwargs):
        assert kwargs["recent_history"] == ""
        return {
            "gameStance": "neutral_play",
            "initialMood": "calm",
            "initialDifficulty": "lv2",
        }

    _gr_patch_all(monkeypatch, "_fetch_recent_history_for_pregame", fake_fetch)
    _gr_patch_all(monkeypatch, "_run_soccer_pregame_context_ai", fake_ai)

    context, source, error = await gr_pregame._build_soccer_pregame_context(
        game_type="soccer",
        session_id="match_1",
        lanlan_name="Lan",
        neko_initiated=False,
        neko_invite_text="",
    )

    assert source == "ai"
    assert error == "recent_history_failed"
    assert context["gameStance"] == "neutral_play"
    assert context["initialDifficulty"] == "lv2"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_soccer_pregame_uses_request_prompt_locale(monkeypatch):
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {
        "lanlan_name": "Lan",
        "master_name": "玩家",
        "lanlan_prompt": "喜欢踢球。",
        "user_language": "zh",
        "user_language_full": "zh-CN",
    })

    async def fake_fetch(_lanlan_name, *, language=None):
        assert language == "zh-TW"
        return "", ""

    async def fake_ai(**kwargs):
        assert kwargs["prompt_locale"] == "zh-TW"
        return {"gameStance": "neutral_play"}

    _gr_patch_all(monkeypatch, "_fetch_recent_history_for_pregame", fake_fetch)
    _gr_patch_all(monkeypatch, "_run_soccer_pregame_context_ai", fake_ai)

    await gr_pregame._build_soccer_pregame_context(
        game_type="soccer",
        session_id="match_locale",
        lanlan_name="Lan",
        neko_initiated=False,
        neko_invite_text="",
        prompt_locale="zh-TW",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_pregame_uses_request_prompt_locale(monkeypatch):
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {
        "lanlan_name": "Lan",
        "master_name": "玩家",
        "lanlan_prompt": "喜欢打球。",
        "user_language": "zh",
        "user_language_full": "zh-CN",
    })

    async def fake_fetch(_lanlan_name, *, language=None):
        assert language == "zh-TW"
        return "", ""

    async def fake_ai(**kwargs):
        assert kwargs["prompt_template"] == "traditional-template"
        return {"gameStance": "neutral_play"}

    def fake_prompt(language):
        assert language == "zh-TW"
        return "traditional-template"

    _gr_patch_all(monkeypatch, "_fetch_recent_history_for_pregame", fake_fetch)
    _gr_patch_all(monkeypatch, "_run_pregame_context_ai", fake_ai)
    _gr_patch_all(monkeypatch, "get_badminton_pregame_context_prompt", fake_prompt)

    await gr_pregame._build_badminton_pregame_context(
        game_type="badminton",
        session_id="match_locale",
        lanlan_name="Lan",
        neko_initiated=False,
        neko_invite_text="",
        prompt_locale="zh-TW",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_pregame_context_invalid_json_falls_back(monkeypatch):
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {
        "lanlan_name": "Lan",
        "master_name": "玩家",
        "lanlan_prompt": "",
        "model": "fake",
        "base_url": "http://fake",
        "api_type": "local",
        "api_key": "key",
    })

    async def fake_fetch(_lanlan_name, **_kwargs):
        return "玩家 | 来踢球", ""

    async def fake_ai(**_kwargs):
        raise ValueError("bad json")

    _gr_patch_all(monkeypatch, "_fetch_recent_history_for_pregame", fake_fetch)
    _gr_patch_all(monkeypatch, "_run_soccer_pregame_context_ai", fake_ai)

    context, source, error = await gr_pregame._build_soccer_pregame_context(
        game_type="soccer",
        session_id="match_1",
        lanlan_name="Lan",
        neko_initiated=False,
        neko_invite_text="",
    )

    assert source == "fallback"
    assert error == "invalid_json"
    assert context["gameStance"] == "neutral_play"
    assert context["initialDifficulty"] == "lv2"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_pregame_context_partial_invalid_fields(monkeypatch):
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {
        "lanlan_name": "Lan",
        "master_name": "玩家",
        "lanlan_prompt": "",
        "model": "fake",
        "base_url": "http://fake",
        "api_type": "local",
        "api_key": "key",
    })

    async def fake_fetch(_lanlan_name, **_kwargs):
        return "玩家 | 你这个笨蛋！", ""

    async def fake_ai(**_kwargs):
        return {
            "gameStance": "punishing",
            "initialDifficulty": "max",
            "initialMood": "angry",
            "emotionIntensity": 2,
            "openingLine": "那我认真了",
        }

    _gr_patch_all(monkeypatch, "_fetch_recent_history_for_pregame", fake_fetch)
    _gr_patch_all(monkeypatch, "_run_soccer_pregame_context_ai", fake_ai)

    context, source, error = await gr_pregame._build_soccer_pregame_context(
        game_type="soccer",
        session_id="match_1",
        lanlan_name="Lan",
        neko_initiated=False,
        neko_invite_text="",
    )

    assert source == "ai"
    assert error == "invalid_fields"
    assert context["gameStance"] == "punishing"
    assert context["initialDifficulty"] == "max"
    assert context["emotionIntensity"] == 0.0
    assert context["openingLine"] == "那我认真了"


@pytest.mark.unit
def test_game_archive_memory_payload_uses_system_note_shape():
    archive = {
        "game_type": "soccer",
        "session_id": "match_1",
        "lanlan_name": "Lan",
        "user_language": "zh-CN",
        "summary": "soccer 小游戏结束。最终/最近比分：玩家 1 : 4 Lan。",
        "game_memory_tail_count": 2,
        "soccer_game_memory_enabled": True,
        "soccer_game_memory_player_interaction_enabled": True,
        "soccer_game_memory_event_reply_enabled": True,
        "soccer_game_memory_archive_enabled": True,
        "soccer_game_memory_postgame_context_enabled": True,
        "memory_highlights": {
            "important_records": ["玩家要求温柔一点，你改成让球式回应。"],
            "important_game_events": ["猫娘大比分领先后开始放水。"],
            "state_carryback": "赛后猫娘仍有点得意，但愿意继续陪玩家玩。",
            "postgame_tone": "得意但放软",
            "memory_summary": "玩家希望猫娘温柔一点，猫娘开始让球。",
        },
        "last_full_dialogues": [
            {"type": "user", "text": "温柔一点"},
            {"type": "assistant", "line": "好好好，让你踢。"},
        ],
        "key_events": [],
        "last_state": {"score": {"player": 1, "ai": 4}},
    }

    messages = gr_archive._build_game_archive_memory_messages(archive)

    assert [msg["role"] for msg in messages] == ["user", "assistant", "system"]
    assert messages[0]["content"][0]["text"] == "温柔一点"
    assert messages[1]["content"][0]["text"] == "好好好，让你踢。"
    system_text = messages[2]["content"][0]["text"]
    assert "Game Module Postgame Record: this is a game-module archive, not a verbatim player utterance." in system_text
    assert "soccer 游戏结束" not in system_text
    assert "官方结果：玩家 1 : 4 Lan。口头让步不改官方结果。" in system_text
    assert "官方结果永远以 finalScore / last_state.score 为准" not in system_text
    assert "口头让步规则" not in system_text
    assert "重要互动：" in system_text
    assert "玩家要求温柔一点，你改成让球式回应。" in system_text
    assert "猫娘记住的本局事件：" in system_text
    assert "赛后状态延续：赛后猫娘仍有点得意，但愿意继续陪玩家玩。" in system_text
    assert "赛后语气：得意但放软" in system_text
    assert "后续记忆摘要：玩家希望猫娘温柔一点，猫娘开始让球。" in system_text
    assert "倒数 2 条规则" in system_text
    assert "本条 system 归档不计入倒数 2 条" in system_text
    assert "本局记录了" not in system_text
    assert "外部接管模式" not in system_text
    assert "玩家最近在比赛里说：温柔一点" not in system_text
    assert "你最后回应：好好好，让你踢。" not in system_text


@pytest.mark.unit
def test_game_archive_memory_tail_uses_game_dialog_order_without_event_labels():
    archive = {
        "game_type": "soccer",
        "session_id": "match_1",
        "lanlan_name": "Lan",
        "user_language": "zh-CN",
        "summary": "soccer 小游戏结束。",
        "game_memory_tail_count": 4,
        "soccer_game_memory_enabled": True,
        "soccer_game_memory_player_interaction_enabled": True,
        "soccer_game_memory_event_reply_enabled": True,
        "soccer_game_memory_archive_enabled": True,
        "soccer_game_memory_postgame_context_enabled": True,
        "memory_highlights": {},
        "full_dialogues": [
            {"type": "user", "text": "很早的话"},
            {"type": "game_event", "kind": "steal", "text": "纯事实没有台词"},
            {"type": "game_event", "kind": "goal-scored", "text": "进球", "result_line": "嘿嘿，这球归我啦"},
            {"type": "user", "text": "你刚才说算我赢？"},
            {"type": "assistant", "source": "game_llm", "line": "那是哄你的，比分可没改哦。"},
        ],
        "last_state": {"score": {"player": 9, "ai": 20}},
    }

    messages = gr_archive._build_game_archive_memory_messages(archive)

    assert [msg["role"] for msg in messages] == ["assistant", "user", "assistant", "system"]
    assert messages[0]["content"][0]["text"] == "嘿嘿，这球归我啦"
    assert "本局游戏事件" not in messages[0]["content"][0]["text"]
    assert messages[1]["content"][0]["text"] == "你刚才说算我赢？"
    assert messages[2]["content"][0]["text"] == "那是哄你的，比分可没改哦。"
    system_text = messages[-1]["content"][0]["text"]
    assert "官方结果：玩家 9 : 20 Lan。口头让步不改官方结果。" in system_text
    assert "口头让步规则" not in system_text
    assert "倒数 4 条规则" in system_text


@pytest.mark.unit
def test_game_archive_memory_prefers_final_score_over_oral_concession_text():
    archive = {
        "game_type": "soccer",
        "session_id": "match_1",
        "lanlan_name": "Lan",
        "user_language": "zh-CN",
        "summary": "soccer 小游戏结束。",
        "finalScore": {"player": 9, "ai": 20},
        "last_state": {"score": {"player": 99, "ai": 0}},
        "soccer_game_memory_enabled": True,
        "soccer_game_memory_player_interaction_enabled": True,
        "soccer_game_memory_event_reply_enabled": True,
        "soccer_game_memory_archive_enabled": True,
        "soccer_game_memory_postgame_context_enabled": True,
        "full_dialogues": [
            {"type": "game_event", "kind": "goal-scored", "result_line": "行吧，这局算你赢。"},
        ],
    }

    messages = gr_archive._build_game_archive_memory_messages(archive, tail_count=1)
    system_text = messages[-1]["content"][0]["text"]

    assert "官方结果：玩家 9 : 20 Lan。口头让步不改官方结果。" in system_text
    assert "官方结果永远以 finalScore / last_state.score 为准" not in system_text
    assert "口头让步规则" not in system_text
    assert messages[0]["role"] == "assistant"
    assert messages[0]["content"][0]["text"] == "行吧，这局算你赢。"


@pytest.mark.unit
def test_game_archive_memory_prefers_explicit_score_text_for_horse_results():
    archive = {
        "game_type": "badminton",
        "session_id": "horse_1",
        "lanlan_name": "Neko",
        "user_language": "zh-CN",
        "summary": "badminton 小游戏结束。",
        "finalScore": {
            "player": 3,
            "ai": 0,
            "score_text": "HORSE HOR : HORSE",
            "winner": "player",
            "outcome": "player_win",
        },
        "last_state": {"score": {"player": 3, "ai": 0}},
        "badminton_game_memory_enabled": True,
        "badminton_game_memory_player_interaction_enabled": True,
        "badminton_game_memory_event_reply_enabled": True,
        "badminton_game_memory_archive_enabled": True,
        "badminton_game_memory_postgame_context_enabled": True,
        "full_dialogues": [],
    }

    messages = gr_archive._build_game_archive_memory_messages(archive, tail_count=1)
    system_text = messages[-1]["content"][0]["text"]

    assert "官方结果：HORSE HOR : HORSE。口头让步不改官方结果。" in system_text
    assert "玩家 3 : 0 Neko" not in system_text


@pytest.mark.unit
def test_game_archive_tail_respects_independent_soccer_memory_policy():
    archive = {
        "game_type": "soccer",
        "session_id": "match_1",
        "lanlan_name": "Lan",
        "summary": "soccer 小游戏结束。",
        "last_state": {"score": {"player": 1, "ai": 2}},
        "soccer_game_memory_enabled": True,
        "soccer_game_memory_player_interaction_enabled": False,
        "soccer_game_memory_event_reply_enabled": True,
        "soccer_game_memory_archive_enabled": True,
        "soccer_game_memory_postgame_context_enabled": True,
        "full_dialogues": [
            {"type": "user", "text": "这句不进记忆"},
            {"type": "assistant", "source": "game_llm", "line": "直接回复也不进记忆"},
            {"type": "game_event", "kind": "goal-scored", "result_line": "事件回复可以进记忆"},
        ],
    }

    messages = gr_archive._build_game_archive_memory_messages(archive, tail_count=3)

    assert [msg["role"] for msg in messages] == ["assistant", "system"]
    assert messages[0]["content"][0]["text"] == "事件回复可以进记忆"

    archive["soccer_game_memory_player_interaction_enabled"] = True
    archive["soccer_game_memory_event_reply_enabled"] = False
    messages = gr_archive._build_game_archive_memory_messages(archive, tail_count=3)

    assert [msg["role"] for msg in messages] == ["user", "assistant", "system"]
    assert messages[0]["content"][0]["text"] == "这句不进记忆"
    assert messages[1]["content"][0]["text"] == "直接回复也不进记忆"


@pytest.mark.unit
def test_postgame_event_aligns_current_state_score_to_final_score():
    event = gr_runtime._build_game_postgame_event(
        "soccer",
        {
            "summary": "soccer 小游戏结束。",
            "lanlan_name": "Lan",
            "finalScore": {"player": 6, "ai": 14},
            "last_state": {
                "score": {"player": 6, "ai": 10},
                "round": 17,
                "mood": "sad",
            },
            "last_full_dialogues": [],
            "memory_highlights": {},
        },
        {"max_chars": 60},
    )

    assert event["scoreText"] == "玩家 6 : 14 Lan"
    assert event["finalScore"] == {"player": 6, "ai": 14}
    assert event["currentState"]["score"] == {"player": 6, "ai": 14}
    assert event["currentState"]["round"] == 17
    assert "scoreText/finalScore" in event["request"]


@pytest.mark.unit
def test_game_archive_summary_keeps_score_not_counters():
    summary = gr_archive._summarize_game_archive(
        {"game_type": "soccer", "lanlan_name": "Lan", "last_state": {"score": {"player": 0, "ai": 5}}},
        [
            {"type": "game_event"},
            {"type": "user"},
            {"type": "assistant"},
        ],
    )

    assert summary == "soccer 游戏结束。最终/最近结果：玩家 0 : 5 Lan。"
    assert "本局记录了" not in summary
    assert "外部接管模式" not in summary


@pytest.mark.unit
def test_game_event_memory_line_does_not_attribute_event_text_to_user():
    line = gr_game_context._dialog_memory_line({
        "type": "game_event",
        "kind": "goal-conceded",
        "text": "不算不算嘛",
        "result_line": "又耍赖？我都懒得防你了，随便你吧。",
    })

    assert "游戏事件 goal-conceded（玩家进球 / 猫娘丢球）" in line
    assert "事件原文「不算不算嘛」" in line
    assert "猫娘回应「又耍赖？我都懒得防你了，随便你吧。」" in line
    assert "玩家：" not in line


@pytest.mark.unit
def test_memory_highlight_source_explains_game_event_text_is_not_user_speech():
    source = gr_archive._build_game_archive_memory_highlight_source({
        "game_type": "soccer",
        "session_id": "match_1",
        "lanlan_name": "Lan",
        "user_language": "zh-CN",
        "last_state": {"score": {"player": 1, "ai": 2}},
        "soccer_game_memory_enabled": True,
        "soccer_game_memory_player_interaction_enabled": True,
        "soccer_game_memory_event_reply_enabled": True,
        "soccer_game_memory_archive_enabled": True,
        "soccer_game_memory_postgame_context_enabled": True,
        "full_dialogues": [
            {
                "type": "game_event",
                "kind": "goal-conceded",
                "text": "不算不算嘛",
                "result_line": "又耍赖？",
            },
        ],
    })

    assert "只有“玩家：...”行是玩家亲口说的话" in source
    assert "“事件原文”是游戏模块/猫娘气泡或事件标签，不要归因给玩家" in source
    assert "游戏事件 goal-conceded（玩家进球 / 猫娘丢球）" in source
    assert "固定顺序是玩家在前、当前角色在后" in source
    assert "官方结果，来源优先级为 finalScore / last_state.score" in source
    assert "口头让步、安抚或玩笑" in source


@pytest.mark.unit
def test_memory_highlight_source_keeps_role_markers_aligned_in_english(monkeypatch):
    _gr_patch_all(monkeypatch, "_archive_prompt_language", lambda _archive: "en")

    source = gr_archive._build_game_archive_memory_highlight_source({
        "game_type": "soccer",
        "session_id": "match_1",
        "lanlan_name": "Lan",
        "last_state": {"score": {"player": 1, "ai": 2}},
        "soccer_game_memory_enabled": True,
        "soccer_game_memory_player_interaction_enabled": True,
        "soccer_game_memory_event_reply_enabled": True,
        "soccer_game_memory_archive_enabled": True,
        "soccer_game_memory_postgame_context_enabled": True,
        "full_dialogues": [
            {"type": "user", "text": "I almost caught up"},
            {
                "type": "game_event",
                "kind": "goal-conceded",
                "text": "goal",
                "result_line": "Nice shot.",
            },
        ],
    })

    assert 'literal marker "Player:"' in source
    assert '"event text" inside "Game event" lines' in source
    assert "Player: I almost caught up" in source
    assert "Game event goal-conceded" in source


@pytest.mark.unit
def test_archive_memory_fallback_highlights_use_requested_locale(monkeypatch):
    _gr_patch_all(monkeypatch, "_archive_prompt_language", lambda _archive: "en")

    highlights = gr_archive._fallback_game_archive_memory_highlights({
        "game_type": "soccer",
        "session_id": "match_1",
        "lanlan_name": "Lan",
        "last_state": {"score": {"player": 1, "ai": 2}},
        "soccer_game_memory_enabled": True,
        "soccer_game_memory_player_interaction_enabled": True,
        "last_full_dialogues": [
            {"type": "user", "text": "That was close"},
            {"type": "assistant", "line": "Almost."},
        ],
        "key_events": [],
    })

    assert highlights["important_records"] == [
        'The player last said "That was close", and you replied "Almost.".'
    ]
    assert "玩家最后" not in highlights["important_records"][0]


@pytest.mark.unit
def test_memory_highlight_prompt_rejects_bare_or_reversed_scores(monkeypatch):
    captured = {}

    class FakeLlm:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, messages):
            captured["system"] = messages[0].content
            captured["user"] = messages[1].content

            class Result:
                content = '{"important_records":[],"important_game_events":[]}'

            return Result()

    def fake_create_chat_llm(*_args, **_kwargs):
        return FakeLlm()

    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {
        "model": "test-model",
        "base_url": "http://example.test",
        "api_key": "key",
        "api_type": "",
    })
    monkeypatch.setattr("utils.llm_client.create_chat_llm", fake_create_chat_llm)

    result = asyncio.run(gr_archive._select_game_archive_memory_highlights({
        "game_type": "soccer",
        "session_id": "match_1",
        "lanlan_name": "Lan",
        "user_language": "zh-CN",
        "last_state": {"score": {"player": 0, "ai": 10}},
        "full_dialogues": [],
    }))

    assert result["important_records"] == []
    assert result["important_game_events"] == []
    assert "不要写无主体裸结果" in captured["system"]
    assert "不要前后混用不同视角" in captured["system"]
    assert "固定顺序是玩家在前、当前角色在后" in captured["user"]
    assert "======以上为赛后记忆筛选材料======" in captured["user"]


@pytest.mark.unit
def test_game_route_helper_llm_info_uses_summary_tier(monkeypatch):
    class FakeConfigManager:
        def get_model_api_config(self, tier):
            assert tier == "game_summary"
            return {
                "model": "summary-model",
                "base_url": "http://summary.test/v1",
                "api_key": "summary-key",
                "api_type": "summary-api",
            }

    _gr_patch_all(monkeypatch, "_get_character_info", lambda _lanlan_name=None: {
        "lanlan_name": "Lan",
        "model": "conversation-model",
        "base_url": "http://conversation.test/v1",
        "api_key": "conversation-key",
        "api_type": "conversation-api",
        "user_language": "zh",
    })
    _gr_patch_all(monkeypatch, "get_config_manager", lambda: FakeConfigManager())

    info = gr_char_info._get_game_route_summary_llm_info("Lan")

    assert info["lanlan_name"] == "Lan"
    assert info["user_language"] == "zh"
    assert info["model"] == "summary-model"
    assert info["base_url"] == "http://summary.test/v1"
    assert info["api_key"] == "summary-key"
    assert info["api_type"] == "summary-api"


@pytest.mark.unit
def test_game_route_helper_llm_info_allows_no_auth_summary_tier(monkeypatch):
    class FakeConfigManager:
        def get_model_api_config(self, tier):
            assert tier == "game_summary"
            return {
                "model": "local-summary-model",
                "base_url": "http://localhost:8081/v1",
                "api_key": "",
                "api_type": "local",
            }

    _gr_patch_all(monkeypatch, "_get_character_info", lambda _lanlan_name=None: {
        "lanlan_name": "Lan",
        "model": "conversation-model",
        "base_url": "http://conversation.test/v1",
        "api_key": "conversation-key",
        "api_type": "conversation-api",
        "user_language": "zh",
    })
    _gr_patch_all(monkeypatch, "get_config_manager", lambda: FakeConfigManager())

    info = gr_char_info._get_game_route_summary_llm_info("Lan")

    assert info["model"] == "local-summary-model"
    assert info["base_url"] == "http://localhost:8081/v1"
    assert info["api_key"] == ""
    assert info["api_type"] == "local"


@pytest.mark.unit
def test_game_route_helper_llm_info_does_not_mix_partial_summary_config(monkeypatch):
    class FakeConfigManager:
        def get_model_api_config(self, tier):
            assert tier == "game_summary"
            return {
                "model": "summary-model",
                "base_url": "",
                "api_key": "summary-key",
                "api_type": "summary-api",
            }

    _gr_patch_all(monkeypatch, "_get_character_info", lambda _lanlan_name=None: {
        "lanlan_name": "Lan",
        "model": "conversation-model",
        "base_url": "http://conversation.test/v1",
        "api_key": "conversation-key",
        "api_type": "conversation-api",
        "user_language": "zh",
    })
    _gr_patch_all(monkeypatch, "get_config_manager", lambda: FakeConfigManager())

    info = gr_char_info._get_game_route_summary_llm_info("Lan")

    assert info["model"] == "conversation-model"
    assert info["base_url"] == "http://conversation.test/v1"
    assert info["api_key"] == "conversation-key"
    assert info["api_type"] == "conversation-api"


@pytest.mark.unit
def test_build_game_llm_visible_event_filters_soccer_internal_fields():
    event = {
        "kind": "mailbox-batch",
        "lanlan_name": "Lan",
        "soccerGameMemoryEnabled": True,
        "soccer_game_memory_enabled": True,
        "soccerGameMemoryPlayerInteractionEnabled": True,
        "soccer_game_memory_player_interaction_enabled": True,
        "soccerGameMemoryEventReplyEnabled": True,
        "soccer_game_memory_event_reply_enabled": True,
        "soccerGameMemoryArchiveEnabled": True,
        "soccer_game_memory_archive_enabled": True,
        "soccerGameMemoryPostgameContextEnabled": True,
        "soccer_game_memory_postgame_context_enabled": True,
        "gameMemoryEnabled": True,
        "game_memory_enabled": True,
        "gameMemoryPlayerInteractionEnabled": True,
        "game_memory_player_interaction_enabled": True,
        "gameMemoryEventReplyEnabled": True,
        "game_memory_event_reply_enabled": True,
        "balanceHint": {"message": "keep this pending judgment"},
        "angerPressureCap": {"message": "keep this pending judgment", "reason": "internal-ish but undecided"},
        "currentState": {
            "round": 12,
            "score": {"player": 1, "ai": 3},
            "aiFreezeSec": 0.2,
            "playerKickStartleWindowSec": 0.5,
            "playerKickWallBounceForStartle": True,
            "startle": {"directCdSec": 1},
            "startleDirectCdSec": 1,
            "startleGrazeCdSec": 2,
            "startleMutualLockSec": 3,
            "zoneoutCooldownSec": 4,
            "ballGhost": True,
        },
        "pendingItems": [{
            "kind": "goal-scored",
            "priority": 8,
            "source": "voice_input_gate",
            "builtinFallback": "备用台词",
            "snapshot": {
                "round": 11,
                "score": {"player": 1, "ai": 2},
                "aiFreezeSec": 0.1,
                "ballGhost": False,
            },
        }],
    }

    visible = gr_visible_events._build_game_llm_visible_event("soccer", event)

    assert "lanlan_name" not in visible
    assert "soccerGameMemoryEnabled" not in visible
    assert "soccer_game_memory_enabled" not in visible
    assert "gameMemoryEnabled" not in visible
    assert "game_memory_enabled" not in visible
    assert visible["balanceHint"] == event["balanceHint"]
    assert visible["angerPressureCap"] == event["angerPressureCap"]
    assert visible["pendingItems"][0]["priority"] == 8
    assert visible["pendingItems"][0]["source"] == "voice_input_gate"
    assert visible["pendingItems"][0]["builtinFallback"] == "备用台词"
    for state in (visible["currentState"], visible["pendingItems"][0]["snapshot"]):
        assert "aiFreezeSec" not in state
        assert "playerKickStartleWindowSec" not in state
        assert "playerKickWallBounceForStartle" not in state
        assert "startle" not in state
        assert "zoneoutCooldownSec" not in state
        assert "ballGhost" not in state
    assert event["currentState"]["aiFreezeSec"] == 0.2
    assert event["pendingItems"][0]["snapshot"]["ballGhost"] is False


@pytest.mark.unit
def test_build_game_llm_visible_event_filters_badminton_memory_flags_from_camel_case():
    event = {
        "kind": "shot-made",
        "badmintonGameMemoryEnabled": True,
        "badminton_game_memory_enabled": True,
        "badmintonGameMemoryPlayerInteractionEnabled": True,
        "badminton_game_memory_player_interaction_enabled": True,
        "badmintonGameMemoryEventReplyEnabled": True,
        "badminton_game_memory_event_reply_enabled": True,
        "badmintonGameMemoryArchiveEnabled": True,
        "badminton_game_memory_archive_enabled": True,
        "badmintonGameMemoryPostgameContextEnabled": True,
        "badminton_game_memory_postgame_context_enabled": True,
        "currentState": {"mode": "duel", "streak": 3},
    }

    visible = gr_visible_events._build_game_llm_visible_event("badminton", event)

    assert visible == {
        "kind": "shot-made",
        "currentState": {"mode": "duel", "streak": 3},
    }
    assert event["badmintonGameMemoryEnabled"] is True


@pytest.mark.unit
def test_build_game_llm_visible_event_filters_badminton_memory_flags():
    event = {
        "kind": "shot-made",
        "badmintonGameMemoryEnabled": True,
        "badminton_game_memory_enabled": True,
        "badmintonGameMemoryPlayerInteractionEnabled": True,
        "badminton_game_memory_player_interaction_enabled": True,
        "badmintonGameMemoryEventReplyEnabled": True,
        "badminton_game_memory_event_reply_enabled": True,
        "badmintonGameMemoryArchiveEnabled": True,
        "badminton_game_memory_archive_enabled": True,
        "badmintonGameMemoryPostgameContextEnabled": True,
        "badminton_game_memory_postgame_context_enabled": True,
        "currentState": {"mode": "duel", "streak": 3},
    }

    visible = gr_visible_events._build_game_llm_visible_event("badminton", event)

    assert visible == {
        "kind": "shot-made",
        "currentState": {"mode": "duel", "streak": 3},
    }
    assert event["badmintonGameMemoryEnabled"] is True


@pytest.mark.unit
def test_postgame_context_snapshot_excludes_recent_dialogues(monkeypatch):
    state = {
        "preGameContext": {"story": "opening"},
        "game_context_summary": "summary",
        "game_context_signals": {},
        "game_context_organizer": {},
        "game_dialog_log": [],
    }
    gr_runtime._append_game_dialog(state, {
        "type": "game_event",
        "kind": "goal-scored",
        "text": "scored",
        "result_line": "Nice.",
    })

    snapshot = gr_runtime._build_postgame_context_snapshot(state)

    assert snapshot["game_context"]["summary"] == "summary"
    assert snapshot["game_context"]["recent_dialogues"] == []


@pytest.mark.unit
def test_postgame_context_request_id_is_archive_scoped():
    first = {
        "game_type": "soccer",
        "session_id": "default",
        "ended_at": 10.5,
    }
    second = {
        "game_type": "soccer",
        "session_id": "default",
        "ended_at": 11.5,
    }

    assert gr_runtime._postgame_context_request_id(first) == "soccer:default:10.5"
    assert gr_runtime._postgame_context_request_id(second) == "soccer:default:11.5"
    assert gr_runtime._postgame_context_request_id(first) != gr_runtime._postgame_context_request_id(second)
    assert gr_runtime._postgame_context_request_id({"game_type": "soccer", "session_id": "default"}) is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_chat_event_user_turn_keeps_watermark(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.last_text = ""

        async def stream_text(self, text):
            self.last_text = text

        async def update_session(self, _config):
            return None

    fake_session = FakeSession()
    key = gr_runtime._game_session_key("Lan", "soccer", "match_1")
    gr_runtime._game_sessions[key] = {
        "session": fake_session,
        "reply_chunks": [],
        "lanlan_name": "Lan",
        "lanlan_prompt": "",
        "user_language": "en",
        "game_type": "soccer",
        "session_id": "match_1",
        "last_activity": 0,
        "lock": asyncio.Lock(),
        "instructions": "stub",
    }
    _gr_patch_all(monkeypatch, "_refresh_game_session_instructions", AsyncMock())

    result = await gr_runtime._run_game_chat(
        "soccer",
        "match_1",
        {"kind": "goal-scored", "lanlan_name": "Lan"},
    )

    assert result["line"] == ""
    assert "======以上为游戏事件输入======" in fake_session.last_text
    assert '"kind": "goal-scored"' in fake_session.last_text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pregame_context_ai_human_message_keeps_watermark(monkeypatch):
    from config.prompts.prompts_minigame_common import PREGAME_CONTEXT_INPUT_WATERMARK

    captured = {}

    class FakeResult:
        content = '{"launchIntent": "unknown"}'

    class FakeLLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def ainvoke(self, messages):
            captured["messages"] = messages
            return FakeResult()

    async def fake_create(*_args, **_kwargs):
        return FakeLLM()

    monkeypatch.setattr("utils.llm_client.create_chat_llm_async", fake_create)
    _gr_patch_all(monkeypatch, "_get_character_info",
        lambda _name: {"model": "m", "base_url": "u", "api_key": "k"},
    )

    await gr_pregame._run_pregame_context_ai(
        lanlan_name="Lan",
        master_name="玩家",
        lanlan_prompt="人设摘录",
        recent_history="昨天一起聊了很久",
        neko_initiated=True,
        neko_invite_text="一起踢球吗",
        prompt_template="开局上下文分析器系统提示",
        extra_payload={"gameType": "soccer"},
    )

    human_message = captured["messages"][1]
    # 收尾水印必须在 human message 末尾，且把近期记录原文包在水印之上。
    assert human_message.content.endswith(PREGAME_CONTEXT_INPUT_WATERMARK)
    assert "昨天一起聊了很久" in human_message.content


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_game_chat_sends_filtered_llm_visible_event(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.last_text = ""

        async def stream_text(self, text):
            self.last_text = text

        async def update_session(self, _config):
            return None

    fake_session = FakeSession()
    key = gr_runtime._game_session_key("Lan", "soccer", "match_filtered")
    gr_runtime._game_sessions[key] = {
        "session": fake_session,
        "reply_chunks": [],
        "lanlan_name": "Lan",
        "lanlan_prompt": "",
        "user_language": "zh",
        "game_type": "soccer",
        "session_id": "match_filtered",
        "last_activity": 0,
        "lock": asyncio.Lock(),
        "instructions": "stub",
    }
    _gr_patch_all(monkeypatch, "_refresh_game_session_instructions", AsyncMock())

    await gr_runtime._run_game_chat(
        "soccer",
        "match_filtered",
        {
            "kind": "mailbox-batch",
            "lanlan_name": "Lan",
            "soccerGameMemoryEnabled": True,
            "soccer_game_memory_enabled": True,
            "soccerGameMemoryPlayerInteractionEnabled": True,
            "soccer_game_memory_player_interaction_enabled": True,
            "soccerGameMemoryEventReplyEnabled": True,
            "soccer_game_memory_event_reply_enabled": True,
            "gameMemoryEnabled": True,
            "game_memory_enabled": True,
            "balanceHint": {"message": "暂时保留"},
            "angerPressureCap": {"message": "暂时保留", "reached": False},
            "currentState": {
                "round": 2,
                "score": {"player": 1, "ai": 1},
                "aiFreezeSec": 0,
                "playerKickStartleWindowSec": 0,
                "playerKickWallBounceForStartle": False,
                "startle": {"directCdSec": 0, "grazeCdSec": 0, "mutualLockSec": 0},
                "zoneoutCooldownSec": 0,
                "ballGhost": False,
            },
            "pendingItems": [{
                "kind": "user-voice",
                "priority": 8,
                "source": "voice_input_gate",
                "builtinFallback": "备用台词",
                "snapshot": {
                    "round": 1,
                    "score": {"player": 0, "ai": 1},
                    "aiFreezeSec": 0.3,
                    "ballGhost": True,
                },
            }],
        },
    )

    payload_text = fake_session.last_text.split("======以下为游戏事件输入======", 1)[1]
    payload_text = payload_text.split("======以上为游戏事件输入======", 1)[0].strip()
    payload = json.loads(payload_text)

    assert "lanlan_name" not in payload
    assert "soccerGameMemoryEnabled" not in payload
    assert "soccer_game_memory_enabled" not in payload
    assert "gameMemoryEnabled" not in payload
    assert "game_memory_enabled" not in payload
    assert "aiFreezeSec" not in payload["currentState"]
    assert "playerKickStartleWindowSec" not in payload["currentState"]
    assert "playerKickWallBounceForStartle" not in payload["currentState"]
    assert "startle" not in payload["currentState"]
    assert "zoneoutCooldownSec" not in payload["currentState"]
    assert "ballGhost" not in payload["currentState"]
    assert "aiFreezeSec" not in payload["pendingItems"][0]["snapshot"]
    assert "ballGhost" not in payload["pendingItems"][0]["snapshot"]
    assert payload["pendingItems"][0]["priority"] == 8
    assert payload["pendingItems"][0]["source"] == "voice_input_gate"
    assert payload["pendingItems"][0]["builtinFallback"] == "备用台词"
    assert isinstance(payload["balanceHint"].get("message"), str)
    assert payload["angerPressureCap"]["message"] == "暂时保留"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_game_chat_rejects_stale_session_before_llm(monkeypatch):
    manager = _LocaleTrackingManager()
    async def fake_run_game_chat(*_args, **_kwargs):
        raise AssertionError("stale badminton chat should not start an LLM session")

    _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": manager})

    with reset_game_route_state():
        state = gr_runtime._activate_game_route("badminton", "fresh-session", "Lan")
        state["mode"] = "duel"
        state["user_language"] = "en"
        state["user_language_source"] = "render"

        result = await gr_runtime.game_chat("badminton", _FakeRequest({
            "session_id": "old-session",
            "lanlan_name": "Lan",
            "i18n_language": "ja",
            "event": {"kind": "shot_missed", "mode": "duel"},
        }))

    assert result["ok"] is True
    assert result["skipped"] == "stale_session"
    assert result["reason"] == "session_id_mismatch"
    assert result["line"] == ""
    assert result["control"] == {}
    assert manager.language_updates == []
    assert state["user_language"] == "en"
    assert state["user_language_source"] == "render"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_game_chat_rejects_missing_route_before_llm(monkeypatch):
    manager = _LocaleTrackingManager()
    async def fake_run_game_chat(*_args, **_kwargs):
        raise AssertionError("inactive badminton chat should not start an LLM session")

    _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": manager})

    with reset_game_route_state():
        result = await gr_runtime.game_chat("badminton", _FakeRequest({
            "session_id": "old-session",
            "lanlan_name": "Lan",
            "i18n_language": "ja",
            "event": {"kind": "shot_missed", "mode": "duel"},
        }))

    assert result == {
        "ok": True,
        "skipped": "route_inactive",
        "reason": "route_not_active",
        "handled": False,
        "line": "",
        "control": {},
        "lanlan_name": "Lan",
        "method": "game_chat",
    }
    assert manager.language_updates == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generic_game_chat_rejects_missing_route_before_session_creation(monkeypatch):
    async def fail_create_session(*_args, **_kwargs):
        raise AssertionError("missing generic route should not create an LLM session")

    monkeypatch.setattr(gr_runtime, "_get_or_create_session", fail_create_session)
    with reset_game_route_state():
        result = await gr_runtime.game_chat("soccer", _FakeRequest({
            "session_id": "missing-session",
            "lanlan_name": "Lan",
            "event": {"kind": "goal"},
        }))

    assert result["skipped"] == "route_inactive"
    assert result["line"] == ""
    assert result["control"] == {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_route_drain_preserves_outputs_beyond_requested_limit(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    with reset_game_route_state():
        state = gr_runtime._activate_game_route("soccer", "drain-session", "Lan")
        state["pending_outputs"] = [
            {"type": "first"},
            {"type": "second"},
            {"type": "third"},
        ]
        first = await gr_runtime.game_route_drain("soccer", _FakeRequest({
            "session_id": "drain-session",
            "lanlan_name": "Lan",
            "limit": 2,
        }))
        second = await gr_runtime.game_route_drain("soccer", _FakeRequest({
            "session_id": "drain-session",
            "lanlan_name": "Lan",
            "limit": 2,
        }))

    assert [item["type"] for item in first["outputs"]] == ["first", "second"]
    assert [item["type"] for item in second["outputs"]] == ["third"]
    assert state["pending_outputs"] == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_game_chat_rate_limit_does_not_refresh_route_locale(monkeypatch):
    manager = _LocaleTrackingManager()

    async def fake_run_game_chat(*_args, **_kwargs):
        raise AssertionError("rate-limited badminton chat should not start an LLM session")

    _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)
    _gr_patch_all(monkeypatch, "_check_badminton_chat_rate", lambda *_args: False)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": manager})

    with reset_game_route_state():
        state = gr_runtime._activate_game_route("badminton", "duel-session", "Lan")
        state["mode"] = "duel"
        state["user_language"] = "en"
        state["user_language_source"] = "render"

        result = await gr_runtime.game_chat("badminton", _FakeRequest({
            "session_id": "duel-session",
            "lanlan_name": "Lan",
            "i18n_language": "ja",
            "render_language": "ko",
            "event": {"kind": "shot_missed", "mode": "duel"},
        }))

    assert result == {
        "error": "rate_limited",
        "line": "",
        "control": {},
        "retry_after": 2,
    }
    assert manager.language_updates == []
    assert state["user_language"] == "en"
    assert state["user_language_source"] == "render"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_chat_refreshes_matching_route_locale_with_live_precedence(monkeypatch):
    manager = _LocaleTrackingManager()
    prompt_locales = []

    async def fake_run_game_chat(
        _game_type,
        _session_id,
        _event,
        *,
        prompt_locale=None,
        lanlan_name="",
        **_route_kwargs,
    ):
        assert lanlan_name == "Lan"
        prompt_locales.append(prompt_locale)
        return {"line": "ok", "control": {}}

    _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": manager})

    with reset_game_route_state():
        state = gr_runtime._activate_game_route("soccer", "match-session", "Lan")
        state["user_language"] = "en"
        state["user_language_source"] = "render"
        cases = [
            ("en", False, {"render_language": "ja"}, "ja", ("ja", "render")),
            ("zh-TW", True, {"render_language": "pt"}, "zh-TW", ("zh-TW", "session")),
            (
                "zh-TW", True,
                {"i18n_language": "ko", "render_language": "pt"},
                "ko", ("ko", "request"),
            ),
        ]
        for manager_language, explicit, payload, prompt, route_locale in cases:
            manager.user_language = manager_language
            manager._user_language_explicit = explicit
            await gr_runtime.game_chat("soccer", _FakeRequest({
                "session_id": "match-session",
                "lanlan_name": "Lan",
                "event": {"kind": "round_end"},
                **payload,
            }))
            assert prompt_locales[-1] == prompt
            assert (state["user_language"], state["user_language_source"]) == route_locale

        before_stale_calls = len(prompt_locales)
        manager.user_language = "en"
        manager._user_language_explicit = False
        stale = await gr_runtime.game_chat("soccer", _FakeRequest({
            "session_id": "stale-session",
            "lanlan_name": "Lan",
            "render_language": "es",
            "event": {"kind": "round_end"},
        }))
        assert stale["skipped"] == "route_inactive"
        assert len(prompt_locales) == before_stale_calls
        assert (state["user_language"], state["user_language_source"]) == ("ko", "request")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_standard_game_chat_binds_llm_to_validated_route_generation(monkeypatch):
    captured = {}

    async def fake_run_game_chat(
        game_type,
        session_id,
        event,
        *,
        expected_route_state=None,
        expected_route_instance_id="",
        **_kwargs,
    ):
        captured.update({
            "game_type": game_type,
            "session_id": session_id,
            "event": event,
            "expected_route_state": expected_route_state,
            "expected_route_instance_id": expected_route_instance_id,
        })
        return {"line": "ok", "control": {}}

    _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    with reset_game_route_state():
        state = gr_runtime._activate_game_route(
            "example-game", "reused-session", "Lan"
        )
        state["_sdk_route_instance_id"] = "route-A"

        result = await gr_runtime.game_chat(
            "example-game",
            _FakeRequest({
                "session_id": "reused-session",
                "lanlan_name": "Lan",
                "sdk_route_instance_id": "route-A",
                "event": {"kind": "neutral-turn"},
            }),
        )

        assert result["line"] == "ok"
        assert captured["game_type"] == "example-game"
        assert captured["session_id"] == "reused-session"
        assert captured["event"]["kind"] == "neutral-turn"
        assert captured["expected_route_state"] is state
        assert captured["expected_route_instance_id"] == "route-A"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_standard_game_chat_drops_reply_from_replaced_route_generation(monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()
    reply_chunks = []

    class BlockingSession:
        async def stream_text(self, _text):
            entered.set()
            await release.wait()
            reply_chunks.append('late reply')

        async def update_session(self, _config):
            return None

    _gr_patch_all(monkeypatch, "_refresh_game_session_instructions", AsyncMock())
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    with reset_game_route_state():
        route_a = gr_runtime._activate_game_route(
            "example-game", "reused-session", "Lan"
        )
        route_a["_sdk_route_instance_id"] = "route-A"
        session = BlockingSession()
        key = gr_runtime._game_session_key(
            "Lan", "example-game", "reused-session"
        )
        gr_runtime._game_sessions[key] = {
            "session": session,
            "reply_chunks": reply_chunks,
            "lanlan_name": "Lan",
            "lanlan_prompt": "",
            "user_language": "en",
            "game_type": "example-game",
            "session_id": "reused-session",
            "last_activity": 0,
            "lock": asyncio.Lock(),
            "instructions": "stub",
            "source": {"provider": "fake"},
        }

        task = asyncio.create_task(
            gr_runtime.game_chat(
                "example-game",
                _FakeRequest({
                    "session_id": "reused-session",
                    "lanlan_name": "Lan",
                    "sdk_route_instance_id": "route-A",
                    "event": {"kind": "neutral-turn"},
                }),
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=1)

        route_b = gr_runtime._activate_game_route(
            "example-game", "reused-session", "Lan"
        )
        route_b["_sdk_route_instance_id"] = "route-B"
        release.set()
        result = await asyncio.wait_for(task, timeout=1)

        assert result["skipped"] == "route_superseded"
        assert result["line"] == ""
        assert reply_chunks == []
        assert route_a["game_dialog_log"] == []
        assert route_b["game_dialog_log"] == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_game_chat_does_not_archive_late_client_timeout_reply(monkeypatch):
    async def fake_run_game_chat(*_args, **_kwargs):
        return {
            "line": "这句来晚了",
            "control": {},
            "metrics": {"total_ms": 2300, "llm_ms": 2290},
        }

    _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    with reset_game_route_state():
        state = gr_runtime._activate_game_route("badminton", "duel-session", "Lan")
        state["mode"] = "duel"

        result = await gr_runtime.game_chat("badminton", _FakeRequest({
            "session_id": "duel-session",
            "lanlan_name": "Lan",
            "event": {
                "kind": "long_aim",
                "mode": "duel",
                "label": "neko_duel_turn",
                "client_timeout_ms": 2200,
            },
        }))

    assert result["line"] == "这句来晚了"
    assert result["skipped_memory"] == "client_timeout"
    assert state["game_dialog_log"] == []


@pytest.mark.unit
def test_route_state_key_is_tuple_no_collision_no_prefix_false_match(monkeypatch):
    """The previous f"{lanlan}:{game_type}" string key collided when a
    lanlan_name contained a literal ':' and the prefix-style lookup
    false-matched 'Lan' against 'Lan2:soccer'."""
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    # Tuple key — no string-concat collision possible.
    state_a = gr_runtime._activate_game_route("soccer", "match_1", "Lan:Alt")
    state_b = gr_runtime._activate_game_route("soccer", "match_2", "Lan")
    state_c = gr_runtime._activate_game_route("soccer", "match_3", "Lan2")

    # Slot identity is preserved despite ':' in one lanlan_name.
    assert gr_runtime._game_route_states[("Lan:Alt", "soccer")] is state_a
    assert gr_runtime._game_route_states[("Lan", "soccer")] is state_b
    assert gr_runtime._game_route_states[("Lan2", "soccer")] is state_c

    # Prefix false-match defense: looking up 'Lan' must NOT return state_c
    # (which used to collide because 'Lan2:soccer'.startswith('Lan:') is False
    # but 'Lan:soccer'.startswith('Lan:') IS true; symmetrically a real bug
    # was 'Lan'.startswith vs 'Lan' returning the wrong slot for ambiguous
    # equality. With tuple keys we compare lanlan_name by exact string).
    found = gr_runtime._get_active_game_route_state("Lan")
    assert found is state_b
    found2 = gr_runtime._get_active_game_route_state("Lan2")
    assert found2 is state_c
    found_alt = gr_runtime._get_active_game_route_state("Lan:Alt")
    assert found_alt is state_a


@pytest.mark.unit
def test_memory_review_prompt_protects_game_module_archive_records():
    """All five locales' HISTORY_REVIEW_PROMPT must reference the English
    archive tags 'Game Module Memory Record' / 'Game Module Postgame Record'
    that the game module emits verbatim into chat history (write side at
    main_routers.gr_archive._build_game_archive_memory_text /
    _build_game_archive_memory_summary_text). The previous design used
    Chinese-literal tags; the project standardised on English-only tags so
    every review-LLM in any UI locale matches the same string."""
    from config.prompts.prompts_memory import get_history_review_prompt

    expected_tags = (
        "Game Module Memory Record",
        "Game Module Postgame Record",
    )
    for lang in ("zh", "en", "ja", "ko", "ru"):
        prompt = get_history_review_prompt(lang)
        for tag in expected_tags:
            assert tag in prompt, (
                f"locale={lang} HISTORY_REVIEW_PROMPT missing archive tag {tag!r}"
            )

    # zh-specific assertions retained as a localised-content check.
    zh_prompt = get_history_review_prompt("zh")
    assert "不同时间/会话的同一类游戏默认代表不同局" in zh_prompt
    assert "不要整条删除" in zh_prompt


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_highlight_selector_uses_full_dialogue_log(monkeypatch):
    calls = []

    class _FakeLLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def ainvoke(self, messages):
            calls.append(messages)
            return type("Resp", (), {
                "content": '{"important_records":["保留了第一句互动"],"important_game_events":["记住了关键抢断"]}'
            })()

    def fake_create_chat_llm(*_args, **_kwargs):
        return _FakeLLM()

    _gr_patch_all(monkeypatch, "_get_current_character_info",
        lambda: {
            "model": "test-model",
            "base_url": "http://llm.test",
            "api_key": "key",
            "api_type": "test",
        },
    )
    monkeypatch.setattr("utils.llm_client.create_chat_llm", fake_create_chat_llm)

    archive = {
        "game_type": "soccer",
        "session_id": "match_1",
        "lanlan_name": "Lan",
        "last_state": {"score": {"player": 0, "ai": 5}},
        "soccer_game_memory_enabled": True,
        "soccer_game_memory_player_interaction_enabled": True,
        "soccer_game_memory_event_reply_enabled": True,
        "soccer_game_memory_archive_enabled": True,
        "soccer_game_memory_postgame_context_enabled": True,
        "full_dialogues": [
            {"type": "user", "text": "第一句也要参与筛选"},
            {"type": "assistant", "line": "我记着呢。"},
            {"type": "user", "text": "最后一句"},
        ],
        "last_full_dialogues": [
            {"type": "user", "text": "最后一句"},
        ],
        "key_events": [],
    }

    highlights = await gr_archive._select_game_archive_memory_highlights(archive)

    assert highlights["important_records"] == ["保留了第一句互动"]
    assert highlights["important_game_events"] == ["记住了关键抢断"]
    assert "第一句也要参与筛选" in calls[0][1].content


@pytest.mark.unit
def test_route_liveness_ignores_recent_activity_when_heartbeat_is_stale():
    state = {
        "created_at": 100.0,
        "last_heartbeat_at": 110.0,
        "last_activity": 125.0,
    }

    assert gr_runtime._route_liveness_at(state) == 110.0


@pytest.mark.unit
def test_route_liveness_uses_created_at_before_first_heartbeat():
    state = {
        "created_at": 100.0,
        "last_activity": 125.0,
    }

    assert gr_runtime._route_liveness_at(state) == 100.0


@pytest.mark.unit
def test_route_heartbeat_timeout_uses_hidden_grace_window():
    assert gr_runtime._route_heartbeat_timeout_seconds({"page_visible": True}) == (
        gr_runtime._GAME_ROUTE_HEARTBEAT_TIMEOUT_SECONDS
    )
    assert gr_runtime._route_heartbeat_timeout_seconds({"page_visible": False}) == (
        gr_runtime._GAME_ROUTE_HIDDEN_HEARTBEAT_TIMEOUT_SECONDS
    )
    assert gr_runtime._route_heartbeat_timeout_seconds({"visibility_state": "hidden"}) == (
        gr_runtime._GAME_ROUTE_HIDDEN_HEARTBEAT_TIMEOUT_SECONDS
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_close_and_remove_session_closes_client():
    fake_session = type("FakeSession", (), {"close": AsyncMock()})()
    key = _put_game_session("Lan", "soccer", "test_sid", fake_session)

    closed = await gr_runtime._close_and_remove_session("soccer", "test_sid", "Lan")

    assert closed is True
    fake_session.close.assert_awaited_once()
    assert key not in gr_runtime._game_sessions


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_returns_closed_flag_for_missing_session():
    result = await gr_runtime.game_end("soccer", _FakeRequest({"session_id": "missing"}))

    assert result == {
        "ok": True,
        "closed": False,
        "session_id": "missing",
        "route_closed": False,
        "archive": None,
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_closes_existing_session():
    fake_session = type("FakeSession", (), {"close": AsyncMock()})()
    _put_game_session("Lan", "soccer", "match_1", fake_session)

    result = await gr_runtime.game_end(
        "soccer",
        _FakeRequest({"lanlan_name": "Lan", "session_id": "match_1"}),
    )

    assert result == {
        "ok": True,
        "closed": True,
        "session_id": "match_1",
        "route_closed": False,
        "archive": None,
    }
    fake_session.close.assert_awaited_once()


class _FakeRealtimeSession:
    def __init__(self, *, model_lower="qwen-realtime", delivered=True):
        self._model_lower = model_lower
        self.model = model_lower
        self.base_url = "https://generativelanguage.googleapis.com" if "gemini" in model_lower else "https://dashscope.aliyuncs.com"
        self._api_type = "openai"
        self._is_gemini = "gemini" in model_lower
        self._is_responding = False
        self._audio_delta_total = 0
        self._input_audio_committed_total = 0
        self._response_created_total = 0
        self._response_done_total = 0
        self._last_response_transcript = ""
        self._active_instructions = "base realtime instructions"
        self.delivered = delivered
        self.prime_context_calls = []
        self.update_session_calls = []
        self.prompt_calls = []
        self.create_response_calls = []

    async def prime_context(self, text, skipped=False):
        self.prime_context_calls.append((text, skipped))

    async def update_session(self, config):
        self.update_session_calls.append(config)
        if "instructions" in config:
            self._active_instructions = config["instructions"]

    async def prompt_ephemeral(self, *args, language="zh"):
        call = {"language": language}
        if args:
            call["instruction"] = args[0]
        self.prompt_calls.append(call)
        if self.delivered:
            self._input_audio_committed_total += 1
            self._response_created_total += 1
            self._response_done_total += 1
        return self.delivered

    async def create_response(self, text):
        self.create_response_calls.append(text)


class _FakeRealtimeManager:
    def __init__(self, session, append_context_result=None):
        self.session = session
        self.is_active = True
        self.user_language = "zh-CN"
        self.current_speech_id = "previous-speech"
        self.lock = None
        self.use_tts = False
        self._speech_output_total = 0
        self.voice_nudge_calls = 0
        self.voice_nudge_kwargs = []
        self.voice_nudge_event = asyncio.Event()
        self.append_context_calls = []
        self.append_context_result = append_context_result

    async def append_context(self, **kwargs):
        self.append_context_calls.append(kwargs)
        if self.append_context_result is not None:
            return self.append_context_result
        source = str(kwargs.get("source") or "")
        text = kwargs["text"]
        if source not in {"game.realtime_context", "game.postgame"}:
            text = f"{kwargs['role']}: {text}"
        await self.session.prime_context(text, skipped=True)
        return SimpleNamespace(appended=True, deduped=False, targets=("realtime_prime",), reason=None)

    async def trigger_voice_proactive_nudge(self, **kwargs):
        self.voice_nudge_calls += 1
        self.voice_nudge_kwargs.append(kwargs)
        self.voice_nudge_event.set()
        return True


@pytest.fixture
def _fake_realtime(monkeypatch):
    import main_logic.omni_realtime_client as realtime_mod

    monkeypatch.setattr(realtime_mod, "OmniRealtimeClient", _FakeRealtimeSession)
    _gr_patch_all(monkeypatch, "_get_current_character_info",
        lambda: {"lanlan_name": "Lan"},
    )

    return _FakeRealtimeSession


@pytest.mark.unit
@pytest.mark.asyncio
async def test_realtime_context_skips_gemini_prime_to_avoid_hidden_response(monkeypatch, _fake_realtime):
    session = _fake_realtime(model_lower="gemini-2.5-flash-native-audio-preview", delivered=True)
    mgr = _FakeRealtimeManager(session)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})

    with reset_game_route_state():
        state = gr_runtime._activate_game_route(
            "example-game", "context-session", "Lan"
        )
        state["_sdk_route_instance_id"] = "context-route"
        result = await gr_runtime.game_realtime_context(
            "example-game",
            _FakeRequest({
                "lanlan_name": "Lan",
                "session_id": "context-session",
                "sdk_route_instance_id": "context-route",
                "source": "game_event",
                "currentState": {"phase": "active"},
                "pendingItems": [{"type": "game_event", "kind": "phase-changed"}],
            }),
        )

    assert result["ok"] is True
    assert result["action"] == "skip"
    assert result["reason"] == "gemini_no_session_update"
    assert session.prime_context_calls == []
    assert session.create_response_calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_realtime_context_aborts_when_active_session_changes_before_append(monkeypatch, _fake_realtime):
    original = _fake_realtime(model_lower="qwen-realtime", delivered=True)
    replacement = _fake_realtime(model_lower="qwen-realtime", delivered=True)
    mgr = _FakeRealtimeManager(original)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})

    def swap_session(_game_type, _payload, _language=None):
        mgr.session = replacement
        return "[Game Realtime Context]\nrace"

    _gr_patch_all(monkeypatch, "_compact_realtime_context_text", swap_session)

    with reset_game_route_state():
        state = gr_runtime._activate_game_route(
            "example-game", "context-session", "Lan"
        )
        state["_sdk_route_instance_id"] = "context-route"
        result = await gr_runtime.game_realtime_context(
            "example-game",
            _FakeRequest({
                "lanlan_name": "Lan",
                "session_id": "context-session",
                "sdk_route_instance_id": "context-route",
                "source": "game_event",
                "currentState": {"phase": "active"},
                "pendingItems": [{"type": "game_event", "kind": "phase-changed"}],
            }, path="/api/game/example-game/realtime-context"),
        )

    assert result == {"ok": False, "reason": "realtime_session_changed", "lanlan_name": "Lan"}
    assert mgr.append_context_calls == []
    assert original.prime_context_calls == []
    assert replacement.prime_context_calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_realtime_context_revalidates_route_at_append_boundary(monkeypatch, _fake_realtime):
    session = _fake_realtime(model_lower="qwen-realtime", delivered=True)
    mgr = _FakeRealtimeManager(session)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})

    with reset_game_route_state():
        route_a = gr_runtime._activate_game_route(
            "example-game", "reused-session", "Lan"
        )
        route_a["_sdk_route_instance_id"] = "route-A"

        def replace_route(_game_type, _payload, _language=None):
            route_b = gr_runtime._activate_game_route(
                "example-game", "reused-session", "Lan"
            )
            route_b["_sdk_route_instance_id"] = "route-B"
            return "[Game Realtime Context]\nstale"

        _gr_patch_all(monkeypatch, "_compact_realtime_context_text", replace_route)
        result = await gr_runtime.game_realtime_context(
            "example-game",
            _FakeRequest({
                "lanlan_name": "Lan",
                "session_id": "reused-session",
                "sdk_route_instance_id": "route-A",
                "source": "game_event",
                "currentState": {"phase": "active"},
            }, path="/api/game/example-game/realtime-context"),
        )

    assert result == {
        "ok": False,
        "reason": "route_superseded",
        "lanlan_name": "Lan",
    }
    assert mgr.append_context_calls == []
    assert session.prime_context_calls == []


class _FakeGameRouteManager:
    def __init__(self):
        self.is_active = False
        self.session = None
        self.input_mode = "audio"
        self.mirrored = []
        self.assistant_mirrored = []
        self.spoken = []
        self.preloaded = []
        self.preload_render_languages = []
        self.language_updates = []
        self.user_language = ""
        self._user_language_explicit = False
        self.statuses = []
        self.user_activity_count = 0
        self._takeover_active = False
        self._takeover_input_dispatcher = None
        self.render_language_at_mirror = []

    def set_user_language(self, language):
        self.language_updates.append(language)
        self.user_language = language
        self._user_language_explicit = True

    async def mirror_user_input(self, text, **kwargs):
        self.mirrored.append((text, kwargs))

    async def mirror_assistant_output(self, text, **kwargs):
        self.render_language_at_mirror.append(
            getattr(self, "_conversation_render_language", None)
        )
        self.assistant_mirrored.append((text, kwargs))
        return {"ok": True, "mirrored": True, "method": "project_text_mirror"}

    async def send_user_activity(self):
        self.user_activity_count += 1

    async def mirror_assistant_speech(self, line, **kwargs):
        self.render_language_at_mirror.append(
            getattr(self, "_conversation_render_language", None)
        )
        self.spoken.append((line, kwargs))
        return {
            "ok": True,
            "method": "project_tts",
            "speech_id": "game-speech",
            "audio_sent": True,
            "voice_source": {"provider": "project_tts"},
        }

    async def preload_game_speech_audio(self, lines, *, render_language=""):
        self.render_language_at_mirror.append(
            getattr(self, "_conversation_render_language", None)
        )
        self.preload_render_languages.append(render_language)
        self.preloaded.append(list(lines))
        return {
            "ok": True,
            "results": [
                {"index": index, "status": "loaded"}
                for index in range(len(lines))
            ],
            "loaded": len(lines),
            "hits": 0,
            "failed": 0,
        }

    def set_render_language(self, language):
        self._conversation_render_language = language

    async def send_status(self, message):
        self.statuses.append(message)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sdk_protocol_context_and_memory_endpoints_are_session_bound_and_bounded(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    with reset_game_route_state():
        state = gr_runtime._activate_game_route("soccer", "sdk-session", "Lan")
        _set_soccer_game_memory_policy(state, enabled=True)
        state["game_context_summary"] = "刚刚讨论了防守策略"
        state["last_state"] = {"score": {"player": 1, "ai": 0}}
        state["preGameContext"] = {"gameStance": "friendly"}
        monkeypatch.setattr(
            gr_runtime,
            "_load_game_character_prompt_locale",
            AsyncMock(return_value=("zh-CN", True)),
        )

        accepted = await gr_runtime.game_sdk_protocol(
            "soccer",
            _FakeRequest({
                "protocolVersion": "1",
                "sequence": 1,
                "kind": "event",
                "type": "round-started",
                "timestamp": 100,
                "sessionId": "sdk-session",
                "session_id": "sdk-session",
                "lanlan_name": "Lan",
                "payload": {"round": 1},
            }, path="/api/game/soccer/protocol"),
        )
        replayed = await gr_runtime.game_sdk_protocol(
            "soccer",
            _FakeRequest({
                "protocolVersion": "1",
                "sequence": 1,
                "kind": "event",
                "type": "round-started",
                "sessionId": "sdk-session",
                "session_id": "sdk-session",
                "lanlan_name": "Lan",
                "payload": {"round": 2},
            }, path="/api/game/soccer/protocol"),
        )
        context = await gr_runtime.game_sdk_context_read(
            "soccer",
            _FakeRequest({
                "session_id": "sdk-session",
                "lanlan_name": "Lan",
                "scopes": [
                    "character-public",
                    "recent-chat-summary",
                    "current-state",
                    "pregame-context",
                    "not-reviewed",
                ],
            }, path="/api/game/soccer/context/read"),
        )
        memory = await gr_runtime.game_sdk_memory_submit(
            "soccer",
            _FakeRequest({
                "session_id": "sdk-session",
                "lanlan_name": "Lan",
                "submission": {
                    "events": [{"kind": "goal", "text": "玩家进球"}],
                    "state": {"score": {"player": 2, "ai": 0}},
                    "result": {"winner": "player"},
                    "summary": "玩家赢下了本局",
                },
            }, path="/api/game/soccer/memory/submit"),
        )

        assert accepted == {
            "ok": True,
            "accepted": True,
            "kind": "event",
            "type": "round-started",
            "sequence": 1,
            "session_id": "sdk-session",
        }
        assert replayed["reason"] == "sequence_replayed"
        assert len(state["_sdk_protocol_events"]) == 1
        assert context["scopes"]["character-public"] == {
            "lanlan_name": "Lan",
            "language": "zh-CN",
            "language_preference_resolved": True,
            "game_type": "soccer",
        }
        assert context["scopes"]["recent-chat-summary"]["summary"] == "刚刚讨论了防守策略"
        assert context["scopes"]["current-state"]["score"]["player"] == 1
        assert context["unavailable_scopes"] == ["not-reviewed"]
        assert memory["accepted"] is True
        assert state["last_state"]["score"]["player"] == 2
        assert state["game_context_summary"] == "玩家赢下了本局"
        archive = gr_archive._build_game_archive(state)
        assert archive["sdk_memory_submissions"][-1]["events"][0]["text"] == "玩家进球"
        assert archive["sdk_memory_submissions"][-1]["result"] == {"winner": "player"}
        source_labels = gr_archive.get_game_archive_highlight_source_labels(
            gr_archive._archive_prompt_language(archive)
        )
        assert source_labels["sdk_memory_submissions"] in (
            gr_archive._build_game_archive_memory_highlight_source(archive)
        )
        for language in ("zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"):
            assert gr_archive.get_game_archive_highlight_source_labels(language)[
                "sdk_memory_submissions"
            ]

        fallback = gr_archive._fallback_game_archive_memory_highlights(archive)
        assert fallback["memory_summary"] == "玩家赢下了本局"
        assert any("玩家进球" in item for item in fallback["important_game_events"])
        assert any('"winner":"player"' in item for item in fallback["important_game_events"])
        assert '"player":2' in fallback["state_carryback"]

        degraded_archive = dict(archive)
        degraded_archive["game_context_degraded"] = True
        degraded_archive.pop("memory_highlights", None)
        degraded = await gr_archive._ensure_game_archive_memory_highlights(degraded_archive)
        assert degraded["memory_summary"] == "玩家赢下了本局"
        assert any("玩家进球" in item for item in degraded["important_game_events"])
        memory_messages = gr_archive._build_game_archive_memory_messages(degraded_archive)
        assert "玩家赢下了本局" in memory_messages[-1]["content"][0]["text"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sdk_context_read_allows_only_public_character_scope_before_route(monkeypatch):
    monkeypatch.setattr(
        gr_runtime,
        "_load_game_character_prompt_locale",
        AsyncMock(return_value=("zh-CN", True)),
    )

    with reset_game_route_state():
        context = await gr_runtime.game_sdk_context_read(
            "example-game",
            _FakeRequest({
                "session_id": "pregame-session",
                "lanlan_name": "Lan",
                "scopes": [
                    "character-public",
                    "recent-chat-summary",
                    "current-state",
                    "pregame-context",
                ],
            }, path="/api/game/example-game/context/read"),
        )
        stale = await gr_runtime.game_sdk_context_read(
            "example-game",
            _FakeRequest({
                "session_id": "pregame-session",
                "lanlan_name": "Lan",
                "sdk_route_instance_id": "stale-route",
                "scopes": ["character-public"],
            }, path="/api/game/example-game/context/read"),
        )

    assert context["ok"] is True, context
    assert context["session_id"] == "pregame-session"
    assert context["scopes"] == {
        "character-public": {
            "lanlan_name": "Lan",
            "language": "zh-CN",
            "language_preference_resolved": True,
            "game_type": "example-game",
        },
    }
    assert context["unavailable_scopes"] == [
        "recent-chat-summary",
        "current-state",
        "pregame-context",
    ]
    assert stale["ok"] is False
    assert stale["reason"] == "route_instance_id_mismatch"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sdk_memory_endpoint_requires_session_consent(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    with reset_game_route_state():
        gr_runtime._activate_game_route("soccer", "sdk-session", "Lan")

        result = await gr_runtime.game_sdk_memory_submit(
            "soccer",
            _FakeRequest({
                "session_id": "sdk-session",
                "lanlan_name": "Lan",
                "submission": {"summary": "不应写入"},
            }, path="/api/game/soccer/memory/submit"),
        )

        assert result == {"ok": False, "reason": "consent_required"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sdk_memory_submission_retention_is_bounded(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    with reset_game_route_state():
        state = gr_runtime._activate_game_route("soccer", "sdk-session", "Lan")
        _set_soccer_game_memory_policy(state, enabled=True)

        for index in range(gr_runtime._SDK_GAME_MEMORY_SUBMISSION_LIMIT + 4):
            result = await gr_runtime.game_sdk_memory_submit(
                "soccer",
                _FakeRequest({
                    "session_id": "sdk-session",
                    "lanlan_name": "Lan",
                    "submission": {"summary": f"summary-{index}"},
                }, path="/api/game/soccer/memory/submit"),
            )
            assert result["accepted"] is True

        retained = state["_sdk_memory_submissions"]
        assert len(retained) == gr_runtime._SDK_GAME_MEMORY_SUBMISSION_LIMIT
        assert retained[0]["summary"] == "summary-4"
        assert retained[-1]["summary"] == "summary-19"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_passive_guard_requires_matching_active_route_before_llm(monkeypatch):
    llm_calls = []

    async def fake_passive_guard(data, lanlan_name):
        llm_calls.append((data, lanlan_name))
        return {"ok": True, "recommendedAction": "observe_more"}

    _gr_patch_all(monkeypatch, "_run_soccer_passive_guard_ai", fake_passive_guard)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    with reset_game_route_state():
        inactive = await gr_runtime.game_passive_guard(
            "soccer",
            _FakeRequest({"session_id": "match_1", "lanlan_name": "Lan"}),
        )
        assert inactive["ok"] is False
        assert inactive["reason"] == "game_route_inactive"

        gr_runtime._activate_game_route("soccer", "match_1", "Lan")
        mismatched = await gr_runtime.game_passive_guard(
            "soccer",
            _FakeRequest({"session_id": "match_old", "lanlan_name": "Lan"}),
        )
        assert mismatched["ok"] is False
        assert mismatched["reason"] == "session_id_mismatch"

        accepted = await gr_runtime.game_passive_guard(
            "soccer",
            _FakeRequest({"sessionId": "match_1", "lanlan_name": "Lan"}),
        )

    assert accepted["ok"] is True
    assert len(llm_calls) == 1
    assert llm_calls[0][0]["session_id"] == "match_1"
    assert llm_calls[0][0]["lanlan_name"] == "Lan"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speak_serializes_bounded_session_workers(monkeypatch):
    class BlockingSpeechManager(_FakeGameRouteManager):
        def __init__(self):
            super().__init__()
            self.started = []
            self.releases = []
            self.active_workers = 0
            self.max_active_workers = 0

        async def mirror_assistant_speech(self, line, **kwargs):
            release = asyncio.Event()
            self.started.append(line)
            self.releases.append(release)
            self.active_workers += 1
            self.max_active_workers = max(self.max_active_workers, self.active_workers)
            try:
                await release.wait()
            finally:
                self.active_workers -= 1
            return {
                "ok": True,
                "speech_id": f"speech-{line}",
                "audio_sent": True,
            }

    mgr = BlockingSpeechManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    with reset_game_route_state():
        state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
        requests = [
            asyncio.create_task(gr_runtime.game_project_speak(
                "soccer",
                _FakeRequest({
                    "line": f"line-{index}",
                    "session_id": "match_1",
                    "lanlan_name": "Lan",
                }),
            ))
            for index in range(4)
        ]
        for _ in range(100):
            if len(mgr.started) == 1 and getattr(mgr, "_sdk_game_speech_pending_count", 0) == 4:
                break
            await asyncio.sleep(0.01)
        assert mgr.started == ["line-0"]
        assert mgr._sdk_game_speech_pending_count == 4

        busy = await gr_runtime.game_project_speak(
            "soccer",
            _FakeRequest({
                "line": "line-4",
                "session_id": "match_1",
                "lanlan_name": "Lan",
            }),
        )
        assert busy["reason"] == "busy"

        for expected_started in range(1, 5):
            mgr.releases[expected_started - 1].set()
            for _ in range(100):
                if len(mgr.started) >= min(expected_started + 1, 4):
                    break
                await asyncio.sleep(0.01)
        results = await asyncio.gather(*requests)

        assert all(result["ok"] for result in results)
        assert mgr.started == ["line-0", "line-1", "line-2", "line-3"]
        assert mgr.max_active_workers == 1
        assert not hasattr(mgr, "_sdk_game_speech_pending_count")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_replacement_cancels_the_previous_routes_active_speech(monkeypatch):
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingSpeechManager(_FakeGameRouteManager):
        async def mirror_assistant_speech(self, line, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    mgr = BlockingSpeechManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(
        monkeypatch,
        "_submit_game_archive_to_memory",
        AsyncMock(return_value={"ok": True, "status": "skipped"}),
    )
    with reset_game_route_state():
        state_a = gr_runtime._activate_game_route(
            "example-speech", "session-a", "Lan"
        )
        state_a["_sdk_route_instance_id"] = "route-a"
        speech_task = asyncio.create_task(gr_runtime.game_project_speak(
            "example-speech",
            _FakeRequest({
                "line": "route A speech",
                "lanlan_name": "Lan",
                "session_id": "session-a",
                "sdk_route_instance_id": "route-a",
            }),
        ))
        await asyncio.wait_for(entered.wait(), timeout=1)

        replacement = await gr_runtime.game_route_start(
            "example-speech",
            _FakeRequest({
                "lanlan_name": "Lan",
                "session_id": "session-b",
                "sdk_route_instance_id": "route-b",
            }),
        )

        assert replacement["ok"] is True
        assert replacement["state"]["session_id"] == "session-b"
        await asyncio.wait_for(cancelled.wait(), timeout=1)
        with pytest.raises(asyncio.CancelledError):
            await speech_task
        assert "_sdk_active_speech_task" not in state_a


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_start_activates_stt_gate_when_audio_already_active(monkeypatch, _fake_realtime):
    mgr = _FakeGameRouteManager()
    mgr.is_active = True
    mgr.session = _fake_realtime(model_lower="qwen-realtime", delivered=True)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})

    async def fake_pregame_context(**kwargs):
        assert kwargs["neko_initiated"] is False
        return (
            gr_pregame._default_soccer_pregame_context(initial_difficulty="lv2"),
            "fallback",
            "ai_failed",
        )

    _gr_patch_all(monkeypatch, "_build_soccer_pregame_context", fake_pregame_context)

    result = await gr_runtime.game_route_start(
        "soccer",
        _FakeRequest({"lanlan_name": "Lan", "session_id": "match_1"}),
    )

    assert result["ok"] is True
    public_state = result["state"]
    # Built-in soccer is not an SDK route, so it keeps the historical full
    # lifecycle shape that soccer-demo.js ``_applyPreGameContext`` reads.
    assert "preGameContext" in public_state
    assert "pre_game_context_error" in public_state
    state = gr_runtime._get_active_game_route_state("Lan", "soccer")
    assert state["before_game_external_mode"] == "audio"
    assert state["before_game_external_active"] is True
    assert state["game_started"] is False
    assert state["game_external_voice_route_active"] is True
    assert state["game_input_mode"] == "voice"
    assert state["preGameContext"]["gameStance"] == "neutral_play"
    assert state["preGameContext"]["initialDifficulty"] == "lv2"
    assert state["pre_game_context_source"] == "fallback"
    assert state["pre_game_context_error"] == "ai_failed"
    assert "GAME_VOICE_STT_GATE_ACTIVE" in mgr.statuses[0]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_start_prefers_explicit_manager_locale_over_render_fallback(monkeypatch):
    mgr = _FakeGameRouteManager()
    mgr.user_language = "zh-TW"
    mgr._user_language_explicit = True
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    prompt_locales = []

    async def fake_pregame_context(**kwargs):
        prompt_locales.append(kwargs["prompt_locale"])
        return (
            gr_pregame._default_soccer_pregame_context(initial_difficulty="lv2"),
            "fallback",
            "",
        )

    _gr_patch_all(monkeypatch, "_build_soccer_pregame_context", fake_pregame_context)

    with reset_game_route_state():
        result = await gr_runtime.game_route_start(
            "soccer",
            _FakeRequest({
                "lanlan_name": "Lan",
                "session_id": "match-explicit-locale",
                "render_language": "ja",
            }),
        )

        assert result["ok"] is True
        assert prompt_locales == ["zh-TW"]
        assert result["state"]["user_language"] == "zh-TW"
        assert result["state"]["user_language_source"] == "session"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_start_accepts_neko_invite_context(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    async def fake_pregame_context(**kwargs):
        assert kwargs["neko_initiated"] is True
        assert kwargs["neko_invite_text"] == "来踢球吧，玩家。"
        return (
            {
                **gr_pregame._default_soccer_pregame_context(initial_difficulty="lv3"),
                "launchIntent": "neko_invite",
                "openingLine": "看我这一脚",
            },
            "ai",
            "",
        )

    _gr_patch_all(monkeypatch, "_build_soccer_pregame_context", fake_pregame_context)

    result = await gr_runtime.game_route_start(
        "soccer",
        _FakeRequest({
            "lanlan_name": "Lan",
            "session_id": "match_1",
            "nekoInitiated": True,
            "nekoInviteText": "来踢球吧，玩家。",
            "gameMemoryTailCount": 3,
            "i18n_language": "zh-TW",
        }),
    )

    assert result["ok"] is True
    public_state = result["state"]
    # Built-in soccer keeps the historical full lifecycle shape: the pregame
    # opening stance/mood/difficulty only reach the game through this field.
    assert public_state["preGameContext"]["launchIntent"] == "neko_invite"
    assert public_state["pre_game_context_source"] == "ai"
    state = gr_runtime._get_active_game_route_state("Lan", "soccer")
    assert state["nekoInitiated"] is True
    assert state["nekoInviteText"] == "来踢球吧，玩家。"
    assert state["preGameContext"]["launchIntent"] == "neko_invite"
    assert state["preGameContext"]["initialDifficulty"] == "lv3"
    assert state["preGameContext"]["openingLine"] == "看我这一脚"
    assert state["pre_game_context_source"] == "ai"
    assert state["pre_game_context_error"] == ""
    assert state["game_memory_tail_count"] == 3
    assert state["soccer_game_memory_enabled"] is False
    assert state["soccer_game_memory_player_interaction_enabled"] is False
    assert state["soccer_game_memory_event_reply_enabled"] is False
    assert state["soccer_game_memory_archive_enabled"] is False
    assert state["soccer_game_memory_postgame_context_enabled"] is False
    assert state["game_memory_enabled"] is False
    assert state["user_language"] == "zh-TW"
    assert gr_archive._build_game_archive(state)["user_language"] == "zh-TW"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_start_finalizes_old_active_route_before_replacing(monkeypatch):
    fake_session = type("FakeSession", (), {"close": AsyncMock()})()
    gr_runtime._game_sessions[gr_runtime._game_session_key("Lan", "soccer", "old_match")] = {
        "session": fake_session,
        "reply_chunks": [],
        "last_activity": gr_runtime.time.time(),
        "lock": None,
    }
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    old_state = gr_runtime._activate_game_route("soccer", "old_match", "Lan")
    _set_soccer_game_memory_policy(old_state, enabled=True)
    _mark_game_started(old_state)

    submitted = []

    async def fake_submit(archive):
        submitted.append(archive)
        return {"ok": True, "status": "cached", "count": 1}

    async def fake_pregame_context(**_kwargs):
        return (
            gr_pregame._default_soccer_pregame_context(initial_difficulty="lv2"),
            "fallback",
            "",
        )

    _gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", fake_submit)
    _gr_patch_all(monkeypatch, "_build_soccer_pregame_context", fake_pregame_context)

    result = await gr_runtime.game_route_start(
        "soccer",
        _FakeRequest({"lanlan_name": "Lan", "session_id": "new_match"}),
    )

    assert result["ok"] is True
    assert result["state"]["session_id"] == "new_match"
    assert old_state["game_route_active"] is False
    assert old_state["exit_reason"] == "superseded_by_route_start"
    assert old_state["_sdk_route_superseded"] is True
    assert submitted[0]["session_id"] == "old_match"
    assert submitted[0]["exit_reason"] == "superseded_by_route_start"
    fake_session.close.assert_awaited_once()
    assert gr_runtime._game_route_states[gr_runtime._route_state_key("Lan", "soccer")]["session_id"] == "new_match"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_start_finalizes_other_game_types_for_same_lanlan(monkeypatch):
    """Starting a route must close every active route for the same character."""
    fake_session = type("FakeSession", (), {"close": AsyncMock()})()
    gr_runtime._game_sessions[gr_runtime._game_session_key("Lan", "soccer", "soccer_match")] = {
        "session": fake_session,
        "reply_chunks": [],
        "last_activity": gr_runtime.time.time(),
        "lock": None,
    }
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    old_state = gr_runtime._activate_game_route("soccer", "soccer_match", "Lan")
    _set_soccer_game_memory_policy(old_state, enabled=True)
    _mark_game_started(old_state)

    async def fake_submit(archive):
        return {"ok": True, "status": "cached", "count": 1}

    _gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", fake_submit)

    # 假设的另一种游戏 game_type=chess；非 soccer 路径会跳过 _build_soccer_pregame_context。
    result = await gr_runtime.game_route_start(
        "chess",
        _FakeRequest({"lanlan_name": "Lan", "session_id": "chess_match"}),
    )

    assert result["ok"] is True
    assert old_state["game_route_active"] is False
    assert old_state["exit_reason"] == "superseded_by_route_start"
    fake_session.close.assert_awaited_once()
    assert game_router.is_game_route_active("Lan", "chess") is True
    assert game_router.is_game_route_active("Lan", "soccer") is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_end_holds_supersede_lock_until_finalize_releases_takeover(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})

    old_state = gr_runtime._activate_game_route("badminton", "old_match", "Lan")
    _mark_game_started(old_state)
    mgr._takeover_active = True
    mgr._takeover_input_dispatcher = object()

    release_finalize = asyncio.Event()
    finalize_started = asyncio.Event()

    async def fake_push(*_args, **_kwargs):
        finalize_started.set()
        await release_finalize.wait()

    _gr_patch_all(monkeypatch, "_push_game_window_state_change", fake_push)
    _gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", AsyncMock(return_value={"ok": True}))
    _gr_patch_all(monkeypatch, "_build_soccer_pregame_context",
        AsyncMock(return_value=(gr_pregame._default_soccer_pregame_context(initial_difficulty="lv2"), "fallback", "")),
    )

    end_task = asyncio.create_task(gr_runtime.game_route_end(
        "badminton",
        _FakeRequest({
            "lanlan_name": "Lan",
            "session_id": "old_match",
            "reason": "badminton_game_over",
            "game_started": True,
            "round_completed": True,
            "currentState": {"score": {"player": 1, "ai": 0}},
            "finalScore": {"player": 1, "ai": 0},
        }),
    ))
    await asyncio.wait_for(finalize_started.wait(), timeout=1)

    start_task = asyncio.create_task(gr_runtime.game_route_start(
        "soccer",
        _FakeRequest({"lanlan_name": "Lan", "session_id": "new_match"}),
    ))
    await asyncio.sleep(0)
    assert not start_task.done()

    release_finalize.set()
    end_result = await asyncio.wait_for(end_task, timeout=1)
    start_result = await asyncio.wait_for(start_task, timeout=1)

    assert end_result["ok"] is True
    assert start_result["ok"] is True
    assert old_state["game_route_active"] is False
    assert gr_runtime._game_route_states[gr_runtime._route_state_key("Lan", "soccer")]["session_id"] == "new_match"
    assert mgr._takeover_active is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_external_text_to_game_llm_defers_voice_to_frontend_arbiter(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})

    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
    state["last_state"] = {
        "round": 3,
        "mood": "happy",
        "difficulty": "lv2",
        "score": {"player": 1, "ai": 4},
    }
    state["user_language"] = "zh-TW"
    state["user_language_source"] = "render"

    async def fake_run_game_chat(
        game_type, session_id, event, *, prompt_locale=None, **_route_kwargs
    ):
        assert game_type == "soccer"
        assert session_id == "match_1"
        assert prompt_locale == "zh-TW"
        assert event["kind"] == "user-text"
        assert event["userText"] == "你是不是在放水？"
        assert event["scoreDiff"] == 3
        return {
            "line": "才没有放水呢。",
            "control": {"mood": "happy"},
            "llm_source": {"provider": "fake"},
        }

    _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)

    handled = await gr_runtime.route_external_stream_message(
        "Lan",
        {"input_type": "text", "data": "你是不是在放水？", "request_id": "req-1"},
    )

    assert handled is True
    assert state["game_external_text_route_active"] is True
    assert state["game_input_mode"] == "text"
    assert state["activation_source"] == "external_text_hijacked_by_game"
    assert mgr.mirrored == [("你是不是在放水？", {
        "metadata": {
            "source": "external_text_route",
            "kind": "soccer",
            "session_id": "match_1",
            "mirror": {
                "kind": "soccer",
                "session_id": "match_1",
                "event": {"memory_enabled": False},
            },
        },
        "request_id": "req-1",
        "input_type": "mirror_text",
        "send_to_frontend": False,
    })]
    assert mgr.user_activity_count == 1
    assert mgr.spoken == []
    assert [output["type"] for output in state["pending_outputs"]] == ["game_external_input", "game_llm_result"]
    assert state["pending_outputs"][0]["meta"]["inputText"] == "你是不是在放水？"
    assert state["pending_outputs"][1]["meta"]["voiceAlreadyHandled"] is False
    assert state["pending_outputs"][1]["result"]["line"] == "才没有放水呢。"
    assert [item["type"] for item in state["game_dialog_log"]] == ["user", "assistant"]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("input_kind", "explicit", "expected_locale", "expected_source"),
    [("text", False, "ja", "render"), ("voice", True, "zh-TW", "session")],
)
async def test_external_route_refreshes_live_locale_before_prompt(
    monkeypatch,
    input_kind,
    explicit,
    expected_locale,
    expected_source,
):
    mgr = _FakeGameRouteManager()
    mgr.user_language = expected_locale
    mgr._user_language_explicit = explicit
    mgr._conversation_render_language = "ja"
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})

    with reset_game_route_state():
        state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
        state["user_language"] = "en"
        state["user_language_source"] = "render"
        prompt_locales = []

        async def fake_run_game_chat(
            game_type,
            session_id,
            event,
            *,
            prompt_locale=None,
            **_route_kwargs,
        ):
            prompt_locales.append(prompt_locale)
            return {"line": "localized", "control": {}, "llm_source": {"provider": "fake"}}

        _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)

        if input_kind == "text":
            handled = await gr_runtime.route_external_stream_message(
                "Lan",
                {"input_type": "text", "data": "localized", "request_id": "locale-refresh"},
            )
        else:
            handled = await gr_runtime.route_external_voice_transcript(
                "Lan", "localized", request_id="locale-refresh",
                game_type="soccer", session_id="match_1",
            )

        assert handled is True
        assert prompt_locales == [expected_locale]
        assert (state["user_language"], state["user_language_source"]) == (
            expected_locale,
            expected_source,
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_external_text_uses_no_memory_input_type_when_game_memory_disabled(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})

    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
    _set_soccer_game_memory_policy(state, enabled=False)

    async def fake_run_game_chat(game_type, session_id, event, **_route_kwargs):
        assert event["kind"] == "user-text"
        assert event["soccerGameMemoryPlayerInteractionEnabled"] is False
        return {"line": "这句只在本局里回应。", "control": {}, "llm_source": {"provider": "fake"}}

    _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)

    handled = await gr_runtime.route_external_stream_message(
        "Lan",
        {"input_type": "text", "data": "这局不要记", "request_id": "req-no-memory"},
    )

    assert handled is True
    assert mgr.mirrored == [("这局不要记", {
        "metadata": {
            "source": "external_text_route",
            "kind": "soccer",
            "session_id": "match_1",
            "mirror": {
                "kind": "soccer",
                "session_id": "match_1",
                "event": {"memory_enabled": False},
            },
        },
        "request_id": "req-no-memory",
        "input_type": "mirror_text",
        "send_to_frontend": False,
    })]
    assert state["pending_outputs"][0]["meta"]["soccerGameMemoryPlayerInteractionEnabled"] is False
    assert state["pending_outputs"][1]["meta"]["soccerGameMemoryPlayerInteractionEnabled"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_external_audio_activates_game_stt_gate(monkeypatch):
    mgr = _FakeGameRouteManager()
    mgr._suspend_independent_voice_input_for_game = AsyncMock()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")

    handled = await gr_runtime.route_external_stream_message("Lan", {"input_type": "audio", "data": [0, 1]})
    handled_again = await gr_runtime.route_external_stream_message("Lan", {"input_type": "audio", "data": [2, 3]})
    for idx in range(40):
        assert await gr_runtime.route_external_stream_message(
            "Lan",
            {"input_type": "audio", "data": [idx]},
        ) is True

    assert handled is True
    assert handled_again is True
    assert state["game_external_voice_route_active"] is True
    assert state["game_input_mode"] == "voice"
    assert state["activation_source"] == "external_voice_hijacked_by_game"
    assert "GAME_VOICE_STT_GATE_ACTIVE" in mgr.statuses[0]
    status_payload = json.loads(mgr.statuses[0])
    assert status_payload["details"] == {
        "game_type": "soccer",
        "session_id": "match_1",
        "lanlan_name": "Lan",
        "capture_owner": "host",
        "transcription_mode": "backend_pending",
        "provider": "",
        "ready": False,
        "stt_provider": "realtime",
        "message": (
            "游戏期间主语音入口已被游戏路由接管。宿主正在按 Core 能力和用户设置选择"
            "原生或独立 STT；最终转写交给游戏路由，普通 chat LLM 输出在 SessionManager"
            " 层被静音（session takeover）。"
        ),
    }
    assert len(mgr.statuses) == 1
    assert len(state["game_input_activation_log"]) == 1
    assert state["game_input_activation_log"][0]["source"] == "external_voice_hijacked_by_game"
    assert state["game_input_activation_log"][0]["mode"] == "voice"
    assert state["game_input_activation_log"][0]["detail"] == {}
    mgr._suspend_independent_voice_input_for_game.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_external_voice_transcript_to_game_llm(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
    state["user_language"] = "zh-TW"
    state["user_language_source"] = "session"

    async def fake_run_game_chat(
        game_type, session_id, event, *, prompt_locale=None, **_route_kwargs
    ):
        assert game_type == "soccer"
        assert session_id == "match_1"
        assert prompt_locale == "zh-TW"
        assert event["kind"] == "user-voice"
        assert event["userVoiceText"] == "我马上要进球了"
        return {
            "line": "那我可要认真防你啦。",
            "control": {"difficulty": "max"},
            "llm_source": {"provider": "fake"},
        }

    _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)

    handled = await gr_runtime.route_external_voice_transcript(
        "Lan",
        "我马上要进球了",
        request_id="voice-1",
        game_type="soccer",
        session_id="match_1",
    )

    assert handled is True
    assert state["game_external_voice_route_active"] is True
    assert state["game_input_mode"] == "voice"
    assert mgr.mirrored == [("我马上要进球了", {
        "metadata": {
            "source": "external_voice_route",
            "kind": "soccer",
            "session_id": "match_1",
            "mirror": {
                "kind": "soccer",
                "session_id": "match_1",
                "event": {"memory_enabled": False},
            },
        },
        "request_id": "voice-1",
        "input_type": "mirror_voice_transcript",
        "send_to_frontend": True,
    })]
    assert mgr.user_activity_count == 1
    assert mgr.spoken == []
    assert [output["type"] for output in state["pending_outputs"]] == ["game_external_input", "game_llm_result"]
    assert state["pending_outputs"][0]["meta"]["inputText"] == "我马上要进球了"
    assert state["pending_outputs"][1]["meta"]["kind"] == "user-voice"
    assert state["pending_outputs"][1]["meta"]["hasUserSpeech"] is True
    assert "skipOrdinaryMemory" not in state["pending_outputs"][1]["meta"]
    assert state["pending_outputs"][1]["meta"]["voiceAlreadyHandled"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_external_route_drops_a_superseded_chat_result_without_post_side_effects(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})

    with reset_game_route_state():
        state = gr_runtime._activate_game_route(
            "example-game", "reused-session", "Lan"
        )
        state["_sdk_route_instance_id"] = "route-A"
        _gr_patch_all(
            monkeypatch,
            "_run_game_chat",
            AsyncMock(return_value={
                "line": "",
                "control": {},
                "skipped": "route_superseded",
            }),
        )

        handled = await gr_runtime.route_external_voice_transcript(
            "Lan",
            "stale input",
            request_id="voice-stale-result",
            game_type="example-game",
            session_id="reused-session",
            sdk_route_instance_id="route-A",
        )

    assert handled is True
    assert [item["type"] for item in state["game_dialog_log"]] == ["user"]
    assert [item["type"] for item in state["pending_outputs"]] == [
        "game_external_input"
    ]
    assert not any("GAME_ROUTE_LLM_FAILED" in status for status in mgr.statuses)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_external_voice_transcript_rejects_a_superseded_route_generation(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})

    with reset_game_route_state():
        original = gr_runtime._activate_game_route(
            "example-game", "reused-session", "Lan"
        )
        original["_sdk_route_instance_id"] = "route-A"
        replacement = gr_runtime._activate_game_route(
            "example-game", "reused-session", "Lan"
        )
        replacement["_sdk_route_instance_id"] = "route-B"

        handled = await gr_runtime.route_external_voice_transcript(
            "Lan",
            "this belongs to route A",
            request_id="voice-route-a",
            game_type="example-game",
            session_id="reused-session",
            expected_state=original,
        )

    assert handled is False
    assert replacement["pending_outputs"] == []
    assert mgr.mirrored == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stream_audio_transcript_preserves_the_source_route_generation(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    state = gr_runtime._activate_game_route(
        "example-game", "reused-session", "Lan"
    )
    state["_sdk_route_instance_id"] = "route-A"
    captured = {}

    async def fake_route_external_voice_transcript(
        lanlan_name,
        transcript,
        *,
        request_id=None,
        game_type=None,
        session_id=None,
        expected_state=None,
    ):
        captured.update({
            "lanlan_name": lanlan_name,
            "transcript": transcript,
            "request_id": request_id,
            "game_type": game_type,
            "session_id": session_id,
            "expected_state": expected_state,
        })
        return True

    _gr_patch_all(
        monkeypatch,
        "route_external_voice_transcript",
        fake_route_external_voice_transcript,
    )

    handled = await gr_runtime.route_external_stream_message(
        "Lan",
        {
            "input_type": "audio",
            "transcript": "source-bound transcript",
            "request_id": "voice-route-a",
        },
        expected_state=state,
    )

    assert handled is True
    assert captured == {
        "lanlan_name": "Lan",
        "transcript": "source-bound transcript",
        "request_id": "voice-route-a",
        "game_type": "example-game",
        "session_id": "reused-session",
        "expected_state": state,
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_voice_transcript_endpoint_rejects_a_stale_route_instance(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    route_voice = AsyncMock(return_value=True)
    _gr_patch_all(monkeypatch, "route_external_voice_transcript", route_voice)

    with reset_game_route_state():
        state = gr_runtime._activate_game_route(
            "example-game", "reused-session", "Lan"
        )
        state["_sdk_route_instance_id"] = "route-B"
        result = await gr_runtime.game_route_voice_transcript(
            "example-game",
            _FakeRequest({
                "lanlan_name": "Lan",
                "session_id": "reused-session",
                "sdk_route_instance_id": "route-A",
                "request_id": "voice-route-a",
                "transcript": "stale source",
            }),
        )

    assert result["ok"] is False
    assert result["handled"] is False
    assert result["reason"] == "route_instance_id_mismatch"
    route_voice.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_external_voice_transcript_dedup_idempotent_on_request_id(monkeypatch):
    """The dedup must be a true idempotency check on request_id, not a
    "last seen" single slot:
      - voice-1, voice-2 (different shouts) both deliver
      - voice-1 retransmitted → still squashed even after voice-2 was the
        most recent (out-of-order replay protection — the original
        single-slot version would let this through because last==voice-2)
    """
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    gr_runtime._activate_game_route("soccer", "match_1", "Lan")

    chat_calls = []

    async def fake_run_game_chat(game_type, session_id, event, **_route_kwargs):
        chat_calls.append((event["userVoiceText"], event.get("requestId")))
        return {"line": "好。", "control": {}, "llm_source": {"provider": "fake"}}

    _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)

    handled1 = await gr_runtime.route_external_voice_transcript(
        "Lan", "再来", request_id="voice-1", game_type="soccer", session_id="match_1",
    )
    handled2 = await gr_runtime.route_external_voice_transcript(
        "Lan", "再来", request_id="voice-2", game_type="soccer", session_id="match_1",
    )
    # Out-of-order retry of voice-1 after voice-2 — must still be squashed.
    handled3 = await gr_runtime.route_external_voice_transcript(
        "Lan", "再来", request_id="voice-1", game_type="soccer", session_id="match_1",
    )
    # Same request_id retransmitted right away — also squashed.
    handled4 = await gr_runtime.route_external_voice_transcript(
        "Lan", "再来", request_id="voice-2", game_type="soccer", session_id="match_1",
    )

    assert handled1 is True
    assert handled2 is True
    assert handled3 is True
    assert handled4 is True
    assert [call[0] for call in chat_calls] == ["再来", "再来"]
    assert len(mgr.mirrored) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_external_voice_transcript_dedup_ttl_evicts(monkeypatch):
    """After the TTL window passes, the same request_id is allowed to
    deliver again (it isn't "stuck" in the dedup set forever)."""
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    gr_runtime._activate_game_route("soccer", "match_1", "Lan")

    async def fake_run_game_chat(game_type, session_id, event, **_route_kwargs):
        return {"line": "好。", "control": {}, "llm_source": {"provider": "fake"}}

    _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)

    fake_now = {"t": 10_000.0}
    # The dedup TTL bookkeeping this test drives lives in
    # main_routers.game_router.runtime (_route_external_transcript_to_game), so
    # the fake clock belongs on that module.
    patch_module_clock(monkeypatch, gr_runtime, time=lambda: fake_now["t"])

    h1 = await gr_runtime.route_external_voice_transcript(
        "Lan", "射门", request_id="voice-x", game_type="soccer", session_id="match_1",
    )
    fake_now["t"] += 0.1
    h2 = await gr_runtime.route_external_voice_transcript(
        "Lan", "射门", request_id="voice-x", game_type="soccer", session_id="match_1",
    )
    fake_now["t"] += 60.0
    h3 = await gr_runtime.route_external_voice_transcript(
        "Lan", "射门", request_id="voice-x", game_type="soccer", session_id="match_1",
    )
    assert h1 is True and h2 is True and h3 is True
    # voice-x at base and at base+60.1s both deliver (TTL=30s evicted the
    # first entry by then); the in-window retry at base+0.1s is squashed.
    assert len(mgr.mirrored) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_external_voice_transcript_dedup_membership_check_before_lru_cap(
    monkeypatch,
):
    """If the LRU cap is enforced BEFORE the membership check, the
    oldest still-in-window entry can be evicted right before its retry
    arrives — breaking request-id idempotency at >=64 unique-id high
    throughput. Verify membership is checked first."""
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    gr_runtime._activate_game_route("soccer", "match_1", "Lan")

    async def fake_run_game_chat(game_type, session_id, event, **_route_kwargs):
        return {"line": "好。", "control": {}, "llm_source": {"provider": "fake"}}

    _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)

    # Lower the cap for the test so we don't have to spin 64 unique ids.
    _gr_patch_all(monkeypatch, "_EXTERNAL_VOICE_DEDUP_MAX_ENTRIES", 4)

    # Fill the dedup set to capacity with 4 distinct request_ids; the
    # very first one (voice-1) is the oldest entry.
    for i in range(1, 5):
        await gr_runtime.route_external_voice_transcript(
            "Lan", "上场", request_id=f"voice-{i}",
            game_type="soccer", session_id="match_1",
        )
    assert len(mgr.mirrored) == 4

    # Now retry voice-1. It IS in the dedup set; the LRU cap (4) IS
    # already at the limit. If the cap is enforced before the membership
    # check, voice-1 (the oldest) is evicted, then idempotency_key not in
    # seen_ids → deliver again. The fix: check membership first.
    handled_retry = await gr_runtime.route_external_voice_transcript(
        "Lan", "上场", request_id="voice-1",
        game_type="soccer", session_id="match_1",
    )
    assert handled_retry is True
    assert len(mgr.mirrored) == 4, "voice-1 retry must be squashed even when cap is full"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_external_voice_transcript_dedup_no_request_id_fallback_window(
    monkeypatch,
):
    """The no-request_id fallback uses a wall-clock 1.0s window (not an
    int(now)-second bucket), so close pairs that straddle a second
    boundary like 0.95s → 1.05s are correctly squashed."""
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    gr_runtime._activate_game_route("soccer", "match_1", "Lan")

    async def fake_run_game_chat(game_type, session_id, event, **_route_kwargs):
        return {"line": "好。", "control": {}, "llm_source": {"provider": "fake"}}

    _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)

    fake_now = {"t": 1000.95}
    # Same as the TTL test above: the no-request_id 1.0s window is computed in
    # main_routers.game_router.runtime (_route_external_transcript_to_game), so
    # scope the fake clock there.
    patch_module_clock(monkeypatch, gr_runtime, time=lambda: fake_now["t"])

    h1 = await gr_runtime.route_external_voice_transcript(
        "Lan", "再来", request_id=None,
        game_type="soccer", session_id="match_1",
    )
    fake_now["t"] = 1001.05  # crossed second boundary, but only +0.10s
    h2 = await gr_runtime.route_external_voice_transcript(
        "Lan", "再来", request_id=None,
        game_type="soccer", session_id="match_1",
    )
    fake_now["t"] = 1002.10  # +1.05s from first → outside 1.0s window
    h3 = await gr_runtime.route_external_voice_transcript(
        "Lan", "再来", request_id=None,
        game_type="soccer", session_id="match_1",
    )
    assert h1 is True and h2 is True and h3 is True
    # h1 delivered, h2 squashed (within 1s), h3 delivered (outside 1s)
    assert len(mgr.mirrored) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_heartbeat_refreshes_last_state(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
    before = state["last_heartbeat_at"]

    result = await gr_runtime.game_route_heartbeat(
        "soccer",
        _FakeRequest({
            "lanlan_name": "Lan",
            "session_id": "match_1",
            "currentState": {"score": {"player": 3, "ai": 2}},
            "gameStarted": True,
            "gameStartedElapsedMs": 15_000,
        }),
    )

    assert result["ok"] is True
    assert result["active"] is True
    assert state["last_heartbeat_at"] >= before
    assert state["last_state"] == {"score": {"player": 3, "ai": 2}}
    assert result["heartbeat_timeout_seconds"] == gr_runtime._GAME_ROUTE_HEARTBEAT_TIMEOUT_SECONDS
    assert state["page_visible"] is True
    assert state["visibility_state"] == "visible"
    assert state["game_started"] is True
    assert state["game_started_elapsed_ms"] == 15_000


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_heartbeat_rechecks_authoritative_state_after_route_lock(
    monkeypatch,
):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    with reset_game_route_state():
        state = gr_runtime._activate_game_route(
            "neutral-sdk-game",
            "session-1",
            "Lan",
        )
        state["_sdk_route_instance_id"] = "route-A"
        previous_heartbeat = state["last_heartbeat_at"]
        route_lock = gr_runtime._get_route_lock("Lan", "neutral-sdk-game")
        await route_lock.acquire()
        try:
            heartbeat_task = asyncio.create_task(
                gr_runtime.game_route_heartbeat(
                    "neutral-sdk-game",
                    _FakeRequest({
                        "lanlan_name": "Lan",
                        "session_id": "session-1",
                        "sdk_route_instance_id": "route-A",
                    }),
                )
            )
            await asyncio.sleep(0)
            assert heartbeat_task.done() is False
            state["game_route_active"] = False
        finally:
            route_lock.release()

        result = await heartbeat_task

        assert result["active"] is False
        assert state["last_heartbeat_at"] == previous_heartbeat


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("route_input", ["heartbeat", "voice"])
async def test_route_inputs_refresh_render_locale_without_overriding_explicit(
    monkeypatch,
    route_input,
):
    manager = _LocaleTrackingManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": manager})
    voice_router = AsyncMock(return_value=True)
    if route_input == "voice":
        _gr_patch_all(monkeypatch, "route_external_voice_transcript", voice_router)

    with reset_game_route_state():
        state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
        state["user_language"] = "en"
        state["user_language_source"] = "render"
        cases = [
            ({"render_language": "ja"}, ("ja", "render"), []),
            (
                {"i18n_language": "zh-TW", "render_language": "ko"},
                ("zh-TW", "request"), ["zh-TW"],
            ),
            ({"render_language": "pt"}, ("zh-TW", "session"), ["zh-TW"]),
        ]
        for index, (languages, route_locale, updates) in enumerate(cases):
            payload = {"lanlan_name": "Lan", "session_id": "match_1", **languages}
            if route_input == "voice":
                payload.update(request_id=f"voice-{index}", transcript="localized")
                result = await gr_runtime.game_route_voice_transcript(
                    "soccer", _FakeRequest(payload),
                )
                assert result["handled"] is True
            else:
                result = await gr_runtime.game_route_heartbeat(
                    "soccer", _FakeRequest(payload),
                )
                assert result["active"] is True
            assert (state["user_language"], state["user_language_source"]) == route_locale
            assert manager.language_updates == updates

    if route_input == "voice":
        assert voice_router.await_count == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_heartbeat_refreshes_enabled_debug_log_idle_ttl(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
    game_log.enable_game_session_debug_log("soccer", "match_1", lanlan_name="Lan")
    entry = game_log.find_game_session_debug_log("match_1", "soccer")
    assert entry is not None
    stale_updated_at = game_log.time.time() - (game_log.GAME_SESSION_DEBUG_ACTIVE_IDLE_TTL_SECONDS / 2)
    entry["updated_at"] = stale_updated_at

    result = await gr_runtime.game_route_heartbeat(
        "soccer",
        _FakeRequest({
            "lanlan_name": "Lan",
            "session_id": "match_1",
        }),
    )

    assert result["ok"] is True
    assert result["active"] is True
    assert state["last_heartbeat_at"] <= entry["updated_at"]
    assert entry["updated_at"] > stale_updated_at


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_heartbeat_does_not_create_debug_log_when_disabled(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    gr_runtime._activate_game_route("soccer", "match_1", "Lan")

    result = await gr_runtime.game_route_heartbeat(
        "soccer",
        _FakeRequest({
            "lanlan_name": "Lan",
            "session_id": "match_1",
        }),
    )

    assert result["ok"] is True
    assert result["active"] is True
    assert game_log.find_game_session_debug_log("match_1", "soccer") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_heartbeat_records_hidden_visibility(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")

    result = await gr_runtime.game_route_heartbeat(
        "soccer",
        _FakeRequest({
            "lanlan_name": "Lan",
            "session_id": "match_1",
            "pageVisible": False,
            "visibilityState": "hidden",
        }),
    )

    assert result["ok"] is True
    assert result["active"] is True
    assert result["heartbeat_timeout_seconds"] == gr_runtime._GAME_ROUTE_HIDDEN_HEARTBEAT_TIMEOUT_SECONDS
    assert state["page_visible"] is False
    assert state["visibility_state"] == "hidden"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_heartbeat_timeout_finalize_archives_and_closes_session(monkeypatch):
    fake_session = type("FakeSession", (), {"close": AsyncMock()})()
    _put_game_session("Lan", "soccer", "match_1", fake_session)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
    game_log.enable_game_session_debug_log("soccer", "match_1", lanlan_name="Lan")
    _set_soccer_game_memory_policy(state, enabled=True)
    _mark_game_started(state)

    submitted = []

    async def fake_submit(archive):
        submitted.append(archive)
        return {"ok": True, "status": "cached", "count": 1}

    _gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", fake_submit)

    result = await gr_runtime._finalize_game_route_state(
        state,
        reason="heartbeat_timeout",
        close_game_session=True,
    )

    assert state["game_route_active"] is False
    assert state["heartbeat_enabled"] is False
    assert state["exit_reason"] == "heartbeat_timeout"
    assert result["game_session_closed"] is True
    assert result["archive"]["exit_reason"] == "heartbeat_timeout"
    assert result["archive_memory"] == {"ok": True, "status": "cached", "count": 1}
    assert result["debug_log_ended"] is True
    assert submitted[0]["exit_reason"] == "heartbeat_timeout"
    debug_log = game_log.find_game_session_debug_log("match_1", "soccer")
    assert debug_log is not None
    assert debug_log["status"] == "ended"
    assert [item["event"] for item in debug_log["entries"]] == ["session_ended"]
    fake_session.close.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_finalize_continues_when_voice_input_resume_fails(monkeypatch):
    mgr = _FakeGameRouteManager()
    mgr._resume_independent_voice_input_after_game = AsyncMock(
        side_effect=RuntimeError("resume failed")
    )
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(
        monkeypatch,
        "_submit_game_archive_to_memory",
        AsyncMock(return_value={"ok": True, "status": "cached"}),
    )
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
    _set_soccer_game_memory_policy(state, enabled=True)
    _mark_game_started(state)

    result = await gr_runtime._finalize_game_route_state(
        state,
        reason="route_end",
        close_game_session=False,
    )

    assert result["archive_memory"] == {"ok": True, "status": "cached"}
    assert result["realtime_restore"] == {
        "attempted": True,
        "ok": False,
        "reason": "voice_input_resume_failed",
    }
    status = json.loads(mgr.statuses[-1])
    assert status["code"] == "GAME_ROUTE_ENDED"
    assert status["details"]["realtime_restore"] == result["realtime_restore"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_finalize_skips_voice_resume_when_lease_stayed_with_core(monkeypatch):
    # realtime-STT 游戏：前端保持普通麦克风上传，租约 owner 全程停留在
    # core。退出时不得调用 resume——core->core 空转换会 bump transition
    # generation 并清空在途麦克风 PCM（codex P2）。
    mgr = _FakeGameRouteManager()
    mgr._voice_lease_owner = "core"
    mgr._resume_independent_voice_input_after_game = AsyncMock()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(
        monkeypatch,
        "_submit_game_archive_to_memory",
        AsyncMock(return_value={"ok": True, "status": "cached"}),
    )
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
    _set_soccer_game_memory_policy(state, enabled=True)
    _mark_game_started(state)

    result = await gr_runtime._finalize_game_route_state(
        state,
        reason="route_end",
        close_game_session=False,
    )

    mgr._resume_independent_voice_input_after_game.assert_not_awaited()
    assert result["realtime_restore"] == {
        "attempted": False,
        "ok": True,
        "reason": "voice_lease_not_taken",
    }
    status = json.loads(mgr.statuses[-1])
    assert status["code"] == "GAME_ROUTE_ENDED"
    assert status["details"]["realtime_restore"] == result["realtime_restore"]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("lease_owner", ["game", "none"])
async def test_finalize_resumes_voice_when_lease_left_core(monkeypatch, lease_owner):
    # 浏览器 STT gate 游戏：租约被游戏接管（或接管后玩家中途关麦变 none）。
    # 只有 game_release 能把 SUSPENDED 的 runtime 拉回来，退出时必须 resume。
    mgr = _FakeGameRouteManager()
    mgr._voice_lease_owner = lease_owner
    mgr._resume_independent_voice_input_after_game = AsyncMock()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(
        monkeypatch,
        "_submit_game_archive_to_memory",
        AsyncMock(return_value={"ok": True, "status": "cached"}),
    )
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
    _set_soccer_game_memory_policy(state, enabled=True)
    _mark_game_started(state)

    result = await gr_runtime._finalize_game_route_state(
        state,
        reason="route_end",
        close_game_session=False,
    )

    mgr._resume_independent_voice_input_after_game.assert_awaited_once()
    assert result["realtime_restore"] == {
        "attempted": True,
        "ok": True,
        "reason": "voice_input_resumed",
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_finalize_resumes_voice_when_core_owner_but_lifecycle_suspended(monkeypatch):
    # 边界：浏览器 STT gate 中途失败回退普通麦克风，owner 已回 core 但
    # lifecycle 仍卡在 SUSPENDED（lease_sync 不触发 resume）。退出时仍要
    # 补一次 game_release，否则本会话语音永久失效。
    mgr = _FakeGameRouteManager()
    mgr._voice_lease_owner = "core"
    mgr._asr_runtime = SimpleNamespace(
        _asr_lifecycle=SimpleNamespace(
            snapshot=SimpleNamespace(state=SimpleNamespace(value="suspended")),
        ),
    )
    mgr._resume_independent_voice_input_after_game = AsyncMock()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(
        monkeypatch,
        "_submit_game_archive_to_memory",
        AsyncMock(return_value={"ok": True, "status": "cached"}),
    )
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
    _set_soccer_game_memory_policy(state, enabled=True)
    _mark_game_started(state)

    result = await gr_runtime._finalize_game_route_state(
        state,
        reason="route_end",
        close_game_session=False,
    )

    mgr._resume_independent_voice_input_after_game.assert_awaited_once()
    assert result["realtime_restore"]["attempted"] is True
    assert result["realtime_restore"]["reason"] == "voice_input_resumed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_heartbeat_timeout_ignores_recent_activity_and_finalizes(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    now = gr_runtime.time.time()
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
    state["last_heartbeat_at"] = now - gr_runtime._GAME_ROUTE_HEARTBEAT_TIMEOUT_SECONDS - 1.0
    state["last_activity"] = now

    assert gr_runtime._route_heartbeat_expired(state, now) is True

    result = await gr_runtime._finalize_game_route_state(
        state,
        reason="heartbeat_timeout",
        close_game_session=False,
    )

    assert state["game_route_active"] is False
    assert state["heartbeat_enabled"] is False
    assert state["exit_reason"] == "heartbeat_timeout"
    assert result["archive"]["exit_reason"] == "heartbeat_timeout"


@pytest.mark.unit
def test_heartbeat_timeout_keeps_fresh_heartbeat_despite_old_activity():
    now = gr_runtime.time.time()
    state = {
        "created_at": now - 600.0,
        "last_heartbeat_at": now - 1.0,
        "last_activity": now - gr_runtime._GAME_ROUTE_HEARTBEAT_TIMEOUT_SECONDS - 20.0,
        "page_visible": True,
    }

    assert gr_runtime._route_heartbeat_expired(state, now) is False


@pytest.mark.unit
def test_heartbeat_timeout_uses_created_at_before_first_heartbeat():
    now = gr_runtime.time.time()
    timeout = gr_runtime._GAME_ROUTE_HEARTBEAT_TIMEOUT_SECONDS
    state = {
        "created_at": now - timeout + 1.0,
        "last_activity": now,
        "page_visible": True,
    }

    assert gr_runtime._route_heartbeat_expired(state, now) is False

    state["created_at"] = now - timeout - 1.0
    assert gr_runtime._route_heartbeat_expired(state, now) is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_heartbeat_timeout_without_start_skips_only_game_archive_memory(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
    gr_runtime._append_game_dialog(state, {
        "type": "assistant",
        "source": "opening_line",
        "line": "准备好了吗",
    })

    async def fake_submit(_archive):
        raise AssertionError("pre-start heartbeat timeout should not write game archive memory")

    _gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", fake_submit)

    result = await gr_runtime._finalize_game_route_state(
        state,
        reason="heartbeat_timeout",
        close_game_session=False,
    )

    assert result["archive_memory"]["status"] == "skipped"
    assert result["archive_memory"]["reason"] == "game_not_started"
    assert result["archive"]["memory_skipped"] is True
    assert result["archive"]["last_full_dialogues"][0]["line"] == "准备好了吗"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_memory_disabled_skips_archive_memory(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
    _set_soccer_game_memory_policy(state, enabled=True)
    _mark_game_started(state)
    _set_soccer_game_memory_policy(state, enabled=False)
    gr_runtime._append_game_dialog(state, {
        "type": "user",
        "source": "external_text_route",
        "text": "这局别进记忆",
    })

    async def fake_submit(_archive):
        raise AssertionError("disabled game memory should not submit archive payload")

    _gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", fake_submit)

    result = await gr_runtime._finalize_game_route_state(
        state,
        reason="manual",
        close_game_session=False,
    )

    assert result["archive_memory"]["status"] == "skipped"
    assert result["archive_memory"]["reason"] == "game_memory_archive_disabled"
    assert result["archive"]["game_memory_enabled"] is False
    assert result["archive"]["memory_skipped"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speak_uses_manager_project_tts(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    with reset_game_route_state():
        state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
        result = await gr_runtime.game_project_speak(
            "soccer",
            _FakeRequest({
                "line": "换我进攻了",
                "session_id": "match_1",
                "request_id": "req-2",
                "render_language": "ja",
                "sdk_speech_correlation_id": "  sdk-correlation-1  ",
            }),
        )

    assert result["ok"] is True
    assert state["_sdk_active_speech_correlation_ids"] == ["sdk-correlation-1"]
    assert result["method"] == "project_tts"
    assert result["voice_source"]["provider"] == "project_tts"
    assert mgr.render_language_at_mirror == ["ja"]
    spoken_event = mgr.spoken[0][1]["metadata"]["mirror"]["event"]
    assert spoken_event["soccer_game_memory_enabled"] is False
    assert mgr.spoken == [("换我进攻了", {
        "metadata": {
            "source": "game_route",
            "kind": "soccer",
            "session_id": "match_1",
            "mirror": {"kind": "soccer", "session_id": "match_1", "event": spoken_event},
        },
        "request_id": "req-2",
        "mirror_text": True,
        "emit_turn_end_after": True,
        "interrupt_audio": False,
        "playback_gain": 1.0,
        "reuse_synthesized_audio": False,
        "wait_for_audio_completion": False,
        "audio_completion_timeout": 45.0,
        "speech_correlation_id": "sdk-correlation-1",
    })]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speak_can_skip_text_mirror_for_frontend_arbiter(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    with reset_game_route_state():
        gr_runtime._activate_game_route("soccer", "match_1", "Lan")
        result = await gr_runtime.game_project_speak(
            "soccer",
            _FakeRequest({
                "line": "只播放语音",
                "session_id": "match_1",
                "request_id": "req-voice",
                "mirror_text": False,
                "emit_turn_end": False,
            }),
        )

    assert result["ok"] is True
    spoken_event = mgr.spoken[0][1]["metadata"]["mirror"]["event"]
    assert spoken_event["soccer_game_memory_enabled"] is False
    assert mgr.spoken == [("只播放语音", {
        "metadata": {
            "source": "game_route",
            "kind": "soccer",
            "session_id": "match_1",
            "mirror": {"kind": "soccer", "session_id": "match_1", "event": spoken_event},
        },
        "request_id": "req-voice",
        "mirror_text": False,
        "emit_turn_end_after": False,
        "interrupt_audio": False,
        "playback_gain": 1.0,
        "reuse_synthesized_audio": False,
        "wait_for_audio_completion": False,
        "audio_completion_timeout": 45.0,
        "speech_correlation_id": "",
    })]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speak_forwards_interrupt_audio(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    with reset_game_route_state():
        gr_runtime._activate_game_route("soccer", "match_1", "Lan")
        result = await gr_runtime.game_project_speak(
            "soccer",
            _FakeRequest({
                "line": "先听我说完",
                "session_id": "match_1",
                "request_id": "req-interrupt",
                "mirror_text": False,
                "emit_turn_end": False,
                "interrupt_audio": True,
            }),
        )

    assert result["ok"] is True
    spoken_event = mgr.spoken[0][1]["metadata"]["mirror"]["event"]
    assert spoken_event["soccer_game_memory_enabled"] is False
    assert mgr.spoken == [("先听我说完", {
        "metadata": {
            "source": "game_route",
            "kind": "soccer",
            "session_id": "match_1",
            "mirror": {"kind": "soccer", "session_id": "match_1", "event": spoken_event},
        },
        "request_id": "req-interrupt",
        "mirror_text": False,
        "emit_turn_end_after": False,
        "interrupt_audio": True,
        "playback_gain": 1.0,
        "reuse_synthesized_audio": False,
        "wait_for_audio_completion": False,
        "audio_completion_timeout": 45.0,
        "speech_correlation_id": "",
    })]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speak_forwards_synthesized_audio_reuse_opt_in(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    with reset_game_route_state():
        gr_runtime._activate_game_route("soccer", "match_1", "Lan")
        result = await gr_runtime.game_project_speak(
            "soccer",
            _FakeRequest({
                "line": "重复使用这句",
                "session_id": "match_1",
                "reuse_synthesized_audio": True,
            }),
        )

    assert result["ok"] is True
    assert mgr.spoken[0][1]["reuse_synthesized_audio"] is True
    # Audio reuse does not imply blocking: that is its own opt-in below.
    assert mgr.spoken[0][1]["wait_for_audio_completion"] is False
    assert mgr.spoken[0][1]["audio_completion_timeout"] == 45.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speak_blocks_on_playback_only_when_asked(monkeypatch):
    """Blocking until playback finishes is opt-in per request.

    The pre-SDK contract for this endpoint is "return once the line is queued",
    and the built-in callers depend on it: badminton_demo.html posts /speak with
    a 3.5s client timeout, so a server that always waited for the whole
    utterance would fail every badminton line into its browser-TTS fallback.
    """
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    with reset_game_route_state():
        gr_runtime._activate_game_route("soccer", "match_1", "Lan")
        result = await gr_runtime.game_project_speak(
            "soccer",
            _FakeRequest({
                "line": "等我说完",
                "session_id": "match_1",
                "wait_for_audio_completion": True,
            }),
        )

    assert result["ok"] is True
    assert mgr.spoken[0][1]["wait_for_audio_completion"] is True
    assert mgr.spoken[0][1]["audio_completion_timeout"] == 45.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_external_transcript_stops_when_the_route_is_replaced_mid_mirror(monkeypatch):
    """A replacement route must not inherit the previous route's side effects.

    ``mirror_user_input`` awaits the frontend websocket, so route B can start and
    finalize route A's state while A is blocked there. Everything after that
    await is unrecoverable: ``send_user_activity()`` is manager-wide and would
    interrupt whatever B is currently speaking, and the dialog/output appends
    land on a route that is already finished. The ownership check further down
    only guards the LLM call and cannot undo either.
    """
    class SwappingMirrorManager(_FakeGameRouteManager):
        def __init__(self):
            super().__init__()
            self.swap = None

        async def mirror_user_input(self, text, **kwargs):
            await super().mirror_user_input(text, **kwargs)
            if self.swap is not None:
                self.swap()
                self.swap = None

    mgr = SwappingMirrorManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    async def unexpected_chat(*_args, **_kwargs):
        raise AssertionError("the superseded route still reached the game LLM")

    _gr_patch_all(monkeypatch, "_run_game_chat", unexpected_chat)

    with reset_game_route_state():
        state = gr_runtime._activate_game_route("example-game", "route-A", "Lan")
        dialog_before = len(state.get("game_dialog_log") or [])
        outputs_before = len(state.get("pending_outputs") or [])

        # While the mirror is awaiting, a replacement route takes the slot.
        def replace_route():
            gr_runtime._activate_game_route("example-game", "route-B", "Lan")

        mgr.swap = replace_route

        routed = await gr_runtime._route_external_transcript_to_game(
            "Lan",
            state,
            "说给 A 听的话",
            source="test",
            mode="voice",
            kind="user-voice",
            request_id="req-1",
        )

    # Consumed, not retried elsewhere -- the same answer supersession already
    # gets once the LLM call is skipped.
    assert routed is True
    assert mgr.user_activity_count == 0, (
        "a superseded route interrupted the replacement route's speech"
    )
    assert len(state.get("game_dialog_log") or []) == dialog_before
    assert len(state.get("pending_outputs") or []) == outputs_before


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speak_applies_render_language_under_the_speech_lock(monkeypatch):
    """Overlapping speaks must not synthesize under each other's locale.

    ``set_render_language()`` mutates the shared session manager. Applying it
    before the per-character speech lock lets a second request retarget the
    locale while the first is still queued, so the first synthesizes in the
    wrong language -- and ``game_speech_audio_cache_identity()`` keys on the
    same field, so the wrong pronunciation can be cached and replayed later.
    """
    class BlockingSpeechManager(_FakeGameRouteManager):
        def __init__(self):
            super().__init__()
            self.first_speech_started = asyncio.Event()
            self.release_first_speech = asyncio.Event()

        async def mirror_assistant_speech(self, line, **kwargs):
            self.render_language_at_mirror.append(
                getattr(self, "_conversation_render_language", None)
            )
            if not self.first_speech_started.is_set():
                self.first_speech_started.set()
                await self.release_first_speech.wait()
            self.spoken.append((line, kwargs))
            return {"ok": True, "audio_sent": True, "speech_id": "sid"}

    mgr = BlockingSpeechManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    with reset_game_route_state():
        gr_runtime._activate_game_route("example-game", "match_1", "Lan")
        first = asyncio.create_task(gr_runtime.game_project_speak(
            "example-game",
            _FakeRequest({
                "line": "日本語の台詞",
                "session_id": "match_1",
                "render_language": "ja-JP",
            }),
        ))
        await asyncio.wait_for(mgr.first_speech_started.wait(), timeout=2.0)

        # A second request arrives with a different locale while the first is
        # still synthesizing.
        second = asyncio.create_task(gr_runtime.game_project_speak(
            "example-game",
            _FakeRequest({
                "line": "English line",
                "session_id": "match_1",
                "render_language": "en-US",
            }),
        ))
        await asyncio.sleep(0.05)
        mgr.release_first_speech.set()
        await asyncio.wait_for(first, timeout=2.0)
        await asyncio.wait_for(second, timeout=2.0)

    # Locales are stored normalized ("ja-JP" -> "ja").
    assert mgr.render_language_at_mirror[0] == "ja", (
        "the first speech synthesized under the second request's locale"
    )
    assert mgr.render_language_at_mirror[-1] == "en"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speech_preload_is_silent_and_deduplicates_lines(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    result = await gr_runtime.game_project_speech_preload(
        "soccer",
        _FakeRequest({
            "lines": ["  开球了  ", "开球了", "看我的"],
            "session_id": "match_1",
            "render_language": "ja",
            "i18n_language": "ja",
        }),
    )

    assert result["ok"] is True
    assert result["method"] == "project_tts_preload"
    assert mgr.preloaded == [["开球了", "看我的"]]
    # The request locale reaches the batch as an argument and never touches the
    # shared session field: a batch runs for tens of seconds behind its own
    # lock, and the audio cache identity is derived from that field, so any
    # concurrent writer would invalidate audio this batch already synthesized.
    assert mgr.preload_render_languages == ["ja"]
    # And the healing write reaches the shared session only once the batch is
    # actually about to run -- every refusal above it (unavailable / superseded /
    # busy) must leave the character's language alone.
    assert mgr.language_updates == ["ja"]
    # Observed from inside the batch: the shared field is still unwritten.
    assert mgr.render_language_at_mirror == [None]
    assert getattr(mgr, "_conversation_render_language", None) is None
    assert mgr.spoken == []
    assert mgr.assistant_mirrored == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speech_preload_absorbs_language_on_the_route_locked_path(monkeypatch):
    """The route-locked branch has its own absorb, and it is the one that can be refused.

    With an active route the preload takes the locked branch, where
    `route_superseded` and `preload_busy` are still ahead of it. The healing
    write must land there too -- and only there -- or the branch either never
    heals or heals on a refusal.
    """
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    with reset_game_route_state():
        gr_runtime._activate_game_route("soccer", "match_1", "Lan")
        result = await gr_runtime.game_project_speech_preload(
            "soccer",
            _FakeRequest({
                "lines": ["开球了"],
                "session_id": "match_1",
                "lanlan_name": "Lan",
                "render_language": "ja",
                "i18n_language": "ja",
            }),
        )

    assert result["ok"] is True
    assert mgr.preloaded == [["开球了"]]
    assert mgr.preload_render_languages == ["ja"]
    assert mgr.language_updates == ["ja"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speech_preload_rejects_a_stale_active_route_generation(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    with reset_game_route_state():
        state = gr_runtime._activate_game_route("example-game", "reused-session", "Lan")
        state["_sdk_route_instance_id"] = "route-B"
        result = await gr_runtime.game_project_speech_preload(
            "example-game",
            _FakeRequest({
                "lines": ["route A preload"],
                "session_id": "reused-session",
                "sdk_route_instance_id": "route-A",
                "render_language": "ja",
            }),
        )

    assert result["ok"] is False
    assert result["reason"] == "route_instance_id_mismatch"
    assert result["method"] == "project_tts_preload"
    assert mgr.preloaded == []
    assert mgr.render_language_at_mirror == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speech_preload_allows_a_new_preroute_after_completed_generation(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    with reset_game_route_state():
        state = gr_runtime._activate_game_route("example-game", "reused-session", "Lan")
        state["_sdk_route_instance_id"] = "completed-route"
        state["game_route_active"] = False
        result = await gr_runtime.game_project_speech_preload(
            "example-game",
            _FakeRequest({
                "lines": ["next round preload"],
                "session_id": "reused-session",
                "sdk_route_instance_id": "",
            }),
        )

    assert result["ok"] is True
    assert mgr.preloaded == [["next round preload"]]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speech_preload_rejects_old_generation_without_active_route(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    with reset_game_route_state():
        result = await gr_runtime.game_project_speech_preload(
            "example-game",
            _FakeRequest({
                "lines": ["late route A preload"],
                "session_id": "reused-session",
                "sdk_route_instance_id": "route-A",
            }),
        )

    assert result["ok"] is False
    assert result["reason"] == "route_instance_id_mismatch"
    assert result["method"] == "project_tts_preload"
    assert mgr.preloaded == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speech_preload_disconnect_cancels_backend_work(monkeypatch):
    class DisconnectingRequest(_FakeRequest):
        async def is_disconnected(self):
            return True

    class BlockingPreloadManager(_FakeGameRouteManager):
        def __init__(self):
            super().__init__()
            self.cancelled = False

        async def preload_game_speech_audio(self, lines, *, render_language=""):
            self.preloaded.append(list(lines))
            self.preload_render_languages.append(render_language)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    mgr = BlockingPreloadManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    result = await gr_runtime.game_project_speech_preload(
        "soccer",
        DisconnectingRequest({
            "lines": ["断开后停止预载"],
            "session_id": "match_1",
        }),
    )

    assert result["ok"] is False
    assert result["reason"] == "cancelled"
    assert mgr.cancelled is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speech_preload_is_cancelled_with_its_active_route(monkeypatch):
    class BlockingPreloadManager(_FakeGameRouteManager):
        def __init__(self):
            super().__init__()
            self.preload_started = asyncio.Event()
            self.preload_cancelled = asyncio.Event()

        async def preload_game_speech_audio(self, lines, *, render_language=""):
            self.preloaded.append(list(lines))
            self.preload_render_languages.append(render_language)
            self.preload_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.preload_cancelled.set()
                raise

    mgr = BlockingPreloadManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    with reset_game_route_state():
        state = gr_runtime._activate_game_route("example-game", "route-A", "Lan")
        state["_sdk_route_instance_id"] = "generation-A"
        preload_request = asyncio.create_task(
            gr_runtime.game_project_speech_preload(
                "example-game",
                _FakeRequest({
                    "lines": ["owned preload"],
                    "lanlan_name": "Lan",
                    "session_id": "route-A",
                    "sdk_route_instance_id": "generation-A",
                }),
            )
        )
        await asyncio.wait_for(mgr.preload_started.wait(), timeout=1.0)
        assert len(state["_sdk_active_speech_preload_tasks"]) == 1

        route_lock = gr_runtime._get_route_lock("Lan", "example-game")
        supersede_lock = gr_runtime._get_supersede_lock("Lan")
        async with supersede_lock:
            async with route_lock:
                await gr_runtime._finalize_game_route_state(
                    state,
                    reason="test_route_end",
                    close_game_session=False,
                )
        result = await asyncio.wait_for(preload_request, timeout=1.0)

        assert result["ok"] is False
        assert result["reason"] == "cancelled"
        assert mgr.preload_cancelled.is_set() is True
        assert "_sdk_active_speech_preload_tasks" not in state


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speech_preload_propagates_cancellation_of_its_own_request(monkeypatch):
    """Cancelling the REQUEST must stop it, not produce a response.

    The sibling test above covers the other producer of the same exception: the
    child preload cancelled by route teardown, which must degrade to a normal
    ``cancelled`` response so an ordinary end-of-round does not 500. Those two
    have to be told apart -- converting our own cancellation into a value means
    the handler keeps running cleanup, logging and response construction after
    being asked to terminate, which is the defect that was already fixed one
    layer down in ``preload_game_speech_audio``.
    """
    class BlockingPreloadManager(_FakeGameRouteManager):
        def __init__(self):
            super().__init__()
            self.preload_started = asyncio.Event()
            self.preload_cancelled = asyncio.Event()

        async def preload_game_speech_audio(self, lines, *, render_language=""):
            self.preloaded.append(list(lines))
            self.preload_render_languages.append(render_language)
            self.preload_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.preload_cancelled.set()
                raise

    mgr = BlockingPreloadManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    with reset_game_route_state():
        state = gr_runtime._activate_game_route("example-game", "route-A", "Lan")
        state["_sdk_route_instance_id"] = "generation-A"
        preload_request = asyncio.create_task(
            gr_runtime.game_project_speech_preload(
                "example-game",
                _FakeRequest({
                    "lines": ["cancelled request"],
                    "lanlan_name": "Lan",
                    "session_id": "route-A",
                    "sdk_route_instance_id": "generation-A",
                }),
            )
        )
        await asyncio.wait_for(mgr.preload_started.wait(), timeout=1.0)

        preload_request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(preload_request, timeout=1.0)

        # The child is still torn down, and the route-owned set is released.
        assert mgr.preload_cancelled.is_set() is True
        assert "_sdk_active_speech_preload_tasks" not in state


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speech_preload_rejects_above_route_task_limit(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})
    blockers = {
        asyncio.create_task(asyncio.Event().wait())
        for _ in range(gr_runtime._SDK_GAME_ROUTE_PRELOAD_TASK_LIMIT)
    }

    try:
        with reset_game_route_state():
            state = gr_runtime._activate_game_route("example-game", "route-A", "Lan")
            state["_sdk_route_instance_id"] = "generation-A"
            state["_sdk_active_speech_preload_tasks"] = blockers
            result = await gr_runtime.game_project_speech_preload(
                "example-game",
                _FakeRequest({
                    "lines": ["over capacity"],
                    "lanlan_name": "Lan",
                    "session_id": "route-A",
                    "sdk_route_instance_id": "generation-A",
                }),
            )

            assert result["ok"] is False
            assert result["reason"] == "preload_busy"
            assert result["limit"] == gr_runtime._SDK_GAME_ROUTE_PRELOAD_TASK_LIMIT
            assert len(state["_sdk_active_speech_preload_tasks"]) == len(blockers)
            assert mgr.preloaded == []
    finally:
        for task in blockers:
            task.cancel()
        await asyncio.gather(*blockers, return_exceptions=True)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speech_preload_rejects_non_object_manager_result(monkeypatch):
    mgr = _FakeGameRouteManager()
    mgr.preload_game_speech_audio = AsyncMock(return_value=["not", "an", "object"])
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    result = await gr_runtime.game_project_speech_preload(
        "soccer",
        _FakeRequest({"lines": ["invalid result"], "session_id": "match_1"}),
    )

    assert result["ok"] is False
    assert result["reason"] == "invalid_preload_result"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speech_preload_has_total_timeout_and_cleans_task(monkeypatch):
    class BlockingPreloadManager(_FakeGameRouteManager):
        def __init__(self):
            super().__init__()
            self.cancelled = False

        async def preload_game_speech_audio(self, _lines, *, render_language=""):
            self.preload_render_languages.append(render_language)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    mgr = BlockingPreloadManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})
    monkeypatch.setattr(gr_runtime, "_SDK_GAME_SPEECH_PRELOAD_TIMEOUT_SECONDS", 0.01)

    result = await gr_runtime.game_project_speech_preload(
        "soccer",
        _FakeRequest({"lines": ["timeout"], "session_id": "match_1"}),
    )

    assert result["ok"] is False
    assert result["reason"] == "timeout"
    assert mgr.cancelled is True


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested_gain", "expected_gain"),
    [(-1, 0.0), (0, 0.0), (1.5, 1.5), (3, 2.0), ("invalid", 1.0), (True, 1.0)],
)
async def test_project_speak_clamps_per_game_voice_playback_gain(
    monkeypatch, requested_gain, expected_gain
):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    with reset_game_route_state():
        gr_runtime._activate_game_route("soccer", "match_1", "Lan")
        result = await gr_runtime.game_project_speak(
            "soccer",
            _FakeRequest({
                "line": "音量测试",
                "session_id": "match_1",
                "playback_gain": requested_gain,
            }),
        )

    assert result["playback_gain"] == expected_gain
    assert mgr.spoken[0][1]["playback_gain"] == expected_gain


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speak_allows_preroute_output_without_generation(monkeypatch):
    with reset_game_route_state():
        mgr = _FakeGameRouteManager()
        _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})

        result = await gr_runtime.game_project_speak(
            "soccer",
            _FakeRequest({
                "line": "opening speech before route",
                "session_id": "match_1",
                "lanlan_name": "Lan",
                "source": "game",
            }),
        )

    assert result["ok"] is True
    assert mgr.spoken[0][0] == "opening speech before route"
    assert mgr.spoken[0][1]["metadata"]["session_id"] == "match_1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speak_preroute_disconnect_cancels_backend_work(monkeypatch):
    class DisconnectingRequest(_FakeRequest):
        async def is_disconnected(self):
            return True

    class BlockingSpeechManager(_FakeGameRouteManager):
        def __init__(self):
            super().__init__()
            self.cancelled = False

        async def mirror_assistant_speech(self, line, **kwargs):
            self.spoken.append((line, kwargs))
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    with reset_game_route_state():
        mgr = BlockingSpeechManager()
        _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})

        result = await gr_runtime.game_project_speak(
            "example-game",
            DisconnectingRequest({
                "line": "opening speech cancelled with its request",
                "session_id": "pregame-session",
                "lanlan_name": "Lan",
            }),
        )

    assert result["ok"] is False
    assert result["reason"] == "cancelled"
    assert result["audio_sent"] is False
    assert mgr.cancelled is True
    assert not hasattr(mgr, "_sdk_game_speech_pending_count")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speak_disconnect_cancels_the_audio_it_already_handed_to_the_browser(monkeypatch):
    """Cancelling the worker does not un-send chunks the browser already has.

    Route teardown pushes a cancel for every correlation the route registered,
    but a pre-route utterance is registered nowhere (there is no route state to
    register it on), and a route-scoped one whose caller merely aborted the
    fetch gets no teardown at all. Either way the browser keeps playing a line
    the game has abandoned unless this request cancels its own correlation.
    """
    class ConnectedState:
        CONNECTED = "connected"

        def __eq__(self, other):
            return other == self.CONNECTED

    class DisconnectingRequest(_FakeRequest):
        async def is_disconnected(self):
            return True

    class BlockingSpeechManager(_FakeGameRouteManager):
        def __init__(self):
            super().__init__()
            self.websocket = SimpleNamespace(
                client_state=ConnectedState(),
                send_json=AsyncMock(),
            )

        async def mirror_assistant_speech(self, line, **kwargs):
            self.spoken.append((line, kwargs))
            await asyncio.Event().wait()

    with reset_game_route_state():
        mgr = BlockingSpeechManager()
        _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})

        result = await gr_runtime.game_project_speak(
            "example-game",
            DisconnectingRequest({
                "line": "abandoned pre-route line",
                "session_id": "pregame-session",
                "lanlan_name": "Lan",
                "sdk_speech_correlation_id": "speech-correlation-x",
            }),
        )

    assert result["reason"] == "cancelled"
    mgr.websocket.send_json.assert_awaited_once_with({
        "type": "game_route_speech_cancel",
        "lanlan_name": "Lan",
        "game_type": "example-game",
        "session_id": "pregame-session",
        "sdk_speech_correlation_id": "speech-correlation-x",
    })


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speak_disconnect_without_a_correlation_cancels_nothing(monkeypatch):
    """Built-in REST callers send no correlation, and an unscoped cancel would
    clear chat audio this request never owned."""
    class ConnectedState:
        CONNECTED = "connected"

        def __eq__(self, other):
            return other == self.CONNECTED

    class DisconnectingRequest(_FakeRequest):
        async def is_disconnected(self):
            return True

    class BlockingSpeechManager(_FakeGameRouteManager):
        def __init__(self):
            super().__init__()
            self.websocket = SimpleNamespace(
                client_state=ConnectedState(),
                send_json=AsyncMock(),
            )

        async def mirror_assistant_speech(self, line, **kwargs):
            self.spoken.append((line, kwargs))
            await asyncio.Event().wait()

    with reset_game_route_state():
        mgr = BlockingSpeechManager()
        _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})

        result = await gr_runtime.game_project_speak(
            "soccer",
            DisconnectingRequest({
                "line": "legacy line",
                "session_id": "match_1",
                "lanlan_name": "Lan",
            }),
        )

    assert result["reason"] == "cancelled"
    mgr.websocket.send_json.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speak_rejects_stale_route_session(monkeypatch):
    with reset_game_route_state():
        mgr = _FakeGameRouteManager()
        mgr._conversation_render_language = "en"
        _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
        state = gr_runtime._activate_game_route("soccer", "match_new", "Lan")

        result = await gr_runtime.game_project_speak(
            "soccer",
            _FakeRequest({
                "line": "old line",
                "session_id": "match_old",
                "lanlan_name": "Lan",
                "request_id": "req-stale-speak",
                "render_language": "ja",
            }),
        )

        assert result["ok"] is True
        assert result["skipped"] == "stale_session"
        assert result["reason"] == "session_id_mismatch"
        assert result["handled"] is False
        assert result["method"] == "project_tts"
        assert result["audio_sent"] is False
        assert result["state"]["session_id"] == "match_new"
        assert mgr.spoken == []
        assert mgr._conversation_render_language == "en"
        assert state["game_route_active"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speak_rejects_closed_game_route_output(monkeypatch):
    with reset_game_route_state():
        mgr = _FakeGameRouteManager()
        _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})

        result = await gr_runtime.game_project_speak(
            "badminton",
            _FakeRequest({
                "line": "stale line",
                "session_id": "closed-session",
                "lanlan_name": "Lan",
                "source": "game-llm-result",
                "request_id": "req-closed-speak",
            }),
        )

        assert result["ok"] is True
        assert result["skipped"] == "stale_session"
        assert result["reason"] == "route_closed"
        assert result["handled"] is False
        assert result["method"] == "project_tts"
        assert result["audio_sent"] is False
        assert result["state"]["game_route_active"] is False
        assert mgr.spoken == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speak_pre_route_rejects_output_while_another_game_owns_the_character(monkeypatch):
    """Pre-route speech is admitted on an empty *own* slot, not an empty character.

    ``_game_route_states`` is keyed by ``(lanlan_name, game_type)`` but the
    audio sink is per character. A second surface opened for the same character
    -- a game whose ``/route/start`` failed and fell back to local play, or an
    SDK game speaking before its own start -- would otherwise have every line
    played on the owner's stream.
    """
    with reset_game_route_state():
        mgr = _FakeGameRouteManager()
        _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
        gr_runtime._activate_game_route("badminton", "owner-session", "Lan")

        result = await gr_runtime.game_project_speak(
            "soccer",
            _FakeRequest({"line": "开球了", "lanlan_name": "Lan"}),
        )

        assert result["ok"] is False
        assert result["reason"] == "route_owned_by_other_game"
        assert result["audio_sent"] is False
        assert mgr.spoken == []
        # The counter this endpoint increments before its try/finally must not
        # leak, or four rejections wedge the character on ``busy`` forever.
        assert getattr(mgr, "_sdk_game_speech_pending_count", 0) == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_refused_output_requests_do_not_move_the_character_language(monkeypatch):
    """A request that gets refused must not have moved the shared session first.

    ``_absorb_request_language`` writes ``mgr.user_language``, and it used to run
    before the ownership fence on every output endpoint. A pre-route line from a
    game that does not own the character therefore switched the character's
    language on its way to being refused -- and the winning route then rendered
    in it. Same shape as the stale ``/route/start`` and the silent preload.
    """
    for endpoint, payload_extra in (
        (gr_runtime.game_project_speak, {}),
        (gr_runtime.game_project_mirror_assistant, {}),
    ):
        with reset_game_route_state():
            mgr = _LocaleTrackingManager(language="en")
            _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
            gr_runtime._activate_game_route("badminton", "owner-session", "Lan")

            result = await endpoint(
                "soccer",
                _FakeRequest({
                    "line": "开球了",
                    "lanlan_name": "Lan",
                    "i18n_language": "ja",
                    **payload_extra,
                }),
            )

            assert result["reason"] == "route_owned_by_other_game"
            assert mgr.language_updates == [], endpoint.__name__
            assert mgr.user_language == "en", endpoint.__name__


@pytest.mark.unit
@pytest.mark.asyncio
async def test_admitted_output_requests_still_absorb_the_character_language(monkeypatch):
    """The healing side effect must survive on the path it exists for."""
    class _SpeakingLocaleManager(_FakeGameRouteManager):
        def __init__(self):
            super().__init__()
            self.user_language = "en"
            self._user_language_explicit = False
            self.language_updates = []

        def set_user_language(self, language):
            self.language_updates.append(language)
            self.user_language = language
            self._user_language_explicit = True

    with reset_game_route_state():
        mgr = _SpeakingLocaleManager()
        _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})

        result = await gr_runtime.game_project_speak(
            "soccer",
            _FakeRequest({
                "line": "开球了",
                "lanlan_name": "Lan",
                "i18n_language": "ja",
            }),
        )

        assert result["ok"] is True
        assert mgr.language_updates == ["ja"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speak_pre_route_still_allowed_when_the_character_is_free(monkeypatch):
    """The documented opening-screen path is untouched when nobody owns the character."""
    with reset_game_route_state():
        mgr = _FakeGameRouteManager()
        _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})

        result = await gr_runtime.game_project_speak(
            "soccer",
            _FakeRequest({"line": "开球了", "lanlan_name": "Lan"}),
        )

        assert result["ok"] is True
        assert [entry[0] for entry in mgr.spoken] == ["开球了"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_mirror_assistant_pre_route_rejects_output_while_another_game_owns_the_character(monkeypatch):
    """Same fence as /speak: the text mirror writes to the same per-character sink."""
    with reset_game_route_state():
        mgr = _FakeGameRouteManager()
        _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
        gr_runtime._activate_game_route("badminton", "owner-session", "Lan")

        result = await gr_runtime.game_project_mirror_assistant(
            "soccer",
            _FakeRequest({"line": "开球了", "lanlan_name": "Lan"}),
        )

        assert result["ok"] is False
        assert result["reason"] == "route_owned_by_other_game"
        assert result["mirrored"] is False
        assert mgr.assistant_mirrored == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_mirror_assistant_uses_text_only_mirror(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})
    route_state = {"game_route_active": True, "session_id": "match_1", "lanlan_name": "Lan"}
    _gr_patch_all(
        monkeypatch,
        "_sdk_active_route_from_payload",
        lambda _game_type, _data, **_kwargs: ("Lan", "match_1", route_state, None),
    )
    _gr_patch_all(
        monkeypatch,
        "_get_active_game_route_state",
        lambda _lanlan, _game_type=None: route_state,
    )

    result = await gr_runtime.game_project_mirror_assistant(
        "soccer",
        _FakeRequest({
            "line": "文字先进入主聊天窗",
            "session_id": "match_1",
            "request_id": "req-mirror",
            "turn_id": "turn-mirror",
            "source": "game-llm-result",
            "render_language": "ja",
        }),
    )

    assert result["ok"] is True
    assert result["method"] == "project_text_mirror"
    assert mgr.render_language_at_mirror == ["ja"]
    mirrored_event = mgr.assistant_mirrored[0][1]["metadata"]["mirror"]["event"]
    assert mirrored_event["soccer_game_memory_enabled"] is False
    assert mgr.assistant_mirrored == [("文字先进入主聊天窗", {
        "metadata": {
            "source": "game-llm-result",
            "kind": "soccer",
            "session_id": "match_1",
            "mirror": {"kind": "soccer", "session_id": "match_1", "event": mirrored_event},
        },
        "request_id": "req-mirror",
        "turn_id": "turn-mirror",
        "finalize_turn": False,
    })]
    assert mgr.spoken == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_mirror_assistant_rejects_stale_route_session(monkeypatch):
    with reset_game_route_state():
        mgr = _FakeGameRouteManager()
        _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
        state = gr_runtime._activate_game_route("soccer", "match_new", "Lan")

        result = await gr_runtime.game_project_mirror_assistant(
            "soccer",
            _FakeRequest({
                "line": "old mirror line",
                "session_id": "match_old",
                "lanlan_name": "Lan",
                "request_id": "req-stale-mirror",
                "turn_id": "turn-stale-mirror",
            }),
        )

        assert result["ok"] is True
        assert result["skipped"] == "stale_session"
        assert result["reason"] == "session_id_mismatch"
        assert result["handled"] is False
        assert result["method"] == "project_text_mirror"
        assert result["mirrored"] is False
        assert result["state"]["session_id"] == "match_new"
        assert mgr.assistant_mirrored == []
        assert mgr.spoken == []
        assert state["game_dialog_log"] == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_mirror_assistant_rejects_closed_game_route_output(monkeypatch):
    with reset_game_route_state():
        mgr = _FakeGameRouteManager()
        _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})

        result = await gr_runtime.game_project_mirror_assistant(
            "badminton",
            _FakeRequest({
                "line": "stale mirror line",
                "session_id": "closed-session",
                "lanlan_name": "Lan",
                "source": "game-llm-result",
                "request_id": "req-closed-mirror",
                "turn_id": "turn-closed-mirror",
            }),
        )

        assert result["ok"] is True
        assert result["skipped"] == "stale_session"
        assert result["reason"] == "route_closed"
        assert result["handled"] is False
        assert result["method"] == "project_text_mirror"
        assert result["mirrored"] is False
        assert result["state"]["game_route_active"] is False
        assert mgr.assistant_mirrored == []
        assert mgr.spoken == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_mirror_assistant_allows_preroute_output_without_generation(monkeypatch):
    with reset_game_route_state():
        mgr = _FakeGameRouteManager()
        _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})

        result = await gr_runtime.game_project_mirror_assistant(
            "soccer",
            _FakeRequest({
                "line": "开局前镜像",
                "session_id": "inactive-session",
                "lanlan_name": "Lan",
                "source": "game",
            }),
        )

    assert result["ok"] is True
    assert result["method"] == "project_text_mirror"
    assert result["mirrored"] is True
    assert mgr.assistant_mirrored[0][0] == "开局前镜像"
    assert mgr.assistant_mirrored[0][1]["metadata"]["session_id"] == "inactive-session"
    assert mgr.spoken == []


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["speak", "mirror"])
async def test_preroute_speech_rejects_stale_route_generation(monkeypatch, endpoint):
    with reset_game_route_state():
        mgr = _FakeGameRouteManager()
        _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
        payload = {
            "line": "stale opening output",
            "session_id": "new-session",
            "lanlan_name": "Lan",
            "sdk_route_instance_id": "ended-route",
        }
        if endpoint == "speak":
            result = await gr_runtime.game_project_speak(
                "example-game", _FakeRequest(payload)
            )
        else:
            result = await gr_runtime.game_project_mirror_assistant(
                "example-game", _FakeRequest(payload)
            )

    assert result["ok"] is False
    assert result["reason"] == "route_instance_id_mismatch"
    assert mgr.spoken == []
    assert mgr.assistant_mirrored == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_mirror_assistant_finalizes_user_reply_by_default(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})
    route_state = {"game_route_active": True, "session_id": "match_1", "lanlan_name": "Lan"}
    _gr_patch_all(
        monkeypatch,
        "_sdk_active_route_from_payload",
        lambda _game_type, _data, **_kwargs: ("Lan", "match_1", route_state, None),
    )
    _gr_patch_all(
        monkeypatch,
        "_get_active_game_route_state",
        lambda _lanlan, _game_type=None: route_state,
    )

    result = await gr_runtime.game_project_mirror_assistant(
        "soccer",
        _FakeRequest({
            "line": "听见啦，我会放慢一点。",
            "session_id": "match_1",
            "request_id": "req-user-reply",
            "source": "game-llm-result",
            "event": {
                "kind": "user-text",
                "hasUserText": True,
            },
        }),
    )

    assert result["ok"] is True
    mirrored_event = mgr.assistant_mirrored[0][1]["metadata"]["mirror"]["event"]
    assert mirrored_event["kind"] == "user-text"
    assert mirrored_event["hasUserText"] is True
    assert mirrored_event["soccer_game_memory_enabled"] is False
    assert mgr.assistant_mirrored == [("听见啦，我会放慢一点。", {
        "metadata": {
            "source": "game-llm-result",
            "kind": "soccer",
            "session_id": "match_1",
            "mirror": {
                "kind": "soccer",
                "session_id": "match_1",
                "event": mirrored_event,
            },
        },
        "request_id": "req-user-reply",
        "turn_id": None,
        "finalize_turn": True,
    })]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_mirror_assistant_records_opening_line_in_game_log(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")

    result = await gr_runtime.game_project_mirror_assistant(
        "soccer",
        _FakeRequest({
            "line": "看我这一脚",
            "session_id": "match_1",
            "request_id": "opening-1",
            "source": "game-llm-result",
            "event": {
                "kind": "opening-line",
                "hasUserSpeech": False,
                "hasUserText": False,
            },
        }),
    )

    assert result["ok"] is True
    assert mgr.assistant_mirrored[0][0] == "看我这一脚"
    mirror_kwargs = mgr.assistant_mirrored[0][1]
    assert mirror_kwargs["request_id"] == "opening-1"
    assert mirror_kwargs["turn_id"] is None
    assert mirror_kwargs["finalize_turn"] is False
    metadata = mirror_kwargs["metadata"]
    assert metadata["source"] == "game-llm-result"
    assert metadata["kind"] == "soccer"
    assert metadata["session_id"] == "match_1"
    event = metadata["mirror"]["event"]
    assert event["kind"] == "opening-line"
    assert event["hasUserSpeech"] is False
    assert event["hasUserText"] is False
    assert event["soccerGameMemoryEventReplyEnabled"] is False
    assert event["soccer_game_memory_event_reply_enabled"] is False
    assert state["game_dialog_log"] == [{
        "id": "glog_0001",
        "type": "assistant",
        "source": "opening_line",
        "kind": "opening-line",
        "line": "看我这一脚",
        "request_id": "opening-1",
        "ts": state["game_dialog_log"][0]["ts"],
    }]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_mirror_assistant_serializes_publish_before_replacement_route(
    monkeypatch,
):
    entered = asyncio.Event()
    release = asyncio.Event()

    class BlockingMirrorManager(_FakeGameRouteManager):
        async def mirror_assistant_output(self, text, **kwargs):
            self.assistant_mirrored.append((text, kwargs))
            entered.set()
            await release.wait()
            return {"ok": True, "mirrored": True, "method": "project_text_mirror"}

    mgr = BlockingMirrorManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"MirrorLockLan": mgr})
    _gr_patch_all(
        monkeypatch,
        "_get_current_character_info",
        lambda: {"lanlan_name": "MirrorLockLan"},
    )

    with reset_game_route_state():
        route_a = gr_runtime._activate_game_route(
            "example-mirror-lock", "reused-session", "MirrorLockLan"
        )
        route_a["_sdk_route_instance_id"] = "route-A"
        task = asyncio.create_task(gr_runtime.game_project_mirror_assistant(
            "example-mirror-lock",
            _FakeRequest({
                "line": "route A opening",
                "session_id": "reused-session",
                "sdk_route_instance_id": "route-A",
                "event": {"kind": "opening-line"},
            }),
        ))
        await entered.wait()
        route_lock = gr_runtime._get_route_lock(
            "MirrorLockLan", "example-mirror-lock"
        )
        replacement_started = asyncio.Event()

        async def replace_route():
            replacement_started.set()
            async with route_lock:
                route_b = gr_runtime._activate_game_route(
                    "example-mirror-lock", "reused-session", "MirrorLockLan"
                )
                route_b["_sdk_route_instance_id"] = "route-B"
                return route_b

        replacement_task = asyncio.create_task(replace_route())
        await replacement_started.wait()
        await asyncio.sleep(0)
        assert replacement_task.done() is False
        release.set()
        result = await task
        route_b = await replacement_task

        assert result["ok"] is True
        assert route_a["game_dialog_log"][0]["line"] == "route A opening"
        assert route_b["game_dialog_log"] == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_mirror_timeout_releases_route_lock_for_replacement(monkeypatch):
    entered = asyncio.Event()

    class StalledMirrorManager(_FakeGameRouteManager):
        async def mirror_assistant_output(self, text, **kwargs):
            entered.set()
            await asyncio.Event().wait()

    mgr = StalledMirrorManager()
    monkeypatch.setattr(gr_runtime, "_SDK_GAME_MIRROR_PUBLISH_TIMEOUT_SECONDS", 0.01)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"MirrorTimeoutLan": mgr})
    _gr_patch_all(
        monkeypatch,
        "_get_current_character_info",
        lambda: {"lanlan_name": "MirrorTimeoutLan"},
    )

    with reset_game_route_state():
        route_a = gr_runtime._activate_game_route(
            "example-mirror-timeout", "session-a", "MirrorTimeoutLan"
        )
        route_a["_sdk_route_instance_id"] = "route-a"
        mirror_task = asyncio.create_task(gr_runtime.game_project_mirror_assistant(
            "example-mirror-timeout",
            _FakeRequest({
                "line": "stalled output",
                "session_id": "session-a",
                "sdk_route_instance_id": "route-a",
            }),
        ))
        await asyncio.wait_for(entered.wait(), timeout=1)
        result = await asyncio.wait_for(mirror_task, timeout=1)
        assert result["ok"] is False
        assert result["reason"] == "mirror_timeout"

        route_lock = gr_runtime._get_route_lock(
            "MirrorTimeoutLan", "example-mirror-timeout"
        )
        async with asyncio.timeout(1):
            async with route_lock:
                route_b = gr_runtime._activate_game_route(
                    "example-mirror-timeout", "session-b", "MirrorTimeoutLan"
                )
        assert route_b["session_id"] == "session-b"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_mirror_assistant_drops_route_replaced_before_publish_lock(
    monkeypatch,
):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"MirrorStaleLan": mgr})

    with reset_game_route_state():
        route_a = gr_runtime._activate_game_route(
            "example-mirror-stale", "reused-session", "MirrorStaleLan"
        )
        route_a["_sdk_route_instance_id"] = "route-A"
        route_lock = gr_runtime._get_route_lock(
            "MirrorStaleLan", "example-mirror-stale"
        )
        await route_lock.acquire()
        try:
            task = asyncio.create_task(gr_runtime.game_project_mirror_assistant(
                "example-mirror-stale",
                _FakeRequest({
                    "line": "stale route A opening",
                    "lanlan_name": "MirrorStaleLan",
                    "session_id": "reused-session",
                    "sdk_route_instance_id": "route-A",
                    "event": {"kind": "opening-line"},
                }),
            ))
            await asyncio.sleep(0)
            route_b = gr_runtime._activate_game_route(
                "example-mirror-stale", "reused-session", "MirrorStaleLan"
            )
            route_b["_sdk_route_instance_id"] = "route-B"
        finally:
            route_lock.release()

        result = await task

        assert result["ok"] is False
        assert result["reason"] == "route_superseded"
        assert mgr.assistant_mirrored == []
        assert route_a["game_dialog_log"] == []
        assert route_b["game_dialog_log"] == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_archives_active_route_to_memory(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
    _mark_game_started(state)
    state["last_state"] = {
        "score": {"player": 2, "ai": 5},
    }
    state["preGameContext"] = {
        **gr_pregame._default_soccer_pregame_context(initial_difficulty="lv2"),
        "gameStance": "soft_teasing",
    }
    state["pre_game_context_source"] = "ai"
    state["pre_game_context_error"] = ""
    gr_runtime._append_game_dialog(state, {
        "type": "user",
        "source": "external_text_route",
        "text": "你是不是在放水？",
    })
    gr_runtime._append_game_dialog(state, {
        "type": "assistant",
        "source": "game_llm",
        "line": "才没有放水呢。",
        "control": {"mood": "happy"},
    })

    submitted = []

    async def fake_submit(archive):
        submitted.append(archive)
        return {"ok": True, "status": "cached", "count": 1}

    _gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", fake_submit)

    result = await gr_runtime.game_end(
        "soccer",
        _FakeRequest({
            "session_id": "match_1",
            "lanlan_name": "Lan",
            "currentState": {"score": {"player": 3, "ai": 6}, "round": 9},
            "gameMemoryTailCount": 4,
            "gameMemoryEnabled": True,
            "gameStarted": True,
            "gameStartedElapsedMs": 15_000,
        }),
    )

    assert result["route_closed"] is True
    assert result["archive_memory"] == {"ok": True, "status": "cached", "count": 1}
    assert result["archive"]["summary"].startswith("soccer 游戏结束")
    assert "待接入 memory_server" not in result["archive"]["summary"]
    assert result["archive"]["preGameContext"]["gameStance"] == "soft_teasing"
    assert result["archive"]["pre_game_context_source"] == "ai"
    assert result["archive"]["finalScore"] == {"player": 3, "ai": 6}
    assert result["archive"]["game_memory_tail_count"] == 4
    assert submitted[0]["last_full_dialogues"][-1]["line"] == "才没有放水呢。"
    assert submitted[0]["preGameContext"]["initialDifficulty"] == "lv2"
    assert state["game_route_active"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_skips_game_archive_when_game_never_started(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
    gr_runtime._append_game_dialog(state, {
        "type": "assistant",
        "source": "opening_line",
        "line": "准备好了吗",
    })

    async def fake_submit(_archive):
        raise AssertionError("accidental pre-start entry should not write game archive memory")

    _gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", fake_submit)

    result = await gr_runtime.game_end(
        "soccer",
        _FakeRequest({
            "session_id": "match_1",
            "lanlan_name": "Lan",
            "reason": "accidental_page_entry",
            "gameStarted": False,
            "accidentalGameEntry": True,
        }),
    )

    assert result["route_closed"] is True
    assert result["archive_memory"]["status"] == "skipped"
    assert result["archive_memory"]["reason"] == "accidental_page_entry"
    assert result["postgame"] == {"ok": True, "action": "skip", "reason": "disabled"}
    assert result["archive"]["memory_skipped"] is True
    assert result["archive"]["last_full_dialogues"][0]["source"] == "opening_line"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_under_10s_skips_archive_without_suppressing_user_reply_memory(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
    _set_soccer_game_memory_policy(state, enabled=True)
    _mark_game_started(state, elapsed_ms=5_000)

    async def fake_run_game_chat(_game_type, _session_id, event, **_route_kwargs):
        assert event["kind"] == "user-voice"
        assert "skipOrdinaryMemory" not in event
        return {"line": "先热身一下。", "control": {}, "llm_source": {"provider": "fake"}}

    async def fake_submit(_archive):
        raise AssertionError("too-short game should not write game archive memory")

    _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)
    _gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", fake_submit)

    handled = await gr_runtime.route_external_voice_transcript(
        "Lan",
        "刚开始吗？",
        request_id="voice-grace",
        game_type="soccer",
        session_id="match_1",
    )

    assert handled is True
    assert state["pending_outputs"][0]["meta"]["hasUserSpeech"] is True
    assert "skipOrdinaryMemory" not in state["pending_outputs"][0]["meta"]
    assert state["pending_outputs"][1]["meta"]["hasUserSpeech"] is True
    assert "skipOrdinaryMemory" not in state["pending_outputs"][1]["meta"]

    result = await gr_runtime.game_end(
        "soccer",
        _FakeRequest({
            "session_id": "match_1",
            "lanlan_name": "Lan",
            "reason": "manual_return_to_start",
            "gameStarted": True,
            "gameStartedElapsedMs": 9_000,
        }),
    )

    assert result["archive_memory"]["status"] == "skipped"
    assert result["archive_memory"]["reason"] == "started_under_10s"
    assert result["postgame"] == {"ok": True, "action": "skip", "reason": "disabled"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_injects_postgame_context_into_active_realtime(monkeypatch, _fake_realtime):
    session = _fake_realtime(model_lower="qwen-realtime", delivered=True)
    mgr = _FakeRealtimeManager(session)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_POSTGAME_REALTIME_NUDGE_DELAYS", (0.0,))
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
    _set_soccer_game_memory_policy(state, enabled=True)
    _mark_game_started(state)
    state["last_state"] = {"score": {"player": 1, "ai": 3}}
    gr_runtime._append_game_dialog(state, {
        "type": "user",
        "source": "external_voice_route",
        "text": "我是不是不适合玩这个？",
    })
    gr_runtime._append_game_dialog(state, {
        "type": "assistant",
        "source": "game_llm",
        "line": "别认输嘛，再来一脚。",
        "control": {"mood": "relaxed"},
    })

    async def fake_submit(archive):
        return {"ok": True, "status": "cached", "count": 1}

    _gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", fake_submit)

    result = await gr_runtime.game_end(
        "soccer",
        _FakeRequest({"session_id": "match_1", "lanlan_name": "Lan", "reason": "manual"}),
    )

    assert result["postgame"]["mode"] == "realtime"
    assert result["postgame"]["context_injected"] is True
    assert result["postgame"]["nudge_scheduled"] is True
    await asyncio.wait_for(mgr.voice_nudge_event.wait(), timeout=1.0)
    assert mgr.voice_nudge_calls == 1
    # qwen_manual_commit/instruction surface was removed; the postgame nudge
    # now relies on plain prompt_ephemeral (guarded realtime text injection). The
    # postgame instruction reaches the model via prime_context (assert below).
    assert session.prime_context_calls
    assert mgr.append_context_calls[0]["source"] == "game.postgame"
    assert mgr.append_context_calls[0]["audience"] == "model"
    context_text, skipped = session.prime_context_calls[0]
    assert skipped is True
    assert not context_text.startswith("system: ")
    assert "[Game Module Postgame Context]" in context_text
    assert "我是不是不适合玩这个？" in context_text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_skips_postgame_nudge_when_context_append_deduped(monkeypatch, _fake_realtime):
    session = _fake_realtime(model_lower="qwen-realtime", delivered=True)
    mgr = _FakeRealtimeManager(
        session,
        append_context_result=SimpleNamespace(
            appended=False,
            deduped=True,
            targets=(),
            reason="duplicate_request_id",
        ),
    )
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_POSTGAME_REALTIME_NUDGE_DELAYS", (0.0,))
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
    _set_soccer_game_memory_policy(state, enabled=True)
    _mark_game_started(state)
    state["last_state"] = {"score": {"player": 1, "ai": 3}}
    gr_runtime._append_game_dialog(state, {
        "type": "assistant",
        "source": "game_llm",
        "line": "别认输嘛，再来一脚。",
    })

    async def fake_submit(archive):
        return {"ok": True, "status": "cached", "count": 1}

    _gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", fake_submit)

    result = await gr_runtime.game_end(
        "soccer",
        _FakeRequest({"session_id": "match_1", "lanlan_name": "Lan", "reason": "manual"}),
    )

    assert result["postgame"]["mode"] == "realtime"
    assert result["postgame"]["context_injected"] is True
    assert result["postgame"]["nudge_scheduled"] is False
    assert result["postgame"]["reason"] == "context_deduped"
    assert mgr.voice_nudge_calls == 0
    assert session.prime_context_calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_postgame_realtime_nudge_skips_replacement_session(monkeypatch, _fake_realtime):
    original = _fake_realtime(model_lower="qwen-realtime", delivered=True)
    replacement = _fake_realtime(model_lower="qwen-realtime", delivered=True)
    mgr = _FakeRealtimeManager(original)
    _gr_patch_all(monkeypatch, "_POSTGAME_REALTIME_NUDGE_DELAYS", (0.01,))

    result = await gr_runtime._deliver_postgame_to_realtime(
        mgr,
        {
            "game_type": "soccer",
            "session_id": "match_1",
            "lanlan_name": "Lan",
            "ended_at": "100.0",
        },
        {"trigger_voice": True},
    )
    mgr.session = replacement
    await asyncio.sleep(0.05)

    assert result["mode"] == "realtime"
    assert result["nudge_scheduled"] is True
    assert mgr.voice_nudge_calls == 0
    assert len(original.prime_context_calls) == 1
    assert replacement.prime_context_calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_postgame_realtime_context_aborts_when_active_session_changes(monkeypatch, _fake_realtime):
    original = _fake_realtime(model_lower="qwen-realtime", delivered=True)
    replacement = _fake_realtime(model_lower="qwen-realtime", delivered=True)
    mgr = _FakeRealtimeManager(original)

    def swap_session(_archive):
        mgr.session = replacement
        return "[Game Module Postgame Context]\nrace"

    _gr_patch_all(monkeypatch, "_build_game_postgame_context_text", swap_session)

    result = await gr_runtime._deliver_postgame_to_realtime(
        mgr,
        {
            "game_type": "soccer",
            "session_id": "match_1",
            "lanlan_name": "Lan",
            "ended_at": "100.0",
        },
        {"trigger_voice": True},
    )

    assert result == {
        "ok": False,
        "mode": "realtime",
        "action": "skip",
        "reason": "realtime_session_changed",
    }
    assert mgr.append_context_calls == []
    assert original.prime_context_calls == []
    assert replacement.prime_context_calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_postgame_realtime_publish_serializes_against_route_takeover(
    monkeypatch,
    _fake_realtime,
):
    session = _fake_realtime(model_lower="qwen-realtime", delivered=True)
    mgr = _FakeRealtimeManager(session)
    append_started = asyncio.Event()
    release_append = asyncio.Event()
    replacement_activated = asyncio.Event()

    async def blocking_append(**kwargs):
        mgr.append_context_calls.append(kwargs)
        append_started.set()
        await release_append.wait()
        return SimpleNamespace(
            appended=True,
            deduped=False,
            targets=("realtime_prime",),
            reason=None,
        )

    mgr.append_context = blocking_append
    with reset_game_route_state():
        source_state = {
            "game_route_active": False,
            "game_type": "example-game",
            "session_id": "route-A",
            "lanlan_name": "Lan",
        }
        route_key = gr_runtime._route_state_key("Lan", "example-game")
        gr_runtime._game_route_states[route_key] = source_state

        delivery_task = asyncio.create_task(
            gr_runtime._deliver_postgame_to_realtime(
                mgr,
                {
                    "game_type": "example-game",
                    "session_id": "route-A",
                    "lanlan_name": "Lan",
                    "ended_at": "100.0",
                },
                {"trigger_voice": False},
                source_state=source_state,
            )
        )
        await asyncio.wait_for(append_started.wait(), timeout=1.0)

        async def activate_replacement():
            async with gr_runtime._get_supersede_lock("Lan"):
                gr_runtime._game_route_states[route_key] = {
                    "game_route_active": True,
                    "game_type": "example-game",
                    "session_id": "route-B",
                    "lanlan_name": "Lan",
                }
                replacement_activated.set()

        replacement_task = asyncio.create_task(activate_replacement())
        await asyncio.sleep(0)
        assert replacement_activated.is_set() is False

        release_append.set()
        result = await asyncio.wait_for(delivery_task, timeout=1.0)
        await asyncio.wait_for(replacement_task, timeout=1.0)

        assert result["context_injected"] is True
        assert replacement_activated.is_set() is True
        assert len(mgr.append_context_calls) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_postgame_realtime_publish_timeout_releases_takeover_lock(
    monkeypatch,
    _fake_realtime,
):
    session = _fake_realtime(model_lower="qwen-realtime", delivered=True)
    mgr = _FakeRealtimeManager(session)
    append_started = asyncio.Event()
    append_cancelled = asyncio.Event()
    replacement_activated = asyncio.Event()

    async def blocking_append(**_kwargs):
        append_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            append_cancelled.set()
            raise

    mgr.append_context = blocking_append
    # A wedged provider is bounded by the BODY budget. The acquisition budget
    # is deliberately separate and must not bound the delivery itself.
    monkeypatch.setattr(gr_postgame, "_POSTGAME_DELIVERY_BODY_TIMEOUT_SECONDS", 0.01)

    with reset_game_route_state():
        source_state = {
            "game_route_active": False,
            "game_type": "example-game",
            "session_id": "route-A",
            "lanlan_name": "Lan",
        }
        route_key = gr_runtime._route_state_key("Lan", "example-game")
        gr_runtime._game_route_states[route_key] = source_state
        delivery_task = asyncio.create_task(
            gr_runtime._deliver_postgame_to_realtime(
                mgr,
                {
                    "game_type": "example-game",
                    "session_id": "route-A",
                    "lanlan_name": "Lan",
                    "ended_at": "100.0",
                },
                {"trigger_voice": False},
                source_state=source_state,
            )
        )
        await asyncio.wait_for(append_started.wait(), timeout=1.0)

        async def activate_replacement():
            async with gr_runtime._get_supersede_lock("Lan"):
                replacement_activated.set()

        replacement_task = asyncio.create_task(activate_replacement())
        result = await asyncio.wait_for(delivery_task, timeout=1.0)
        await asyncio.wait_for(replacement_task, timeout=1.0)

        assert result["reason"] == "postgame_delivery_timeout"
        assert append_cancelled.is_set() is True
        assert replacement_activated.is_set() is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_postgame_realtime_publish_survives_a_slow_but_healthy_delivery(
    monkeypatch,
    _fake_realtime,
):
    """A delivery slower than the lock-acquisition budget must still publish.

    One postgame LLM reply plus its TTS feed routinely exceeds the few seconds
    allowed for taking the character lock. Bounding the delivery body with the
    acquisition budget turned those ordinary lines into
    ``postgame_delivery_timeout`` and the player heard nothing after the match.
    """
    session = _fake_realtime(model_lower="qwen-realtime", delivered=True)
    mgr = _FakeRealtimeManager(session)
    appended = asyncio.Event()

    async def slow_append(**_kwargs):
        await asyncio.sleep(0.05)
        appended.set()
        return {"ok": True}

    mgr.append_context = slow_append
    # Acquisition is tight; the body budget is what covers the real work.
    monkeypatch.setattr(gr_postgame, "_POSTGAME_DELIVERY_LOCK_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(gr_postgame, "_POSTGAME_DELIVERY_BODY_TIMEOUT_SECONDS", 5.0)

    with reset_game_route_state():
        source_state = {
            "game_route_active": False,
            "game_type": "example-game",
            "session_id": "route-A",
            "lanlan_name": "Lan",
        }
        route_key = gr_runtime._route_state_key("Lan", "example-game")
        gr_runtime._game_route_states[route_key] = source_state
        result = await asyncio.wait_for(
            gr_runtime._deliver_postgame_to_realtime(
                mgr,
                {
                    "game_type": "example-game",
                    "session_id": "route-A",
                    "lanlan_name": "Lan",
                    "ended_at": "100.0",
                },
                {"trigger_voice": False},
                source_state=source_state,
            ),
            timeout=2.0,
        )

    assert appended.is_set() is True
    assert result.get("reason") != "postgame_delivery_timeout"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_postgame_nudge_skips_when_replacement_route_activates(
    monkeypatch,
    _fake_realtime,
):
    session = _fake_realtime(model_lower="qwen-realtime", delivered=True)
    mgr = _FakeRealtimeManager(session)
    _gr_patch_all(monkeypatch, "_POSTGAME_REALTIME_NUDGE_DELAYS", (0.02,))

    with reset_game_route_state():
        source_state = {
            "game_route_active": False,
            "game_type": "example-game",
            "session_id": "route-A",
            "lanlan_name": "Lan",
        }
        route_key = gr_runtime._route_state_key("Lan", "example-game")
        gr_runtime._game_route_states[route_key] = source_state
        result = await gr_runtime._deliver_postgame_to_realtime(
            mgr,
            {
                "game_type": "example-game",
                "session_id": "route-A",
                "lanlan_name": "Lan",
                "ended_at": "100.0",
            },
            {"trigger_voice": True},
            source_state=source_state,
        )
        gr_runtime._game_route_states[route_key] = {
            "game_route_active": True,
            "game_type": "example-game",
            "session_id": "route-B",
            "lanlan_name": "Lan",
        }
        await asyncio.sleep(0.05)

        assert result["nudge_scheduled"] is True
        assert mgr.voice_nudge_calls == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_uses_direct_response_for_gemini_postgame(monkeypatch, _fake_realtime):
    session = _fake_realtime(model_lower="gemini-2.5-flash-native-audio-preview", delivered=True)
    mgr = _FakeRealtimeManager(session)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
    state["user_language"] = "en"
    state["user_language_source"] = "request"
    _set_soccer_game_memory_policy(state, enabled=True)
    _mark_game_started(state)
    state["last_state"] = {"score": {"player": 3, "ai": 14}}
    gr_runtime._append_game_dialog(state, {
        "type": "user",
        "source": "external_voice_route",
        "text": "哇,你是笨蛋。",
    })
    gr_runtime._append_game_dialog(state, {
        "type": "assistant",
        "source": "game_llm",
        "line": "十二比三，帅的是我。",
    })

    async def fake_submit(archive):
        return {"ok": True, "status": "cached", "count": 1}

    _gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", fake_submit)

    result = await gr_runtime.game_end(
        "soccer",
        _FakeRequest({"session_id": "match_1", "lanlan_name": "Lan", "reason": "manual"}),
    )

    assert result["postgame"]["mode"] == "realtime"
    assert result["postgame"]["action"] == "direct_response"
    assert result["postgame"]["reason"] == "gemini_direct_response"
    assert session.prime_context_calls == []
    assert session.prompt_calls == []
    assert mgr.voice_nudge_calls == 0
    assert len(session.create_response_calls) == 1
    assert "[Game Module Postgame Context]" in session.create_response_calls[0]
    assert "[Game Module Postgame Proactive Greeting]" in session.create_response_calls[0]
    assert "do not keep acting as if the game is still in progress" in session.create_response_calls[0]


class _FakePostgameState:
    def __init__(self):
        self.events = []

    async def fire(self, event, **kwargs):
        self.events.append((event, kwargs))


class _FakePostgameTextManager:
    def __init__(self):
        self.is_active = False
        self.session = None
        self.current_speech_id = "postgame-sid"
        self.state = _FakePostgameState()
        self.prepare_calls = []
        self.feed_tts_calls = []
        self.finish_calls = []

    async def prepare_proactive_delivery(self, **kwargs):
        self.prepare_calls.append(kwargs)
        return True

    async def finish_proactive_delivery(self, text, **kwargs):
        self.finish_calls.append((text, kwargs))
        return True

    async def feed_tts_chunk(self, text, **kwargs):
        self.feed_tts_calls.append((text, kwargs))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_postgame_text_drops_llm_result_after_route_takeover(monkeypatch):
    mgr = _FakePostgameTextManager()
    llm_started = asyncio.Event()
    release_llm = asyncio.Event()

    async def blocking_run_game_chat(*_args, **_kwargs):
        llm_started.set()
        await release_llm.wait()
        return {"line": "late postgame line", "llm_source": {"provider": "fake"}}

    _gr_patch_all(monkeypatch, "_run_game_chat", blocking_run_game_chat)

    with reset_game_route_state():
        source_state = {
            "game_route_active": False,
            "game_type": "example-game",
            "session_id": "route-A",
            "lanlan_name": "Lan",
        }
        route_key = gr_runtime._route_state_key("Lan", "example-game")
        gr_runtime._game_route_states[route_key] = source_state
        delivery_task = asyncio.create_task(
            gr_runtime._deliver_postgame_text_bubble(
                "example-game",
                "route-A",
                mgr,
                {
                    "game_type": "example-game",
                    "session_id": "route-A",
                    "lanlan_name": "Lan",
                },
                {"enabled": True},
                source_state=source_state,
            )
        )
        await asyncio.wait_for(llm_started.wait(), timeout=1.0)
        gr_runtime._game_route_states[route_key] = {
            "game_route_active": True,
            "game_type": "example-game",
            "session_id": "route-B",
            "lanlan_name": "Lan",
        }
        release_llm.set()
        result = await asyncio.wait_for(delivery_task, timeout=1.0)

        assert result["action"] == "skip"
        assert result["reason"] == "route_superseded"
        assert mgr.feed_tts_calls == []
        assert mgr.finish_calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_postgame_text_stays_superseded_after_replacement_route_ends(monkeypatch):
    mgr = _FakePostgameTextManager()

    async def completed_run_game_chat(*_args, **_kwargs):
        return {"line": "stale postgame line", "llm_source": {"provider": "fake"}}

    _gr_patch_all(monkeypatch, "_run_game_chat", completed_run_game_chat)
    source_state = {
        "game_route_active": False,
        "game_type": "example-game",
        "session_id": "route-A",
        "lanlan_name": "Lan",
        "_sdk_route_superseded": True,
    }

    result = await gr_runtime._deliver_postgame_text_bubble(
        "example-game",
        "route-A",
        mgr,
        {
            "game_type": "example-game",
            "session_id": "route-A",
            "lanlan_name": "Lan",
        },
        {"enabled": True},
        source_state=source_state,
    )

    assert result["action"] == "skip"
    assert result["reason"] == "route_superseded"
    assert mgr.feed_tts_calls == []
    assert mgr.finish_calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_postgame_text_publish_timeout_releases_takeover_lock(monkeypatch):
    mgr = _FakePostgameTextManager()
    feed_started = asyncio.Event()
    feed_cancelled = asyncio.Event()

    async def completed_run_game_chat(*_args, **_kwargs):
        return {"line": "bounded postgame line", "llm_source": {"provider": "fake"}}

    async def blocking_feed_tts(*_args, **_kwargs):
        feed_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            feed_cancelled.set()
            raise

    _gr_patch_all(monkeypatch, "_run_game_chat", completed_run_game_chat)
    mgr.feed_tts_chunk = blocking_feed_tts
    monkeypatch.setattr(gr_postgame, "_POSTGAME_DELIVERY_LOCK_TIMEOUT_SECONDS", 0.01)
    source_state = {
        "game_route_active": False,
        "game_type": "example-game",
        "session_id": "route-A",
        "lanlan_name": "Lan",
    }

    with reset_game_route_state():
        gr_runtime._game_route_states[
            gr_runtime._route_state_key("Lan", "example-game")
        ] = source_state
        result = await gr_runtime._deliver_postgame_text_bubble(
            "example-game",
            "route-A",
            mgr,
            {
                "game_type": "example-game",
                "session_id": "route-A",
                "lanlan_name": "Lan",
            },
            {"enabled": True},
            source_state=source_state,
        )

    assert result["reason"] == "postgame_delivery_timeout"
    assert feed_started.is_set() is True
    assert feed_cancelled.is_set() is True
    assert mgr.finish_calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_delivers_one_shot_postgame_text_bubble(monkeypatch):
    mgr = _FakePostgameTextManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
    _set_soccer_game_memory_policy(state, enabled=True)
    _mark_game_started(state)
    state["last_state"] = {"score": {"player": 2, "ai": 4}}
    gr_runtime._append_game_dialog(state, {
        "type": "user",
        "source": "external_text_route",
        "text": "我好像踢不进去。",
    })

    async def fake_submit(archive):
        return {"ok": True, "status": "cached", "count": 1}

    async def fake_run_game_chat(game_type, session_id, event, **kwargs):
        assert game_type == "soccer"
        assert session_id == "match_1"
        assert event["kind"] == "postgame"
        assert event["lastUserText"] == "我好像踢不进去。"
        assert event["scoreText"] == "玩家 2 : 4 Lan"
        # Postgame must opt into the inactive-route bypass; the production
        # caller passes ``allow_postgame=True`` so the chat can run after
        # finalize.
        assert kwargs.get("allow_postgame") is True
        return {
            "line": "刚才那局不算，我下次慢点陪你踢。",
            "llm_source": {"provider": "fake"},
        }

    _gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", fake_submit)
    _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)

    result = await gr_runtime.game_end(
        "soccer",
        _FakeRequest({"session_id": "match_1", "lanlan_name": "Lan", "reason": "manual"}),
    )

    assert result["postgame"]["mode"] == "text"
    assert result["postgame"]["action"] == "chat"
    assert result["postgame"]["line"] == "刚才那局不算，我下次慢点陪你踢。"
    assert result["postgame"]["tts_fed"] is True
    assert mgr.prepare_calls == [{"min_idle_secs": 0.0}]
    assert mgr.feed_tts_calls == [("刚才那局不算，我下次慢点陪你踢。", {
        "expected_speech_id": "postgame-sid",
    })]
    assert mgr.finish_calls == [("刚才那局不算，我下次慢点陪你踢。", {
        "expected_speech_id": "postgame-sid",
    })]
    assert any(getattr(event, "name", "") == "PROACTIVE_PHASE2" for event, _ in mgr.state.events)
    assert any(getattr(event, "name", "") == "PROACTIVE_DONE" for event, _ in mgr.state.events)


async def _assert_route_end_uses_full_game_end_contract(monkeypatch):
    mgr = _FakePostgameTextManager()
    fake_session = type("FakeSession", (), {"close": AsyncMock()})()
    gr_runtime._game_sessions[gr_runtime._game_session_key("Lan", "soccer", "match_1")] = {
        "session": fake_session,
        "reply_chunks": [],
        "last_activity": gr_runtime.time.time(),
        "lock": None,
    }
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
    state["user_language"] = "en"
    state["user_language_source"] = "render"
    _set_soccer_game_memory_policy(state, enabled=True)
    _mark_game_started(state)
    state["last_state"] = {"score": {"player": 1, "ai": 2}}
    gr_runtime._append_game_dialog(state, {
        "type": "user",
        "source": "external_text_route",
        "text": "再来一球就追上了。",
    })

    async def fake_submit(archive):
        return {"ok": True, "status": "cached", "count": 1}

    async def fake_run_game_chat(game_type, session_id, event, **kwargs):
        assert game_type == "soccer"
        assert session_id == "match_1"
        assert event["kind"] == "postgame"
        assert event["lastUserText"] == "再来一球就追上了。"
        assert kwargs.get("allow_postgame") is True
        assert kwargs.get("prompt_locale") == "ja"
        return {"line": "刚才那脚挺像样的。", "llm_source": {"provider": "fake"}}

    _gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", fake_submit)
    _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)

    result = await gr_runtime.game_route_end(
        "soccer",
        _FakeRequest({
            "session_id": "match_1",
            "lanlan_name": "Lan",
            "render_language": "ja",
        }),
    )

    assert result["ok"] is True
    assert result["closed"] is True
    assert result["route_closed"] is True
    assert result["archive"]["exit_reason"] == "route_end"
    assert result["archive"]["user_language"] == "ja"
    assert result["archive"]["user_language_source"] == "render"
    assert result["archive_memory"] == {"ok": True, "status": "cached", "count": 1}
    assert result["postgame"]["mode"] == "text"
    assert result["postgame"]["action"] == "chat"
    assert result["postgame"]["line"] == "刚才那脚挺像样的。"
    assert mgr.finish_calls == [("刚才那脚挺像样的。", {
        "expected_speech_id": "postgame-sid",
    })]
    fake_session.close.assert_awaited_once()
    assert state["game_route_active"] is False
    assert state["exit_reason"] == "route_end"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_end_uses_full_game_end_contract(monkeypatch):
    with reset_game_route_state():
        await _assert_route_end_uses_full_game_end_contract(monkeypatch)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_route_end_does_not_refresh_active_route_locale(monkeypatch):
    manager = _LocaleTrackingManager()
    close_session = AsyncMock(return_value=False)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": manager})
    _gr_patch_all(monkeypatch, "_close_and_remove_session", close_session)

    with reset_game_route_state():
        state = gr_runtime._activate_game_route("soccer", "fresh-session", "Lan")
        state["user_language"] = "en"
        state["user_language_source"] = "render"

        result = await gr_runtime.game_route_end(
            "soccer",
            _FakeRequest({
                "session_id": "stale-session",
                "lanlan_name": "Lan",
                "i18n_language": "ja",
                "render_language": "ko",
            }),
        )

        assert result["route_closed"] is False
        assert state["game_route_active"] is True
        assert state["user_language"] == "en"
        assert state["user_language_source"] == "render"

    assert manager.language_updates == []
    close_session.assert_awaited_once_with("soccer", "stale-session", "Lan")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_skips_postgame_on_heartbeat_timeout(monkeypatch):
    mgr = _FakePostgameTextManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
    _mark_game_started(state)

    async def fake_submit(archive):
        return {"ok": True, "status": "cached", "count": 1}

    async def fake_run_game_chat(*_args, **_kwargs):
        raise AssertionError("postgame should not run during heartbeat timeout")

    _gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", fake_submit)
    _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)

    result = await gr_runtime.game_end(
        "soccer",
        _FakeRequest({"session_id": "match_1", "lanlan_name": "Lan", "reason": "heartbeat_timeout"}),
    )

    assert result["postgame"] == {"ok": True, "action": "skip", "reason": "disabled"}
    assert mgr.prepare_calls == []
    assert state["exit_reason"] == "heartbeat_timeout"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_skips_postgame_on_manual_return_to_start(monkeypatch):
    mgr = _FakePostgameTextManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
    _mark_game_started(state)

    async def fake_submit(archive):
        return {"ok": True, "status": "cached", "count": 1}

    async def fake_run_game_chat(*_args, **_kwargs):
        raise AssertionError("return-to-start should only archive, not deliver postgame")

    _gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", fake_submit)
    _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)

    result = await gr_runtime.game_end(
        "soccer",
        _FakeRequest({"session_id": "match_1", "lanlan_name": "Lan", "reason": "manual_return_to_start"}),
    )

    assert result["postgame"] == {"ok": True, "action": "skip", "reason": "disabled"}
    assert mgr.prepare_calls == []
    assert state["exit_reason"] == "manual_return_to_start"
