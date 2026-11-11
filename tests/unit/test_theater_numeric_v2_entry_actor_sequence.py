"""One fixed-entry draft keeps authorized causes before originals and replies after them."""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_actor as actor
from services.theater.numeric_v2_actor_output import NumericV2ActorOutputError, _parse_output
from services.theater.numeric_v2_performance import performance_content_blocks
from tests.unit.test_theater_numeric_v2_entry_trigger_order import CASES, _case
from tests.utils.theater_prompt import prompt_payload


COMBINED_CASES = [*CASES,
    ("观测台", "小岚启动星图，观测结果显现。", "星图显示：北侧亮点正在接近观测轴线。",
     "小岚启动观测台的星图。", "北侧的位置已经显示出来，我们核对下一项吧。"),
    ("温室", "小岚打开传感器读数页，温室记录展开。", "温室记录：东区湿度为百分之六十，所有阀门保持关闭。",
     "小岚打开传感器的读数页。", "东区读数已经显示，我们先讨论是否需要调整。"),
    ("展览室", "管理员苏禾开启展柜的记录屏，保管记录显示出来。", "保管记录：展品编号C-19于周一完成入库。",
     "管理员苏禾开启展柜的记录屏。", "入库时间已经显示，我们继续核对下一项。"),
    ("大厅", "大厅广播正在自动播报当日公告。", "大厅公告：本日展览于下午五点闭馆。",
     "", "公告说明了闭馆时间，我们按剩余时间安排吧。"),
]


@pytest.mark.parametrize('tail', [False, True])
def test_entry_prefix_uses_reviewed_source_instead_of_reissuing_source_author_tasks(tail):
    engine, session, outcome, _ = _case(CASES[0])
    source = engine.nodes[session.current_node_id]['story_beat']
    source['summary'] = '旧幕作者待演的登记流程。'
    source['character_state'] = {'catgirl_state': '旧幕作者的工作台位置。'}
    outcome = replace(outcome, transition_contract={**outcome.transition_contract,
        'must_deliver': ['本次获准的桥段内容。'], 'must_preserve': ['保持已确认分工。']})
    reviewed = {'performance': '这封信已经核对过了。'}
    messages = actor._turn_messages(engine,session,outcome,'好，我们去录音室。','沉着。','小岚','林舟',
        entry_part='prefix', **({'transition_source':reviewed} if tail else {}))
    data = prompt_payload(messages[1].content)
    transition = data['transition']
    assert transition['must_deliver'] == ['本次获准的桥段内容。']
    assert transition['must_preserve'] == ['保持已确认分工。']
    assert 'target_scene' in transition
    assert data['acting_context']['target_dialogue_policy'] == outcome.session.dialogue_policy
    assert transition['reason']
    if tail:
        assert transition['reviewed_source'] == reviewed
        assert 'source_scene' not in transition
        assert not {'source_author_entry_state','source_author_entry_situation'} & data['acting_context'].keys()
        assert '旧幕作者待演的登记流程' not in '\n'.join(message.content for message in messages)
    else:
        assert transition['source_scene']['story_direction'] == source['summary']
        assert data['acting_context']['source_author_entry_state']['catgirl_state'] == '旧幕作者的工作台位置。'


