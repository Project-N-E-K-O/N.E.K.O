"""Shared-agreement tasks elicit a role attitude without granting completion."""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_actor as actor
from services.theater.numeric_v2_runtime import TurnRequestV2
from tests.unit.test_theater_numeric_v2_entry_trigger_order import CASES, _case
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_source_delivery_routing import _engine, KEY
from tests.utils.theater_prompt import prompt_payload


ATTITUDE = "自然说清是否接受当前提议以及仍需满足的条件"


def _task(kind="shared_agreement"):
    return {**({"kind": kind} if kind is not None else {}), "pending_results": [
        {"key": KEY, "value": True, "description": "双方已同意同一份设备去向安排。"}]}


def _ordinary():
    engine = _engine()
    session = engine.create_session(session_id="shared-attitude", catgirl_binding=_binding(),
        opening_performance={"performance": "设备放在这里，我们先商量去向。", "suggested_inputs": []})
    outcome = engine.resolve_turn(session, TurnRequestV2("proposal", 0, "你愿意按这个安排办吗？"), ())
    return engine, session, outcome


@pytest.mark.parametrize("message", ["你愿意按这个安排办吗？", "我建议先留在这里，你有什么不同意见？", "你现在只是能配合，还是已经接受这个安排？"])
@pytest.mark.parametrize("combined", [False, True])
def test_shared_task_preserves_original_input_and_requires_attitude_without_fixed_wording(message, combined):
    engine, session, outcome = _ordinary()
    before = deepcopy(session.to_dict())
    task = _task()
    messages = actor._turn_messages(engine, session, outcome, message, "温和。", "小岚", "你",
                                   source_delivery_task=task, combined_evaluation=combined)
    system = messages[0].content
    data = prompt_payload(messages[1].content)
    assert ATTITUDE in system
    assert "能力说明或可配合不能代替表达态度" in system
    assert "不要求固定口令" in system
    assert "可以不同意" in system and "不替玩家补同意" in system
    assert "同意待做安排不等于已经执行" in system
    assert data["source_delivery_task"] == task
    assert data["player_input"] == message
    assert ("turn_evaluation" in system) is combined
    assert session.to_dict() == before


@pytest.mark.parametrize("kind", [None, "physical_delivery", "other"])
def test_nonshared_tasks_keep_the_existing_delivery_instruction(kind):
    engine, session, outcome = _ordinary()
    base = actor._turn_messages(engine, session, outcome, "请交付签收回执。", "温和。", "小岚", "你",
                                source_delivery_task=_task(None))
    actual = actor._turn_messages(engine, session, outcome, "请交付签收回执。", "温和。", "小岚", "你",
                                  source_delivery_task=_task(kind))
    assert actual[0].content == base[0].content
    assert ATTITUDE not in actual[0].content
    assert "要求实际交付时须写出结果，不能仅答应" in actual[0].content


@pytest.mark.parametrize("mode", ["transition", "ending", "reinvite"])
def test_shared_task_does_not_add_attitude_instruction_outside_source_response(mode):
    if mode == "reinvite":
        engine, session, outcome = _ordinary()
    else:
        engine, session, outcome, _ = _case(CASES[0])
        if mode == "ending":
            outcome = replace(outcome, session=replace(outcome.session, status="ended"),
                              ledger_event={**outcome.ledger_event, "natural_ending_ready": True})
    messages = actor._turn_messages(engine, session, outcome, "你愿意吗？", "温和。", "小岚", "你",
        source_delivery_task=_task(), input_source="reinvite" if mode == "reinvite" else "freeform")
    assert ATTITUDE not in messages[0].content
    assert "source_delivery_task" not in prompt_payload(messages[1].content)


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["我愿意按这个安排办，后面的实际操作还没开始。", "我不接受这个去向，我们先说清理由。", "如果先确认保管责任，我才愿意接受；现在还没定。"])
async def test_combined_source_provider_receives_shared_task_without_approving_its_reply(monkeypatch, reply):
    engine, session, outcome = _ordinary()
    before = deepcopy(session.to_dict())
    calls = []

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            calls.append(messages)
            assert ATTITUDE in messages[0].content
            assert prompt_payload(messages[1].content)["source_delivery_task"] == _task()
            assert "turn_evaluation" in kwargs["response_format"]["json_schema"]["schema"]["properties"]
            return SimpleNamespace(content=json.dumps({"performance": reply, "transition_offer": "",
                "suggested_inputs": [], "fact_candidates": [], "turn_evaluation": {
                    "scene_complete": False, "metric_changes": {}, "fact_candidates": []}}, ensure_ascii=False))

    async def factory(*args, **kwargs): return Client()

    monkeypatch.setattr(actor, "_model_config", config)
    monkeypatch.setattr(actor, "create_chat_llm_async", factory)
    writer = actor.NumericV2Actor(object())
    result = await writer.generate_turn(engine=engine, session=session, outcome=outcome,
        player_input="你愿意按这个安排办吗？", character_profile="温和。",
        source_delivery_task=_task(), combined_evaluation=True)
    assert len(calls) == writer.provider_call_count == 1
    assert result["performance"] == reply
    assert result["_turn_evaluation"]["fact_candidates"] == [] and result["fact_candidates"] == []
    assert session.to_dict() == before
