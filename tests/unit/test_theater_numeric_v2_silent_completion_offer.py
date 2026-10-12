from copy import deepcopy

import pytest

from services.theater.numeric_v2 import NumericV2CompileError, NumericV2Compiler
from tests.unit.test_theater_numeric_v2_unified_pacing import story_for


def _story(genre, policy="optional", effects=("forbidden",)):
    story = story_for("下一处已公开的地点")
    beat = story["nodes"][0]["story_beat"]
    beat.pop("must_happen")
    beat["summary"] = {"scifi": "终端关闭前完成交接。", "fantasy": "封印落定前完成交接。"}[genre]
    beat["acting_contract"] = {
        "cognition_state": "normal", "memory_state": "available",
        "self_reference_mode": "persona_allowed", "persona_scope": "full",
        "dialogue_policy": policy, "allowed_behaviors": [], "forbidden_behaviors": [],
    }
    beat["goals"] = [{
        "id": f"start.result_{index}", "owner": "environment",
        "description": "公开交付当前结果。",
        "evidence": {"mode": "semantic", "anchors": []},
        "delivery": {
            "type": "environment_fact", "output_field": "scene_update",
            "source_ids": ["opening.start"],
            **({"state_effects": {"dialogue_policy": effect}} if effect else {}),
        },
    } for index, effect in enumerate(effects)]
    story["nodes"][0]["route_gates"][1]["transition_contract"].pop("fallback_offer")
    return story


@pytest.mark.parametrize("genre", ["scifi", "fantasy"])
@pytest.mark.parametrize("policy,effects", [
    ("optional", ("forbidden",)),
    ("forbidden", (None,)),
    ("optional", ("forbidden", None)),
])
def test_silent_completion_allows_omitted_fallback_without_changing_the_package(genre, policy, effects):
    story = _story(genre, policy, effects)
    before = deepcopy(story)
    compiled = NumericV2Compiler().compile(story)
    assert compiled.story == before
    assert story == before


@pytest.mark.parametrize("genre", ["scifi", "fantasy"])
@pytest.mark.parametrize("policy,effects", [
    ("optional", (None,)),
    ("optional", ("forbidden", "optional")),
    ("forbidden", ("required",)),
])
def test_speaking_completion_still_requires_fallback(genre, policy, effects):
    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile(_story(genre, policy, effects))
    assert any(issue.path.endswith("fallback_offer") for issue in caught.value.issues)


@pytest.mark.parametrize("field,value", [("accept_input", None), ("fallback_offer", "")])
def test_silent_completion_keeps_accept_input_and_supplied_fallback_validation(field, value):
    story = _story("scifi")
    contract = story["nodes"][0]["route_gates"][1]["transition_contract"]
    contract[field] = value
    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile(story)
    assert any(issue.path.endswith(field) for issue in caught.value.issues)
