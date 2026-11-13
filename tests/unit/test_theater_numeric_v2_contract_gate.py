"""Retired metadata cannot affect play; final public contracts gate atomic commits."""

from copy import deepcopy
import json
from types import SimpleNamespace

from tests.utils.theater_prompt import contract_audit_payload

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater import numeric_v2_workflow as workflow
from services.theater.numeric_v2_context import _visible_narration_text
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from services.theater.numeric_v2_store import NumericV2StoreError
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from utils.tokenize import count_tokens


@pytest.mark.asyncio
async def test_legacy_auxiliary_metadata_is_ignored_without_rewriting_on_read(tmp_path):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(numeric_v2_story()), tmp_path)
    current = await runtime.start_session(session_id="legacy_metadata", catgirl_binding=_binding(), opening_performance=_opening())
    outcome = runtime.prepare_turn(current, TurnRequestV2("one", 0, "先等一下。"), (), scene_complete=False)
    current = await runtime.commit_turn(outcome, {"performance": "（点头）我在这里等。", "suggested_inputs": []})
    path = runtime.store._path(current.session.session_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["session"]["narrative_state"] = {"malformed_retired_data": True}
    payload["session"]["opening_performance"]["narrative_record"] = "retired"
    payload["session"]["performance_history"][0]["narrative_record"] = "retired"
    payload["session"]["performance_history"][0]["narrative_events"] = ["retired"]
    payload["ledger_events"][0].update(narrative_record="retired", narrative_pending={"old_job": True})
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()
    loaded = await runtime.restore_session(current.session.session_id)
    assert loaded == current
    assert path.read_bytes() == before
    fork = await runtime.fork_session_for_test(current.session.session_id, session_id="legacy_fork", through_revision=1)
    assert fork.session.story_state == current.session.story_state
    assert fork.session.metrics == current.session.metrics
    outcome = runtime.prepare_turn(loaded, TurnRequestV2("two", 1, "继续等。"), (), scene_complete=False)
    await runtime.commit_turn(outcome, {"performance": "（望向门口）还没有消息。", "suggested_inputs": []})
    text = path.read_text(encoding="utf-8")
    for field in ("narrative_state", "narrative_record", "narrative_events", "narrative_pending"):
        assert field not in text
    corrupted = json.loads(text)
    corrupted["session"]["performance_history"][0] = "corrupted original record"
    path.write_text(json.dumps(corrupted), encoding="utf-8")
    with pytest.raises(NumericV2StoreError, match="numeric_performance_record_invalid"):
        await runtime.restore_session(current.session.session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("body,rule,with_claim", [
    ("（再次递出已经交付的凭证）现在再交一次。", "凭证已经交付，不得重置持有人。", True),
    ("（再次扣除已经支付的费用）交易完成。", "同一笔交易已结清，不得重复支付。", True),
    ("（重新发送已成功发出的信号）发送完成。", "同一任务不得复位为尚未发送。", True),
    ("费用已经扣除了。", "本幕只议价，不得扣款。", False),
])
@pytest.mark.parametrize("repair_succeeds", [False, True, None])
async def test_contract_rewrite_is_bounded_and_rejected_body_never_commits(
    tmp_path, monkeypatch, body, rule, with_claim, repair_succeeds,
):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    engine.nodes["start"]["story_beat"]["must_not_happen"] = [rule]
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="contract_gate", catgirl_binding=_binding(), opening_performance=_opening())
    before = runtime.store._path("contract_gate").read_bytes()
    actors = []; checks = []

    async def options():
        return {"evaluator": True, "review": False, "review_contract": True, "actor_retry": False}

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        actors.append(kwargs)
        return {"performance": "（点头）已经完成，按刚才的结果继续。" if repair_succeeds and len(actors) > 1 else body,
                "suggested_inputs": [], "transition_offered": False,
                **({"fact_candidates": [{"key": "new_result", "value": True, "evidence_quote": body}]}
                   if with_claim and len(actors) == 1 else {})}

    async def check(self, **kwargs):
        checks.append(deepcopy(kwargs["actor_performance"]))
        assert kwargs["session"] == current.session
        assert kwargs["include_all_segments"] is True
        if repair_succeeds is None:
            raise evaluator.NumericV2EvaluatorError("numeric_v2_contract_check_timeout")
        return (rule,) if kwargs["actor_performance"]["performance"] == body else ()

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "克制。")
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "verify_contract_boundaries", check)
    args = dict(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("one", 0, "按刚才的结果继续。"), ensure_current_binding=lambda _: _binding())
    if repair_succeeds:
        result = await workflow.execute_numeric_v2_turn(**args)
        assert result.stored.session.revision == 1
        assert len(result.stored.ledger_events) == 1
        assert body not in json.dumps(result.stored.session.performance_history, ensure_ascii=False)
        assert await runtime.restore_session("contract_gate") == result.stored
    elif repair_succeeds is False:
        with pytest.raises(workflow.NumericV2ActorOutputError, match="contract_violation"):
            await workflow.execute_numeric_v2_turn(**args)
        assert runtime.store._path("contract_gate").read_bytes() == before
    else:
        with pytest.raises(workflow.NumericV2ActorOutputError, match="contract_check_failed"):
            await workflow.execute_numeric_v2_turn(**args)
        assert runtime.store._path("contract_gate").read_bytes() == before
    assert len(actors) == (1 if repair_succeeds is None else 2)
    assert len(checks) == (2 if repair_succeeds else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("confirms_violation", [False, True])
@pytest.mark.parametrize("finding,expected_index", [
    (None, None),
    ({"rule_index": 1, "candidate_ref": "c4", "quote": "再次收取费用", "prior_ref": ""}, 1),
    ({"rule_index": 1, "candidate_ref": "c4", "quote": "再次收取费用", "prior_ref": "a1"}, 1),
    ({"rule_index": 1, "candidate_ref": "c4", "quote": "再次收取费用", "prior_ref": "a0"}, None),
    ({"rule_index": 1, "candidate_ref": "c5", "quote": "请核对", "prior_ref": "a1"}, 1),
    ({"rule_index": 0, "candidate_ref": "c4", "quote": "再次收取费用", "prior_ref": ""}, 0),
    ({"rule_index": 1, "candidate_ref": "h1", "quote": "费用已结清", "prior_ref": ""}, None),
    ({"rule_index": 2, "candidate_ref": "c4", "quote": "再次收取费用", "prior_ref": "h1"}, 2),
    ({"rule_index": 2, "candidate_ref": "c4", "quote": "再次收取费用", "prior_ref": "h999"}, None),
    ({"rule_index": 8, "candidate_ref": "c4", "quote": "再次收取费用", "prior_ref": ""}, 8),
    ({"rule_index": 8, "candidate_ref": "c0", "quote": "收好收据", "prior_ref": ""}, None),
])
async def test_native_contract_request_binds_verdict_to_current_phase_and_exact_sources(monkeypatch, finding, expected_index, confirms_violation):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="native_contract", catgirl_binding=_binding(),
        opening_performance={"performance": "（收起收据）费用已结清。", "suggested_inputs": []})
    source = {"story_beat": {"must_not_happen": ["不得在来源幕提前签收。"], "summary": "留好付款凭证。"}}
    target = {"story_beat": {"must_not_happen": ["不得再次收费。"], "summary": "核对包裹和费用。"}}
    candidate = {"segments": [
        {"phase": "source_response", "performance": "（收好收据）走吧。"},
        {"phase": "transition_bridge", "scene_narration": "两人来到领取处。"},
        {"phase": "target_opening", "performance": "（递来包裹，再次收取费用）请核对。", "scene_narration": "无需再次付款。"},
    ]}
    requests = []

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            requests.append(messages)
            data = json.loads(messages[1].content.split("\n", 1)[1])
            payload = [finding] if finding else []
            if len(requests) > 1:
                assert data["finding"]["quote"] == finding["quote"]
                assert data["finding"]["candidate"]["phase"] == "target_opening"
                if finding["rule_index"] < 2:
                    assert data["finding"]["rule_index"] == finding["rule_index"]
                    assert data["finding"]["rule_origin"] == {
                        "origin_node_id": session.current_node_id if finding["rule_index"] == 0 else "",
                        "origin_phase": "source_response" if finding["rule_index"] == 0 else "target_opening",
                    }
                assert "测试、示范或预演确实做了受限动作时仍按执行核对" in messages[0].content
                # A source-only restriction is now reviewable across the transition,
                # so the focused applicability check, not a blanket phase filter, clears it.
                return SimpleNamespace(content=json.dumps(contract_audit_payload({"applicable": finding["rule_index"] != 0,
                                                           "confirmed": confirms_violation}, messages)))
            return SimpleNamespace(content=json.dumps(contract_audit_payload({"violations": payload}, messages), ensure_ascii=False))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    before = deepcopy(candidate)
    result = await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node=source, target_node=target, session=session, actor_performance=candidate,
        player_input="一起去领取吧。", include_all_segments=True)
    assert candidate == before
    assert len(requests) == 1 + int(expected_index is not None)
    wire = "\n".join(m.content for m in requests[0])
    data = json.loads(requests[0][1].content.split("\n", 1)[1])
    expected = (() if expected_index is None or expected_index == 0 or not confirms_violation
                else (data["author_boundaries"][expected_index]["text"],))
    if finding and finding["rule_index"] < len(data["author_checks_required"]) and expected_index is None:
        # A claimed author violation with invalid evidence leaves that rule unchecked.
        expected = (data["author_boundaries"][finding["rule_index"]]["text"],)
    assert result == expected
    assert list(dict.fromkeys(part["phase"] for part in data["sources"] if part["id"].startswith("c"))) == [
        "source_response", "transition_bridge", "target_opening"]
    assert [row["text"] for row in data["author_boundaries"][:2]] == [
        source["story_beat"]["must_not_happen"][0], target["story_beat"]["must_not_happen"][0]]
    assert data["author_checks_required"] == [
        {"rule_index": 0, "origin_node_id": session.current_node_id, "origin_phase": "source_response",
         "check_phases": ["source_response", "transition_bridge", "target_opening"]},
        {"rule_index": 1, "origin_node_id": "", "origin_phase": "target_opening", "check_phases": ["target_opening"]},
    ]
    assert data["author_boundaries"][8]["text"].startswith("[target_opening] 共用开场沿用已选路线")
    assert "把受限动作称作测试、示范或预演也算执行" in requests[0][0].content
    for text in ("费用已结清", "无需再次付款", "递来包裹", "target_opening", "sources"):
        assert text in wire
    assert sum(count_tokens(m.content) for m in requests[0]) <= 7000
    assert "narrative_state" not in wire


