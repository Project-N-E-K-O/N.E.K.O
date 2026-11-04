"""Recommendation refill inherits only the current performance's entry limits."""

from copy import deepcopy
import json

import pytest

from services.theater.numeric_v2_actor import NumericV2Actor
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_entry_player_inputs import _ordinary_payload, _transition_payload
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_pending_offer import _commit
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


SOURCE_ENTRY = "不得在来源入幕时说明仓库钥匙的位置。"
TARGET_ENTRY = "不得在目标入幕时透露阁楼里的约定；玩家以后取得当事人许可时仍可谈论。"
SOURCE_ALWAYS = "不得损坏来源幕的账本。"
TARGET_ALWAYS = "不得损坏目标幕的信件。"
CHOICES = ["你愿意先介绍一下这里吗？", "（留在原地）"]


async def _case(tmp_path, formal):
    story = deepcopy(_engine(ordinary=True).story)
    for node_id, entry, always in (
        ("start", SOURCE_ENTRY, SOURCE_ALWAYS), ("middle", TARGET_ENTRY, TARGET_ALWAYS),
    ):
        beat = next(node["story_beat"] for node in story["nodes"] if node["id"] == node_id)
        beat["opening_only_boundaries"] = [entry]
        beat["must_not_happen"] = [always]
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    stored = await runtime.start_session(
        session_id="fill-entry", catgirl_binding=_binding(), opening_performance=_opening(),
    )
    stored = await _commit(runtime, stored, "我们去廊下看看，好吗？", offer=True)
    message = "好，我们去廊下。" if formal else "先说说这里的天气。"
    outcome = runtime.prepare_turn(stored, TurnRequestV2("next", stored.session.revision, message), (),
                                   transition_intent="accept" if formal else "unclear")
    assert (outcome.session.current_node_id != stored.session.current_node_id) is formal
    return runtime, stored, outcome, message


async def _generate(monkeypatch, runtime, stored, outcome, message, *, formal, allow_fill, primary_choices):
    instance = NumericV2Actor(object())
    payload = _transition_payload() if formal else _ordinary_payload()
    payload["suggested_inputs"] = list(primary_choices)
    calls = []

    async def invoke(messages, **options):
        calls.append((messages, options))
        if len(calls) == 1:
            assert not options.get("suggestions_only")
            return deepcopy(payload)
        assert len(calls) == 2 and options["suggestions_only"] is True
        return {"suggested_inputs": list(CHOICES)}

    monkeypatch.setattr(instance, "_invoke", invoke)
    before = deepcopy((runtime.engine.story, stored.session.to_dict(), outcome.session.to_dict()))
    result = await instance.generate_turn(
        engine=runtime.engine, session=stored.session, outcome=outcome,
        player_input=message, character_profile="温和克制。", allow_suggestion_fill=allow_fill,
    )
    assert (runtime.engine.story, stored.session.to_dict(), outcome.session.to_dict()) == before
    return instance, result, calls, payload


@pytest.mark.asyncio
@pytest.mark.parametrize("formal", [False, True])
async def test_native_turn_refill_has_target_entry_limit_only_on_actual_entry(tmp_path, monkeypatch, formal):
    runtime, stored, outcome, message = await _case(tmp_path, formal)
    instance, result, calls, payload = await _generate(
        monkeypatch, runtime, stored, outcome, message,
        formal=formal, allow_fill=True, primary_choices=[],
    )
    assert len(calls) == 2 and instance.suggestion_fill_attempt_count == 1
    first_messages = calls[0][0]
    data = json.loads(calls[1][0][1].content)
    boundaries = data["hard_boundaries"]
    assert (TARGET_ENTRY in boundaries) is formal
    assert SOURCE_ENTRY not in boundaries
    assert f"禁止发生的剧情：{TARGET_ALWAYS if formal else SOURCE_ALWAYS}" in boundaries
    assert (f"禁止发生的剧情：{SOURCE_ALWAYS if formal else TARGET_ALWAYS}") not in boundaries
    if formal:
        assert TARGET_ENTRY in first_messages[0].content
        assert data["player_input"] == ""
        assert result["segments"][2]["performance"] == payload["target_performance"]
    else:
        assert SOURCE_ENTRY not in first_messages[0].content
        assert data["player_input"] == message
        assert result["performance"] == payload["performance"]
    assert result["suggested_inputs"] == CHOICES


@pytest.mark.asyncio
@pytest.mark.parametrize("formal", [False, True])
@pytest.mark.parametrize("already_usable", [False, True])
async def test_disabled_or_usable_recommendations_never_add_a_call(
    tmp_path, monkeypatch, formal, already_usable,
):
    runtime, stored, outcome, message = await _case(tmp_path, formal)
    allow_fill = already_usable
    assert already_usable or allow_fill is False
    primary = CHOICES[:1] if already_usable else []
    instance, result, calls, _ = await _generate(
        monkeypatch, runtime, stored, outcome, message,
        formal=formal, allow_fill=allow_fill, primary_choices=primary,
    )
    assert len(calls) == 1 and instance.suggestion_fill_attempt_count == 0
    assert result["suggested_inputs"] == primary