@pytest.mark.asyncio
@pytest.mark.parametrize("topic", COMBINED_CASES)
@pytest.mark.parametrize("tail", [False, True])
async def test_entry_actor_generates_one_ordered_draft_around_exact_program_original(monkeypatch, topic, tail):
    engine, session, outcome, _ = _case(topic, tail=tail)
    _, _, original, cause, reaction = topic
    calls = []
    caps = []
    source = {"performance": "那就过去吧。"} if tail else None

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            schema = kwargs["response_format"]["json_schema"]["schema"]
            fields = set(schema["properties"])
            calls.append((messages, fields))
            assert {"bridge_scene_narration", "target_scene_narration", "target_entry_trigger",
                    "target_performance", "suggested_inputs"} <= fields
            ordered = list(schema["properties"])
            assert ordered.index("target_scene_narration") < ordered.index("target_entry_trigger") < ordered.index("target_performance")
            assert "target_entry_trigger" in schema["required"]
            assert schema["properties"]["target_performance"]["minLength"] == 1
            data = prompt_payload(messages[1].content)
            assert data["player_input"] == f"好，我们去{topic[0]}。"
            assert original not in messages[0].content
            assert data["transition"]["program_entry_delivery"]["fixed_narrations"][0]["text"] == original
            assert data["transition"]["program_entry_delivery"]["after"] == "target_entry_trigger"
            assert "须用明确主体在target_entry_trigger交付一次" in messages[0].content
            assert "required含" in messages[0].content
            prefix = {"bridge_scene_narration": f"走廊尽头便是{topic[0]}。", "target_scene_narration": "",
                      "target_entry_trigger": cause}
            if not tail:
                prefix.update(source_scene_narration="", source_performance="")
            assert "entry_prefix" not in data["transition"]
            return SimpleNamespace(content=json.dumps({**prefix, "target_performance": reaction,
                                                        "suggested_inputs": ["说说你听后的想法。"]}, ensure_ascii=False))

    async def factory(*args, **kwargs):
        caps.append(kwargs["max_completion_tokens"])
        return Client()

    monkeypatch.setattr(actor, "_model_config", config)
    monkeypatch.setattr(actor, "create_chat_llm_async", factory)
    writer = actor.NumericV2Actor(object())
    before = deepcopy(session.to_dict())
    result = await writer.generate_turn(engine=engine, session=session, outcome=outcome,
        player_input=f"好，我们去{topic[0]}。", character_profile="沉着。", transition_source=source)
    assert len(calls) == 1 and writer.provider_call_count == 1
    assert sum(caps) == actor.NUMERIC_V2_ACTOR_TRANSITION_MAX_OUTPUT_TOKENS
    texts = [row["text"] for row in performance_content_blocks(result)]
    expected = [*([cause] if cause else []), original, reaction]
    assert texts[-len(expected):] == expected
    assert result["segments"][2]["fixed_narrations"][0]["text"] == original
    assert texts.count(original) == 1
    assert session.to_dict() == before
    assert "entry_prefix" not in result and "entry_part" not in result
    assert "target_entry_trigger" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize("part,payload", [
    ("prefix", {"source_performance": "", "bridge_scene_narration": "", "target_scene_narration": "灯亮了。", "target_performance": "先听。"}),
    ("prefix", {"source_performance": "", "bridge_scene_narration": "", "target_scene_narration": "灯亮了。", "suggested_inputs": []}),
    ("response", {"target_performance": "我听到了。", "suggested_inputs": [], "target_scene_narration": "偷偷改了前因。"}),
    ("response", {"target_performance": "我听到了。", "suggested_inputs": [], "source_performance": "改写来源。"}),
])
def test_entry_call_parser_cannot_write_the_other_half(part, payload):
    with pytest.raises(NumericV2ActorOutputError):
        _parse_output(json.dumps(payload, ensure_ascii=False), entry_part=part, entry_narration=True,
                      source_dialogue_policy="optional", target_dialogue_policy="required")


@pytest.mark.asyncio
@pytest.mark.parametrize("topic", CASES)
async def test_failed_combined_request_is_not_submitted_or_reused_on_new_draft(monkeypatch, topic):
    engine, session, outcome, _ = _case(topic)
    _, _, original, cause, reaction = topic
    writer = actor.NumericV2Actor(object())
    before = deepcopy(session.to_dict())
    calls = []

    async def invoke(messages, **kwargs):
        calls.append(kwargs)
        assert "entry_part" not in kwargs
        if len(calls) == 1:
            raise actor.NumericV2ActorError("numeric_v2_actor_model_call_failed")
        assert "entry_prefix" not in prompt_payload(messages[1].content)["transition"]
        return {"source_performance": "", "bridge_scene_narration": "门外的灯亮着。",
                "target_scene_narration": cause + "窗外下起细雨。", "target_performance": reaction,
                "suggested_inputs": ["说说你的想法。"]}

    monkeypatch.setattr(writer, "_invoke", invoke)
    kwargs = dict(engine=engine, session=session, outcome=outcome,
                  player_input=f"好，我们去{topic[0]}。", character_profile="沉着。")
    with pytest.raises(actor.NumericV2ActorError, match="model_call_failed"):
        await writer.generate_turn(**kwargs)
    assert session.to_dict() == before
    result = await writer.generate_turn(**kwargs)
    assert len(calls) == 2
    assert performance_content_blocks(result)[-3]["text"] == cause + "窗外下起细雨。"
    assert session.to_dict() == before


@pytest.mark.asyncio
async def test_combined_entry_request_keeps_one_generation_deadline(monkeypatch):
    import asyncio

    engine, session, outcome, _ = _case(CASES[0])
    writer = actor.NumericV2Actor(object())
    before = deepcopy(session.to_dict())
    calls = []

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://example.invalid/v1", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            calls.append(messages)
            await asyncio.sleep(1)

    async def factory(*args, **kwargs): return Client()

    monkeypatch.setattr(actor, "_model_config", config)
    monkeypatch.setattr(actor, "create_chat_llm_async", factory)
    monkeypatch.setattr(actor, "NUMERIC_V2_ACTOR_TIMEOUT_SECONDS", 0.05)
    with pytest.raises(actor.NumericV2ActorError, match="numeric_v2_actor_timeout"):
        await writer.generate_turn(engine=engine, session=session, outcome=outcome,
                                  player_input="好，我们去录音室。", character_profile="沉着。")
    assert len(calls) == writer.provider_call_count == 1
    assert session.to_dict() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("topic", CASES)
