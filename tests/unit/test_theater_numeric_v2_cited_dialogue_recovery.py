"""A cited author violation can be removed only as a newly audited candidate."""

from copy import deepcopy

import pytest

from services.theater import numeric_v2_options as options, numeric_v2_workflow as wf
from services.theater.numeric_v2_evaluator import NumericV2EvaluationResult, NumericV2StoryEvidenceReview, _PLAYER_AUTHORITY_RULE
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding


KEY = "scene:start:verified"
TOPICS = {
    "gardening": ("不得编造未公开的肥料配方。", "这袋肥料含有秘密配方，浇水后会让花瓣变蓝。", "请园丁查看标签。"),
    "astronomy": ("不得编造未观测到的天体特征。", "这颗星的内部有金属海洋，每小时会翻转一次。", "我们先看已拍到的影像。"),
    "document": ("不得编造印泥配方或未展示的凭证。", "此证印泥掺有松烟，遇冷色淡，对光可见原纹。", "请管事验看。"),
}
ACTION = "（上前半步，语气平稳）"
SCENE = "值班人员把台灯转向桌面，等待进一步说明。"
MESSAGE = "我在旁边等候，请继续说明眼前能确认的内容。"


async def _case(tmp_path, topic):
    rule, _, _ = TOPICS[topic]
    story = numeric_v2_story()
    story["intro"].update(background="双方在工作室核对眼前的材料。",
                          player_identity="林舟，到访者。", catgirl_identity="小岚，协助核对的同伴。")
    source = story["nodes"][0]
    source["story_beat"].update(summary="材料的关键性质仍未核实，角色可以回应当前问题。",
        must_happen=["回应眼前问题。"], must_not_happen=[rule])
    story["fact_contract"] = {"facts": {KEY: {
        "value_type": "bool", "visibility": "public", "evidence_source": "performance",
        "description": "眼前材料的关键性质已经实际核实。"}}}
    source["completion_contract"] = {"all": [{"key": KEY, "equals": True}]}
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(session_id="cited-dialogue-" + topic, catgirl_binding=_binding(),
        opening_performance={"performance": "材料在桌上，我们还没有确认它的性质。", "suggested_inputs": []})
    return runtime, current


