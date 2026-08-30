# -*- coding: utf-8 -*-
"""Game-neutral contracts for the public mini-game runtime."""

from pathlib import Path

import pytest

from main_routers.game_router import memory_policy
from main_routers.game_router import runtime


@pytest.mark.unit
def test_unknown_game_uses_only_generic_memory_policy_fields():
    state = {
        "game_type": "example-game",
        "game_memory_enabled": True,
        "game_memory_player_interaction_enabled": False,
        "game_memory_event_reply_enabled": True,
        "soccer_game_memory_enabled": True,
        "badminton_game_memory_enabled": True,
    }

    assert memory_policy._normalize_game_memory_type("example-game") == "generic"
    policy = memory_policy._game_memory_policy("example-game", state)

    assert policy == {
        "game_memory_enabled": True,
        "gameMemoryEnabled": True,
        "game_memory_player_interaction_enabled": False,
        "game_memory_event_reply_enabled": True,
        "game_memory_archive_enabled": True,
        "game_memory_postgame_context_enabled": True,
    }
    assert not any(key.startswith(("soccer_", "badminton_")) for key in policy)


@pytest.mark.unit
def test_generic_external_event_does_not_invent_game_rules_or_legacy_aliases():
    state = {
        "game_type": "example-game",
        "lanlan_name": "Test Neko",
        "game_memory_enabled": True,
        "game_memory_player_interaction_enabled": False,
        "game_memory_event_reply_enabled": True,
        "last_state": {
            "checkpoint": 4,
            "stance": "ready",
            "score": {"player": 9, "ai": 1},
            "mood": "legacy-looking-value",
        },
    }

    event = runtime._build_external_user_event(
        state,
        "continue",
        kind="user-text",
        source="external_text_route",
    )

    assert event["kind"] == "user-text"
    assert event["currentState"] == state["last_state"]
    assert event["gameMemoryEnabled"] is False
    assert event["gameMemoryEventReplyEnabled"] is True
    for key in ("round", "mood", "score", "scoreDiff", "difficulty"):
        assert key not in event
    assert not any(
        key.startswith(("soccer", "badminton"))
        for key in event
    )


@pytest.mark.unit
def test_legacy_external_event_aliases_are_explicit_and_do_not_cross_games():
    soccer = runtime._build_external_user_event(
        {"game_type": "soccer", "last_state": {}},
        "one",
        kind="user-text",
        source="external_text_route",
    )
    badminton = runtime._build_external_user_event(
        {"game_type": "badminton", "last_state": {}},
        "two",
        kind="user-voice",
        source="external_voice_route",
    )

    assert "soccerGameMemoryEnabled" in soccer
    assert "badmintonGameMemoryEnabled" not in soccer
    assert "badmintonGameMemoryEnabled" in badminton
    assert "soccerGameMemoryEnabled" not in badminton


@pytest.mark.unit
def test_public_lifecycle_state_excludes_context_and_internal_collections():
    state = {
        "game_type": "example-game",
        "session_id": "round-1",
        "lanlan_name": "Test Neko",
        "game_route_active": True,
        # Only a route that opted into SDK generations gets the narrowed
        # projection; that opt-in is what makes /context/read the sole path to
        # in-session material.
        "_sdk_route_instance_id": "route-instance-1",
        "preGameContext": {"history": "private context"},
        "game_dialog_log": [{"text": "private dialogue"}],
        "pending_outputs": [{"result": "private output"}],
        "last_state": {"secret": "game-owned state"},
    }

    visible = runtime._public_route_state(state)

    assert visible["game_route_active"] is True
    assert visible["dialog_count"] == 1
    assert visible["pending_output_count"] == 1
    assert "preGameContext" not in visible
    assert "game_dialog_log" not in visible
    assert "pending_outputs" not in visible
    assert "last_state" not in visible


@pytest.mark.unit
def test_public_lifecycle_state_keeps_the_builtin_shape_for_non_sdk_routes():
    """Built-in routes predate the capability gate and keep their full shape.

    soccer-demo.js ``_applyPreGameContext`` and badminton_demo.html
    ``applyPreGameContext`` read ``preGameContext`` (plus its source/error
    diagnostics) straight off the lifecycle response. Narrowing them to the SDK
    projection silently drops the pregame opening stance, mood and difficulty.
    """
    state = {
        "game_type": "soccer",
        "session_id": "match-1",
        "lanlan_name": "Test Neko",
        "game_route_active": True,
        "preGameContext": {"initialMood": "happy"},
        "pre_game_context_source": "ai",
        "pre_game_context_error": "",
        "game_dialog_log": [{"text": "dialogue"}],
        "pending_outputs": [],
        "_exit_flow_started": False,
    }

    visible = runtime._public_route_state(state)

    assert visible["preGameContext"] == {"initialMood": "happy"}
    assert visible["pre_game_context_source"] == "ai"
    assert visible["dialog_count"] == 1
    # Underscore-prefixed internals stay private in both projections.
    assert "_exit_flow_started" not in visible


@pytest.mark.unit
def test_a_legacy_caller_may_still_omit_session_id(monkeypatch):
    """Omitting session_id is "no assertion", not a mismatch, for legacy callers.

    The endpoints this helper replaced read a missing ``session_id`` as "skip
    the check" (``if session_id and session_id != ...``). Treating it as a hard
    mismatch silently drops e.g. a ``/route/voice-transcript`` POST from any
    page that never sent one.
    """
    state = {
        "game_type": "example-game",
        "session_id": "round-1",
        "lanlan_name": "Lan",
        "game_route_active": True,
    }
    monkeypatch.setattr(runtime, "_resolve_lanlan_name", lambda _value: "Lan")
    monkeypatch.setattr(
        runtime, "_get_active_game_route_state", lambda *_a, **_k: state
    )

    lanlan_name, session_id, resolved, error = runtime._sdk_active_route_from_payload(
        "example-game", {"lanlan_name": "Lan"}
    )

    assert error is None
    assert resolved is state
    # The active session is adopted so callers still get an authoritative id.
    assert (lanlan_name, session_id) == ("Lan", "round-1")


@pytest.mark.unit
def test_an_sdk_caller_must_still_pin_an_exact_session_id(monkeypatch):
    """Opting into SDK route generations keeps the strict session binding."""
    state = {
        "game_type": "example-game",
        "session_id": "round-1",
        "lanlan_name": "Lan",
        "game_route_active": True,
        "_sdk_route_instance_id": "route-instance-1",
    }
    monkeypatch.setattr(runtime, "_resolve_lanlan_name", lambda _value: "Lan")
    monkeypatch.setattr(
        runtime, "_get_active_game_route_state", lambda *_a, **_k: state
    )

    _lanlan, _session, _resolved, error = runtime._sdk_active_route_from_payload(
        "example-game",
        {"lanlan_name": "Lan", "sdk_route_instance_id": "route-instance-1"},
    )

    assert error is not None
    assert error["reason"] == "session_id_mismatch"


@pytest.mark.unit
def test_common_same_origin_host_has_no_game_specific_dispatch():
    source = (
        Path(__file__).parents[2]
        / "static"
        / "game"
        / "sdk"
        / "neko-minigame-same-origin-host.js"
    ).read_text(encoding="utf-8")

    assert "evaluatePassiveGuard" not in source
    assert "passive-guard" not in source
    assert "this.gameType === 'soccer'" not in source
    assert "this.gameType === 'badminton'" not in source