@pytest.mark.parametrize("kind", ["without_fixed", "fixed_entry_only", "condition_only", "natural_ending"])
async def test_other_transition_paths_keep_one_request(monkeypatch, topic, kind):
    engine, session, outcome, _ = _case(topic, fixed=kind != "without_fixed",
                                      fixed_entry_only=kind == "fixed_entry_only")
    if kind == "condition_only":
        target = engine.nodes[outcome.session.current_node_id]
        target["story_beat"]["fixed_narrations"][0]["trigger"] = {"type": "condition", "condition": "角色揭开封签。"}
    elif kind == "natural_ending":
        outcome = replace(outcome, session=replace(outcome.session, status="ended"),
                          ledger_event={**outcome.ledger_event, "natural_ending_ready": True})
    writer = actor.NumericV2Actor(object())
    calls = []

    async def invoke(messages, **kwargs):
        if kwargs.get("suggestions_only"):
            return {"suggested_inputs": ["说说你的想法。"]}
        calls.append(kwargs)
        assert "entry_part" not in kwargs
        assert kwargs["entry_trigger_required"] is False
        return {"source_performance": "", "bridge_scene_narration": "门外的灯亮着。",
                "target_scene_narration": "" if kind == "fixed_entry_only" else "室内安静。",
                "target_performance": "" if kind == "fixed_entry_only" else topic[4], "suggested_inputs": []}

    monkeypatch.setattr(writer, "_invoke", invoke)
    await writer.generate_turn(engine=engine, session=session, outcome=outcome,
                              player_input=f"好，我们去{topic[0]}。", character_profile="沉着。")
    assert len(calls) == 1
    assert calls[0]["max_output_tokens"] == actor.NUMERIC_V2_ACTOR_TRANSITION_MAX_OUTPUT_TOKENS


@pytest.mark.parametrize("topic", CASES)
@pytest.mark.parametrize("tail", [False, True])
def test_second_entry_request_owns_only_target_contract_and_frozen_prefix(topic, tail):
    from dataclasses import replace

    engine, session, outcome, _ = _case(topic)
    source = engine.nodes[session.current_node_id]["story_beat"]
    target = engine.nodes[outcome.session.current_node_id]["story_beat"]
    for beat in (source, target):
        beat["acting_contract"] = {"dialogue_policy": "required", "cognition_state": "normal",
            "memory_state": "available", "self_reference_mode": "persona_allowed", "persona_scope": "full"}
        beat.setdefault("character_state", {})
    source["summary"] = "来源阶段尚待重新整理清单。"
    source["acting_contract"]["allowed_behaviors"] = ["来源专属许可：检查登记簿。"]
    source["character_state"]["catgirl_state"] = "来源旧入幕位置。"
    target["acting_contract"]["allowed_behaviors"] = ["目标专属许可：回应已交付内容。"]
    target["character_state"]["catgirl_state"] = "目标入幕职责仍有效。"
    target["character_state"]["continuity_from_previous"] = ["目标必须保留的连续状态。"]
    contract = {**outcome.transition_contract, "reason": "待演来源原因。",
                "must_deliver": ["待演桥段职责。"], "must_preserve": ["目标必须保留的连续状态。"]}
    outcome = replace(outcome, transition_contract=contract)
    frozen = {"status": "uncommitted_this_turn", "blocks": [
        {"type": "narration", "text": topic[3]}, {"type": "narration", "text": topic[2]}]}
    kwargs = {"transition_source": {"performance": "来源实际已交付原句。"}} if tail else {}
    messages = actor._turn_messages(engine, session, outcome, "我同意继续，但不能代我操作。", "沉着。", "小岚", "林舟",
                                   entry_part="response", entry_prefix=frozen, **kwargs)
    data = prompt_payload(messages[1].content)
    transition = data["transition"]
    assert not set(transition) & {"source_scene", "reason", "must_deliver", "must_preserve",
        "bridge_required", "bridge_scene_narration", "reviewed_source", "source_fact_targets", "source_committed_completion"}
    context = data["acting_context"]
    assert context["dialogue_policy"] == outcome.session.dialogue_policy == "required"
    assert context["acting_contract"]["allowed_behaviors"] == ["目标专属许可：回应已交付内容。"]
    assert not any(key.startswith(("target_", "source_")) for key in context)
    assert "same_as" not in context["relationship_control"]
    assert "opening_situation" not in transition["target_scene"]
    assert transition["target_scene"]["performed_entry_ref"] == "entry_prefix.blocks"
    assert transition["target_scene"]["author_entry_plan"]["catgirl_state"] == "目标入幕职责仍有效。"
    assert transition["target_scene"]["author_entry_plan"]["continuity_from_previous"] == ["目标必须保留的连续状态。"]
    assert transition["entry_prefix"] == frozen
    assert data["player_input"] == "我同意继续，但不能代我操作。"
    wire = "\n".join(message.content for message in messages)
    assert "来源阶段尚待重新整理清单" not in wire
    assert "来源专属许可" not in wire and "来源旧入幕位置" not in wire
    assert "acting_context.target_dialogue_policy" not in messages[0].content