@pytest.mark.asyncio
async def test_contract_over_budget_rejects_before_config_or_transport(monkeypatch):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="oversized_contract", catgirl_binding=_binding(), opening_performance=_opening())

    async def forbidden(*args, **kwargs): raise AssertionError("must reject before transport")
    monkeypatch.setattr(evaluator, "_model_config", forbidden)
    with pytest.raises(evaluator.NumericV2EvaluatorError, match="input_over_budget"):
        await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
            node=engine.nodes["start"], session=session, player_input="当前输入" * 20000,
            actor_performance={"performance": "（点头）我听着。"})


def test_enacted_actions_are_checked_but_dialogue_proposals_are_not_execution():
    candidate = {"performance": "（推开门，走进档案室）要在这里查看吗？"}
    assert "推开门，走进档案室" in _visible_narration_text(candidate)
    assert "要在这里查看吗" not in _visible_narration_text(candidate)


@pytest.mark.parametrize("input_source", ["freeform", "reinvite"])
def test_continuing_and_reinvitation_prompts_have_no_retired_state(input_source):
    from services.theater import numeric_v2_actor as actor
    from services.theater.numeric_v2_runtime import ScriptSessionV2

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="retired_prompt", catgirl_binding=_binding(), opening_performance=_opening())
    payload = session.to_dict()
    payload["narrative_state"] = {"current": {"forbidden_old_value": "must not reach prompts"}}
    session = ScriptSessionV2.from_mapping(payload)
    outcome = engine.resolve_turn(session, TurnRequestV2("one", 0, "继续听。"), (), scene_complete=False)
    messages = actor._turn_messages(engine, session, outcome, "继续听。", "温和。", "角色", "玩家", input_source=input_source)
    messages += evaluator._build_messages(engine, session, "继续听。")
    messages += evaluator._build_transition_judge_messages(engine, session,
        actor_performance={"performance": "（点头）我在听。"}, player_input="继续听。")[0]
    wire = "\n".join(message.content for message in messages)
    for field in ("narrative_state", "narrative_events", "动态账本", "叙事账本", "forbidden_old_value"):
        assert field not in wire
