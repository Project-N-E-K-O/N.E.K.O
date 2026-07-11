from __future__ import annotations

from pathlib import Path

import pytest

from .game_route_test_helpers import mark_game_started
from main_routers import game_router
from main_routers.game_router import archive as gr_archive
from main_routers.game_router import game_context as gr_game_context
from main_routers.game_router import memory_policy as gr_memory_policy
from main_routers.game_router import runtime as gr_runtime


def _gr_patch_all(monkeypatch, name, value, raising=True):
    """Patch the same object onto every submodule that holds the binding.

    Restores pre-split semantics: with monolithic game_router a single
    setattr hit the one namespace all flows resolved against; after the
    package split, from-import snapshots live in several submodules'
    globals, so patch them all with the same object."""
    from main_routers.game_router import (
        _shared, char_info, logs, memory_policy, game_context, pregame,
        visible_events, balance, badminton_scores, archive, runtime,
    )
    hit = False
    for _m in (_shared, char_info, logs, memory_policy, game_context, pregame,
               visible_events, balance, badminton_scores, archive, runtime):
        if hasattr(_m, name):
            monkeypatch.setattr(_m, name, value)
            hit = True
    if not hit and raising:
        raise AttributeError("no game_router submodule has %r" % name)



def _badminton_html() -> str:
    return Path(__file__).resolve().parents[2].joinpath("templates/badminton_demo.html").read_text(
        encoding="utf-8"
    )


def _badminton_route_state(monkeypatch: pytest.MonkeyPatch) -> dict:
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})
    return gr_runtime._build_route_state("badminton", "bd-session", "Lan")


@pytest.mark.unit
def test_badminton_game_memory_policy_uses_badminton_fields_and_aliases():
    policy = gr_memory_policy._game_memory_policy("badminton", {})

    for field in gr_memory_policy._game_memory_policy_fields("badminton"):
        assert policy[field] is False
    assert policy["game_memory_enabled"] is False
    assert "soccer_game_memory_enabled" not in policy

    enabled = gr_memory_policy._game_memory_policy(
        "badminton",
        {"badmintonGameMemoryEnabled": True},
    )

    assert enabled["badminton_game_memory_enabled"] is True
    assert enabled["badminton_game_memory_player_interaction_enabled"] is True
    assert enabled["badminton_game_memory_event_reply_enabled"] is True
    assert enabled["badminton_game_memory_archive_enabled"] is True
    assert enabled["badminton_game_memory_postgame_context_enabled"] is True
    assert enabled["gameMemoryEnabled"] is True
    assert enabled["game_memory_archive_enabled"] is True


@pytest.mark.unit
def test_badminton_memory_payload_updates_state_without_touching_soccer_fields(monkeypatch):
    state = _badminton_route_state(monkeypatch)

    gr_runtime._update_game_memory_enabled_from_payload(
        state,
        {
            "badmintonGameMemoryEnabled": True,
            "badmintonGameMemoryPlayerInteractionEnabled": False,
            "badmintonGameMemoryEventReplyEnabled": True,
            "badmintonGameMemoryArchiveEnabled": False,
            "badmintonGameMemoryPostgameContextEnabled": True,
        },
        game_type="badminton",
    )

    assert state["badminton_game_memory_enabled"] is True
    assert state["badminton_game_memory_player_interaction_enabled"] is False
    assert state["badminton_game_memory_event_reply_enabled"] is True
    assert state["badminton_game_memory_archive_enabled"] is False
    assert state["badminton_game_memory_postgame_context_enabled"] is True
    assert state["game_memory_enabled"] is True
    assert state["game_memory_player_interaction_enabled"] is False
    assert state["game_memory_archive_enabled"] is False
    assert state["soccer_game_memory_enabled"] is False


@pytest.mark.unit
def test_badminton_memory_policy_is_attached_to_events_and_archives(monkeypatch):
    state = _badminton_route_state(monkeypatch)
    state.update(
        {
            "badminton_game_memory_enabled": True,
            "badminton_game_memory_player_interaction_enabled": False,
            "badminton_game_memory_event_reply_enabled": True,
            "badminton_game_memory_archive_enabled": True,
            "badminton_game_memory_postgame_context_enabled": False,
        }
    )

    event = gr_memory_policy._attach_game_memory_flag_to_event(
        {"kind": "shot_result"},
        state,
        game_type="badminton",
    )

    assert event["badmintonGameMemoryEnabled"] is True
    assert event["badmintonGameMemoryPlayerInteractionEnabled"] is False
    assert event["badmintonGameMemoryEventReplyEnabled"] is True
    assert event["gameMemoryEnabled"] is True
    assert event["gameMemoryPlayerInteractionEnabled"] is False
    assert event["gameMemoryEventReplyEnabled"] is True

    archive = gr_archive._build_game_archive(state)
    assert archive["badminton_game_memory_enabled"] is True
    assert archive["badminton_game_memory_player_interaction_enabled"] is False
    assert archive["badminton_game_memory_postgame_context_enabled"] is False
    assert archive["game_memory_archive_enabled"] is True