@pytest.mark.asyncio
@pytest.mark.parametrize("topic", CASES)
@pytest.mark.parametrize("cause_present", [False, True])
@pytest.mark.parametrize("tail", [False, True])
async def test_post_original_duties_do_not_turn_permissions_or_missing_causes_into_tasks(
    monkeypatch, topic, cause_present, tail,
):
    engine, session, outcome, _ = _case(topic)
    target = engine.nodes[outcome.session.current_node_id]
    target["story_beat"]["acting_contract"] = {
        "dialogue_policy": "required", "cognition_state": "normal", "memory_state": "available",
        "self_reference_mode": "persona_allowed", "persona_scope": "full",
        "allowed_behaviors": [topic[3], "表达自己的新反应。"], "forbidden_behaviors": [],
    }
    before = deepcopy(session.to_dict())
    writer = actor.NumericV2Actor(object())
    calls = []
    cast = actor.NumericV2CastProjection.from_story(engine.story,
        catgirl_name=session.catgirl_binding["catgirl_name"], player_name=session.catgirl_binding["player_address"])

    async def invoke(messages, **kwargs):
        calls.append(kwargs)
        assert "entry_part" not in kwargs
        system = messages[0].content
        data = prompt_payload(messages[1].content)
        assert topic[2] not in system
        assert data["transition"]["program_entry_delivery"]["fixed_narrations"][0]["text"] == topic[2]
        assert "须用明确主体在target_entry_trigger交付一次" in system
        assert "仅写等待、准备或即将触发" in system
        assert "目标表演从程序原文结束状态接续，不把作者开场计划重新当作待办" in system
        assert "required仍须交付原文未承担的必要对白" in system
        assert data["acting_context"]["target_acting_contract"]["allowed_behaviors"] == cast.value(
            [topic[3], "表达自己的新反应。"])
        assert "entry_prefix" not in data["transition"]
        # Missing causes remain an uncommitted draft for whole-body audit, never a new post-original task.
        result = {"bridge_scene_narration": "门外的灯亮着。",
                  "target_scene_narration": topic[3] if cause_present else "",
                  "target_performance": "（翻开空白记录页）" + topic[4],
                  "suggested_inputs": ["我们接着讨论。"]}
        if not tail:
            result["source_performance"] = ""
        return result

    monkeypatch.setattr(writer, "_invoke", invoke)
    result = await writer.generate_turn(engine=engine, session=session, outcome=outcome,
        player_input=f"好，我们去{topic[0]}。", character_profile="沉着。",
        **({"transition_source": {"performance": "那就过去吧。"}} if tail else {}))
    texts = [row["text"] for row in performance_content_blocks(result)]
    assert texts[-3:] == [topic[2], "翻开空白记录页", topic[4]]
    assert texts.count(topic[3]) == int(cause_present)
    assert "originals_before_response" not in json.dumps(result, ensure_ascii=False)
    assert len(calls) == 1
    assert session.to_dict() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("topic", CASES)
async def test_quote_form_replay_rejects_the_whole_draft_without_rewriting_text(monkeypatch, topic):
    original = "记录开始：'" + topic[2] + "'。记录至此结束。"
    quoted_topic = (*topic[:2], original, *topic[3:])
    engine, session, outcome, _ = _case(quoted_topic)
    before = deepcopy(session.to_dict())
    writer = actor.NumericV2Actor(object())
    calls = []
    response = {"target_performance": "（看向你）" + original.replace("'", '"'),
                "suggested_inputs": ["我们接着讨论。"]}
    response_before = deepcopy(response)

    async def invoke(messages, **kwargs):
        calls.append(kwargs)
        assert "entry_part" not in kwargs
        return {"source_performance": "", "bridge_scene_narration": "门外的灯亮着。",
                "target_scene_narration": topic[3], **response}

    monkeypatch.setattr(writer, "_invoke", invoke)
    with pytest.raises(NumericV2ActorOutputError, match="repeated_output") as caught:
        await writer.generate_turn(engine=engine, session=session, outcome=outcome,
            player_input=f"好，我们去{topic[0]}。", character_profile="沉着。")
    assert caught.value.repetition_guard == "fixed_narration"
    assert len(calls) == 1
    assert session.to_dict() == before
    assert response == response_before
