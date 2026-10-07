"""Keep program-owned text out of Actor prose without discarding valid triggers."""

from dataclasses import replace
import json

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater import numeric_v2_workflow as workflow
from services.theater.numeric_v2_fixed_narration import displayed_ids
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


ACTION = "（指尖触碰你保管的物品）文字清楚了，你可以慢慢看。"
FORGED = "记录显示：另一位来访者已经取走了全部物品。"


def _candidate():
    return {"performance": ACTION, "scene_narration": FORGED,
            "suggested_inputs": ["（留在原地阅读）", "（暂时沉默）"], "transition_offered": False}


def _payload():
    return {
        "offer_present": False, "valid": False, "body_violations": ["author_boundary"],
        "unsafe_suggestion_indexes": [], "failure_reason": "旁白代写了原文。",
        "fixed_narration_triggers": [{"id": "0", "evidence": "指尖触碰你保管的物品"}],
        "body_issues": [{"code": "fixed_narration_content", "field": "scene_update",
                         "quote": FORGED, "violations": ["author_boundary"]}],
        "scene_update_removal_safe": True,
    }


def _review(payload=None, **kwargs):
    return evaluator._parse_transition_judge_output(
        json.dumps(_payload() if payload is None else payload, ensure_ascii=False),
        fixed_narration_review=True, fixed_narration_ids=("record",),
        scene_update_removal_allowed=True,
        body_evidence={"actor_performance": ACTION, "scene_update": FORGED}, **kwargs,
    )


def test_crop_keeps_verified_trigger_but_never_adopts_the_audit_as_facts():
    review = _review()
    repaired = workflow._safe_drop_invalid_scene_update(_candidate(), review)
    assert repaired == {k: v for k, v in _candidate().items() if k != "scene_narration"}
    assert review.fixed_narration_triggers == ({"id": "record", "evidence": "指尖触碰你保管的物品"},)
    assert not review.fact_candidates


@pytest.mark.parametrize("change", [
    {"fixed_narration_triggers": ()},
    {"fixed_narration_triggers": ({"id": "record", "evidence": "另一位来访者"},)},
    {"fixed_narration_triggers": ({"id": "record", "evidence": ""},)},
    {"body_violations": ("author_boundary", "player_action")},
    {"body_issues": ({"code": "other", "field": "scene_update", "quote": FORGED,
                      "violations": ["author_boundary"]},)},
    {"body_issues": ({"code": "fixed_narration_content", "field": "actor_performance",
                      "quote": "文字清楚了", "violations": ["author_boundary"]},)},
    {"scene_update_removal_safe": False}, {"offer_present": True},
    {"approved_evaluator_fact_indexes": (0,)},
])
def test_ambiguous_or_removed_trigger_sources_cannot_authorize_crop(change):
    assert workflow._safe_drop_invalid_scene_update(_candidate(), replace(_review(), **change)) is None


def test_unsolicited_fixed_content_code_cannot_enable_a_crop():
    payload = _payload()
    payload.pop("fixed_narration_triggers")
    review = evaluator._parse_transition_judge_output(
        json.dumps(payload, ensure_ascii=False), scene_update_removal_allowed=True,
        body_evidence={"actor_performance": ACTION, "scene_update": FORGED},
    )
    assert review.body_violations == ("author_boundary",)
    assert not review.body_issues
    assert workflow._safe_drop_invalid_scene_update(_candidate(), review) is None


def test_long_exact_quote_is_verified_before_shortening():
    narration = FORGED * 12
    payload = _payload()
    payload["body_issues"][0]["quote"] = narration
    parsed = evaluator._parse_transition_judge_output(
        json.dumps(payload, ensure_ascii=False), fixed_narration_review=True,
        fixed_narration_ids=("record",), scene_update_removal_allowed=True,
        body_evidence={"actor_performance": ACTION, "scene_update": narration},
    )
    assert parsed.body_issues[0]["quote"] == narration[:120]
    assert workflow._safe_drop_invalid_scene_update({**_candidate(), "scene_narration": narration}, parsed)
    payload["body_issues"][0]["quote"] += "这段尾巴并不存在。"
    rejected = evaluator._parse_transition_judge_output(
        json.dumps(payload, ensure_ascii=False), fixed_narration_review=True,
        fixed_narration_ids=("record",), scene_update_removal_allowed=True,
        body_evidence={"actor_performance": ACTION, "scene_update": narration},
    )
    assert rejected.body_violations == ("author_boundary",)
    assert not rejected.body_issues


