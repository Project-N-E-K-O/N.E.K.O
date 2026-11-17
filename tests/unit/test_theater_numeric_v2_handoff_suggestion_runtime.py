"""Clicked player actions authorize a handoff, never the recipient's completed result."""

from dataclasses import replace

import pytest

from services.theater.numeric_v2_action_projection import player_handoff_is_explicit
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding

KEY = "scene:start:delivered"
INPUT = "推门而入，将剑匣递向旧友"
RECEIPT = "旧友伸出双手，稳稳接住了剑匣。"


def _engine():
    story = numeric_v2_story()
    story["fact_contract"] = {"facts": {KEY: {
        "value_type": "bool", "visibility": "public", "evidence_source": "performance",
        "description": "旧友已实际接稳剑匣。", "player_handoff_item": ["剑匣", "木匣"],
    }}}
    story["nodes"][0]["completion_contract"] = {"all": [{"key": KEY, "equals": True}]}
    return NumericV2Engine.from_mapping(story)


@pytest.mark.parametrize("message", [INPUT, "把剑匣交给旧友。", "双手将木匣递出。"])
def test_omitted_subject_is_opt_in_for_direct_item_actions(message):
    assert not player_handoff_is_explicit(message, ("剑匣", "木匣"))
    assert player_handoff_is_explicit(message, ("剑匣", "木匣"), allow_omitted_subject=True)


@pytest.mark.parametrize("message", [
    "请把剑匣交给旧友。", "你把剑匣交给旧友。", "小葵把剑匣交给旧友。",
    "准备把剑匣交给旧友。", "明天把剑匣交给旧友。", "把剑匣交给旧友吗？",
    "如果他开门，把剑匣交给旧友。", "别把剑匣交给旧友。", "把文书递给旧友。",
])
def test_click_context_never_changes_action_owner_or_time(message):
    assert not player_handoff_is_explicit(message, ("剑匣", "木匣"), allow_omitted_subject=True)


@pytest.mark.parametrize("ending", ["", "。"])
def test_clicked_action_can_keep_its_object_in_the_immediately_previous_clause(ending):
    assert player_handoff_is_explicit("双手托稳陶罐，缓缓递向保管员" + ending, ("陶罐",), allow_omitted_subject=True)


@pytest.mark.parametrize("message", [
    "双手托稳陶罐。缓缓递向保管员", "双手托稳陶罐；缓缓递向保管员", "双手托稳陶罐\n缓缓递向保管员",
    "（双手托稳陶罐）（缓缓递向保管员）", "（双手托稳陶罐），缓缓递向保管员",
    "双手托稳陶罐，准备递向保管员", "双手托稳陶罐，旧友递向柜台",
    "小葵双手托稳陶罐，缓缓递向保管员", "双手托稳徽章，缓缓递向保管员",
])
def test_clicked_action_cannot_borrow_an_object_from_another_action(message):
    assert not player_handoff_is_explicit(message, ("陶罐",), allow_omitted_subject=True)


@pytest.mark.asyncio
async def test_only_exact_current_suggestion_unlocks_the_action(tmp_path):
    runtime = NumericV2Runtime(_engine(), tmp_path)
    current = await runtime.start_session(session_id="suggestion-guard", catgirl_binding=_binding(),
        opening_performance={"performance": "请亲手交给旧友。", "suggested_inputs": [INPUT]})
    engine = runtime.engine
    assert engine.blocked_player_handoffs(current.session, INPUT)
    assert engine.blocked_player_handoffs(current.session, INPUT, input_source="suggestion") == {}
    assert engine.blocked_player_handoffs(current.session, INPUT + "。", input_source="suggestion")
    ordinary = runtime.prepare_turn(current, TurnRequestV2("wait", 0, "先等等。"), ())
    current = await runtime.commit_turn(ordinary, {"performance": "我等你。", "suggested_inputs": []})
    assert engine.blocked_player_handoffs(current.session, INPUT, input_source="suggestion")


@pytest.mark.asyncio
@pytest.mark.parametrize("settlement", ["before_actor", "after_actor"])
async def test_clicked_handoff_survives_fact_settlement_restore_and_fork(tmp_path, settlement):
    engine = _engine()
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="clicked-handoff", catgirl_binding=_binding(),
        opening_performance={"performance": "请亲手交给旧友。", "suggested_inputs": [INPUT]})
    operations = ({"op": "set", "key": KEY, "value": True, "visibility": "public"},)
    turn = TurnRequestV2("handoff", 0, INPUT, "suggestion")
    outcome = runtime.prepare_turn(current, turn, (), fact_operations=operations if settlement == "before_actor" else ())
    if settlement == "after_actor":
        assert KEY not in outcome.session.story_state["facts"]
        outcome, _ = engine.finalize_actor_fact_candidates(current.session, outcome,
            candidates=[{"key": KEY, "value": True, "evidence_quote": RECEIPT}],
            evidence_sources={"actor_performance": RECEIPT})
    stored = await runtime.commit_turn(outcome, {"scene_narration": RECEIPT,
        "performance": "这下交付完成了。", "suggested_inputs": []})
    assert stored.ledger_events[-1]["input_source"] == "suggestion"
    assert stored.session.performance_history[-1]["input_source"] == "suggestion"
    assert stored.session.story_state["facts"][KEY]["value"] is True
    assert await runtime.restore_session(stored.session.session_id) == stored
    fork = await runtime.fork_session_for_test(stored.session.session_id, session_id="handoff-fork", through_revision=1)
    assert fork.session.story_state == stored.session.story_state
    assert fork.ledger_events[-1]["input_source"] == "suggestion"


@pytest.mark.asyncio
async def test_real_click_without_receipt_keeps_completion_unsettled(tmp_path):
    runtime = NumericV2Runtime(_engine(), tmp_path)
    current = await runtime.start_session(session_id="no-receipt", catgirl_binding=_binding(),
        opening_performance={"performance": "请亲手交给旧友。", "suggested_inputs": [INPUT]})
    outcome = runtime.prepare_turn(current, TurnRequestV2("handoff", 0, INPUT, "suggestion"), ())
    stored = await runtime.commit_turn(outcome, {"performance": "他还没接稳，先等等。", "suggested_inputs": []})
    assert KEY not in stored.session.story_state["facts"]
    assert runtime.engine.completion_contract_satisfied(stored.session) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("shown", [False, True])
async def test_historical_click_requires_the_suggestion_actually_shown_before_it(tmp_path, shown):
    runtime = NumericV2Runtime(_engine(), tmp_path)
    current = await runtime.start_session(session_id="historical-click", catgirl_binding=_binding(),
        opening_performance={"performance": "请亲手交给旧友。", "suggested_inputs": [INPUT] if shown else []})
    outcome = runtime.prepare_turn(current, TurnRequestV2("handoff", 0, INPUT, "suggestion"), ())
    stored = await runtime.commit_turn(outcome, {"scene_narration": RECEIPT,
        "performance": "收到了。", "suggested_inputs": []})
    # An input-source marker alone must not authorize a forged historic click.
    record = {**stored.session.performance_history[-1], "input_source": "suggestion"}
    session = replace(stored.session, performance_history=(record,))
    blocked = runtime.engine.blocked_player_handoffs(session, "他还有话要说吗？")
    assert bool(blocked) is not shown
