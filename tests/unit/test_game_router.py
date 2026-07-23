import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
from main_routers.game_router import pregame as gr_pregame
from main_routers.game_router import runtime as gr_runtime
from main_routers.game_router import visible_events as gr_visible_events
from main_routers.system_router import AUTOSTART_CSRF_TOKEN
from main_logic.core import LLMSessionManager
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
@pytest.mark.asyncio
async def test_badminton_route_start_accepts_direct_debug_session(monkeypatch):
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    async def fake_pregame_context(**kwargs):
        assert kwargs["neko_initiated"] is False
        return gr_pregame._default_badminton_pregame_context(mode="duel"), "lightweight", ""

    _gr_patch_all(monkeypatch, "_build_badminton_pregame_context", fake_pregame_context)

    with reset_game_route_state():
        result = await gr_runtime.game_route_start(
            "badminton",
            _FakeRequest({"lanlan_name": "Lan", "session_id": "debug-badminton", "mode": "duel"}),
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
        return gr_pregame._default_soccer_pregame_context(initial_difficulty="lv2"), "lightweight", ""

    _gr_patch_all(monkeypatch, "_build_soccer_pregame_context", fake_pregame_context)

    with reset_game_route_state():
        result = await gr_runtime.game_route_start(
            "soccer",
            _FakeRequest({"lanlan_name": "Lan", "session_id": "soccer-auto-log"}),
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
    assert "var live2dPath = charData.live2d_path || '/static/yui-origin/yui-origin.model3.json';" in html
    assert "window.lanlan_config.model_type = 'live2d';" in html
    assert "window.lanlan_config.live3d_sub_type = '';" in html
    assert "await initLive2DAvatar(live2dPath);" in html
    assert "await initVRMAvatar(vrmPath);" not in html
    assert "initPIXI('neko-l2d-canvas', 'neko-l2d-container'" in html
    assert "loadModel(modelPath)" in html
    assert "var modelPath = live2dPath || '/static/yui-origin/yui-origin.model3.json'" in html
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
    assert "i18n_language: getRequestLanguage()" in html
    assert "function applyCharacterIdentity(charData)" in html
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
    assert "loadBadmintonCharacter().finally(function () { return startRoute(); });" in html
    startup = html[html.rindex("startRouteAfterCharacterReady();"):]
    assert startup.index("startRouteAfterCharacterReady();") < startup.index("initNekoAvatar();")
    assert "voiceArbiter" in html
    assert "mirror_text: false" in html
    assert "post('/mirror-assistant'" in html
    assert "post('/speak'" in html
    assert "if (pending && pending.priority <= entry.priority) return" in html
    assert "if (voiceArbiter.pending.priority <= entry.priority) return" in html
    assert "label: shooter === 'neko' ? 'neko_duel_shot' : 'player_duel_shot'" in html


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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_character_returns_live2d_path(monkeypatch):
    import main_routers.characters_router as characters_router

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
    assert result["model_type"] == "live2d"
    assert result["live2d_path"] == "/user_live2d/Lan/model.model3.json"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_character_returns_vrm_path_for_live3d_vrm(monkeypatch, tmp_path):
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
async def test_game_character_returns_mmd_path_for_live3d_mmd(monkeypatch, tmp_path):
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
    })

    async def fake_fetch(_lanlan_name):
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

    async def fake_fetch(_lanlan_name):
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

    async def fake_fetch(_lanlan_name):
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
    async def fake_run_game_chat(*_args, **_kwargs):
        raise AssertionError("stale badminton chat should not start an LLM session")

    _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    with reset_game_route_state():
        state = gr_runtime._activate_game_route("badminton", "fresh-session", "Lan")
        state["mode"] = "duel"

        result = await gr_runtime.game_chat("badminton", _FakeRequest({
            "session_id": "old-session",
            "lanlan_name": "Lan",
            "event": {"kind": "shot_missed", "mode": "duel"},
        }))

    assert result["ok"] is True
    assert result["skipped"] == "stale_session"
    assert result["reason"] == "session_id_mismatch"
    assert result["line"] == ""
    assert result["control"] == {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_game_chat_rejects_missing_route_before_llm(monkeypatch):
    async def fake_run_game_chat(*_args, **_kwargs):
        raise AssertionError("inactive badminton chat should not start an LLM session")

    _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    with reset_game_route_state():
        result = await gr_runtime.game_chat("badminton", _FakeRequest({
            "session_id": "old-session",
            "lanlan_name": "Lan",
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

    result = await gr_runtime.game_realtime_context(
        "soccer",
        _FakeRequest({
            "lanlan_name": "Lan",
            "source": "game_event",
            "currentState": {"score": {"player": 1, "ai": 2}},
            "pendingItems": [{"type": "game_event", "kind": "goal-scored"}],
        }),
    )

    assert result["ok"] is True
    assert result["action"] == "skip"
    assert result["reason"] == "gemini_no_session_update"
    assert session.prime_context_calls == []
    assert session.create_response_calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_realtime_context_endpoint_requires_local_mutation_csrf(monkeypatch, _fake_realtime):
    session = _fake_realtime(model_lower="qwen-realtime", delivered=True)
    mgr = _FakeRealtimeManager(session)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})

    result = await gr_runtime.game_realtime_context(
        "soccer",
        _FakeRequest({
            "lanlan_name": "Lan",
            "source": "game_event",
            "currentState": {"score": {"player": 1, "ai": 2}},
            "pendingItems": [{"type": "game_event", "kind": "goal-scored"}],
        }, mutation_headers=False, path="/api/game/soccer/realtime-context"),
    )

    assert isinstance(result, JSONResponse)
    assert result.status_code == 403
    assert b"csrf_validation_failed" in result.body
    assert mgr.append_context_calls == []
    assert session.prime_context_calls == []


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

    result = await gr_runtime.game_realtime_context(
        "soccer",
        _FakeRequest({
            "lanlan_name": "Lan",
            "source": "game_event",
            "currentState": {"score": {"player": 1, "ai": 2}},
            "pendingItems": [{"type": "game_event", "kind": "goal-scored"}],
        }, path="/api/game/soccer/realtime-context"),
    )

    assert result == {"ok": False, "reason": "realtime_session_changed", "lanlan_name": "Lan"}
    assert mgr.append_context_calls == []
    assert original.prime_context_calls == []
    assert replacement.prime_context_calls == []


class _FakeGameRouteManager:
    def __init__(self):
        self.is_active = False
        self.session = None
        self.input_mode = "audio"
        self.mirrored = []
        self.assistant_mirrored = []
        self.spoken = []
        self.statuses = []
        self.user_activity_count = 0
        self._takeover_active = False
        self._takeover_input_dispatcher = None

    async def mirror_user_input(self, text, **kwargs):
        self.mirrored.append((text, kwargs))

    async def mirror_assistant_output(self, text, **kwargs):
        self.assistant_mirrored.append((text, kwargs))
        return {"ok": True, "mirrored": True, "method": "project_text_mirror"}

    async def send_user_activity(self):
        self.user_activity_count += 1

    async def mirror_assistant_speech(self, line, **kwargs):
        self.spoken.append((line, kwargs))
        return {
            "ok": True,
            "method": "project_tts",
            "speech_id": "game-speech",
            "audio_sent": True,
            "voice_source": {"provider": "project_tts"},
        }

    async def send_status(self, message):
        self.statuses.append(message)


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
    state = result["state"]
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
        }),
    )

    assert result["ok"] is True
    state = result["state"]
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

    async def fake_run_game_chat(game_type, session_id, event):
        assert game_type == "soccer"
        assert session_id == "match_1"
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
async def test_route_external_text_uses_no_memory_input_type_when_game_memory_disabled(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})

    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
    _set_soccer_game_memory_policy(state, enabled=False)

    async def fake_run_game_chat(game_type, session_id, event):
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

    async def fake_run_game_chat(game_type, session_id, event):
        assert game_type == "soccer"
        assert session_id == "match_1"
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

    async def fake_run_game_chat(game_type, session_id, event):
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

    async def fake_run_game_chat(game_type, session_id, event):
        return {"line": "好。", "control": {}, "llm_source": {"provider": "fake"}}

    _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)

    fake_now = {"t": 10_000.0}
    monkeypatch.setattr(gr_runtime.time, "time", lambda: fake_now["t"])

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

    async def fake_run_game_chat(game_type, session_id, event):
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

    async def fake_run_game_chat(game_type, session_id, event):
        return {"line": "好。", "control": {}, "llm_source": {"provider": "fake"}}

    _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)

    fake_now = {"t": 1000.95}
    monkeypatch.setattr(gr_runtime.time, "time", lambda: fake_now["t"])

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

    result = await gr_runtime.game_project_speak(
        "soccer",
        _FakeRequest({"line": "换我进攻了", "session_id": "match_1", "request_id": "req-2"}),
    )

    assert result["ok"] is True
    assert result["method"] == "project_tts"
    assert result["voice_source"]["provider"] == "project_tts"
    assert mgr.spoken == [("换我进攻了", {
        "metadata": {
            "source": "game_route",
            "kind": "soccer",
            "session_id": "match_1",
            "mirror": {"kind": "soccer", "session_id": "match_1", "event": {}},
        },
        "request_id": "req-2",
        "mirror_text": True,
        "emit_turn_end_after": True,
        "interrupt_audio": False,
    })]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speak_can_skip_text_mirror_for_frontend_arbiter(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

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
    assert mgr.spoken == [("只播放语音", {
        "metadata": {
            "source": "game_route",
            "kind": "soccer",
            "session_id": "match_1",
            "mirror": {"kind": "soccer", "session_id": "match_1", "event": {}},
        },
        "request_id": "req-voice",
        "mirror_text": False,
        "emit_turn_end_after": False,
        "interrupt_audio": False,
    })]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speak_forwards_interrupt_audio(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

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
    assert mgr.spoken == [("先听我说完", {
        "metadata": {
            "source": "game_route",
            "kind": "soccer",
            "session_id": "match_1",
            "mirror": {"kind": "soccer", "session_id": "match_1", "event": {}},
        },
        "request_id": "req-interrupt",
        "mirror_text": False,
        "emit_turn_end_after": False,
        "interrupt_audio": True,
    })]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speak_rejects_stale_route_session(monkeypatch):
    with reset_game_route_state():
        mgr = _FakeGameRouteManager()
        _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
        state = gr_runtime._activate_game_route("soccer", "match_new", "Lan")

        result = await gr_runtime.game_project_speak(
            "soccer",
            _FakeRequest({
                "line": "old line",
                "session_id": "match_old",
                "lanlan_name": "Lan",
                "request_id": "req-stale-speak",
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
async def test_project_mirror_assistant_uses_text_only_mirror(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    result = await gr_runtime.game_project_mirror_assistant(
        "soccer",
        _FakeRequest({
            "line": "文字先进入主聊天窗",
            "session_id": "match_1",
            "request_id": "req-mirror",
            "turn_id": "turn-mirror",
            "source": "game-llm-result",
        }),
    )

    assert result["ok"] is True
    assert result["method"] == "project_text_mirror"
    assert mgr.assistant_mirrored == [("文字先进入主聊天窗", {
        "metadata": {
            "source": "game-llm-result",
            "kind": "soccer",
            "session_id": "match_1",
            "mirror": {"kind": "soccer", "session_id": "match_1", "event": {}},
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
async def test_project_mirror_assistant_finalizes_user_reply_by_default(monkeypatch):
    mgr = _FakeGameRouteManager()
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    _gr_patch_all(monkeypatch, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

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
    assert mgr.assistant_mirrored == [("听见啦，我会放慢一点。", {
        "metadata": {
            "source": "game-llm-result",
            "kind": "soccer",
            "session_id": "match_1",
            "mirror": {
                "kind": "soccer",
                "session_id": "match_1",
                "event": {"kind": "user-text", "hasUserText": True},
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

    async def fake_run_game_chat(_game_type, _session_id, event):
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
    # now relies on plain prompt_ephemeral (server VAD + WAV nudge). The
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
async def test_game_end_uses_direct_response_for_gemini_postgame(monkeypatch, _fake_realtime):
    session = _fake_realtime(model_lower="gemini-2.5-flash-native-audio-preview", delivered=True)
    mgr = _FakeRealtimeManager(session)
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": mgr})
    state = gr_runtime._activate_game_route("soccer", "match_1", "Lan")
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
    assert "不要继续扮演游戏仍在进行" in session.create_response_calls[0]


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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_end_uses_full_game_end_contract(monkeypatch):
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
        return {"line": "刚才那脚挺像样的。", "llm_source": {"provider": "fake"}}

    _gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", fake_submit)
    _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)

    result = await gr_runtime.game_route_end(
        "soccer",
        _FakeRequest({"session_id": "match_1", "lanlan_name": "Lan"}),
    )

    assert result["ok"] is True
    assert result["closed"] is True
    assert result["route_closed"] is True
    assert result["archive"]["exit_reason"] == "route_end"
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