@pytest.mark.unit
def test_badminton_memory_controls_external_events_and_archive_filters(monkeypatch):
    state = _badminton_route_state(monkeypatch)
    state.update(
        {
            "badminton_game_memory_enabled": True,
            "badminton_game_memory_player_interaction_enabled": False,
            "badminton_game_memory_event_reply_enabled": True,
            "last_state": {"score": {"player": 2, "ai": 1}, "round": 4},
        }
    )

    event = gr_runtime._build_external_user_event(
        state,
        "nice shot",
        kind="user-text",
        source="external_text_route",
    )

    assert event["badmintonGameMemoryEnabled"] is True
    assert event["badmintonGameMemoryPlayerInteractionEnabled"] is False
    assert event["gameMemoryEnabled"] is False
    assert event["gameMemoryPlayerInteractionEnabled"] is False
    assert event["gameMemoryEventReplyEnabled"] is True

    archive = {
        "game_type": "badminton",
        "badminton_game_memory_enabled": True,
        "badminton_game_memory_player_interaction_enabled": False,
        "badminton_game_memory_event_reply_enabled": True,
    }
    assert gr_game_context._game_dialog_item_allowed_for_memory({"type": "user"}, archive) is False
    assert (
        gr_game_context._game_dialog_item_allowed_for_memory(
            {"type": "assistant", "source": "opening_line"}, archive
        )
        is True
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_external_transcript_meta_uses_badminton_prefix(monkeypatch):
    state = _badminton_route_state(monkeypatch)
    state.update(
        {
            "badminton_game_memory_enabled": True,
            "badminton_game_memory_player_interaction_enabled": False,
            "badminton_game_memory_event_reply_enabled": True,
            "last_state": {"score": {"player": 3, "ai": 2}, "round": 5},
        }
    )
    _gr_patch_all(monkeypatch, "get_session_manager", lambda: {})

    async def fake_run_game_chat(game_type, session_id, event):
        return {"line": "ok", "control": {}, "game_type": game_type, "session_id": session_id}

    _gr_patch_all(monkeypatch, "_run_game_chat", fake_run_game_chat)

    handled = await gr_runtime._route_external_transcript_to_game(
        "Lan",
        state,
        "nice shot",
        source="external_text_route",
        mode="text",
        kind="user-text",
        request_id="req-1",
    )

    assert handled is True
    first_meta = state["pending_outputs"][0]["meta"]
    result_meta = state["pending_outputs"][1]["meta"]
    assert first_meta["badmintonGameMemoryPlayerInteractionEnabled"] is False
    assert first_meta["badminton_game_memory_player_interaction_enabled"] is False
    assert "soccerGameMemoryPlayerInteractionEnabled" not in first_meta
    assert result_meta["badmintonGameMemoryPlayerInteractionEnabled"] is False
    assert result_meta["gameMemoryEnabled"] is False


@pytest.mark.unit
def test_badminton_archive_disabled_uses_generic_skip_reason(monkeypatch):
    state = mark_game_started(_badminton_route_state(monkeypatch))
    state["badminton_game_memory_archive_enabled"] = False
    state["game_memory_archive_enabled"] = False

    reason = gr_archive._game_archive_memory_skip_reason(state, "route_end")
    skipped = gr_archive._build_game_archive_memory_skipped_result(reason)

    assert reason == "game_memory_archive_disabled"
    assert skipped["status"] == "skipped"
    assert skipped["reason"] == "game_memory_archive_disabled"
    assert skipped["message"].startswith("game archive memory disabled;")


@pytest.mark.unit
def test_badminton_demo_memory_toggle_and_payload_contract():
    html = _badminton_html()

    assert 'id="bd-game-memory-toggle"' in html
    assert "本局对话进入记忆（默认不开启）" in html
    assert "关闭后，玩家输入、NEKO直接回应、事件回应、赛后摘要和后续续接都不会进入或引用记忆。" in html
    assert "function _isBadmintonGameMemoryEnabled()" in html
    assert "function _badmintonGameMemoryPolicyPayload()" in html
    assert "function getGameMemoryPolicyPayload" not in html
    assert html.count("_badmintonGameMemoryPolicyPayload()") >= 7
    assert "badmintonGameMemoryEnabled: enabled" in html
    assert "badminton_game_memory_enabled: enabled" in html
    assert "badmintonGameMemoryEnabled: enabled" in html
    assert "badminton_game_memory_enabled: enabled" in html
    assert "gameMemoryArchiveEnabled: enabled" in html
    assert "game_memory_postgame_context_enabled: enabled" in html
    assert "gameMemoryToggle.checked === true" in html
    assert "readBadmintonStorage('bd_record_distance')" in html
    assert "event: Object.assign({}, event, _badmintonGameMemoryPolicyPayload(), {" in html
