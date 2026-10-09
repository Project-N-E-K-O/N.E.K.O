"""A rejected optional narration can be removed only with complete issue evidence."""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater import numeric_v2_workflow as workflow
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


def _candidate():
    return {
        "performance": "（点头）我先观察一下，你可以留在这里。",
        "scene_narration": "你已经穿过通道，来到了远处的大厅。",
        "suggested_inputs": ["（留在原地观察）", "（询问通道状况）里面怎样？"],
        "transition_offered": False,
        "fact_candidates": [{"key": "arrived", "value": True}],
    }


def _review():
    return evaluator._parse_transition_judge_output(json.dumps({
        "offer_present": False, "valid": False,
        "body_violations": ["player_action"], "unsafe_suggestion_indexes": [],
        "failure_reason": "旁白替玩家移动。",
        "scene_update_removal_safe": True,
        "body_issues": [{"code": "other", "field": "scene_update",
                         "quote": _candidate()["scene_narration"], "violations": ["player_action"]}],
    }, ensure_ascii=False), scene_update_removal_allowed=True, body_evidence={
        "actor_performance": _candidate()["performance"], "scene_update": _candidate()["scene_narration"],
    })


def test_crop_removes_only_the_rejected_narration_and_unused_actor_facts():
    candidate = _candidate()
    original = deepcopy(candidate)
    result = workflow._safe_drop_invalid_scene_update(candidate, _review())
    assert result == {k: v for k, v in candidate.items() if k not in {"scene_narration", "fact_candidates"}}
    assert candidate == original


@pytest.mark.parametrize("change", [
    {"offer_present": True}, {"missed_initiation": True}, {"body_violations": ()},
    {"scene_update_removal_safe": False},
    {"body_violations": ("author_boundary",)},
    {"body_violations": ("player_action", "scene_boundary")},  # incomplete issue coverage
    {"body_issues": ()},
    {"fact_candidates": ({"key": "done", "value": True, "evidence_quote": "已经完成"},)},
    {"approved_evaluator_fact_indexes": (0,)},
    {"fixed_narration_triggers": ({"id": "letter", "evidence": "信"},)},
])
def test_unresolved_conflicts_and_dependencies_keep_the_original_repair_path(change):
    assert workflow._safe_drop_invalid_scene_update(_candidate(), replace(_review(), **change)) is None


@pytest.mark.parametrize("change", [
    {"performance": ""}, {"scene_narration": "另一稿的旁白。"},
    {"performance": _candidate()["scene_narration"]},
    {"segments": [{"phase": "target_opening", "performance": "到了。"}]},
    {"fixed_narrations": [{"id": "letter", "text": "信件内容。"}]},
])
def test_empty_stale_shared_quotes_and_structured_performances_are_not_cropped(change):
    assert workflow._safe_drop_invalid_scene_update({**_candidate(), **change}, _review()) is None


def test_any_dialogue_issue_blocks_cropping_even_with_same_violation_kind():
    review = _review()
    dialogue_issue = {"code": "other", "field": "actor_performance",
                      "quote": "你可以留在这里", "violations": ["player_action"]}
    assert workflow._safe_drop_invalid_scene_update(
        _candidate(), replace(review, body_issues=(*review.body_issues, dialogue_issue)),
    ) is None


@pytest.mark.parametrize("allowed,value,expected", [
    (False, True, False), (True, True, True), (True, False, False),
    (True, "true", False), (True, 1, False), (True, None, False),
])
def test_removal_permission_is_strict_and_cannot_be_granted_by_unsolicited_model_output(allowed, value, expected):
    review = evaluator._parse_transition_judge_output(json.dumps({
        "offer_present": False, "valid": False, "body_violations": ["player_action"],
        "unsafe_suggestion_indexes": [], "scene_update_removal_safe": value,
    }), scene_update_removal_allowed=allowed)
    assert review.body_violations == ("player_action",)
    assert review.scene_update_removal_safe is expected


@pytest.mark.parametrize("narration,expected", [("", False), ("旁白内容。", True)])
def test_optional_narration_requests_locations_without_requiring_player_departure(narration, expected):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="locations", catgirl_binding=_binding(),
                                    opening_performance=_opening())
    messages, _ = evaluator._build_transition_judge_messages(
        engine, session, actor_performance={"performance": "我先看看。", "scene_narration": narration},
        player_input="我先留在这里。",
    )
    assert ('"body_issues"' in messages[0].content) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_facts", [False, True])
