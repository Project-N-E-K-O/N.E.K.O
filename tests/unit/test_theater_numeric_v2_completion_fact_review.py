"""Completion-only Review keeps its authority narrower than full body Review."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as ev
from services.theater.numeric_v2_narrative_proposer import NumericV2NarrativeProposer
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.unit.test_theater_numeric_v2_narrative_review import _event
from tests.unit.test_theater_numeric_v2_unified_pacing import story_for


KEY = "scene:start:done"
RESULT = "管事核对文书后盖了章，说：文书无误，可以通行。"
PLAYER = "我把木匣递给你。请管事核验文书。"


def _context():
    story = story_for("山径的避风棚")
    story["fact_contract"]["facts"][KEY]["description"] = RESULT
    story["nodes"][0]["story_beat"]["fixed_narrations"] = [{
        "id": "record", "text": "登记簿的完整作者原文。",
        "trigger": {"type": "condition", "condition": "玩家已经实际翻开登记簿。"},
        "after": [], "required_before_exit": True,
    }]
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(
        session_id="completion-review", catgirl_binding={"catgirl_name": "测试猫娘"},
        opening_performance={"performance": "管事还没检查文书。", "suggested_inputs": []},
    )
    candidate = {
        "performance": "我接过木匣。", "scene_narration": RESULT, "suggested_inputs": [],
        "fact_candidates": [{"key": KEY, "value": True, "evidence_quote": RESULT}],
        "narrative_events": [_event()],
    }
    return engine, session, candidate


def _response(**extra):
    return {
        "offer_present": False, "offer_quote": "", "offer_kind": "", "valid": False,
        "body_violations": [], "unsafe_suggestion_indexes": [], "failure_reason": "",
        "player_action_kind": "", "fact_candidates": [], "approved_actor_fact_indexes": [0],
        **extra,
    }


async def _capture(monkeypatch, *, mode=None, response=None, message=PLAYER,
                   candidate_override=None, evaluator_fact_claims=(), require_offer_acceptance=False):
    engine, session, candidate = _context()
    if candidate_override is not None:
        candidate = deepcopy(candidate_override)
    original = deepcopy((engine.story, session.to_dict(), candidate))
    factories, requests, auxiliary = [], [], []

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    async def factory(*args, **kwargs):
        factories.append(kwargs)
        return Client()

    async def invoke(client, messages, **kwargs):
        if kwargs["stage"] == "review_player_actions":
            auxiliary.append(kwargs)
            return SimpleNamespace(content='{"body_violations":[],"failure_reason":""}')
        requests.append((messages, kwargs))
        return SimpleNamespace(content=json.dumps(response, ensure_ascii=False), response_metadata={"finish_reason": "stop"})

    async def no_proposal(self, **kwargs):
        raise AssertionError("Review must not create a narrative proposal")

    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    monkeypatch.setattr(ev, "invoke_with_usage", invoke)
    monkeypatch.setattr(NumericV2NarrativeProposer, "propose", no_proposal)
    kwargs = {} if mode is None else {"completion_fact_review_only": mode}
    try:
        result = await ev.NumericV2MetricEvaluator(object()).validate_transition_offer(
            engine=engine, session=session, message=message, actor_performance=candidate,
            evaluator_fact_claims=evaluator_fact_claims,
            require_offer_acceptance=require_offer_acceptance, **kwargs,
        )
    finally:
        assert (engine.story, session.to_dict(), candidate) == original
    assert len(requests) == 1
    assert factories[0]["timeout"] == 8
    assert factories[0]["max_retries"] == 0
    messages, options = requests[0]
    assert options["stage"] == "review"
    raw = messages[1].content
    data = json.loads(raw[raw.index("{"):])
    schema = options["response_format"]["json_schema"]["schema"]
    assert len(factories) == 1 + len(auxiliary)
    return result, data, schema, messages, factories[0]


@pytest.mark.asyncio
async def test_completion_only_native_request_excludes_fixed_and_narrative_authority(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("completion-only mode must not construct unrelated approvals")

    monkeypatch.setattr(ev, "review_candidates", forbidden)
    monkeypatch.setattr(ev, "_narrative_review_events", forbidden)
    result, data, schema, messages, options = await _capture(monkeypatch, mode=True, response=_response())
    assert options["max_completion_tokens"] == 350
    assert data["player_input"] == PLAYER
    assert data["actor_performance"] == "我接过木匣。"
    assert data["scene_update"] == RESULT
    assert "管事还没检查文书。" in json.dumps(data["scene_context"], ensure_ascii=False)
    assert data["actor_fact_candidates"][0]["index"] == 0
    assert data["actor_fact_candidates"][0]["evidence_quote"] == RESULT
    assert "approved_actor_fact_indexes" in schema["required"]
    assert "fixed_narration_candidates" not in data
    assert "narrative_events" not in data
    for field in ("fixed_narration_triggers", "approved_narrative_event_indexes", "narrative_review_complete"):
        assert field not in schema["properties"]
        assert field not in messages[0].content
    assert result.fact_candidates == ({"key": KEY, "value": True, "evidence_quote": RESULT},)


@pytest.mark.asyncio
async def test_default_full_review_keeps_fixed_and_narrative_contracts(monkeypatch):
    payload = _response(fixed_narration_triggers=[], approved_narrative_event_indexes=[0], narrative_review_complete=True)
    implicit = await _capture(monkeypatch, response=payload)
    explicit = await _capture(monkeypatch, mode=False, response=payload)
    assert [m.content for m in implicit[3]] == [m.content for m in explicit[3]]
    assert implicit[2] == explicit[2]
    result, data, schema, _, options = implicit
    assert options["max_completion_tokens"] > 350
    assert data["fixed_narration_candidates"][0]["id"] == "0"
    assert data["narrative_events"][0]["quote"] == "我接过木匣。"
    assert {"fixed_narration_triggers", "approved_narrative_event_indexes", "narrative_review_complete"} <= set(schema["required"])
    assert result.approved_narrative_event_indexes == (0,)
    assert result.narrative_review_complete is True

    departure = PLAYER + "（推门离开，风铃轻响）明天见。"
    full = await _capture(monkeypatch, response=payload, message=departure)
    narrow = await _capture(monkeypatch, mode=True, message=departure,
                            response=_response(scene_update_removal_safe=True))
    assert full[1]["player_action_projection"]["player_left_current_scene"] is True
    assert narrow[1]["player_action_projection"] == full[1]["player_action_projection"]
    assert full[4]["max_completion_tokens"] == options["max_completion_tokens"] + 322
    assert narrow[4]["max_completion_tokens"] == 350
    for field in ("body_issues", "scene_update_removal_safe"):
        assert field in full[2]["required"]
        assert field in full[3][0].content
    assert "body_issues" in narrow[2]["required"]
    assert "body_issues" in narrow[3][0].content
    assert "scene_update_removal_safe" not in narrow[2]["properties"]
    assert narrow[0].scene_update_removal_safe is False


@pytest.mark.asyncio
async def test_completion_only_does_not_accept_unsolicited_narrative_approval(monkeypatch):
    result, _, _, _, _ = await _capture(
        monkeypatch, mode=True,
        response=_response(approved_narrative_event_indexes=[0], narrative_review_complete=True),
    )
    assert result.fact_candidates == ({"key": KEY, "value": True, "evidence_quote": RESULT},)
    assert result.approved_narrative_event_indexes == ()
    assert result.narrative_review_complete is False
    assert result.reviewed_narrative_events == ()
    assert result.reviewed_narrative_body == ""


@pytest.mark.asyncio
async def test_completion_only_cannot_trigger_an_authored_fixed_piece(monkeypatch):
    with pytest.raises(ev.NumericV2EvaluatorOutputError, match="numeric_v2_fixed_narration_review_invalid"):
        await _capture(monkeypatch, mode=True,
            response=_response(fixed_narration_triggers=[{"id": "0", "evidence": "我把木匣递给你。"}]))


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["valid", "missing", "stale", "wrong_field", "partial"])
async def test_completion_repair_uses_only_current_complete_locators_as_uncommitted_edit_material(
    tmp_path, monkeypatch, location,
):
    """A locator guides the sole edit; it never approves untouched prose or carries old fact indexes."""
    from services.theater import numeric_v2_workflow as wf
    from services.theater.numeric_v2_runtime import NumericV2Runtime
    from tests.unit import test_theater_numeric_v2_same_turn_invitation as scenario

    runtime, current, calls = await scenario._setup(tmp_path, monkeypatch)
    path = runtime.store._path(current.session.session_id)
    before = path.read_bytes()
    bad = "你替自己签了名。"
    draft = {"performance": "文书核验结果已经清楚。" + bad,
             "scene_narration": scenario.NPC_RESULT, "suggested_inputs": [],
             "transition_offered": False,
             "fact_candidates": [{"key": scenario.KEY, "value": True,
                                  "evidence_quote": scenario.NPC_RESULT}]}
    violations = ("player_action", "author_boundary") if location == "partial" else ("player_action",)
    issue = {"code": "other", "field": "actor_performance", "quote": bad,
             "violations": ["player_action"]}
    if location == "stale":
        issue["quote"] = "你已经把签名补齐了。"
    elif location == "wrong_field":
        issue["field"] = "scene_update"
    issues = () if location == "missing" else (issue,)
    generated = []

    async def generate(self, **kwargs):
        calls["actor"] += 1
        generated.append(deepcopy(kwargs.get("draft_repair_context")))
        assert kwargs["session"].performance_history == current.session.performance_history
        assert path.read_bytes() == before
        if calls["actor"] == 1:
            assert generated[-1] is None
            return deepcopy(draft)
        assert calls["actor"] == 2
        if location == "valid":
            context = generated[-1]
            assert context["issues"] == [issue]
            assert list(context["body_violations"]) == list(violations)
            assert context["draft"]["performance"] == draft["performance"]
            assert context["draft"]["scene_narration"] == scenario.NPC_RESULT
            assert "fact_candidates" not in context["draft"]
            assert "narrative_events" not in context["draft"]
        else:
            # Invalid or incomplete locations do not clear the veto or skip the existing generic repair.
            assert generated[-1] is None
        return {"performance": "核验结果清楚了，先留在柜台旁。",
                "scene_narration": scenario.NPC_RESULT, "suggested_inputs": [],
                "transition_offered": False, "fact_candidates": deepcopy(draft["fact_candidates"])}

    async def review(self, **kwargs):
        calls["review"] += 1
        assert calls["review"] <= 2
        assert kwargs["completion_fact_review_only"] is True
        assert kwargs["session"].performance_history == current.session.performance_history
        assert path.read_bytes() == before
        if calls["review"] == 1:
            # Even an approved index on the rejected first draft cannot authorize the replacement.
            return ev.NumericV2TransitionOfferReview(False, False, violations, (), body_issues=issues,
                fact_candidates=tuple(draft["fact_candidates"]))
        assert bad not in kwargs["actor_performance"]["performance"]
        return ev.NumericV2TransitionOfferReview(False, False, (), ())

    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "validate_transition_offer", review)
    result = await scenario._turn(runtime, current)
    assert calls == {"evaluator": 1, "actor": 2, "review": 2}
    assert result.diagnostics["completion_fact_rewrite_attempts"] == 1
    assert bad not in str(result.stored.session.performance_history)
    assert scenario.NPC_RESULT in str(result.stored.session.performance_history[-1])
    assert scenario.KEY not in result.stored.session.story_state["facts"]
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
async def test_located_completion_repair_second_veto_rolls_back_even_without_fact_candidates(tmp_path, monkeypatch):
    """Editing context adds no third draft and no authorization to commit a still-rejected body."""
    from services.theater import numeric_v2_workflow as wf
    from services.theater.numeric_v2_actor import NumericV2ActorOutputError
    from services.theater.numeric_v2_runtime import NumericV2Runtime
    from tests.unit import test_theater_numeric_v2_same_turn_invitation as scenario

    runtime, current, calls = await scenario._setup(tmp_path, monkeypatch)
    path = runtime.store._path(current.session.session_id)
    before = path.read_bytes()
    bad = "你替自己签了名。"
    issue = {"code": "other", "field": "actor_performance", "quote": bad,
             "violations": ["player_action"]}

    async def generate(self, **kwargs):
        calls["actor"] += 1
        assert calls["actor"] <= 2
        if calls["actor"] == 1:
            return {"performance": bad, "scene_narration": scenario.NPC_RESULT,
                    "suggested_inputs": [], "transition_offered": False,
                    "fact_candidates": [{"key": scenario.KEY, "value": True,
                                         "evidence_quote": scenario.NPC_RESULT}]}
        assert kwargs["draft_repair_context"]["issues"] == [issue]
        return {"performance": "你又替自己签了一遍。", "suggested_inputs": [], "transition_offered": False}

    async def review(self, **kwargs):
        calls["review"] += 1
        assert calls["review"] <= 2
        assert path.read_bytes() == before
        if calls["review"] == 1:
            return ev.NumericV2TransitionOfferReview(False, False, ("player_action",), (), body_issues=(issue,))
        assert not kwargs["actor_performance"].get("fact_candidates")
        return ev.NumericV2TransitionOfferReview(False, False, ("player_action",), ())

    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "validate_transition_offer", review)
    with pytest.raises(NumericV2ActorOutputError, match="^numeric_v2_completion_fact_review_rejected$"):
        await scenario._turn(runtime, current)
    assert calls == {"evaluator": 1, "actor": 2, "review": 2}
    assert path.read_bytes() == before
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id) == current
