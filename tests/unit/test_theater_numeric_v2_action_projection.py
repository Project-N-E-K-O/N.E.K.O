"""Project player action outcomes using only provable completion and future boundaries."""  # noqa: DOCSTRING_CJK

from copy import deepcopy
import json

import pytest

from services.theater.numeric_v2_action_projection import (
    PLAYER_ACTION_PROJECTION_SCHEMA,
    normalize_player_action_projection,
    project_player_action_result,
)
from services.theater.numeric_v2_actor import _turn_messages
from services.theater.numeric_v2_evaluator import _build_transition_judge_messages
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


def test_parenthetical_exit_to_door_is_not_a_completed_departure():
    projection = project_player_action_result("（背起书包走向门口）嗯，明天见。")

    assert projection["player_left_current_scene"] is False
    assert projection["player_departure_pending"] is True


@pytest.mark.parametrize("message", [
    "（走向门口，停在门内）我还有个问题。",
    "（提起工具箱走向门口，停在气闸内侧）我再核对一下仪表。",
    "（走向巷口，停在巷口前）等一下。",
    "我们走向门口。",
    "我先走向巷口。",
])
def test_approaching_an_exit_does_not_claim_boundary_crossing(message):
    projection = project_player_action_result(message)

    assert projection["player_left_current_scene"] is False
    assert projection["player_departure_pending"] is True
    assert normalize_player_action_projection(projection)["player_departure_pending"] is True


@pytest.mark.parametrize("message", [
    "（走向门口，又折回柜台）先不走了。",
    "（走向门口，又折回控制台）先不走了。",
    "我准备走向门口。",
    "我可以走向门口吗？",
])
def test_cancelled_or_uncommitted_departure_does_not_suppress_new_offer(message):
    projection = project_player_action_result(message)

    assert projection["player_left_current_scene"] is False
    assert projection["player_departure_pending"] is False


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


@pytest.mark.parametrize("message", [
    "我不会离开你。",
    "我不走了，留下来陪你",
    "我绝不回家",
    "我决不离开这里。",
    "我没走。",
    "别走了，我陪着你。",
    "我不签了。",
])
def test_negated_departure_or_action_is_not_projected(message):
    """A verb directly preceded by a negation, optionally with a modal, is not a completed action on either path."""

    projection = project_player_action_result(message)

    assert projection["player_left_current_scene"] is False
    assert projection["has_confirmed_player_action"] is False
    assert projection["confirmed_actions"] == []


@pytest.mark.parametrize("message,kind", [
    ("我离开了。", "leave_current_scene"),
    ("我走了", "leave_current_scene"),
    ("我不得不走了。", "leave_current_scene"),
    ("我签了字。", "player_action"),
])
def test_affirmative_departure_and_action_still_project(message, kind):
    """Negation handling must not suppress plain completed actions."""

    projection = project_player_action_result(message)

    assert [row["kind"] for row in projection["confirmed_actions"]] == [kind]


def test_noun_containing_action_character_is_not_a_player_action():
    projection = project_player_action_result(
        "（并肩跟上步伐）我想尝尝你推荐的拿铁。"
    )

    assert projection["confirmed_actions"] == []


@pytest.mark.parametrize("message,term,clause", [
    ("（拿起地图）等通知来了再决定要不要参加社区活动。", "拿", "（拿起地图）"),
    ("（拿起星图）我先核对坐标，等基地通知来了再决定要不要参加远征。", "拿", "（拿起星图）"),
    ("我已经核对坐标。之后再决定是否参加远征。", "核对", "我已经核对坐标"),
])
def test_action_term_and_evidence_come_from_the_validated_clause(message, term, clause):
    projection = project_player_action_result(message)

    assert len(projection["confirmed_actions"]) == 1
    assert projection["confirmed_actions"][0]["action_term"] == term
    assert projection["confirmed_actions"][0]["evidence_quote"] == clause


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


