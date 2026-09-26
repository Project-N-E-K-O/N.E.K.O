"""玩家动作结果投影只保留可证明的完成与未来边界。"""  # noqa: DOCSTRING_CJK

import pytest

from services.theater.numeric_v2_action_projection import (
    PLAYER_ACTION_PROJECTION_SCHEMA,
    normalize_player_action_projection,
    project_player_action_result,
)
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from main_routers.numeric_theater_router import _public_performance


def test_explicit_departure_and_future_follow_up_are_separate():
    projection = project_player_action_result("先回去复习，周六见。", revision=4)

    assert projection["schema"] == PLAYER_ACTION_PROJECTION_SCHEMA
    assert projection["player_left_current_scene"] is True
    assert projection["confirmed_actions"] == [{
        "kind": "leave_current_scene",
        "status": "completed",
        "source": "player_input",
        "evidence_quote": "先回去复习，周六见。",
    }]
    assert projection["future_references"] == [{
        "kind": "future_return_or_follow_up",
        "status": "future",
        "source": "player_input",
        "evidence_quote": "先回去复习，周六见。",
    }]


def test_parenthetical_departure_can_coexist_with_future_follow_up():
    projection = project_player_action_result("（推门离开，风铃轻响）明天见。")

    assert projection["player_left_current_scene"] is True
    assert projection["confirmed_actions"][0]["kind"] == "leave_current_scene"
    assert projection["future_references"][0]["kind"] == "future_return_or_follow_up"


def test_parenthetical_exit_to_door_is_a_completed_departure():
    projection = project_player_action_result("（背起书包走向门口）嗯，明天见。")

    assert projection["player_left_current_scene"] is True


def test_questions_and_plans_do_not_become_completed_actions():
    for message in (
        "我想回去复习。",
        "可以回去吗？",
        "我准备签字。",
        "如果有空再来。",
        "周六见吗？",
    ):
        projection = project_player_action_result(message)
        assert projection["confirmed_actions"] == []
        assert projection["player_left_current_scene"] is False
        assert projection["future_references"] == []


def test_noun_containing_action_character_is_not_a_player_action():
    projection = project_player_action_result(
        "（并肩跟上步伐）我想尝尝你推荐的拿铁。"
    )

    assert projection["confirmed_actions"] == []


def test_runtime_results_are_projected_without_claiming_player_success():
    projection = project_player_action_result(
        "我已经签了。",
        revision=3,
        transition_intent="accept",
        route_changed=True,
        fact_operations=({"key": "scene:start:signed", "value": True},),
    )

    assert [row["kind"] for row in projection["confirmed_actions"]] == [
        "player_action", "transition_committed", "runtime_result",
    ]
    assert projection["confirmed_actions"][-1] == {
        "kind": "runtime_result",
        "status": "confirmed",
        "source": "runtime_fact",
        "fact_key": "scene:start:signed",
        "value": True,
    }


def test_normalization_rebuilds_flags_and_drops_untrusted_fields():
    normalized = normalize_player_action_projection({
        "schema": PLAYER_ACTION_PROJECTION_SCHEMA,
        "source_revision": "bad",
        "confirmed_actions": [{
            "kind": "leave_current_scene",
            "status": "completed",
            "source": "player_input",
            "evidence_quote": "先走了。",
            "untrusted": "ignore",
        }],
        "future_references": [],
        "has_confirmed_player_action": False,
        "player_left_current_scene": False,
        "untrusted": "ignore",
    })

    assert normalized["has_confirmed_player_action"] is True
    assert normalized["player_left_current_scene"] is True
    assert normalized["source_revision"] == 0
    assert "untrusted" not in normalized["confirmed_actions"][0]


@pytest.mark.asyncio
async def test_runtime_persists_projection_for_replay_but_public_record_can_filter_it(tmp_path):
    runtime = NumericV2Runtime(_engine(), tmp_path)
    stored = await runtime.start_session(
        session_id="action_projection_runtime",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    outcome = runtime.prepare_turn(
        stored,
        TurnRequestV2("leave", 0, "先回去复习，周六见。"),
        (),
    )

    assert outcome.ledger_event["player_action_projection"]["player_left_current_scene"] is True
    committed = await runtime.commit_turn(
        outcome,
        {"performance": "（点头）好，周六见。", "suggested_inputs": []},
    )
    projection = committed.session.performance_history[-1]["player_action_projection"]
    assert projection["future_references"][0]["status"] == "future"
    assert committed.ledger_events[-1]["player_action_projection"] == projection
    assert "player_action_projection" not in _public_performance(
        committed.session.performance_history[-1]
    )
