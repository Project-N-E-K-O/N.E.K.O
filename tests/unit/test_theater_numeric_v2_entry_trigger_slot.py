"""An internal trigger slot preserves the public entry renderer and old protocols."""

from copy import deepcopy
import json

from jsonschema import Draft202012Validator
import pytest

from services.theater.numeric_v2_actor_output import NumericV2ActorOutputError, _parse_output
from services.theater.numeric_v2_fixed_narration import add_entry
from services.theater.numeric_v2_performance import performance_content_blocks
from services.theater.numeric_v2_structured_output import actor_output_schema
from tests.unit.test_theater_numeric_v2_runtime import _binding


def _wire(*, tail=False, trigger="小岚把录音带接入播放器，按下播放键。", narration="档案室的灯亮着。"):
    return {
        **({} if tail else {"source_scene_narration": "", "source_performance": ""}),
        "bridge_scene_narration": "走廊尽头是档案室。", "target_scene_narration": narration,
        "target_entry_trigger": trigger, "target_performance": "我听到他的请求了。", "suggested_inputs": [],
    }


def _parse(payload, *, tail=False, **kwargs):
    return _parse_output(json.dumps(payload, ensure_ascii=False), transition_required=True,
        transition_tail_only=tail, entry_narration=True, source_dialogue_policy="optional",
        entry_trigger_required=True, **kwargs)


@pytest.mark.parametrize("tail", [False, True])
@pytest.mark.parametrize("narration,trigger", [
    ("档案室的灯亮着。", "小岚把录音带接入播放器，按下播放键。"),
    ("", "值班员打开档案终端。"),
    ("已播原文已经完整交付前因。", ""),
])
def test_internal_trigger_merges_once_before_original_without_public_fields(tail, narration, trigger):
    payload = _wire(tail=tail, narration=narration, trigger=trigger)
    before = deepcopy(payload)
    result = _parse(payload, tail=tail)
    assert "target_entry_trigger" not in result and payload == before
    assert result["target_scene_narration"] == "\n".join(text for text in (narration, trigger) if text)
    original = "录音里传来老人完整的请求。"
    node = {"id": "archive", "story_beat": {"fixed_narrations": [{
        "id": "request", "text": original, "trigger": {"type": "entry"}, "after": [], "required_before_exit": True,
    }]}}
    rendered = add_entry(node, {"scene_narration": result["target_scene_narration"],
                               "performance": result["target_performance"]}, _binding(), True)
    texts = [row["text"] for row in performance_content_blocks(rendered)]
    assert texts == ([result["target_scene_narration"]] if narration or trigger else []) + [original, payload["target_performance"]]
    assert texts.count(original) == 1 and "fact_candidates" not in result


@pytest.mark.parametrize("bad", [None, 0, False, [], {}, "missing"])
@pytest.mark.parametrize("tail", [False, True])
def test_new_protocol_requires_explicit_string_trigger(bad, tail):
    payload = _wire(tail=tail)
    if bad == "missing": payload.pop("target_entry_trigger")
    else: payload["target_entry_trigger"] = bad
    with pytest.raises(NumericV2ActorOutputError):
        _parse(payload, tail=tail)


@pytest.mark.parametrize("tail", [False, True])
def test_old_direct_parser_protocol_remains_unchanged(tail):
    payload = _wire(tail=tail)
    with pytest.raises(NumericV2ActorOutputError):
        _parse_output(json.dumps(payload), transition_required=True, transition_tail_only=tail,
                      entry_narration=True, source_dialogue_policy="optional")
    payload.pop("target_entry_trigger")
    result = _parse_output(json.dumps(payload), transition_required=True, transition_tail_only=tail,
                           entry_narration=True, source_dialogue_policy="optional")
    assert result["target_scene_narration"] == payload["target_scene_narration"]


@pytest.mark.parametrize("mode", [
    {"natural_ending": True}, {"single_ending_response": True}, {"fixed_entry_only": True},
    {"entry_part": "prefix"}, {"entry_part": "response"}, {"opening_required": True},
    {"suggestions_only": True}, {"transition_suggestions_only": True}, {"reinvitation_only": True},
])
def test_trigger_mode_cannot_cross_existing_protocols(mode):
    with pytest.raises(NumericV2ActorOutputError):
        _parse(_wire(), **mode)


@pytest.mark.parametrize("tail", [False, True])
def test_new_schema_requires_trigger_and_nonempty_required_target_only_when_enabled(tail):
    schema = actor_output_schema(transition_required=True, transition_tail_only=tail,
                                entry_trigger_required=True, target_dialogue_required=True)
    payload = _wire(tail=tail)
    validator = Draft202012Validator(schema)
    assert not list(validator.iter_errors(payload))
    assert "target_entry_trigger" in schema["required"]
    fields = list(schema["properties"])
    assert fields.index("target_scene_narration") + 1 == fields.index("target_entry_trigger")
    assert fields.index("target_entry_trigger") + 1 == fields.index("target_performance")
    assert schema["required"].index("target_scene_narration") + 1 == schema["required"].index("target_entry_trigger")
    assert schema["properties"]["target_entry_trigger"] == {"type": "string"}
    assert schema["properties"]["target_performance"]["minLength"] == 1
    payload["target_performance"] = ""
    assert any(error.validator == "minLength" for error in validator.iter_errors(payload))
    payload["target_performance"] = "听到了。"
    payload.pop("target_entry_trigger")
    assert any(error.validator == "required" for error in validator.iter_errors(payload))


@pytest.mark.parametrize("mode", [
    {}, {"opening_required": True}, {"suggestions_only": True}, {"transition_suggestions_only": True},
    {"reinvitation_only": True}, {"transition_required": True, "fixed_entry_only": True},
    {"transition_required": True, "single_ending_response": True},
])
def test_trigger_schema_does_not_leak_to_unrelated_modes(mode):
    schema = actor_output_schema(**mode, entry_trigger_required=True, target_dialogue_required=True)
    assert "target_entry_trigger" not in schema["properties"]
    assert schema["properties"].get("target_performance", {}).get("minLength") is None


@pytest.mark.parametrize("policy", ["optional", "forbidden"])
def test_optional_and_forbidden_target_can_still_be_empty(policy):
    payload = _wire(trigger="", narration="")
    payload["target_performance"] = ""
    result = _parse(payload, target_dialogue_policy=policy)
    assert result["target_performance"] == ""
    schema = actor_output_schema(transition_required=True, entry_trigger_required=True)
    assert not list(Draft202012Validator(schema).iter_errors(payload))


def test_default_schema_and_required_parser_keep_existing_semantics():
    schema = actor_output_schema(transition_required=True, target_dialogue_required=True)
    assert "target_entry_trigger" not in schema["properties"]
    assert "minLength" not in schema["properties"]["target_performance"]
    payload = _wire()
    payload["target_performance"] = ""
    with pytest.raises(NumericV2ActorOutputError):
        _parse(payload)


def test_trigger_field_never_grants_player_authority_or_completion():
    trigger = "林舟按下播放键，然后声称已完整理解内容。"
    payload = _wire(trigger=trigger, narration="")
    result = _parse(payload)
    assert result["target_scene_narration"] == trigger
    assert not {"target_entry_trigger", "approved_completion_facts", "player_action_projection", "fact_candidates"} & result.keys()
    # This is still unapproved visible narration, to be audited under the existing ownership contract.
    assert "林舟按下播放键" in performance_content_blocks({
        "scene_narration": result["target_scene_narration"], "performance": result["target_performance"],
    })[0]["text"]
