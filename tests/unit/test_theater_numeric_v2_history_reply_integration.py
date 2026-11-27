"""Old questions must retain their actual replies without becoming current authorization."""

from copy import deepcopy
import json

import pytest

from services.theater import numeric_v2_actor as actor, numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding


OLD_PERMISSION = "观察日记可以公开展出。"
OLD_QUESTION = "观察日记可以展出吗？"
ACTUAL_REPLY = "不，先保密。"
CURRENT_QUESTION = "观察日记还可以展出吗？"


async def _commit(runtime, current, message, performance, *, move=False):
    outcome = runtime.prepare_turn(
        current, TurnRequestV2(f"r{current.session.revision + 1}", current.session.revision, message),
        (), transition_intent="initiate" if move else "unclear",
    )
    if move:
        assert outcome.session.current_node_id == "middle"
        performance = runtime.engine.finalize_transition_performance(outcome, {
            "source_performance": "我们去桌边继续商量。",
            "bridge_scene_narration": "两人来到窗边的长桌前。",
            "target_scene_narration": "长桌旁摆着两把椅子。",
            "target_performance": performance,
            "suggested_inputs": [],
        }, target_opening="长桌旁摆着两把椅子。")
    else:
        performance = {"performance": performance, "suggested_inputs": []}
    return await runtime.commit_turn(outcome, performance)


def _messages(runtime, stored, consumer):
    session = stored.session
    formal = consumer == "formal_actor"
    outcome = runtime.prepare_turn(
        stored, TurnRequestV2("read-only-prompt", 3, CURRENT_QUESTION), (),
        transition_intent="initiate" if formal else "unclear",
    )
    if consumer in {"actor", "formal_actor"}:
        return actor._turn_messages(
            runtime.engine, session, outcome, CURRENT_QUESTION, "谨慎。", "Lan", "哥哥",
            recent_ledger_events=stored.ledger_events,
        )
    if consumer == "evaluator":
        return evaluator._build_messages(runtime.engine, session, CURRENT_QUESTION)
    return evaluator._build_transition_judge_messages(
        runtime.engine, session, player_input=CURRENT_QUESTION,
        actor_performance={"performance": "我先核对之前的答复。", "suggested_inputs": []},
    )[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ["actor", "evaluator", "review", "formal_actor"])
async def test_committed_old_question_keeps_reply_across_consumers_restore_and_fork(tmp_path, consumer):
    runtime = NumericV2Runtime(_engine(ordinary=True), tmp_path)
    current = await runtime.start_session(
        session_id="history-reply", catgirl_binding=_binding(),
        opening_performance={"performance": "先在门边商量。", "suggested_inputs": []},
    )
    current = await _commit(runtime, current, "先说说安排。", OLD_PERMISSION)
    current = await _commit(runtime, current, OLD_QUESTION, ACTUAL_REPLY)
    current = await _commit(runtime, current, "我们到桌边继续谈。", "先坐下来吧。", move=True)
    assert current.session.revision == 3 and current.session.current_node_id == "middle"
    before = deepcopy(current)
    path = runtime.store._path(current.session.session_id)
    saved_bytes = path.read_bytes()
    restored = await runtime.restore_session(current.session.session_id)
    forked = await runtime.fork_session_for_test(
        current.session.session_id, session_id="history-reply-fork", through_revision=3,
    )
    assert restored == current
    assert forked.session.performance_history == current.session.performance_history
    for stored in (current, restored, forked):
        stored_before = deepcopy(stored)
        messages = _messages(runtime, stored, consumer)
        text = messages[1].content
        data = prompt_payload(text)
        if consumer == "actor":
            marker = "history_evidence（已提交原文）："
            assert marker in data["story_so_far"]
            evidence, _ = json.JSONDecoder().raw_decode(data["story_so_far"].split(marker, 1)[1])
        else:
            evidence = data["history_evidence"]
        # An old permission may remain visible, but its later response cannot be
        # discarded merely because it does not repeat the noun in the question.
        assert {"revision": 2, "source": "performance", "current_visit": False,
                "text": ACTUAL_REPLY} in evidence
        assert any(row["revision"] == 2 and row["text"] == OLD_QUESTION
                   and row["source"] == "player_input" and not row["current_visit"]
                   for row in evidence)
        assert data["player_input"] == CURRENT_QUESTION
        assert stored == stored_before
    assert current == before
    assert path.read_bytes() == saved_bytes
