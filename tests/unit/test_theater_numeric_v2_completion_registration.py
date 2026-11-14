"""Completion registration is a state update, not a binding to that turn's prose."""

from copy import deepcopy
import json

import pytest

from services.theater import numeric_v2_actor as actor
from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as wf
from services.theater.numeric_v2_options import default_options
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_evaluator_committed_evidence import (
    KEY, MESSAGE, NODE, PERFORMANCE, QUOTE, _data, _engine, _fact, _parse,
)
from tests.unit.test_theater_numeric_v2_pending_offer import _commit
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_same_turn_invitation import (
    KEY as NPC_KEY, NPC_RESULT, _setup, _turn,
)
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


BACKFILL_RESPONSE = "（望向摊开的试卷）那我们接着看这道题。"


def _assert_projection(runtime, stored, *, key, registered, revision, formal, originals):
    """Inspect real Actor messages, not a duplicate implementation of its helper."""
    session = stored.session
    request = TurnRequestV2("projection-only", session.revision, "我们接着说吧。")
    outcome = runtime.prepare_turn(
        stored, request, (), transition_intent="initiate" if formal else "unclear",
    )
    assert (outcome.route is not None) is formal
    original = deepcopy((session, outcome))
    messages = actor._turn_messages(
        runtime.engine, session, outcome, request.message,
        "温和克制。", "小葵", "老谢", recent_ledger_events=stored.ledger_events,
    )
    assert (session, outcome) == original
    data = _data(messages)
    if formal:
        context = data["transition"].get("source_committed_completion")
        if registered:
            assert set(context) == {"facts"}
            rows = context["facts"]
        else:
            assert context is None
            rows = []
        history = "\n".join(row["committed_text"] for row in data["recent_context"])
    else:
        scene = data["current_scene"]
        start = scene.index("{", scene.index("结构化幕完成事实"))
        context, _ = json.JSONDecoder().raw_decode(scene[start:])
        assert context["status"] == ("satisfied" if registered else "pending")
        rows = context["all"]
        assert "completion_evidence" not in context
        history = data["story_so_far"]
    for original_text in originals:
        assert original_text in history
    row = next((item for item in rows if item["key"] == key), None)
    if registered:
        assert row["committed"] is True and row["satisfied"] is True
        assert row["committed_value"] is True
        assert row["registration_revision"] == revision
        assert "description" not in row
        assert "evidence_revision" not in row
    elif not formal:
        assert row["committed"] is False and row["satisfied"] is False
        assert "registration_revision" not in row
        assert row["description"]
    # Recording metadata must not tell the model where the event itself happened.
    assert "evidence_revision" not in messages[0].content + messages[1].content
    assert "completion_evidence" not in messages[0].content + messages[1].content


@pytest.mark.asyncio
@pytest.mark.parametrize("origin,formal", [("turn", False), ("turn", True),
    ("opening", False), ("scene_entry", False)])
