"""Fixed-original audits see author prerequisites without treating plans as evidence."""

import json

import pytest

from services.theater import numeric_v2_evaluator as ev
from tests.unit.test_theater_numeric_v2_bound_completion_contract import _install
from tests.unit.test_theater_numeric_v2_review_protocol import _session
from tests.utils.theater_prompt import contract_audit_payload


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    pass


TOPICS = [
    ("小岚在固定原文之前完成翻开卷宗的动作。", "小岚翻开卷宗", "卷宗内页完整展示了两项登记内容。"),
    ("值班员在固定原文展示前先按下播放键。", "值班员按下播放键", "录音完整播放了两段声音。"),
]


def _case(topic, kind, *, fixed=True):
    prerequisite, trigger, effect = topic
    session = _session()
    body = {"segments": [
        {"phase": "source_response", "performance": "我同意这项展示安排。"},
        {"phase": "transition_bridge", "scene_narration": ""},
        {"phase": "target_opening", "scene_narration": "",
        "performance": f"（{trigger}）结果已经展示。",
         **({"fixed_narrations": [{"id": "effect", "position": "before", "text": effect}]} if fixed else {})},
    ], "visible_node_id": "work", "transition_delivered": True}
    target = {"id": "work", "type": "scene", "story_beat": {
        "opening_scene": "材料还没有打开或启动。", "summary": "先交付必要动作，再展示原文，最后承接结果。"}}
    if kind == "legacy":
        target["story_beat"]["must_happen"] = [prerequisite, "程序原文只交付一次，角色从交付后的实际状态回应。"]
        expected = {"must_happen": target["story_beat"]["must_happen"]}
    else:
        target["story_beat"]["goals"] = [{
            "id": "work_open", "owner": "catgirl", "description": prerequisite,
            "evidence": {"mode": "semantic", "anchors": []},
            "delivery": {"type": "catgirl_action", "output_field": "performance_action", "timing": "opening",
                         "source_ids": ["opening.work"]},
        }, {
            "id": "work_later_choice", "owner": "player", "description": "玩家在后续互动中自主决定是否继续了解。",
            "evidence": {"mode": "semantic", "anchors": []},
            "delivery": {"type": "player_action", "output_field": "player_input", "timing": "turn",
                         "source_ids": ["runtime.player_input", "goal.work_open"]},
        }]
        expected = {"goals": [{key: value for key, value in goal.items() if key != "evidence"}
                              for goal in target["story_beat"]["goals"]]}
    return session, body, target, expected


async def _review(session, body, target, sink):
    return await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"id": session.current_node_id, "story_beat": {}}, target_node=target, session=session,
        actor_performance=body, player_input="我同意这项展示安排。", include_all_segments=True,
        transition_contract={}, bound_completion_sink=sink)


@pytest.mark.asyncio
@pytest.mark.parametrize("topic", TOPICS)
@pytest.mark.parametrize("kind", ["legacy", "modern"])
async def test_fixed_original_audit_retains_raw_plan_in_both_requests_but_never_as_clearance(monkeypatch, topic, kind):
    session, body, target, expected = _case(topic, kind)
    prerequisite, trigger, effect = topic

    def respond(messages, data, kwargs):
        assert data["transition_context"]["target_entry"]["author_delivery_plan"] == expected
        assert prerequisite in messages[1].content
        assert "只核对本次已展示原文" in messages[0].content
        if "finding" in data:
            source = next(row for row in data["clearance_sources"] if row["id"] == "target_entry")
            assert "author_delivery_plan" not in json.loads(source["text"])
            return {"verdict": "clear", "reason": "作者计划列出了前置动作。", "clearance": ["target_entry"]}
        fixed = next(row for row in data["sources"] if row.get("program_fixed_original"))
        later = next(row for row in data["sources"] if row.get("type") == "action" and trigger in row["text"])
        index = next(row["rule_index"] for row in data["author_boundaries"] if row["text"] == ev._FIXED_ORIGINAL_CAUSE_RULE)
        payload = contract_audit_payload({"violations": [{"rule_index": index, "candidate_ref": fixed["id"],
            "quote": effect, "prior_ref": later["id"]}]}, messages)
        return payload

    calls = _install(monkeypatch, respond)
    sink = []
    assert ev._FIXED_ORIGINAL_CAUSE_RULE in await _review(session, body, target, sink)
    assert sink == [] and len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("topic", TOPICS)
async def test_future_turn_goal_remains_planning_and_does_not_require_entry_completion(monkeypatch, topic):
    session, body, target, expected = _case(topic, "modern")
    # This draft gives the actual entry cause before the original; the future player goal stays open.
    body["segments"][2]["scene_narration"] = topic[1] + "。"
    body["segments"][2]["performance"] = "展示就是这样。"

    def respond(messages, data, kwargs):
        assert data["transition_context"]["target_entry"]["author_delivery_plan"] == expected
        assert expected["goals"][1]["delivery"]["timing"] == "turn"
        assert "不要求未来turn目标在入幕完成" in messages[0].content
        assert "bound_completion_check" not in data
        payload = contract_audit_payload({"violations": []}, messages)
        return payload

    calls = _install(monkeypatch, respond)
    sink = []
    assert await _review(session, body, target, sink) == ()
    assert sink == [] and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["legacy", "modern"])
async def test_no_displayed_fixed_original_keeps_delivery_plan_out_of_regular_review(monkeypatch, kind):
    session, body, target, _ = _case(TOPICS[0], kind, fixed=False)

    def respond(messages, data, kwargs):
        assert "author_delivery_plan" not in data["transition_context"]["target_entry"]
        payload = contract_audit_payload({"violations": []}, messages)
        return payload

    calls = _install(monkeypatch, respond)
    sink = []
    assert await _review(session, body, target, sink) == ()
    assert sink == [] and len(calls) == 1
