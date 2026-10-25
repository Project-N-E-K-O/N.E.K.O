"""Actor retains committed state but leaves new narrative candidates to a separate output contract."""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

from jsonschema import Draft202012Validator
import pytest

from services.theater import numeric_v2_actor as actor
from services.theater.numeric_v2_actor_output import NumericV2ActorOutputError, _parse_output
from services.theater.numeric_v2_narrative import (
    apply_narrative_record, make_narrative_record, narrative_body_fingerprint,
    narrative_event_table, parse_narrative_events,
)
from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from services.theater.numeric_v2_structured_output import actor_output_schema, narrative_output_schema, review_output_schema
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_natural_ending import _engine as ending_engine
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from utils.llm_client import HumanMessage


def _event(*, phase="turn", quote="我已经完成检查。"):
    return {
        "phase": phase, "subject": "character", "kind": "action",
        "changes": [{"entity": "activity:检查", "attribute": "stage_result", "value": "已完成"}],
        "source": "performance", "quote": quote, "known_to": ["player", "character"],
    }


def test_dashscope_array_dialect_keeps_uniqueness_in_local_validation():
    schema = narrative_output_schema()["properties"]["narrative_events"]["items"]["properties"]["known_to"]
    assert "uniqueItems" not in schema
    duplicate = {**_event(), "known_to": ["player", "player"]}
    assert parse_narrative_events([duplicate]) is None


def _payload(phase, *, quote="我已经完成检查。"):
    if phase == "opening":
        return {"scene_narration": "窗外的雨已经停了。", "performance": quote,
                "suggested_inputs": [], "transition_offered": False}
    if phase == "target_opening":
        return {"source_scene_narration": "", "source_performance": "（收好工具）可以走了。",
                "bridge_scene_narration": "两人来到走廊。", "target_scene_narration": "走廊亮着灯。",
                "target_performance": quote, "suggested_inputs": []}
    return {"performance": quote, "suggested_inputs": [], "transition_offered": False}


def _flags(phase):
    return {"opening_required": phase == "opening", "transition_required": phase == "target_opening"}


@pytest.mark.parametrize("phase", ["opening", "turn", "target_opening"])
@pytest.mark.parametrize("quote", ["我已经收好清洁工具。", "我已经收好检修工具。"])
def test_actor_ignores_legacy_candidates_without_treating_them_as_completion_facts(phase, quote):
    payload = {**_payload(phase, quote=quote), "narrative_events": [_event(phase=phase, quote=quote)]}

    parsed = _parse_output(json.dumps(payload, ensure_ascii=False), **_flags(phase))

    assert "narrative_events" not in parsed
    if phase == "turn":
        assert parsed["fact_candidates"] == []
    else:
        assert "fact_candidates" not in parsed
    assert parsed.get("performance", parsed.get("target_performance")) == quote
    validator = Draft202012Validator(actor_output_schema(**_flags(phase)))
    validator.validate(_payload(phase, quote=quote))
    assert not validator.is_valid(payload)


@pytest.mark.parametrize("phase", ["opening", "turn", "target_opening"])
@pytest.mark.parametrize("bad", [None, {}, "finished", [_event()] * 4,
    [{**_event(), "quote": "文" * 161}], [{**_event(), "known_to": []}],
    [{**_event(), "changes": []}], [{**_event(), "kind": "intent"}],
    [{**_event(), "unrecognized": True}], [{**_event(), "summary": "文" * 81}],
])
def test_bad_auxiliary_event_field_does_not_discard_valid_body(phase, bad):
    payload = _payload(phase)
    baseline = _parse_output(json.dumps(payload, ensure_ascii=False), **_flags(phase))
    parsed = _parse_output(json.dumps({**payload, "narrative_events": bad}, ensure_ascii=False), **_flags(phase))
    assert parsed == baseline
    assert "narrative_events" not in parsed


@pytest.mark.parametrize("phase", ["opening", "turn", "target_opening"])
def test_empty_legacy_event_array_is_ignored_like_a_missing_field(phase):
    payload = _payload(phase)
    baseline = _parse_output(json.dumps(payload), **_flags(phase))
    assert "narrative_events" not in baseline
    parsed = _parse_output(json.dumps({**payload, "narrative_events": []}), **_flags(phase))
    assert parsed == baseline
    validator = Draft202012Validator(actor_output_schema(**_flags(phase)))
    assert validator.is_valid(payload)
    assert not validator.is_valid({**payload, "narrative_events": []})


