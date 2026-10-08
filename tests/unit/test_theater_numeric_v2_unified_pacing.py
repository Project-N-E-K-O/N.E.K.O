"""Chat wording must not disable the ordinary theater pacing and invitation path."""

from copy import deepcopy
import json

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as workflow
from services.theater.numeric_v2_runtime import (
    NumericV2Engine,
    NumericV2Runtime,
    TurnRequestV2,
)
from services.theater.numeric_v2_options import default_options
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding


def story_for(destination):
    story = numeric_v2_story()
    story["fact_contract"] = {
        "facts": {
            "scene:start:done": {
                "value_type": "bool",
                "visibility": "public",
                "description": "眼前的事情已经处理完。",
            }
        }
    }
    story["nodes"][0]["completion_contract"] = {
        "all": [{"key": "scene:start:done", "equals": True}]
    }
    contract = story["nodes"][0]["route_gates"][1]["transition_contract"]
    contract.update(
        fallback_offer=f"要现在一起去{destination}吗？",
        accept_input="好，我们现在过去。",
    )
    middle = story["nodes"][2]
    middle.update(type="scene", min_turns=1)
    middle.pop("terminal")
    middle.pop("ending_id")
    middle["route_gates"] = [
        {
            "id": "middle_to_leave",
            "target_node_id": "ending_after_middle",
            "priority": 100,
            "conditions": {"all": []},
            "transition_contract": deepcopy(contract),
        }
    ]
    middle["route_gates"][0]["transition_contract"].pop("fallback_offer")
    story["nodes"].append(
        {
            "id": "ending_after_middle",
            "type": "ending",
            "chapter": "离开",
            "story_beat": deepcopy(middle["story_beat"]),
            "route_gates": [],
            "terminal": True,
            "ending_id": "leave",
        }
    )
    return story


@pytest.mark.parametrize("legacy", ["chat", "scene_action", "mixed_or_unclear"])
def test_legacy_classification_does_not_change_evaluation(legacy):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    payload = {
        "scene_complete": True,
        "transition_intent": "unclear",
        "metric_changes": {},
    }
    baseline = ev._parse_output(json.dumps(payload), engine, "你还好吗？")
    old = ev._parse_output(
        json.dumps({**payload, "interaction_intent": legacy}), engine, "你还好吗？"
    )
    assert old == baseline


def test_truncated_optional_facts_do_not_require_retired_classification():
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    content = (
        '{"public_destination_quote":"","scene_complete":false,'
        '"transition_intent":"unclear","transition_reply_target":"unclear",'
        '"metric_changes":{},"fact_candidates":[{"key":'
    )
    parsed = ev._parse_output(content, engine, "你还好吗？", finish_reason="length")
    assert not parsed.scene_complete and parsed.fact_operations == ()
    assert parsed.transition_intent == "unclear"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "destination,message",
    [
        ("长街继续调查", "其实你刚才是不是也有点紧张？"),
        ("轨道站核对星图", "这趟旅程让你有什么感受？"),
    ],
)
async def test_completed_scene_can_invite_after_subjective_reply_without_auto_accept(
    tmp_path,
    monkeypatch,
    destination,
    message,
):
    engine = NumericV2Engine.from_mapping(story_for(destination))
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id="unified",
        catgirl_binding=_binding(),
        opening_performance={
            "performance": "这边的事情需要先处理。",
            "suggested_inputs": [],
        },
    )
    setup = runtime.prepare_turn(
        current,
        TurnRequestV2("done", 0, "眼前的事情处理完了。"),
        (),
        fact_operations=(
            {
                "op": "set",
                "key": "scene:start:done",
                "value": True,
                "visibility": "public",
            },
        ),
    )
    current = await runtime.commit_turn(
        setup,
        {
            "performance": "眼前的事情已经处理完了。",
            "suggested_inputs": [],
            "transition_offered": False,
        },
    )
    captured = []

    async def options():
        return {**default_options(), "review": True}

    async def evaluate(self, **kwargs):
        return ev._parse_output(
            json.dumps(
                {
                    "interaction_intent": "chat",
                    "scene_complete": False,
                    "transition_intent": "reject"
                    if kwargs["message"] == "先不要去。"
                    else "unclear",
                    "transition_reply_target": "pending_transition",
                    "metric_changes": {},
                }
            ),
            engine,
            kwargs["message"],
            current.session,
        )

    async def invoke(self, messages, **kwargs):
        captured.append(messages)
        return {
            "performance": "刚才确实有些紧张，现在已经安心了。",
            "suggested_inputs": [],
            "transition_offered": False,
        }

    async def review(self, **kwargs):
        return ev.NumericV2TransitionOfferReview(False, False, (), ())

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(
        workflow.NumericV2MetricEvaluator, "validate_transition_offer", review
    )
    monkeypatch.setattr(workflow.NumericV2Actor, "_invoke", invoke)
    monkeypatch.setattr(
        workflow.NumericV2Actor, "_character_profile", lambda self: "温和。"
    )
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(),
        runtime=runtime,
        current=current,
        turn=TurnRequestV2("respond", 1, message),
        ensure_current_binding=lambda _: _binding(),
    )
    assert len(captured) == 1
    assert result.stored.session.current_node_id == "start"
    assert result.stored.session.transition_offered is True
    assert result.performance["performance"].endswith(f"要现在一起去{destination}吗？")
    assert result.performance["suggested_inputs"][0] == "好，我们现在过去。"
    assert (
        await NumericV2Runtime(engine, tmp_path).restore_session("unified")
        == result.stored
    )
    declined = runtime.prepare_turn(
        result.stored,
        TurnRequestV2("decline", 2, "先不要去。"),
        (),
        transition_intent="reject",
    )
    assert (
        declined.session.current_node_id == "start"
        and not declined.session.transition_offered
    )
    accepted = runtime.prepare_turn(
        result.stored,
        TurnRequestV2("accept", 2, "好，我们现在过去。"),
        (),
        transition_intent="accept",
    )
    assert accepted.session.current_node_id == "ending_leave"
    rejected = await workflow.execute_numeric_v2_turn(
        config_manager=object(),
        runtime=runtime,
        current=result.stored,
        turn=TurnRequestV2("decline", 2, "先不要去。"),
        ensure_current_binding=lambda _: _binding(),
    )
    assert not rejected.stored.session.transition_offered
    assert "好，我们现在过去。" not in rejected.performance["suggested_inputs"]
    current = rejected.stored
    continued = await workflow.execute_numeric_v2_turn(
        config_manager=object(),
        runtime=runtime,
        current=current,
        turn=TurnRequestV2("continue", 3, message),
        ensure_current_binding=lambda _: _binding(),
    )
    assert not continued.stored.session.transition_offered
    assert continued.performance["performance"] == "刚才确实有些紧张，现在已经安心了。"
    assert continued.diagnostics["completion_fallback_offer_applied"] == 0
    assert "本轮确定性完成收束合同" not in captured[-1][0].content
    assert "本轮自然收束合同" not in captured[-1][0].content
    resumed = runtime.prepare_turn(
        continued.stored,
        TurnRequestV2("changed_mind", 4, "我休息好了，我们现在过去吧。"),
        (),
        transition_intent="accept",
    )
    assert resumed.session.current_node_id == "ending_leave"