@pytest.mark.parametrize("review_on,approve", [(False, False), (False, True), (True, False), (True, True)])
async def test_backfill_registration_survives_real_commit_restore_and_fork(
    tmp_path, monkeypatch, origin, formal, review_on, approve,
):
    engine = _engine()
    if origin == "scene_entry":
        # 用真实来源幕进入测试目标幕，不用手写history绕过分段提交和恢复校验。
        story = deepcopy(engine.story)
        source = deepcopy(story["nodes"][0])
        source.update(id="before_entry", min_turns=1)
        source.pop("completion_contract")
        source["route_gates"] = [{**source["route_gates"][1], "id": "into_entry", "target_node_id": NODE}]
        story["nodes"][0]["type"] = "scene"
        story["nodes"].insert(0, source)
        story["start_node_id"] = "before_entry"
        engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id="registration", catgirl_binding=_binding(),
        opening_performance={"performance": PERFORMANCE if origin == "opening" else "活动的安排还要商量。",
            "suggested_inputs": []},
    )
    if origin == "turn":
        current = await _commit(runtime, current, PERFORMANCE)
    elif origin == "scene_entry":
        outcome = runtime.prepare_turn(current, TurnRequestV2("enter", 0, "我们进去吧。"), (),
            transition_intent="initiate")
        assert outcome.session.current_node_id == NODE
        candidate = {**_candidate(), "source_performance": "那我们进去吧。", "target_performance": PERFORMANCE}
        current = await runtime.commit_turn(outcome, engine.finalize_transition_performance(
            outcome, candidate, target_opening="作者模板不是实际交付的合作结果。"))
        assert current.session.performance_history[-1]["segments"][2]["performance"] == PERFORMANCE
    # 首次交付漏记后继续两个真实回合，后补事实不能要求结果必须刚刚重演。
    current = await _commit(runtime, current, "（拿起铅笔）先从题目第一行看起。")
    current = await _commit(runtime, current, "（把草稿纸放在桌边）我把这道题的位置留好了。")
    before_revision = current.session.revision
    registration_revision = before_revision + 1
    # 从磁盘恢复后再补记，确保开场/目标段是真实持久化交付，不是本轮候选。
    current = await runtime.restore_session(current.session.session_id)
    assert KEY not in current.session.story_state["facts"]
    original_current = deepcopy(current)
    calls = {"evaluate": 0, "actor": 0, "review": 0}

    async def options():
        return {**{key: key == 'evaluator' for key in default_options()}, "review": review_on,
                "actor_retry": False, "suggestion_fill": False}

    async def evaluate(self, **kwargs):
        calls["evaluate"] += 1
        parsed = _parse(engine, kwargs["session"])
        assert parsed.fact_audit[0]["evidence"] == _fact()["evidence"]
        return parsed

    async def generate(self, **kwargs):
        calls["actor"] += 1
        return {"performance": BACKFILL_RESPONSE, "suggested_inputs": [],
                "transition_offered": False}

    async def review(self, **kwargs):
        calls["review"] += 1
        claims = kwargs["evaluator_fact_claims"]
        assert claims[0]["evidence"] == _fact()["evidence"]
        bank = []
        messages, _ = ev._build_transition_judge_messages(
            engine, kwargs["session"], actor_performance=kwargs["actor_performance"],
            player_input=MESSAGE, evaluator_fact_claims=claims, committed_evidence=bank,
        )
        binding = _data(messages)["committed_fact_evidence"]
        assert binding["content_refs"] and any(QUOTE in source for source in bank)
        return ev.NumericV2TransitionOfferReview(
            False, False, (), (), approved_evaluator_fact_indexes=(0,) if approve else (),
        )

    monkeypatch.setattr(wf, "aload_theater_module_options", options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "温和。")
    before_fork = await runtime.fork_session_for_test(
        current.session.session_id, session_id="before-registration", through_revision=before_revision,
    )
    before_path = runtime.store._path(before_fork.session.session_id)
    before_bytes = before_path.read_bytes()
    result = await wf.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("backfill", before_revision, MESSAGE), ensure_current_binding=lambda _: _binding(),
    )
    registered = approve
    assert (KEY in result.stored.session.story_state["facts"]) is registered
    assert result.performance["performance"] == BACKFILL_RESPONSE
    assert current == original_current
    assert calls == {"evaluate": 1, "actor": 1, "review": 1}
    if registered:
        fact = result.stored.session.story_state["facts"][KEY]
        assert fact["source_revision"] == fact["updated_revision"] == registration_revision
    path = runtime.store._path(current.session.session_id)
    committed_bytes = path.read_bytes()
    restored = await runtime.restore_session(current.session.session_id)
    forked = await runtime.fork_session_for_test(
        current.session.session_id, session_id="after-registration", through_revision=registration_revision,
    )
    assert restored == result.stored
    assert forked.session.story_state == restored.session.story_state
    assert forked.session.performance_history == restored.session.performance_history
    for stored in (result.stored, restored, forked):
        _assert_projection(
            runtime, stored, key=KEY, registered=registered, revision=registration_revision,
            formal=formal, originals=(QUOTE, BACKFILL_RESPONSE),
        )
    _assert_projection(
        runtime, before_fork, key=KEY, registered=False, revision=before_revision,
        formal=formal, originals=(QUOTE,),
    )
    assert path.read_bytes() == committed_bytes
    assert before_path.read_bytes() == before_bytes
    assert calls == {"evaluate": 1, "actor": 1, "review": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("formal", [False, True])
async def test_same_turn_actor_completion_keeps_value_without_creating_occurrence_binding(
    tmp_path, monkeypatch, formal,
):
    runtime, current, calls = await _setup(tmp_path, monkeypatch)
    result = await _turn(runtime, current)
    assert calls == {"evaluator": 1, "actor": 1, "review": 1}
    assert result.stored.session.story_state["facts"][NPC_KEY]["value"] is True
    original = deepcopy(result.stored)
    path = runtime.store._path(current.session.session_id)
    committed_bytes = path.read_bytes()
    restored = await runtime.restore_session(current.session.session_id)
    forked = await runtime.fork_session_for_test(
        current.session.session_id, session_id="same-turn-registration", through_revision=1,
    )
    assert restored == original
    for stored in (result.stored, restored, forked):
        _assert_projection(
            runtime, stored, key=NPC_KEY, registered=True, revision=1,
            formal=formal, originals=(NPC_RESULT,),
        )
    assert result.stored == original
    assert path.read_bytes() == committed_bytes
    assert calls == {"evaluator": 1, "actor": 1, "review": 1}
