"""Rule ownership and whole-draft inspection coverage are separate bindings."""

import pytest

from services.theater import numeric_v2_evaluator as ev
from tests.unit.test_theater_numeric_v2_bound_completion_contract import _install
from tests.unit.test_theater_numeric_v2_review_protocol import _session
from tests.utils.theater_prompt import contract_audit_payload


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("persistent", [False, True])
@pytest.mark.parametrize("operation,text", [("摇铃", "（小岚伸手握住铃柄，轻轻摇动）"), ("打开记录屏", "小岚打开记录屏。")])
async def test_source_rule_origin_survives_whole_coverage_and_confirmation(monkeypatch, persistent, operation, text):
    source_rule = (f"全过程不得{operation}，包括转场和后续场景。" if persistent else
                   f"本幕只商量是否示范，不实际{operation}；操作留到双方同意之后的下一幕。")
    target_rule = "本幕不得拆开备用盒。"
    source = {"id": "start", "story_beat": {"must_not_happen": [source_rule]}}
    target = {"id": "target", "story_beat": {"must_not_happen": [target_rule]}}
    body = {"segments": [
        {"phase": "source_response", "performance": "好，我同意这项示范。"},
        {"phase": "transition_bridge", "scene_narration": ""},
        {"phase": "target_opening", "scene_narration": text, "performance": "示范就是这样。"},
    ]}

    def respond(messages, data, kwargs):
        if "finding" in data:
            finding = data["finding"]
            assert finding["rule"] == source_rule and finding["rule_index"] == 0
            assert finding["rule_origin"] == {"origin_node_id": "start", "origin_phase": "source_response"}
            assert finding["candidate"]["phase"] == "target_opening"
            return {"verdict": "violation" if persistent else "clear",
                "reason": "原禁令明确持续到后幕。" if persistent else "本幕指来源幕，候选动作在目标幕。",
                "clearance": [] if persistent else ["transition_phases"]}
        assert [row["text"] for row in data["author_boundaries"][:2]] == [source_rule, target_rule]
        assert data["author_checks_required"] == [
            {"rule_index": 0, "origin_node_id": "start", "origin_phase": "source_response",
             "check_phases": ["source_response", "transition_bridge", "target_opening"]},
            {"rule_index": 1, "origin_node_id": "target", "origin_phase": "target_opening", "check_phases": ["target_opening"]},
        ]
        return contract_audit_payload({"violations": [{"rule_index": 0, "candidate_ref": "c1", "quote": text, "prior_ref": ""}]}, messages)

    calls = _install(monkeypatch, respond)
    diagnostics = {}
    problems = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node=source, target_node=target, session=_session(), actor_performance=body,
        player_input="你愿意做这项示范吗？", include_all_segments=True, diagnostics=diagnostics, transition_contract={})
    assert bool(problems) is persistent and len(calls) == 2
    assert diagnostics["author_coverage_complete"] is True


@pytest.mark.asyncio
async def test_identical_rules_in_distinct_nodes_keep_the_original_index_and_owner(monkeypatch):
    rule = "本幕不得拆开备用盒。"
    source = {"id": "start", "story_beat": {"must_not_happen": [rule]}}
    target = {"id": "target", "story_beat": {"must_not_happen": [rule]}}
    body = {"segments": [
        {"phase": "source_response", "performance": "继续吧。"},
        {"phase": "transition_bridge", "scene_narration": ""},
        {"phase": "target_opening", "performance": "（拆开备用盒）盒子打开了。"},
    ]}

    def respond(messages, data, kwargs):
        if "finding" in data:
            assert data["finding"]["rule_index"] == 1
            assert data["finding"]["rule_origin"] == {"origin_node_id": "target", "origin_phase": "target_opening"}
            assert [row["id"] for row in data["clearance_sources"] if row["type"] == "contract_rule"] == ["r1"]
            return {"verdict": "violation", "reason": "目标幕禁令禁止该实际动作。", "clearance": []}
        return contract_audit_payload({"violations": [{"rule_index": 1, "candidate_ref": "c1", "quote": "拆开备用盒", "prior_ref": ""}]}, messages)

    calls = _install(monkeypatch, respond)
    assert await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node=source, target_node=target, session=_session(), actor_performance=body,
        player_input="继续。", include_all_segments=True) == (rule,)
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("field,trigger,effect", [
    ("summary", "小岚翻开卷宗", "卷宗内页完整展示了两项登记内容。"),
    ("narrative_summary", "值班员按下播放键", "录音完整播放了声音片段。"),
])
async def test_target_direction_is_retained_as_plan_but_cannot_clear_missing_cause(monkeypatch, field, trigger, effect):
    session = _session()
    body = {"segments": [
        {"phase": "source_response", "performance": "我同意这项展示安排。"},
        {"phase": "transition_bridge", "scene_narration": ""},
        {"phase": "target_opening", "scene_narration": "", "performance": f"（{trigger}）结果已经展示。",
         "fixed_narrations": [{"id": "effect", "position": "before", "text": effect}]},
    ], "visible_node_id": "work", "transition_delivered": True}
    direction = f"先由{trigger}，再由程序原文交付结果，最后回应已经展示的内容。"
    target = {"id": "work", "type": "scene", "story_beat": {
        "opening_scene": "材料尚未打开或启动。", "summary": direction if field == "summary" else "较短的备用方向。",
        **({"narrative_summary": direction} if field == "narrative_summary" else {})}}
    assert "character_state" not in target["story_beat"]

    def respond(messages, data, kwargs):
        assert data["transition_context"]["target_entry"]["story_direction"] == direction
        assert "story_direction是作者目标过程，不证明已发生" in messages[0].content
        if "finding" in data:
            # The plan contains the intended cause but is never evidence it occurred.
            return {"verdict": "clear", "reason": "作者方向列出了操作在先。", "clearance": ["target_entry"]}
        fixed = next(row for row in data["sources"] if row.get("program_fixed_original"))
        later = next(row for row in data["sources"] if row.get("type") == "action" and trigger in row["text"])
        cause_index = next(row["rule_index"] for row in data["author_boundaries"] if row["text"] == ev._FIXED_ORIGINAL_CAUSE_RULE)
        payload = contract_audit_payload({"violations": [{"rule_index": cause_index, "candidate_ref": fixed["id"],
            "quote": effect, "prior_ref": later["id"]}]}, messages)
        return payload

    calls = _install(monkeypatch, respond)
    sink = []
    assert ev._FIXED_ORIGINAL_CAUSE_RULE in await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"id": session.current_node_id, "story_beat": {}}, target_node=target, session=session,
        actor_performance=body, player_input="我同意这项展示安排。", include_all_segments=True,
        transition_contract={}, bound_completion_sink=sink)
    assert not sink and len(calls) == 2