@pytest.mark.parametrize("body_phase,allowed", [
    ("opening", ["opening"]),
    ("turn", ["player_input", "turn"]),
    ("target_opening", ["player_input", "source_response", "transition_bridge", "target_opening"]),
])
@pytest.mark.parametrize("event_phase", ["opening", "player_input", "turn", "source_response", "transition_bridge", "target_opening"])
def test_narrative_schema_only_accepts_events_from_this_calls_phases(body_phase, allowed, event_phase):
    schema = narrative_output_schema(opening=body_phase == "opening", transition=body_phase == "target_opening")
    assert set(schema["properties"]) == set(schema["required"]) == {"narrative_events"}
    assert schema["properties"]["narrative_events"]["items"]["properties"]["phase"]["enum"] == allowed
    payload = {"narrative_events": [_event(phase=event_phase)]}
    assert Draft202012Validator(schema).is_valid(payload) is (event_phase in allowed)
    assert not Draft202012Validator(schema).is_valid({**payload, "performance": "已经完成检查。"})


@pytest.mark.parametrize("phase", ["opening", "turn", "transition_compact"])
def test_actor_output_instructions_leave_event_generation_out_but_keep_state_reading(phase):
    instruction = actor._output_schema_instruction(phase)
    assert "narrative_events" not in instruction
    assert "authorization_quote" not in instruction
    system = actor._system_prompt(catgirl_name="小岚", player_address="你", phase=phase)
    assert "narrative_events" not in system
    assert "narrative_state是已提交动态账本" in system
    assert "last_known只是最后已知状态" in system
    assert "未发送的实体不等于不存在" in system
    assert "stage_result不替代作者幕完成合同" in system
    if phase == "transition_compact":
        assert "后段承接前段已完成结果，不重做或重分配归属" in system


@pytest.mark.parametrize("flags,payload", [
    ({"suggestions_only": True}, {"suggested_inputs": []}),
    ({"transition_suggestions_only": True}, {"accept_input": "（我点头）我们走吧。",
                                           "alternative_inputs": ["（我摇头）我还想等一下。"]}),
])
def test_suggestion_only_calls_cannot_add_or_overwrite_narrative_events(flags, payload):
    assert "narrative_events" not in actor_output_schema(**flags)["properties"]
    with pytest.raises(NumericV2ActorOutputError, match="suggestions_invalid"):
        _parse_output(json.dumps({**payload, "narrative_events": []}), **flags)


def test_narrative_review_schema_preserves_both_existing_fact_approval_contracts():
    disabled = review_output_schema()
    assert "approved_narrative_event_indexes" not in disabled["properties"]
    enabled = review_output_schema(narrative_events=True, actor_facts=True, evaluator_facts=True)
    for field in ("approved_narrative_event_indexes", "narrative_review_complete",
                  "approved_actor_fact_indexes", "approved_evaluator_fact_indexes"):
        assert field in enabled["properties"] and field in enabled["required"]


@pytest.mark.parametrize("bad", [False, True])
@pytest.mark.asyncio
async def test_legacy_narrative_field_is_ignored_in_the_same_single_actor_request(monkeypatch, bad):
    calls = []
    payload = {**_payload("turn"), "narrative_events": {} if bad else [_event()]}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def ainvoke(self, messages, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://example.test/v1"}

    async def factory(*args, **kwargs):
        assert kwargs["max_retries"] == 0
        assert kwargs["max_completion_tokens"] == 1000
        return Client()

    monkeypatch.setattr(actor, "_model_config", config)
    monkeypatch.setattr(actor, "create_chat_llm_async", factory)
    instance = actor.NumericV2Actor(object())
    parsed = await instance._invoke([HumanMessage(content="接着说。")])
    assert len(calls) == instance.provider_call_count == 1
    assert "narrative_events" not in parsed


@pytest.mark.parametrize("formal", [False, True])
def test_committed_projection_stays_in_fixed_context_and_keeps_original_history(monkeypatch, formal):
    engine = ending_engine() if formal else NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="narrative_prompt", catgirl_binding=_binding(), opening_performance=_opening())
    request = TurnRequestV2("one", 0, "继续之前的检查。")
    outcome = engine.resolve_turn(session, request, (), scene_complete=formal, natural_ending_ready=formal)
    state = {"current": {"item:工具": {"holder": {"value": "character"}}},
             "last_known": {"item:工具": {"location": {"value": "门口"}}},
             "recent_events": []}
    monkeypatch.setattr(actor, "project_narrative_state", lambda _, **kwargs: deepcopy(state))

    messages = actor._turn_messages(engine, session, outcome, request.message, "克制。", "小岚", "你")
    data = json.loads(messages[1].content.split("\n", 1)[1])
    assert data["player_input"] == request.message
    if formal:
        assert data["story_context"]["narrative_state"] == state
        assert "recent_context" in data
    else:
        assert list(data) == ["role", "current_scene", "story_so_far", "pacing", "next_scene", "player_input"]
        assert "narrative_state（已提交动态叙事账本）" in data["current_scene"]
        assert json.dumps(state, ensure_ascii=False, separators=(",", ":")) in data["current_scene"]
        assert data["story_so_far"]
    assert "last_known只是最后已知状态" in messages[0].content
    assert "stage_result不替代作者幕完成合同" in messages[0].content
    assert "narrative_events" not in messages[0].content


