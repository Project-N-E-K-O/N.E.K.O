"""Actor output accepts any subset of its optional fields and rejects the rest."""

import itertools
import json

import pytest

from services.theater.numeric_v2_actor_output import NumericV2ActorOutputError, _parse_output


PERFORMANCE = "（抬眼）我听到了。"
SCENE_NARRATION = "雨声停下，花店的灯重新亮起。"
SUGGESTIONS = [
    "（我点头）我先听你说完。",
    "（我后退一步）我想先看看周围。",
]
ORDINARY_OPTIONAL = {
    "scene_update": "门口的风铃轻轻响了一下。",
    "suggested_inputs": SUGGESTIONS,
    "transition_offered": True,
    "fact_candidates": [],
}
OPENING_OPTIONAL = {
    "suggested_inputs": SUGGESTIONS,
    "transition_offered": True,
}


def _subsets(fields):
    names = sorted(fields)
    for size in range(len(names) + 1):
        yield from itertools.combinations(names, size)


@pytest.mark.parametrize("optional", list(_subsets(ORDINARY_OPTIONAL)))
def test_ordinary_turn_accepts_every_optional_field_combination(optional):
    payload = {"performance": PERFORMANCE, **{name: ORDINARY_OPTIONAL[name] for name in optional}}

    parsed = _parse_output(json.dumps(payload, ensure_ascii=False))

    assert parsed["performance"] == PERFORMANCE
    assert parsed["transition_offered"] is ("transition_offered" in optional)
    assert parsed["suggested_inputs"] == (SUGGESTIONS if "suggested_inputs" in optional else [])
    assert parsed["fact_candidates"] == []
    assert ("scene_narration" in parsed) is ("scene_update" in optional)


@pytest.mark.parametrize("optional", list(_subsets(OPENING_OPTIONAL)))
def test_opening_accepts_every_optional_field_combination(optional):
    payload = {
        "scene_narration": SCENE_NARRATION,
        "performance": PERFORMANCE,
        **{name: OPENING_OPTIONAL[name] for name in optional},
    }

    parsed = _parse_output(json.dumps(payload, ensure_ascii=False), opening_required=True)

    assert parsed["scene_narration"] == SCENE_NARRATION
    assert parsed["performance"] == PERFORMANCE
    assert parsed["transition_offered"] is ("transition_offered" in optional)
    assert parsed["suggested_inputs"] == (SUGGESTIONS if "suggested_inputs" in optional else [])


@pytest.mark.parametrize("payload", [
    {"performance": PERFORMANCE, "unknown": 1},
    {"performance": PERFORMANCE, "transition_offered": False, "unknown": 1},
    {"transition_offered": False, "suggested_inputs": SUGGESTIONS},
    {"scene_update": "门口的风铃轻轻响了一下。"},
    {},
])
def test_ordinary_turn_rejects_unknown_or_missing_required_fields(payload):
    with pytest.raises(NumericV2ActorOutputError, match="numeric_v2_actor_fields_invalid"):
        _parse_output(json.dumps(payload, ensure_ascii=False))


@pytest.mark.parametrize("payload", [
    {"scene_narration": SCENE_NARRATION, "performance": PERFORMANCE, "unknown": 1},
    {"scene_narration": SCENE_NARRATION, "performance": PERFORMANCE, "fact_candidates": []},
    {"scene_narration": SCENE_NARRATION, "performance": PERFORMANCE, "scene_update": "x"},
    {"performance": PERFORMANCE, "transition_offered": False},
    {"scene_narration": SCENE_NARRATION, "transition_offered": False},
])
def test_opening_rejects_unknown_or_missing_required_fields(payload):
    with pytest.raises(NumericV2ActorOutputError, match="numeric_v2_actor_fields_invalid"):
        _parse_output(json.dumps(payload, ensure_ascii=False), opening_required=True)
