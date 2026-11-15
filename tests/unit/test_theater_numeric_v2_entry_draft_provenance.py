"""Entry repair retains validated wire fields privately, bound to the final body."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_actor as actor
from services.theater.numeric_v2_actor_output import NumericV2ActorOutputError, _parse_output
from services.theater.numeric_v2_performance import performance_fingerprint
from tests.unit.test_theater_numeric_v2_entry_trigger_order import CASES, _case


def _wire(topic=CASES[0], *, tail=False, narration="室内的灯亮着。", trigger=None):
    return {
        **({} if tail else {"source_performance": "", "source_scene_narration": ""}),
        "bridge_scene_narration": "走廊尽头的门开着。",
        "target_scene_narration": narration,
        "target_entry_trigger": topic[3] if trigger is None else trigger,
        "target_performance": topic[4], "suggested_inputs": ["说说你现在的想法。"],
    }


def _parse(payload, sink, **kwargs):
    return _parse_output(json.dumps(payload, ensure_ascii=False), transition_required=True,
        entry_narration=True, entry_trigger_required=True, source_dialogue_policy="optional",
        draft_provenance_sink=sink, **kwargs)


@pytest.mark.parametrize("tail", [False, True])
@pytest.mark.parametrize("narration,trigger", [
    ("室内的灯亮着。", "小岚打开记录屏。"), ("", "值班员展开记录。"), ("大厅广播正在播报。", ""),
])
def test_parser_retains_original_pair_without_changing_public_shape(tail, narration, trigger):
    payload = _wire(tail=tail, narration=narration, trigger=trigger)
    before = deepcopy(payload)
    sink = {"stale": True}
    result = _parse(payload, sink, transition_tail_only=tail)
    assert sink == {"target_scene_narration": narration, "target_entry_trigger": trigger}
    assert result["target_scene_narration"] == "\n".join(text for text in (narration, trigger) if text)
    assert "target_entry_trigger" not in result and "draft_provenance" not in result
    assert payload == before


@pytest.mark.parametrize("field,value", [("target_entry_trigger", None), ("target_performance", 0)])
def test_parser_failed_validation_clears_stale_and_never_publishes_partial_pair(field, value):
    payload = _wire()
    payload[field] = value
    sink = {"stale": True}
    with pytest.raises(NumericV2ActorOutputError):
        _parse(payload, sink)
    assert sink == {}


@pytest.mark.parametrize("mode,payload", [
    ({}, {"performance": "我们先核对这件事。", "suggested_inputs": []}),
    ({"transition_required": True, "entry_narration": True}, {
        "source_performance": "", "bridge_scene_narration": "门开着。",
        "target_scene_narration": "室内安静。", "target_performance": "我们继续吧。", "suggested_inputs": []}),
    ({"transition_required": True, "natural_ending": True, "entry_narration": True}, {
        "source_performance": "", "bridge_scene_narration": "门开着。",
        "target_scene_narration": "灯光渐暗。", "target_performance": "下次见。", "suggested_inputs": []}),
    ({"entry_part": "prefix", "entry_narration": True}, {
        "source_performance": "", "bridge_scene_narration": "门开着。", "target_scene_narration": "室内安静。"}),
    ({"transition_required": True, "fixed_entry_only": True, "entry_narration": True,
      "target_dialogue_policy": "optional"}, {
        "source_performance": "", "bridge_scene_narration": "门开着。",
        "target_scene_narration": "", "target_performance": "", "suggested_inputs": []}),
])
def test_other_parser_protocols_clear_sink_and_keep_their_result(mode, payload):
    sink = {"stale": True}
    expected = _parse_output(json.dumps(payload), source_dialogue_policy="optional", **mode)
    result = _parse_output(json.dumps(payload), source_dialogue_policy="optional",
                           draft_provenance_sink=sink, **mode)
    assert result == expected and sink == {}


def _provider(monkeypatch, payloads, *, close_fails=False):
    calls = []
    responses = iter(payloads)

    async def config(_):
        return {"model": "fake", "base_url": "https://example.invalid/v1", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args):
            if close_fails: raise RuntimeError("close failed")
        async def ainvoke(self, messages, **kwargs):
            calls.append((messages, kwargs))
            return SimpleNamespace(content=json.dumps(next(responses), ensure_ascii=False))

    async def factory(*args, **kwargs): return Client()

    monkeypatch.setattr(actor, "_model_config", config)
    monkeypatch.setattr(actor, "create_chat_llm_async", factory)
    return calls


def _turn(topic, *, tail=False):
    engine, session, outcome, _ = _case(topic, tail=tail)
    return dict(engine=engine, session=session, outcome=outcome,
        player_input=f"好，我们去{topic[0]}。", character_profile="沉着。",
        **({"transition_source": {"performance": "那就过去吧。"}} if tail else {}))


@pytest.mark.asyncio
@pytest.mark.parametrize("topic", CASES)
@pytest.mark.parametrize("tail", [False, True])
async def test_success_publishes_last_wire_pair_bound_to_unchanged_public_body(monkeypatch, topic, tail):
    payload = _wire(topic, tail=tail)
    calls = _provider(monkeypatch, [payload, payload])
    writer = actor.NumericV2Actor(object())
    kwargs = _turn(topic, tail=tail)
    before = kwargs["session"].to_dict()
    sink = {"stale": True}
    result = await writer.generate_turn(**kwargs, draft_provenance_sink=sink)
    control = await writer.generate_turn(**kwargs)
    assert result == control and kwargs["session"].to_dict() == before
    assert len(calls) == writer.provider_call_count == 2
    assert calls[0][0] == calls[1][0] and calls[0][1] == calls[1][1]
    assert sink == {"body_fingerprint": performance_fingerprint(result), "entry_fields": {
        key: payload[key] for key in ("target_scene_narration", "target_entry_trigger")}}
    assert result["segments"][2]["scene_narration"] == "\n".join(sink["entry_fields"].values())
    assert "target_entry_trigger" not in json.dumps(result) and "body_fingerprint" not in result


@pytest.mark.asyncio
async def test_failed_retry_clears_prior_success_before_replacement(monkeypatch):
    valid = _wire()
    invalid = {**valid, "target_performance": 0}
    replacement = _wire(narration="墙边的台灯亮着。", trigger="小岚启动播放器。")
    calls = _provider(monkeypatch, [valid, invalid, replacement])
    writer = actor.NumericV2Actor(object())
    kwargs = _turn(CASES[0])
    sink = {}
    first = await writer.generate_turn(**kwargs, draft_provenance_sink=sink)
    old = deepcopy(sink)
    with pytest.raises(NumericV2ActorOutputError):
        await writer.generate_turn(**kwargs, draft_provenance_sink=sink)
    assert sink == {}
    result = await writer.generate_turn(**kwargs, draft_provenance_sink=sink)
    assert sink["body_fingerprint"] == performance_fingerprint(result) != performance_fingerprint(first)
    assert sink != old and sink["entry_fields"]["target_entry_trigger"] == replacement["target_entry_trigger"]
    assert len(calls) == writer.provider_call_count == 3


@pytest.mark.asyncio
async def test_later_actor_validation_failure_does_not_publish_parser_pair(monkeypatch):
    _provider(monkeypatch, [_wire()])

    def reject(*args, **kwargs):
        raise NumericV2ActorOutputError("numeric_v2_actor_test_rejected")

    monkeypatch.setattr(actor, "_assert_no_unknown_player_address_leak", reject)
    sink = {"stale": True}
    with pytest.raises(NumericV2ActorOutputError, match="test_rejected"):
        await actor.NumericV2Actor(object()).generate_turn(**_turn(CASES[0]), draft_provenance_sink=sink)
    assert sink == {}


@pytest.mark.asyncio
async def test_suggestion_failure_does_not_publish_earlier_validated_body(monkeypatch):
    _provider(monkeypatch, [_wire()])
    writer = actor.NumericV2Actor(object())

    async def reject(**kwargs):
        raise NumericV2ActorOutputError("numeric_v2_actor_test_suggestions_rejected")

    monkeypatch.setattr(writer, "_ensure_suggestions", reject)
    sink = {"stale": True}
    with pytest.raises(NumericV2ActorOutputError, match="suggestions_rejected"):
        await writer.generate_turn(**_turn(CASES[0]), draft_provenance_sink=sink)
    assert sink == {}


@pytest.mark.asyncio
async def test_empty_trigger_remains_empty_without_guessing_a_trigger_from_narration(monkeypatch):
    _provider(monkeypatch, [_wire(trigger="")])
    sink = {}
    result = await actor.NumericV2Actor(object()).generate_turn(**_turn(CASES[0]), draft_provenance_sink=sink)
    assert sink["entry_fields"] == {"target_scene_narration": "室内的灯亮着。", "target_entry_trigger": ""}
    assert sink["body_fingerprint"] == performance_fingerprint(result)


@pytest.mark.asyncio
async def test_normalized_changed_narration_cannot_be_reverse_split(monkeypatch):
    original = CASES[0][2]
    _provider(monkeypatch, [_wire(narration=original + "\n室内的灯亮着。")])
    sink = {"stale": True}
    result = await actor.NumericV2Actor(object()).generate_turn(**_turn(CASES[0]), draft_provenance_sink=sink)
    assert result["segments"][2]["scene_narration"] == "室内的灯亮着。\n" + CASES[0][3]
    assert sink == {}


@pytest.mark.asyncio
async def test_client_closure_failure_does_not_publish_parsed_pair(monkeypatch):
    _provider(monkeypatch, [_wire()], close_fails=True)
    sink = {"stale": True}
    with pytest.raises(actor.NumericV2ActorError, match="model_call_failed"):
        await actor.NumericV2Actor(object())._invoke([], transition_required=True,
            entry_narration=True, entry_trigger_required=True, source_dialogue_policy="optional",
            draft_provenance_sink=sink)
    assert sink == {}