def test_narrative_projection_is_not_removed_with_older_history_budget(monkeypatch):
    # History trimming must preserve new state in fixed blocks and the current input.
    monkeypatch.setattr(actor, "count_tokens", len)
    state = "narrative_state:当前由character持有工具"
    data = dict(role="角色", current_scene=state, story_so_far="", pacing="回应",
                next_scene="", player_input="保留本轮原话")
    rows = [{"revision": 1, "player_input": "旧" * 500, "performance": "旧对白"},
            {"revision": 2, "player_input": "最近输入", "performance": "最近对白"}]
    fitted = actor._fit_simple_turn_prompt_data(system_prompt="合同", human_prefix="上下文：\n",
        data=data, history_rows=rows, max_tokens=300)
    assert fitted["current_scene"] == state and fitted["player_input"] == data["player_input"]
    assert "旧对白" not in fitted["story_so_far"] and "最近对白" in fitted["story_so_far"]


@pytest.mark.parametrize("item,task", [("工具匣", "检查"), ("资料袋", "整理")])
@pytest.mark.parametrize("with_narrative,formal", [(True, True), (False, True), (False, False)])
def test_restricted_cognition_formal_prompt_accepts_filtered_confirmed_state_only(item, task, with_narrative, formal):
    story = numeric_v2_story()
    for node in story["nodes"]:
        node["story_beat"]["acting_contract"] = dict(cognition_state="limited", memory_state="partial",
            self_reference_mode="persona_allowed", persona_scope="full", dialogue_policy="required",
            assertable_self_facts=["我只记得眼前的事情"], allowed_behaviors=[], forbidden_behaviors=[])
    engine = NumericV2Engine.from_mapping(story)
    public_quote = f"{item}由你拿着，{task}已经做完。"
    private_quote = "秘密信封仍由你持有。"
    events = [
        {**_event(phase="opening", quote=public_quote), "changes": [
            {"entity": "item:" + item, "attribute": "holder", "value": "player"},
            {"entity": "activity:" + task, "attribute": "stage_result", "value": "已经做完"}]},
        {**_event(phase="opening", quote=private_quote), "source": "scene_narration",
         "known_to": ["player"], "changes": [{"entity": "item:秘密信封", "attribute": "holder", "value": "player"}]},
    ]
    opening = {"scene_narration": private_quote, "performance": public_quote,
               "suggested_inputs": [], "transition_offered": False, "narrative_events": events}
    session = engine.create_session(session_id="restricted_narrative", catgirl_binding=_binding(), opening_performance=opening)
    if with_narrative:
        table = narrative_event_table(opening, "", opening=True)
        review = SimpleNamespace(reviewed_narrative_events=table, approved_narrative_event_indexes=(0, 1),
            narrative_review_complete=True, reviewed_narrative_body=narrative_body_fingerprint(opening))
        record = make_narrative_record(opening, "", review, opening=True)
        state = apply_narrative_record(None, record, revision=0, node_id="start", from_node_id="start", performance=opening, message="")
        session = replace(session, narrative_state=state)
    request = TurnRequestV2("restricted_one", 0, "就按已经说好的安排继续。")
    outcome = engine.resolve_turn(session, request, (), scene_complete=formal, natural_ending_ready=formal)
    assert (outcome.session.current_node_id != session.current_node_id) is formal
    messages = actor._turn_messages(engine, session, outcome, request.message, "克制。", "小岚", "你")
    data = json.loads(messages[1].content.split("\n", 1)[1])
    if with_narrative:
        context = data["story_context"]
        assert "story_context.narrative_state中已过滤为角色可知的投影" in context["knowledge_scope"]
        current = context["narrative_state"]["current"]
        assert current["item:" + item]["holder"]["value"] == "player"
        assert current["activity:" + task]["stage_result"]["value"] == "已经做完"
        assert "秘密信封" not in json.dumps(context["narrative_state"], ensure_ascii=False)
        assert "继承current中已确认的持物和已完成结果" in messages[0].content
        assert "静态作者方向不得重新决定这些状态" in messages[0].content
    elif formal:
        assert data["story_context"]["knowledge_scope"] == actor._RESTRICTED_KNOWLEDGE_SCOPE
        assert "narrative_state" not in data["story_context"]
        assert "继承current中已确认的持物和已完成结果" not in messages[0].content
    else:
        assert "narrative_state（已提交动态叙事账本）" not in data["current_scene"]
        assert "继承current中已确认的持物和已完成结果" not in messages[0].content