def _engine(text="原文：全部物品仍由原持有人保管。"):
    story = numeric_v2_story()
    story["nodes"][0]["story_beat"]["fixed_narrations"] = [{
        "id": "record", "text": text,
        "trigger": {"type": "condition", "condition": "猫娘实际触碰玩家保管的物品。",
                    "player_handoff_required": False},
        "after": [], "required_before_exit": True,
    }]
    return NumericV2Engine.from_mapping(story)


def test_review_requests_locations_without_revealing_pending_text():
    engine = _engine()
    session = engine.create_session(session_id="fixed-content", catgirl_binding=_binding(),
                                    opening_performance=_opening())
    messages, _ = evaluator._build_transition_judge_messages(
        engine, session, actor_performance=_candidate(), player_input="请你碰一下。",
    )
    text = "\n".join(m.content for m in messages)
    assert '"body_issues"' in text
    assert "factual_check" not in text
    assert "fixed_narration_content" in text
    assert "原文：全部物品仍由原持有人保管。" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["家书原文：愿你平安。", "航行记录：所有人员生还。"])
async def test_local_crop_commits_only_author_text_once_and_restores(tmp_path, monkeypatch, text):
    runtime = NumericV2Runtime(_engine(text), tmp_path)
    current = await runtime.start_session(session_id="fixed-content", catgirl_binding=_binding(),
                                          opening_performance=_opening())
    calls = {"actor": 0, "review": 0}

    async def enabled():
        return {"evaluator": True, "review": True, "dispute": True, "actor_retry": True}

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        calls["actor"] += 1
        return _candidate()

    async def review(self, **kwargs):
        calls["review"] += 1
        assert not kwargs.get("dispute_review")
        return _review()

    monkeypatch.setattr(workflow, "aload_theater_module_options", enabled)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("new", current.session.revision, "请你触碰，我仍保管物品。"),
        ensure_current_binding=lambda _: _binding(),
    )
    assert calls == {"actor": 1, "review": 1}
    assert result.diagnostics["invalid_scene_update_local_crops"] == 1
    assert result.performance["performance"] == ACTION
    assert "scene_narration" not in result.performance
    assert [piece["text"] for piece in result.performance["fixed_narrations"]] == [text]
    assert FORGED not in json.dumps(result.stored.session.to_dict(), ensure_ascii=False)
    cold = await NumericV2Runtime(runtime.engine, tmp_path).restore_session("fixed-content")
    assert cold == result.stored
    assert displayed_ids(cold.session) == {("start", "record")}
    fork = await runtime.fork_session_for_test("fixed-content", session_id="fixed-fork", through_revision=1)
    assert displayed_ids(fork.session) == displayed_ids(cold.session)


@pytest.mark.asyncio
@pytest.mark.parametrize("located", [True, False])
async def test_unrepaired_verified_forgery_never_enters_semantic_fallback_history(tmp_path, monkeypatch, located):
    runtime = NumericV2Runtime(_engine(), tmp_path)
    current = await runtime.start_session(session_id="reject-forgery", catgirl_binding=_binding(),
                                          opening_performance=_opening())
    calls = []

    async def enabled():
        return {"evaluator": True, "review": True, "dispute": False}

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        calls.append(kwargs.get("retry_hint"))
        return _candidate()

    async def review(self, **kwargs):
        # Removing narration would leave an unresolved dependency in this case.
        return replace(_review(), scene_update_removal_safe=False,
                       body_issues=_review().body_issues if located else ())

    monkeypatch.setattr(workflow, "aload_theater_module_options", enabled)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    with pytest.raises(workflow.NumericV2ActorOutputError, match="transition_review_failed"):
        await workflow.execute_numeric_v2_turn(
            config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2("new", 0, "请你触碰，我仍保管物品。"),
            ensure_current_binding=lambda _: _binding(),
        )
    assert len(calls) == 2
    assert await runtime.restore_session("reject-forgery") == current