async def test_model_cannot_enable_crop_during_fact_review(monkeypatch, pending_facts):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="scope", catgirl_binding=_binding(),
                                    opening_performance=_opening())
    calls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages):
            assert ('"scene_update_removal_safe"' in messages[0].content) is not pending_facts
            return SimpleNamespace(content=json.dumps({
                "offer_present": False, "valid": False, "body_violations": ["player_action"],
                "unsafe_suggestion_indexes": [],
                **({"fact_candidates": []} if pending_facts else {}),
                "scene_update_removal_safe": True,
            }))

    async def config(_):
        return {"model": "test", "base_url": "http://test.invalid"}

    async def factory(*args, **kwargs):
        calls.append(kwargs)
        return Client()

    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    monkeypatch.setattr(evaluator, "_pending_completion_facts", lambda *_: [
        {"key": "done", "value": True, "description": "工作完成。"},
    ] if pending_facts else [])
    review = await evaluator.NumericV2MetricEvaluator(object()).validate_transition_offer(
        engine=engine, session=session, message="我先留下。", actor_performance=_candidate(),
    )
    assert review.scene_update_removal_safe is not pending_facts
    assert review.body_violations == ("player_action",)
    assert len(calls) == 1
    assert calls[0]["max_completion_tokens"] == (350 if pending_facts else 512)
    assert calls[0]["timeout"] == evaluator.NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS


@pytest.mark.asyncio
@pytest.mark.parametrize("dialogue,narration", [
    ("（看向仪表）读数仍在变化，我先检查。", "三个月后，你已经抵达另一个空间站。"),
    ("（侧身走过藤蔓）我先过去，你留在这里。", "你跟着穿过藤蔓，已经进入村庄深处。"),
])
async def test_narration_crop_commits_once_without_rewrite_or_new_invitation(
    tmp_path, monkeypatch, dialogue, narration,
):
    # A satisfied completion contract would otherwise append an unreviewed fallback offer.
    story = numeric_v2_story()
    story["fact_contract"] = {"facts": {"scene:start:done": {
        "value_type": "bool", "visibility": "public", "description": "当前工作已完成。",
    }}}
    story["nodes"][0]["completion_contract"] = {"all": [{"key": "scene:start:done", "equals": True}]}
    contract = story["nodes"][0]["route_gates"][1]["transition_contract"]
    middle = deepcopy(story["nodes"][2])
    middle.update(id="middle", type="scene", min_turns=1, route_gates=[{
        "id": "middle_to_leave", "target_node_id": "ending_leave", "priority": 100,
        "conditions": {"all": []}, "transition_contract": deepcopy(contract),
    }])
    middle.pop("terminal")
    middle.pop("ending_id")
    story["nodes"].append(middle)
    story["nodes"][0]["route_gates"][1]["target_node_id"] = "middle"
    contract["fallback_offer"] = "现在一起离开好吗？"
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(session_id="narration_crop", catgirl_binding=_binding(),
                                          opening_performance=_opening())
    outcome = runtime.prepare_turn(current, TurnRequestV2("setup", 0, "工作完成了。"), (),
                                   fact_operations=({"op": "set", "key": "scene:start:done",
                                                     "value": True, "visibility": "public"},))
    current = await runtime.commit_turn(outcome, {"performance": "工作完成了。", "suggested_inputs": []})
    calls = {"actor": 0, "review": 0}

    async def options():
        return {"evaluator": True, "review": True, "dispute": False}

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        calls["actor"] += 1
        assert calls["actor"] == 1, "A reviewed dialogue must not be regenerated to delete optional narration"
        return {**_candidate(), "performance": dialogue, "scene_narration": narration,
                "suggested_inputs": ["（检查到达地的设施）", *_candidate()["suggested_inputs"]]}

    async def review(self, **kwargs):
        calls["review"] += 1
        assert calls["review"] == 1
        return replace(_review(), unsafe_suggestion_indexes=(0,), body_issues=({
            "code": "other", "field": "scene_update", "quote": narration,
            "violations": ["player_action"],
        },))

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(evaluator.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(evaluator.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("next", current.session.revision, "我先留在这里。"),
        ensure_current_binding=lambda _: _binding(),
    )
    assert calls == {"actor": 1, "review": 1}
    assert result.performance["performance"] == dialogue
    assert result.performance["suggested_inputs"] == []
    assert "scene_narration" not in result.performance
    assert result.diagnostics["invalid_scene_update_local_crops"] == 1
    assert result.diagnostics["semantic_rewrite_attempts"] == 0
    assert result.diagnostics["completion_fallback_offer_applied"] == 0
    assert result.stored.session.current_node_id == current.session.current_node_id
    assert result.stored.session.metrics == current.session.metrics
    assert not result.stored.session.transition_offered
    assert len(result.stored.ledger_events) == len(current.ledger_events) + 1
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session("narration_crop") == result.stored
    fork = await runtime.fork_session_for_test("narration_crop", session_id="fork",
                                               through_revision=result.stored.session.revision)
    assert fork.session.story_state == result.stored.session.story_state
    assert fork.session.performance_history == result.stored.session.performance_history
