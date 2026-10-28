"""Route-choice fact projection preserves author literals without selecting for players."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as wf
from services.theater.numeric_v2_evaluator import _build_messages, _parse_output
from services.theater.numeric_v2_actor import _turn_messages
from services.theater.numeric_v2_options import default_options
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding


KEY = "scene:start:route_decided"


def _story(value_type="string", values=("官道", "山径")):
    story = numeric_v2_story()
    story["fact_contract"] = {"facts": {KEY: {
        "value_type": value_type, "visibility": "public",
        "description": "玩家明确选定的行进路线。",
    }}}
    for route, value in zip(story["nodes"][0]["route_gates"], values):
        route["conditions"] = {"all": [{"type": "fact_equals", "key": KEY, "value": value}]}
    return story


def _session(engine):
    return engine.create_session(
        session_id="route_fact_projection", catgirl_binding={"catgirl_name": "测试猫娘"},
        opening_performance={"performance": "我们可以走官道，也可以走山径。"},
    )


def _prompt(engine, session, message):
    messages = _build_messages(engine, session, message)
    human = messages[1].content
    return messages[0].content, prompt_payload(human)


def _evaluation(value=None, message="走山径。"):
    payload = {"scene_complete": False, "transition_intent": "unclear", "metric_changes": {},
               "fact_candidates": []}
    if value is not None:
        payload["fact_candidates"] = [{
            "op": "set", "key": KEY, "value": value, "visibility": "public",
            "confidence": "confirmed", "subject": "玩家", "action": "选择", "object": "行进路线",
            "result": "玩家确定了路线", "evidence": [{"source": "player_input", "quote": message}],
        }]
    return json.dumps(payload, ensure_ascii=False)


@pytest.mark.parametrize("value_type,values", [
    ("string", ("official_road", "mountain_route")), ("bool", (True, False)), ("int", (1, 2)),
])
def test_route_fact_projection_preserves_declared_literal_values_and_types(value_type, values):
    story = _story(value_type, values)
    engine = NumericV2Engine.from_mapping(story)
    before = deepcopy(engine.story)
    system, data = _prompt(engine, _session(engine), "山径好走吗？")

    projected = data["current_story_beat"]["route_fact_values"][KEY]
    assert projected == list(values)
    assert [type(value) for value in projected] == [type(value) for value in values]
    assert engine.story == before
    assert data["player_input"] == "山径好走吗？"
    assert "明确选择或改变选择" in system
    assert "询问、考虑不确认" in system
    assert "不从性格分数推断" in system


def test_route_fact_projection_deduplicates_only_current_writable_route_keys():
    story = _story(values=("山径", "山径"))
    facts = story["fact_contract"]["facts"]
    facts["scene:ending_leave:destination"] = {
        "value_type": "string", "visibility": "public", "description": "下一幕才成立的去向。",
    }
    facts["scene:start:unrelated"] = {
        "value_type": "bool", "visibility": "public", "description": "本幕与路线无关的事实。",
    }
    facts["story:weather"] = {
        "value_type": "string", "visibility": "public", "description": "玩家确认的天气。",
    }
    routes = story["nodes"][0]["route_gates"]
    routes[0]["conditions"]["all"].extend([
        {"type": "metric_compare", "metric": "trust", "op": ">=", "value": 70},
        {"type": "fact_equals", "key": "scene:ending_leave:destination", "value": "湖边"},
    ])
    routes[1]["conditions"] = {"any": [
        *routes[1]["conditions"]["all"],
        {"type": "fact_equals", "key": "story:weather", "value": "雪"},
    ]}
    engine = NumericV2Engine.from_mapping(story)
    _, data = _prompt(engine, _session(engine), "走山径。")

    assert data["current_story_beat"]["route_fact_values"] == {
        KEY: ["山径"], "story:weather": ["雪"],
    }


def test_noncompletion_route_choice_fact_can_enter_story_state():
    engine = NumericV2Engine.from_mapping(_story())
    session = _session(engine)
    assert "completion_contract" not in engine.nodes[session.current_node_id]
    result = _parse_output(_evaluation("山径"), engine, "走山径。", session)
    outcome = engine.resolve_turn(
        session, TurnRequestV2("choose_route", 0, "走山径。"), (), fact_operations=result.fact_operations,
    )

    assert outcome.session.story_state["facts"][KEY]["value"] == "山径"
    assert outcome.session.current_node_id == "start"
    assert engine.preview_route("start", outcome.session.metrics,
                                facts=outcome.session.story_state["facts"])["id"] == "to_leave"


def test_unknown_route_literal_is_not_silently_normalized_or_matched():
    engine = NumericV2Engine.from_mapping(_story())
    session = _session(engine)
    result = _parse_output(_evaluation("mountain_route"), engine, "走山径。", session)
    outcome = engine.resolve_turn(
        session, TurnRequestV2("unmapped_route", 0, "走山径。"), (), fact_operations=result.fact_operations,
    )

    assert result.fact_operations[0]["value"] == "mountain_route"
    assert engine.preview_route("start", outcome.session.metrics,
                                facts=outcome.session.story_state["facts"]) is None


@pytest.mark.parametrize("message", ["山径好走吗？", "我考虑走山径。", "都可以，听你的。"])
def test_ambiguous_input_does_not_force_a_choice_in_projection_or_parser(message):
    engine = NumericV2Engine.from_mapping(_story())
    session = _session(engine)
    _, data = _prompt(engine, session, message)
    result = _parse_output(_evaluation(), engine, message, session)

    assert data["player_input"] == message
    assert result.fact_operations == ()
    assert KEY not in session.story_state["facts"]
    assert engine.preview_route("start", session.metrics, facts=session.story_state["facts"]) is None


def test_metric_only_routes_do_not_add_fact_choice_context():
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    system, data = _prompt(engine, _session(engine), "谢谢。")

    assert "route_fact_values" not in data["current_story_beat"]
    assert "route_fact_values" not in system


@pytest.mark.parametrize("description,values", [
    ("玩家选定清点后的借书交接方式。", ("交还图书角", "交给值班老师")),
    ("玩家选定检查后的设备交接方式。", ("送回机库", "移交检修队")),
])
def test_actor_sees_alternative_route_targets_without_turning_them_into_completion(description, values):
    story = _story(values=values)
    done_key = "scene:start:counted"
    story["fact_contract"]["facts"][KEY]["description"] = description
    story["fact_contract"]["facts"][done_key] = {
        "value_type": "bool", "visibility": "public", "description": "眼前物品已经清点完毕。",
    }
    story["nodes"][0]["completion_contract"] = {"all": [{"key": done_key, "equals": True}]}
    engine = NumericV2Engine.from_mapping(story)
    session = _session(engine)
    before = deepcopy(session)
    message = "这两种交接方式有什么区别？"
    outcome = engine.resolve_turn(session, TurnRequestV2("ask_options", 0, message), ())
    messages = _turn_messages(engine, session, outcome, message, "温和。", "小葵", "林舟")
    data = prompt_payload(messages[1].content)
    scene = data["current_scene"]
    decoder = json.JSONDecoder()
    completion, _ = decoder.raw_decode(scene[scene.index("{", scene.index("结构化幕完成事实")):])
    groups, _ = decoder.raw_decode(scene[scene.index("[", scene.index("当前幕路线事实条件")):])

    assert completion["status"] == "pending"
    assert [row["key"] for row in completion["all"]] == [done_key]
    assert [(group["route_id"], group["facts"][0]["value"]) for group in groups] == [
        (route["id"], value) for route, value in zip(engine.nodes["start"]["route_gates"], values)
    ]
    assert all(group["condition_mode"] == "all" for group in groups)
    assert groups[0]["facts"][0]["description"] == description
    assert all("description" not in row for group in groups[1:] for row in group["facts"])
    assert all(row["key"] == KEY
               and row["committed"] is False and row["satisfied"] is False
               for group in groups for row in group["facts"])
    assert "fact_candidates" in messages[0].content
    assert KEY not in outcome.session.story_state["facts"]
    assert outcome.route is None
    assert session == before


def test_actor_route_targets_do_not_disclose_hidden_global_or_other_scene_facts():
    story = _story()
    hidden = "scene:start:hidden_result"
    global_key = "prop:inspection"
    later = "scene:ending_leave:approval"
    for key, visibility in ((hidden, "story"), (global_key, "public"), (later, "public")):
        story["fact_contract"]["facts"][key] = {
            "value_type": "bool", "visibility": visibility, "description": "不应由当前路线投影公开的结果。",
        }
        story["nodes"][0]["route_gates"][0]["conditions"]["all"].append(
            {"type": "fact_equals", "key": key, "value": True})
    engine = NumericV2Engine.from_mapping(story)
    session = _session(engine)
    outcome = engine.resolve_turn(session, TurnRequestV2("ask", 0, "现在还缺什么？"), ())
    messages = _turn_messages(engine, session, outcome, "现在还缺什么？", "温和。", "小葵", "林舟")
    data = prompt_payload(messages[1].content)
    scene = data["current_scene"]
    groups, _ = json.JSONDecoder().raw_decode(scene[scene.index("[", scene.index("当前幕路线事实条件")):])

    assert {row["key"] for group in groups for row in group["facts"]} == {KEY}
    assert all(key not in json.dumps(groups, ensure_ascii=False) for key in (hidden, global_key, later))


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", [0, 1])
async def test_both_values_of_one_route_key_receive_only_native_index_approval(monkeypatch, selected):
    values = ("交还图书角", "交给值班老师")
    engine = NumericV2Engine.from_mapping(_story(values=values))
    session = _session(engine)
    body = f"值班老师清点完图书，确认采用{values[selected]}的交接方式。"
    candidate = {"key": KEY, "value": values[selected], "evidence_quote": body}
    requests, settings = [], []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    async def config(_):
        return {"model": "offline", "base_url": "https://invalid.local"}

    async def factory(*args, **kwargs):
        settings.append(kwargs)
        return Client()

    async def invoke(client, messages, **kwargs):
        data = prompt_payload(messages[1].content)
        requests.append(data)
        assert data["actor_fact_candidates"] == [{
            "index": 0, **candidate, "description": "玩家明确选定的行进路线。",
        }]
        return SimpleNamespace(content=json.dumps({
            "offer_present": False, "offer_quote": "", "offer_kind": "", "valid": False,
            "body_violations": [], "unsafe_suggestion_indexes": [], "failure_reason": "",
            "player_action_kind": "", "fact_candidates": [], "approved_actor_fact_indexes": [0],
        }, ensure_ascii=False), response_metadata={"finish_reason": "stop"})

    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    monkeypatch.setattr(ev, "invoke_with_usage", invoke)
    result = await ev.NumericV2MetricEvaluator(object()).validate_transition_offer(
        engine=engine, session=session, message=f"我选{values[selected]}。",
        actor_performance={"performance": body, "fact_candidates": [candidate], "suggested_inputs": []},
        completion_fact_review_only=True,
    )

    assert result.fact_candidates == (candidate,)
    assert len(requests) == len(settings) == 1
    assert settings[0]["timeout"] == 8 and settings[0]["max_retries"] == 0
    assert settings[0]["max_completion_tokens"] == 350
    assert KEY not in session.story_state["facts"]


def _handoff_choice_engine():
    """Require both a player decision and independent receipt before a handoff can complete."""
    story = _story(values=("交还图书角", "交给值班老师"))
    story["fact_contract"]["facts"][KEY]["description"] = "玩家明确确定本次借书交接方式。"
    receipt_key = "scene:start:receipt"
    story["fact_contract"]["facts"][receipt_key] = {
        "value_type": "bool", "visibility": "public", "description": "值班老师已经清点借书并实际签收。",
    }
    for route in story["nodes"][0]["route_gates"]:
        route["conditions"]["all"].append({"type": "fact_equals", "key": receipt_key, "value": True})
    return NumericV2Engine.from_mapping(story), receipt_key


def test_source_only_fact_review_distinguishes_current_decision_from_npc_receipt():
    engine, receipt_key = _handoff_choice_engine()
    session = _session(engine)
    message = "我选交给值班老师；请先问他是否能签收，没答复前不能算完成。"
    claims = ({
        "op": "set", "key": KEY, "value": "交给值班老师", "visibility": "public",
        "subject": "玩家", "action": "确定交接方式", "object": "借书", "result": "选定交给值班老师",
        "description": engine.fact_contract[KEY]["description"],
        "evidence": [{"source": "player_input", "quote": message}],
    },)
    messages, _ = ev._build_transition_judge_messages(
        engine, session, actor_performance={}, player_input=message,
        evaluator_fact_claims=claims, completion_fact_review_only=True,
    )
    system = messages[0].content
    data = prompt_payload(messages[1].content)

    assert data["actor_performance"] == data["scene_update"] == ""
    assert "actor_fact_candidates" not in data
    assert data["evaluator_fact_claims"] == [{**claims[0], "index": 0}]
    assert any(row["key"] == receipt_key for row in data["pending_completion_facts"])
    # 前置审只拿到玩家原话，合同须独立定义其任务；不能把不存在的Actor稿当成审批前提。
    assert "逐项核对actor_fact_candidates" not in system
    assert "evaluator_fact_claims" in system
    assert "没有待审正文" in system
    assert "approved_actor_fact_indexes" not in system
    assert "选择或承诺" in system
    assert "不证明NPC" in system
    assert "询问" in system and "考虑" in system and "有条件" in system
    assert KEY not in session.story_state["facts"]
    assert receipt_key not in session.story_state["facts"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [
    "source_only", "empty_actor_shape", "actor_draft", "ordinary_review",
    "formal_transition", "offer_acceptance", "missed_initiation",
])
async def test_only_bodyless_source_approval_closes_new_fact_schema_channel(monkeypatch, mode):
    engine, receipt_key = _handoff_choice_engine()
    session = _session(engine)
    message = "我选交给值班老师，等他真实答复。"
    claim = {
        "op": "set", "key": KEY, "value": "交给值班老师", "visibility": "public",
        "description": engine.fact_contract[KEY]["description"],
        "evidence": [{"source": "player_input", "quote": message}],
    }
    options = {"actor_performance": {}, "completion_fact_review_only": True}
    if mode == "empty_actor_shape":
        options["actor_performance"] = {"performance": "", "suggested_inputs": [], "transition_offered": False}
    elif mode == "actor_draft":
        options["actor_performance"] = {"performance": "我先问问老师能不能收。"}
    elif mode == "ordinary_review":
        options["completion_fact_review_only"] = False
    elif mode == "formal_transition":
        options["transition_outcome"] = engine.resolve_turn(
            session, TurnRequestV2("schema_formal", 0, "现在去交接。"), (),
            fact_operations=(
                {"op": "set", "key": KEY, "value": "交给值班老师", "visibility": "public"},
                {"op": "set", "key": receipt_key, "value": True, "visibility": "public"},
            ), transition_intent="initiate",
        )
        assert options["transition_outcome"].route is not None
    elif mode == "offer_acceptance":
        options["require_offer_acceptance"] = True
    elif mode == "missed_initiation":
        options["check_missed_initiation"] = True
    base_schemas, requests, settings = [], [], []
    native_schema = ev.review_output_schema

    def schema(**kwargs):
        original = native_schema(**kwargs)
        base_schemas.append(deepcopy(original))
        return original

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    async def config(_):
        # 连接与调用全部mock；兼容配置仅使原生response_format真实携带Schema。
        return {"model": "qwen3.8-flash", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"}

    async def factory(*args, **kwargs):
        settings.append(kwargs)
        return Client()

    async def invoke(client, messages, **kwargs):
        actual_schema = kwargs["response_format"]["json_schema"]["schema"]
        data = prompt_payload(messages[1].content)
        requests.append((deepcopy(actual_schema), data))
        # 这里只检查实际发送的合同，不让mock自行批准事实或证明模型遵守合同。
        empty_values = {"array": [], "boolean": False, "string": "", "integer": -1}
        payload = {key: deepcopy(empty_values[prop["type"]])
                   for key, prop in actual_schema["properties"].items()}
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False),
                               response_metadata={"finish_reason": "stop"})

    monkeypatch.setattr(ev, "review_output_schema", schema)
    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    monkeypatch.setattr(ev, "invoke_with_usage", invoke)
    result = await ev.NumericV2MetricEvaluator(object()).validate_transition_offer(
        engine=engine, session=session, message=message, evaluator_fact_claims=(claim,), **options,
    )

    assert len(base_schemas) == 1  # Only the scene half owns the fact schema.
    assert len(requests) == len(settings) == (2 if mode == "formal_transition" else 1)
    actual, data = requests[0]
    expected = base_schemas[0]
    if mode == "source_only":
        expected["properties"]["fact_candidates"]["maxItems"] = 0
        assert settings[0]["max_completion_tokens"] == 350
        assert settings[0]["timeout"] == 8 and settings[0]["max_retries"] == 0
    elif mode == "formal_transition":
        expected["properties"]["body_violations"]["items"]["enum"] = ["scene_boundary", "author_boundary"]
    elif mode == "offer_acceptance":
        # 原有邀约接受扩展保留；本轮只能关闭无正文的前置审批通道。
        expected["properties"]["offer_acceptance_valid"] = {"type": "boolean"}
        expected["required"].append("offer_acceptance_valid")
    assert actual == expected
    assert data["evaluator_fact_claims"] == [{**claim, "index": 0}]
    assert data["player_input"] == message
    assert result.approved_evaluator_fact_indexes == ()
    assert KEY not in session.story_state["facts"]


@pytest.mark.asyncio
@pytest.mark.parametrize("approve_decision", [False, True])
async def test_current_decision_still_requires_original_review_index_without_completing_receipt(
    tmp_path, monkeypatch, approve_decision,
):
    engine, receipt_key = _handoff_choice_engine()
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id="current_decision_review", catgirl_binding=_binding(),
        opening_performance={"performance": "借书可以交还图书角，也可以交给值班老师。", "suggested_inputs": []},
    )
    before = runtime.store._path(current.session.session_id).read_bytes()
    message = "我选交给值班老师；请先问他是否能签收，没答复前不能算完成。"
    calls = {"evaluate": 0, "review": 0, "actor": 0}

    async def options():
        return {**{key: key == 'evaluator' for key in default_options()}, "review": False, "actor_retry": False, "suggestion_fill": False}

    async def evaluate(self, **kwargs):
        calls["evaluate"] += 1
        # 同一原话故意被提议为两个不同结果；独立审批只能放行获得的原始索引。
        payload = json.loads(_evaluation("交给值班老师", message))
        payload["fact_candidates"].append({
            **payload["fact_candidates"][0], "key": receipt_key, "value": True,
            "subject": "值班老师", "action": "签收", "result": "借书已清点签收",
        })
        return _parse_output(json.dumps(payload, ensure_ascii=False), engine, kwargs["message"], kwargs["session"])

    async def review(self, **kwargs):
        calls["review"] += 1
        assert kwargs["completion_fact_review_only"] is True
        assert kwargs["actor_performance"] == {}
        assert [claim["key"] for claim in kwargs["evaluator_fact_claims"]] == [KEY, receipt_key]
        assert runtime.store._path(current.session.session_id).read_bytes() == before
        assert KEY not in kwargs["session"].story_state["facts"]
        return ev.NumericV2TransitionOfferReview(
            False, False, (), (), approved_evaluator_fact_indexes=(0,) if approve_decision else (),
        )

    async def generate(self, **kwargs):
        calls["actor"] += 1
        prepared = kwargs["outcome"].session.story_state["facts"]
        assert (KEY in prepared) is approve_decision
        assert receipt_key not in prepared
        assert kwargs["outcome"].route is None
        assert runtime.store._path(current.session.session_id).read_bytes() == before
        return {"performance": "我先问问老师能否接收，等他答复。", "suggested_inputs": [], "transition_offered": False}

    monkeypatch.setattr(wf, "aload_theater_module_options", options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "温和。")
    result = await wf.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("current_decision", 0, message), ensure_current_binding=lambda _: _binding(),
    )

    facts = result.stored.session.story_state["facts"]
    assert calls == {"evaluate": 1, "review": 1, "actor": 1}
    assert (KEY in facts) is approve_decision
    if approve_decision:
        assert facts[KEY]["value"] == "交给值班老师"
    assert receipt_key not in facts
    assert result.stored.session.current_node_id == "start"
    assert not result.stored.session.transition_offered
    assert result.diagnostics["completion_fact_review_calls"] == 1
    assert result.diagnostics["evaluator_fact_claims_approved"] == int(approve_decision)
    assert await runtime.restore_session(current.session.session_id) == result.stored
    fork = await runtime.fork_session_for_test(
        current.session.session_id, session_id="decision-review-fork", through_revision=1,
    )
    assert fork.session.story_state == result.stored.session.story_state
    assert fork.ledger_events[-1].get("fact_operations", []) == result.stored.ledger_events[-1].get("fact_operations", [])
