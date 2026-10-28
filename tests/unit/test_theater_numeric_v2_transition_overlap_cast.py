"""Bridge provenance compares author and performed text in the same runtime cast."""

from copy import deepcopy

import pytest

from services.theater import numeric_v2_evaluator as evaluator, numeric_v2_workflow as workflow
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from services.theater.numeric_v2_cast import NumericV2CastProjection
from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
from tests.unit.test_theater_numeric_v2_runtime import _binding


_CAST_CASES = [
    pytest.param(
        "小岚停在院门外等林舟同行。", "星野",
        "星野停在院门外等阿河同行。", "星野停在院门外等你同行。",
        id="both-author-names",
    ),
    pytest.param(
        "男女主角停在院门外等候管理员。", "星野",
        "阿河和星野停在院门外等候管理员。", "你和星野停在院门外等候管理员。",
        id="generic-lead-pair",
    ),
    pytest.param(
        "小岚停在院门外等林舟同行。", "林舟",
        "林舟停在院门外等阿河同行。", "林舟停在院门外等你同行。",
        id="runtime-name-equals-other-author-name",
    ),
]


def test_explicit_author_name_placeholders_use_single_pass_and_preserve_protocol_fields():
    cast = NumericV2CastProjection("林舟", "小岚", "小岚", "{{player_name}}")
    original = {
        "id": "{{catgirl_name}}", "target_node_id": "{{player_name}}",
        "opening_scene": "{{player_name}}和{{catgirl_name}}等着林舟与小岚。",
        "literal": "{{unknown}}不是姓名占位。",
    }
    result = cast.value(original)
    assert result["opening_scene"] == "小岚和{{player_name}}等着小岚与{{player_name}}。"
    assert result["id"] == "{{catgirl_name}}"
    assert result["target_node_id"] == "{{player_name}}"
    assert result["literal"] == original["literal"]
    assert original["opening_scene"] == "{{player_name}}和{{catgirl_name}}等着林舟与小岚。"


async def _run_case(tmp_path, monkeypatch, *, authored, actual, catgirl_name,
                    known, permitted, rewrite_from_actual_duplicate=False, review_enabled=True):
    case = initiation_case()
    engine = case["engine"]
    engine.story["intro"].update(player_name="林舟", catgirl_name="小岚")
    engine.story["initial_state"]["player_address_known"] = known
    engine.nodes["ending_leave"]["story_beat"]["opening_scene"] = authored
    contract = engine.nodes["start"]["route_gates"][1]["transition_contract"]
    contract["bridge_scene_narration"] = authored if permitted else "两人沿石阶前行。"
    before_story = deepcopy(engine.story)
    binding = {**_binding(), "catgirl_name": catgirl_name, "player_address": "阿河"}
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id="cast_overlap", catgirl_binding=binding,
        opening_performance=case["session"].opening_performance,
    )
    assert current.session.player_address_known is known
    before_bytes = runtime.store._path("cast_overlap").read_bytes()
    calls = {"actor": 0, "review": 0}
    delivered = []
    different_opening = "院门旁的石灯照亮了脚下的路。"
    first_duplicate = "窗旁的阅信桌上只剩展开的信纸。"

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((), False, transition_intent="initiate")

    async def generate(self, **kwargs):
        calls["actor"] += 1
        if rewrite_from_actual_duplicate and calls["actor"] == 1:
            bridge = opening = first_duplicate
        else:
            bridge = actual
            # Permission must match the real target, while unauthorized author
            # overlap must remain detectable even if this draft's target differs.
            opening = actual if permitted else different_opening
        candidate = {
            "source_performance": "",
            "bridge_scene_narration": bridge,
            "target_scene_narration": opening,
            "target_performance": "这里可以先看看。",
            "suggested_inputs": [],
        }
        result = engine.finalize_transition_performance(
            kwargs["outcome"], candidate, target_opening=opening,
            source_dialogue_policy="optional",
        )
        delivered.append(deepcopy(result))
        return result

    async def review(self, **kwargs):
        calls["review"] += 1
        return evaluator.NumericV2TransitionOfferReview(
            False, False, (), (), initiation_authorized=True, delivery_matches_route=True,
        )

    async def options():
        return {"evaluator": True, "review": review_enabled}

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    diagnostics = {}
    kwargs = dict(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("go", 0, "带路吧。"),
        ensure_current_binding=lambda _: binding, diagnostics_sink=diagnostics,
    )
    if permitted:
        result = await workflow.execute_numeric_v2_turn(**kwargs)
        assert calls == {"actor": 1, "review": int(review_enabled)}
        assert diagnostics["semantic_rewrite_attempts"] == 0
        assert result.stored.session.revision == 1
        # Never run the cast over actual candidate text a second time.
        assert result.performance["segments"] == delivered[0]["segments"]
        assert result.performance["segments"][1]["scene_narration"] == actual
        assert await runtime.restore_session("cast_overlap") == result.stored
    else:
        with pytest.raises(workflow.NumericV2ActorOutputError, match="transition_segment_overlap"):
            await workflow.execute_numeric_v2_turn(**kwargs)
        assert calls == {"actor": 2, "review": 0}
        assert diagnostics["semantic_rewrite_attempts"] == 1
        assert diagnostics["transition_bridge_leak_markers_after_rewrite"] == [actual.rstrip("。")]
        assert runtime.store._path("cast_overlap").read_bytes() == before_bytes
        assert await runtime.restore_session("cast_overlap") == current
    assert engine.story == before_story


@pytest.mark.asyncio
@pytest.mark.parametrize("authored,catgirl_name,known_text,unknown_text", _CAST_CASES)
@pytest.mark.parametrize("known", [True, False], ids=["address-known", "address-unknown"])
@pytest.mark.parametrize("permitted", [True, False], ids=["author-exemption", "author-only-overlap"])
async def test_formal_bridge_provenance_uses_current_runtime_cast(
    tmp_path, monkeypatch, authored, catgirl_name, known_text, unknown_text, known, permitted,
):
    await _run_case(
        tmp_path, monkeypatch, authored=authored,
        actual=known_text if known else unknown_text, catgirl_name=catgirl_name,
        known=known, permitted=permitted,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("permitted", [True, False])
async def test_bridge_provenance_without_protagonist_names_is_unchanged(tmp_path, monkeypatch, permitted):
    text = "石阶旁的灯光已经照亮整条通道。"
    await _run_case(
        tmp_path, monkeypatch, authored=text, actual=text, catgirl_name="星野",
        known=True, permitted=permitted,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("known", [True, False])
async def test_rewritten_bridge_is_still_checked_against_projected_author_opening(tmp_path, monkeypatch, known):
    await _run_case(
        tmp_path, monkeypatch, authored="小岚停在院门外等林舟同行。",
        actual=f"星野停在院门外等{'阿河' if known else '你'}同行。",
        catgirl_name="星野", known=known, permitted=False,
        rewrite_from_actual_duplicate=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("permitted", [True, False], ids=["exempt-commits", "overlap-rolls-back"])
async def test_default_review_disabled_retains_the_same_bridge_protection(tmp_path, monkeypatch, permitted):
    await _run_case(
        tmp_path, monkeypatch, authored="小岚停在院门外等林舟同行。",
        actual="星野停在院门外等你同行。", catgirl_name="星野",
        known=False, permitted=permitted, review_enabled=False,
    )