def test_approach_does_not_hide_a_subsequent_explicit_departure():
    projection = project_player_action_result("我们走向门口，我离开这里。")
    assert projection["player_left_current_scene"] is True
    assert projection["player_departure_pending"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("include_projection", [True, False])
async def test_legacy_committed_projection_survives_restore_and_fork(tmp_path, monkeypatch, include_projection):
    import json
    from copy import deepcopy
    from services.theater import numeric_v2_runtime as runtime_module

    message = "（走向门口，停在门内）我还有个问题。"
    legacy = normalize_player_action_projection({
        "schema": PLAYER_ACTION_PROJECTION_SCHEMA,
        "source_revision": 1,
        "confirmed_actions": [{
            "kind": "leave_current_scene", "status": "completed",
            "source": "player_input", "evidence_quote": message,
        }],
        "future_references": [],
    })
    assert legacy["player_left_current_scene"] is True
    runtime = NumericV2Runtime(_engine(), tmp_path)
    current = await runtime.start_session(
        session_id="legacy_departure_projection", catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    # Emulate a record already committed by the old extractor. The new parser
    # changes future extraction only, never silently repairs stored evidence.
    with monkeypatch.context() as old_extractor:
        old_extractor.setattr(runtime_module, "project_player_action_result",
                              lambda *_args, **_kwargs: deepcopy(legacy))
        outcome = runtime.prepare_turn(current, TurnRequestV2("old-turn", 0, message), ())
        committed = await runtime.commit_turn(
            outcome, {"performance": "（点头）我继续听着。", "suggested_inputs": []},
        )
    path = runtime.store._path(current.session.session_id)
    if not include_projection:
        # Pre-projection archives omit the field on both records. Recreate
        # that old serialized shape only in this test's isolated directory.
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["ledger_events"][-1].pop("player_action_projection")
        payload["session"]["performance_history"][-1].pop("player_action_projection")
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        committed = await runtime.restore_session(current.session.session_id)
    assert project_player_action_result(message)["player_left_current_scene"] is False
    before = path.read_bytes()
    assert await runtime.restore_session(current.session.session_id) == committed
    fork = await runtime.fork_session_for_test(
        current.session.session_id, session_id="legacy_departure_fork", through_revision=1,
    )
    for stored in (committed, fork):
        for record in (stored.ledger_events[-1], stored.session.performance_history[-1]):
            if include_projection:
                assert record["player_action_projection"] == legacy
            else:
                assert "player_action_projection" not in record
    assert await runtime.restore_session(fork.session.session_id) == fork
    assert path.read_bytes() == before
    next_turn = runtime.prepare_turn(fork, TurnRequestV2("new-turn", 1, message), ())
    assert next_turn.ledger_event["player_action_projection"]["player_left_current_scene"] is False


# 完整输入判定与证据摘录边界；保留提交、冷恢复和分叉验证。
PADDING = " a" * 90


@pytest.mark.parametrize("ending", ["吗？", "？", "?"])
def test_question_after_evidence_limit_does_not_become_departure(ending):
    message = "我离开这里" + PADDING + ending
    assert len(message) > 180
    projection = project_player_action_result(message)
    assert projection["confirmed_actions"] == []
    assert not projection["player_left_current_scene"]


def test_uncommitted_marker_after_evidence_limit_still_qualifies_the_clause():
    projection = project_player_action_result("我离开这里" + PADDING + " 只是准备")
    assert projection["confirmed_actions"] == []
    assert not projection["player_left_current_scene"]


def test_generic_action_uses_the_question_at_the_end_of_the_full_clause():
    projection = project_player_action_result("我已经签了字" + PADDING + "吗？")
    assert projection["confirmed_actions"] == []


def test_future_follow_up_uses_the_question_at_the_end_of_the_full_input():
    projection = project_player_action_result("周六见" + PADDING + "吗？")
    assert projection["future_references"] == []


@pytest.mark.parametrize("message,field,kind", [
    (PADDING + " 我已经签了字。", "confirmed_actions", "player_action"),
    (PADDING + " 周六见。", "future_references", "future_return_or_follow_up"),
])
def test_a_late_action_or_future_reference_is_not_lost(message, field, kind):
    projection = project_player_action_result(message)
    assert projection[field][0]["kind"] == kind
    assert len(projection[field][0]["evidence_quote"]) <= 180


@pytest.mark.parametrize("prefix,kind", [
    ("我离开这里", "leave_current_scene"),
    ("我走向门口", "approach_scene_exit"),
    ("我已经签了字", "player_action"),
])
def test_long_affirmative_inputs_keep_short_evidence_without_losing_the_action(prefix, kind):
    message = prefix + PADDING
    projection = project_player_action_result(message)
    assert projection["confirmed_actions"][0]["kind"] == kind
    quote = projection["confirmed_actions"][0]["evidence_quote"]
    assert len(quote) <= 180
    assert message.startswith(quote)
    normalized = normalize_player_action_projection(projection)
    assert normalized["confirmed_actions"][0]["kind"] == kind
    assert normalized["player_left_current_scene"] == projection["player_left_current_scene"]


@pytest.mark.asyncio
async def test_valid_long_question_stays_unperformed_through_commit_restore_and_fork(tmp_path):
    message = "我离开这里" + PADDING + "吗？"
    # 字符摘录上限与真实 140-token 输入上限不同；先走正式请求校验，避免用非法输入复现。
    turn = TurnRequestV2.from_mapping({"client_turn_id": "long-question", "base_revision": 0,
        "message": message, "input_source": "freeform"})
    runtime = NumericV2Runtime(_engine(), tmp_path)
    current = await runtime.start_session(session_id="full-input", catgirl_binding=_binding(),
        opening_performance=_opening())
    untouched = deepcopy(current)
    outcome = runtime.prepare_turn(current, turn, ())
    assert not outcome.ledger_event["player_action_projection"]["player_left_current_scene"]
    assert current == untouched
    committed = await runtime.commit_turn(outcome,
        {"performance": "（点头）你可以再考虑一下。", "suggested_inputs": []})
    path = runtime.store._path(current.session.session_id)
    saved = path.read_bytes()
    restored = await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id)
    assert restored == committed
    fork = await runtime.fork_session_for_test(current.session.session_id,
        session_id="full-input-fork", through_revision=1)
    for stored in (restored, fork):
        assert stored.session.current_node_id == "start"
        assert stored.ledger_events[-1]["player_action_projection"]["confirmed_actions"] == []
        assert stored.session.performance_history[-1]["input_text"] == message
        assert await runtime.restore_session(stored.session.session_id) == stored
    assert path.read_bytes() == saved
    assert current == untouched


# 明确区分玩家与其他角色的动作主体，避免串用离场授权。
@pytest.mark.parametrize("message", [
    "（她离开）",
    "(你离开)",
    "（您离开）",
    "（他们离开）",
    "（你们离开）",
    "（她们离开）",
    "（他打开门）",
])
def test_explicit_other_subject_is_not_a_player_action(message):
    # 括号表示动作描述，但不能因此把明确属于别人的动作归给玩家。
    projection = project_player_action_result(message)

    assert projection["confirmed_actions"] == []
    assert projection["has_confirmed_player_action"] is False
    assert projection["player_left_current_scene"] is False
    assert projection["player_departure_pending"] is False


@pytest.mark.parametrize("message", [
    "（我签了字，她离开）",
    "（她离开，我签了字）",
])
def test_players_signature_does_not_lend_its_subject_to_another_departure(message):
    projection = project_player_action_result(message)

    assert projection["player_left_current_scene"] is False
    assert projection["player_departure_pending"] is False
    assert [row["kind"] for row in projection["confirmed_actions"]] == ["player_action"]
    assert projection["confirmed_actions"][0]["action_term"] == "签"


@pytest.mark.parametrize("message", [
    "（你走向门口，我留下）",
    "（我留下，你走向门口）",
])
def test_players_other_action_does_not_validate_another_subjects_exit_approach(message):
    projection = project_player_action_result(message)

    assert projection["player_left_current_scene"] is False
    assert projection["player_departure_pending"] is False
    assert [row["kind"] for row in projection["confirmed_actions"]] == ["player_action"]
    assert projection["confirmed_actions"][0]["action_term"] == "留下"


@pytest.mark.parametrize("message", [
    "（点头致意，转身推门离开）",
    "（向她告别后转身离开）",
    "（她看着我离开）",
    "（她站着我离开）",
    "（你的包我提着离开）",
    "（他们的包我拿着离开）",
    "（我离开，她签了字）",
    "先回去复习，周六见。",
])
def test_subject_filter_preserves_existing_player_departures(message):
    # 本轮只排除明确的他人主语；省略主语、物主代词和后续明确的“我”维持原语义。
    projection = project_player_action_result(message)

    assert projection["player_left_current_scene"] is True
    assert projection["player_departure_pending"] is False
    assert [row["kind"] for row in projection["confirmed_actions"]] == ["leave_current_scene"]


@pytest.mark.asyncio
async def test_subject_attribution_reaches_prompts_and_survives_commit_restore_and_fork(tmp_path):
    runtime = NumericV2Runtime(_engine(), tmp_path)
    current = await runtime.start_session(
        session_id="explicit-subject", catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    untouched = deepcopy(current)
    message = "（我签了字，她离开）"
    outcome = runtime.prepare_turn(current, TurnRequestV2("signature", 0, message), ())
    projection = outcome.ledger_event["player_action_projection"]
    assert projection["player_left_current_scene"] is False
    assert projection["player_departure_pending"] is False
    assert projection["confirmed_actions"][0]["action_term"] == "签"

    # 使用正式 prompt builders，验证错误离场标志不会继续传给 Actor 或 Review；不调用模型。
    actor_messages = _turn_messages(
        runtime.engine, current.session, outcome, message, "克制。", "小岚", "你",
        player_action_projection=projection,
    )
    actor_text = actor_messages[0].content
    projection_start = actor_text.index('{"schema":"neko.script.player_action_projection.numeric.v1"')
    actor_projection, _ = json.JSONDecoder().raw_decode(actor_text[projection_start:])
    assert actor_projection == projection
    performance = {"performance": "（点头）签字收到了。", "suggested_inputs": []}
    review_messages, _ = _build_transition_judge_messages(
        runtime.engine, current.session, player_input=message,
        actor_performance=performance, player_action_projection=projection,
    )
    review_text = review_messages[1].content
    review_data = json.loads(review_text[review_text.index("{"):])
    assert review_data["player_action_projection"] == projection

    committed = await runtime.commit_turn(outcome, performance)
    path = runtime.store._path(current.session.session_id)
    saved = path.read_bytes()
    restored = await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id)
    assert restored == committed
    fork = await runtime.fork_session_for_test(
        current.session.session_id, session_id="explicit-subject-fork", through_revision=1,
    )
    for stored in (restored, fork):
        assert stored.session.current_node_id == "start"
        assert stored.ledger_events[-1]["player_action_projection"] == projection
        assert stored.session.performance_history[-1]["player_action_projection"] == projection
        assert await runtime.restore_session(stored.session.session_id) == stored
    assert path.read_bytes() == saved
    assert current == untouched