def _install(monkeypatch, topic, *, mode="clear"):
    rule, quote, remainder = TOPICS[topic]
    calls = {"actor": 0, "contract": []}
    original = {"performance": ACTION + quote + ("" if mode == "no_dialogue" else remainder),
        "scene_narration": SCENE, "suggested_inputs": ["继续听说明。"], "fact_candidates": [],
        "transition_offered": False, "_transition_offer_provided": True, "_published_transition_offer": ""}
    if mode == "duplicate":
        original["performance"] += quote
    elif mode == "duplicate_scene":
        original["scene_narration"] += quote
    elif mode == "fact_candidate":
        original["fact_candidates"] = [{"key": KEY, "value": True, "evidence_quote": quote}]
    elif mode == "fixed":
        original["fixed_narrations"] = [{"id": "locked", "text": quote, "position": "after"}]

    async def module_options():
        return {**options.default_options(), "evaluator": True, "review": False,
                "review_contract": True, "review_delivery": True, "actor_retry": False}

    async def evaluate(self, **kwargs):
        if mode == "completion_claim":
            return NumericV2EvaluationResult((), True,
                fact_operations=({"op": "set", "key": KEY, "value": True, "visibility": "public"},),
                fact_audit=({"key": KEY, "subject": "active_catgirl", "action": "verify", "object": "material",
                    "result": "verified", "evidence": [{"source": "actor_performance", "quote": quote}]},))
        return NumericV2EvaluationResult((), False)

    async def approve(self, **kwargs):
        return NumericV2StoryEvidenceReview()

    async def generate(self, **kwargs):
        calls["actor"] += 1
        assert kwargs["outcome"].route is None
        candidate = deepcopy(original)
        if mode == "changed_rewrite" and calls["actor"] == 2:
            candidate["performance"] += "我再确认一下。"
        return candidate

    async def contract(self, **kwargs):
        candidate = deepcopy(kwargs["actor_performance"])
        calls["contract"].append(candidate)
        diagnostics = kwargs["diagnostics"]
        diagnostics.update(author_coverage_complete=True, player_authority_complete=True)
        if quote not in candidate["performance"]:
            assert len(calls["contract"]) == 2
            assert candidate["performance"] == ACTION + remainder
            assert candidate["scene_narration"] == original["scene_narration"]
            assert candidate.get("suggested_inputs", []) == []
            assert candidate.get("fact_candidates", []) == []
            assert not candidate.get("transition_offered")
            assert not candidate.get("_published_transition_offer")
            if mode != "still_violates":
                return ()
            return (rule,)
        blocks = wf.performance_content_blocks(candidate)
        index, block = next((index, block) for index, block in enumerate(blocks)
            if block["type"] == "dialogue" and quote in block["text"])
        finding = {"rule": _PLAYER_AUTHORITY_RULE if mode == "player_action" else rule,
            "quote": quote, "candidate": {"id": "c" + str(index), **block, "phase": "turn"},
            "prior": None, "rule_index": 0,
            "rule_origin": {"origin_node_id": "start", "origin_phase": "turn"}}
        if mode == "fixed":
            finding["candidate"]["program_fixed_original"] = True
        elif mode == "historical":
            finding["candidate"]["id"] = "h0"
        elif mode == "candidate_mismatch":
            finding["candidate"]["text"] = quote
        elif mode == "quote_mismatch":
            finding["quote"] = "原文没有的句子。"
        elif mode == "not_author_rule":
            finding["rule"] = "审核自行新增的禁止事项。"
        diagnostics["findings"] = [finding]
        return (finding["rule"],)

    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(wf, "aload_theater_module_options", module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "review_story_evidence", approve)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", contract)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "沉着、自然。")
    return calls, original


async def _execute(runtime, current):
    return await wf._execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("cited-recovery", current.session.revision, MESSAGE),
        ensure_current_binding=lambda _: _binding())


@pytest.mark.asyncio
@pytest.mark.parametrize("topic", TOPICS)
async def test_same_rejected_dialogue_can_remove_only_the_cited_sentence_and_recheck(tmp_path, monkeypatch, topic):
    runtime, current = await _case(tmp_path, topic)
    calls, _ = _install(monkeypatch, topic)
    result = await _execute(runtime, current)
    assert calls["actor"] == 2 and len(calls["contract"]) == 2
    assert result.performance["performance"] == ACTION + TOPICS[topic][2]
    assert result.performance["scene_narration"] == SCENE
    assert result.performance["suggested_inputs"] == []
    assert result.stored.session.current_node_id == "start"
    assert result.stored.session.revision == current.session.revision + 1
    assert result.stored.session.story_state["facts"] == current.session.story_state["facts"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["still_violates", "no_dialogue", "fixed", "player_action", "duplicate",
    "duplicate_scene", "fact_candidate", "completion_claim", "historical", "candidate_mismatch", "quote_mismatch",
    "not_author_rule", "changed_rewrite"])
async def test_citation_recovery_cannot_relax_review_authority_or_fact_dependencies(tmp_path, monkeypatch, mode):
    runtime, current = await _case(tmp_path, "document")
    calls, _ = _install(monkeypatch, "document", mode=mode)
    before = runtime.store._path(current.session.session_id).read_bytes()
    with pytest.raises(wf.NumericV2ActorOutputError, match="contract_violation"):
        await _execute(runtime, current)
    assert len(calls["contract"]) == (2 if mode in {"still_violates", "changed_rewrite"} else 1)
    assert calls["actor"] == (1 if mode == "fixed" else 2)
    assert runtime.store._path(current.session.session_id).read_bytes() == before
