"""Required contract stages keep bounded waits and the actual transition phase."""

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

from tests.utils.theater_prompt import contract_audit_payload, serialized_prompt_tokens, prompt_payload

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater import numeric_v2_workflow as workflow
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    """Use the native contract adapter instead of the orchestration fixture's stub."""


def _session():
    return NumericV2Engine.from_mapping(numeric_v2_story()).create_session(
        session_id="scheduled-contract", catgirl_binding=_binding(), opening_performance=_opening())


async def _config(_):
    return {"model": "test", "base_url": "https://test.invalid", "api_key": "test"}


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed", [False, True])
async def test_first_review_does_not_consume_the_required_confirmation_wait(monkeypatch, confirmed):
    requests = []

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            requests.append(messages)
            data = prompt_payload(messages[1].content)
            assert "transition_context" not in data and "transition_phases" not in data
            # Both requests fit their individual wait; their sum exceeds one wait.
            await asyncio.sleep(0.12)
            return SimpleNamespace(content=(json.dumps(contract_audit_payload({"violations": [{
                "rule_index": 0, "candidate_ref": "c0", "quote": "启用设备", "prior_ref": ""}]}, messages))
                if len(requests) == 1 else json.dumps(contract_audit_payload({"applicable": True, "confirmed": confirmed}, messages))))

    async def factory(*args, **kwargs):
        assert kwargs["max_retries"] == 0
        assert kwargs["timeout"] == 0.2
        return Client()

    monkeypatch.setattr(evaluator, "_model_config", _config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    monkeypatch.setattr(evaluator, "NUMERIC_V2_CONTRACT_CHECK_TIMEOUT_SECONDS", 0.2)
    result = await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": ["不得启用设备。"]}},
        transition_contract={"reason": "未选中的出口不应送入普通回合。"},
        session=_session(), player_input="只检查外观。", actor_performance={"performance": "（启用设备）"})
    assert len(requests) == 2 and bool(result) is confirmed


@pytest.mark.asyncio
@pytest.mark.parametrize("slow_stage", [1, 2])
async def test_each_contract_request_still_times_out_and_cancels_without_retry(monkeypatch, slow_stage):
    requests = []; cancelled = []

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            stage = len(requests) + 1
            requests.append(messages)
            if stage == slow_stage:
                try:
                    await asyncio.sleep(1)
                except asyncio.CancelledError:
                    cancelled.append(stage)
                    raise
            return SimpleNamespace(content=json.dumps(contract_audit_payload({"violations": [{
                "rule_index": 0, "candidate_ref": "c0", "quote": "启用设备", "prior_ref": ""}]}, messages)))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(evaluator, "_model_config", _config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    monkeypatch.setattr(evaluator, "NUMERIC_V2_CONTRACT_CHECK_TIMEOUT_SECONDS", 0.04)
    with pytest.raises(evaluator.NumericV2EvaluatorError, match="contract_check_timeout"):
        await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
            node={"story_beat": {"must_not_happen": ["不得启用设备。"]}},
            session=_session(), player_input="只检查外观。", actor_performance={"performance": "（启用设备）"})
    assert len(requests) == slow_stage and cancelled == [slow_stage]


@pytest.mark.asyncio
@pytest.mark.parametrize("subject,operation", [("设备", "交接"), ("文件", "签署")])
@pytest.mark.parametrize("scope", ["source_only", "persistent"])
@pytest.mark.parametrize("phase", ["transition_bridge", "target_opening"])
async def test_bridge_review_keeps_rule_origin_and_distinguishes_source_stage_from_persistent_limits(
    monkeypatch, subject, operation, scope, phase,
):
    rule = (f"本幕只讨论，不得{operation}{subject}，执行属于下一幕。" if scope == "source_only"
            else f"全过程不得{operation}{subject}，包括转场和后续场景。")
    node = {"story_beat": {"summary": f"只讨论{subject}的{operation}方案。", "must_not_happen": [rule]}}
    target = {"story_beat": {"summary": f"按已确认方案{operation}{subject}。",
        "opening_scene": f"两人已在操作台旁，{subject}尚未完成{operation}。",
        "must_not_happen": ["不得替玩家确认最终结果。"],
        "acting_contract": {"forbidden_behaviors": ["不得宣称本轮已获检验通过。"]},
    }}
    contract = {"reason": f"方案已确认，进入{subject}{operation}阶段。", "bridge_required": True,
        "bridge_scene_narration": "两人来到操作台。", "must_deliver": ["两人来到操作台。"],
        "must_preserve": ["保持原有分工。"], "tone": "自然"}
    candidate = {"segments": [
        {"phase": "source_response", "performance": "方案已经确认。"},
        {"phase": "transition_bridge", "scene_narration": f"角色{operation}{subject}。" if phase == "transition_bridge" else "两人来到操作台。"},
        {"phase": "target_opening", "performance": f"角色{operation}{subject}。" if phase == "target_opening" else "接着核对结果。"},
    ]}
    original = deepcopy((node, target, candidate, contract)); requests = []

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            data = prompt_payload(messages[1].content); requests.append(data)
            assert data["transition_phases"] == {
                "source_response": "source_scene", "transition_bridge": "authorized_transition",
                "target_opening": "target_scene"}
            assert "持续或跨幕的禁令仍按原文生效" in messages[0].content
            context = data["transition_context"]
            assert context["transition_contract"] == {**contract, "must_deliver": [{"content_ref": "bridge_scene_narration"}]}
            assert context["target_entry"] == {
                "opening_situation": target["story_beat"]["opening_scene"],
                "story_direction": target["story_beat"]["summary"],
                "hard_boundaries": ["当前角色禁止行为：不得宣称本轮已获检验通过。", "禁止发生的剧情：不得替玩家确认最终结果。"],
            }
            assert "不是已播证据" in messages[0].content
            if len(requests) == 1:
                assert any(row["text"].endswith(rule) for row in data["author_boundaries"])
                source_plan = next(row for row in data["sources"] if row["id"] == "a0")
                assert source_plan["phases"] == ["source_response"]
                return SimpleNamespace(content=json.dumps(contract_audit_payload({"violations": [{
                    "rule_index": 0, "candidate_ref": "c1" if phase == "transition_bridge" else "c2",
                    "quote": f"角色{operation}{subject}", "prior_ref": ""}]}, messages)))
            assert data["finding"]["candidate"]["phase"] == phase
            assert rule in data["finding"]["rule"]
            assert context == requests[0]["transition_context"]
            return SimpleNamespace(content=json.dumps(contract_audit_payload({"applicable": scope == "persistent", "confirmed": True}, messages)))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(evaluator, "_model_config", _config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    result = await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node=node, target_node=target, session=_session(), player_input="按刚才同意的方案继续。",
        transition_contract=contract,
        actor_performance=candidate, include_all_segments=True)
    assert bool(result) is (scope == "persistent") and len(requests) == 2
    assert (node, target, candidate, contract) == original


@pytest.mark.asyncio
async def test_required_review_remains_inside_whole_turn_deadline_and_keeps_original_store(tmp_path, monkeypatch):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(numeric_v2_story()), tmp_path)
    current = await runtime.start_session(session_id="required-review-deadline", catgirl_binding=_binding(),
                                          opening_performance=_opening())
    before = runtime.store._path(current.session.session_id).read_bytes()
    checks = []; cancelled = []

    async def options(): return {"evaluator": True, "review": False, "review_contract": True, "actor_retry": False}
    async def evaluate(self, **kwargs): return evaluator.NumericV2EvaluationResult((), False)
    async def generate(self, **kwargs): return {"scene_narration": "桌上的指示灯亮起。", "performance": "先看状态。", "suggested_inputs": []}
    async def check(self, **kwargs):
        checks.append(kwargs)
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow, "NUMERIC_V2_TURN_MODEL_BUDGET_SECONDS", 0.65)
    monkeypatch.setattr(workflow, "NUMERIC_V2_TURN_REVIEW_RESERVE_SECONDS", 0.05)
    monkeypatch.setattr(workflow, "NUMERIC_V2_CONTRACT_CHECK_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "克制。")
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "verify_contract_boundaries", check)
    with pytest.raises(workflow.NumericV2ActorOutputError, match="contract_check_failed"):
        await workflow._execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2("one", 0, "请检查状态。"), ensure_current_binding=lambda _: _binding())
    assert len(checks) == 1 and cancelled == [True]
    assert runtime.store._path(current.session.session_id).read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("contract,full_review,evaluation,expected_reserve", [
    (False, False, False, 8.5), (False, False, True, 8.5),
    (True, True, True, 8.5), (False, True, True, 8.5),
    (True, False, True, 8.5), (True, False, False, 8.5),
])
async def test_extra_generation_reserve_only_applies_to_required_contract_review(
    tmp_path, monkeypatch, contract, full_review, evaluation, expected_reserve,
):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(numeric_v2_story()), tmp_path)
    current = await runtime.start_session(session_id="reserve", catgirl_binding=_binding(), opening_performance=_opening())
    observed = []

    async def options():
        return {"evaluator": evaluation, "review": full_review, "review_contract": contract, "actor_retry": False}
    async def evaluate(self, **kwargs): return evaluator.NumericV2EvaluationResult((), False)
    async def generate(actor, **kwargs):
        observed.append(kwargs["generation_deadline"])
        raise RuntimeError("captured_generation_deadline")

    monkeypatch.setattr(workflow, "time", SimpleNamespace(monotonic=lambda: 100.0))
    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "克制。")
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow, "_generate_actor_turn_with_output_retry", generate)
    with pytest.raises(RuntimeError, match="captured_generation_deadline"):
        await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2("one", 0, "继续商量。"), ensure_current_binding=lambda _: _binding())
    assert observed == [100.0 + workflow.NUMERIC_V2_TURN_MODEL_BUDGET_SECONDS - expected_reserve]


@pytest.mark.asyncio
@pytest.mark.parametrize("over_budget_stage", ["initial", "confirmation"])
async def test_selected_transition_context_cannot_bypass_either_input_budget(monkeypatch, over_budget_stage):
    requests = []
    contract = {"reason": "进入已经选定的交接阶段。", "bridge_scene_narration": "来到交接台。", "must_deliver": [],
                "must_preserve": ["每项原始约束。" * (10000 if over_budget_stage == "initial" else 1)]}
    original = deepcopy(contract)

    async def config(_):
        assert over_budget_stage != "initial", "Initial context budget must fail before model configuration."
        return await _config(_)

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            requests.append(messages)
            return SimpleNamespace(content=json.dumps(contract_audit_payload({"violations": [{"rule_index": 0,
                "candidate_ref": "c0", "quote": "设备已经移交。", "prior_ref": ""}]}, messages)))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    kwargs = dict(
        node={"story_beat": {"must_not_happen": ["本幕不得移交设备。"]}},
        target_node={"story_beat": {"opening_scene": "两人在交接台旁。"}}, transition_contract=contract,
        actor_performance={"segments": [{"phase": "transition_bridge", "scene_narration":
            "设备已经移交。" + ("交接台旁的指示灯亮着。" * 100 if over_budget_stage == "confirmation" else "")}]},
        session=_session(), player_input="先检查。")
    if over_budget_stage == "confirmation":
        await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(**kwargs)
        assert len(requests) == 2
        initial_tokens, confirmation_tokens = (
            serialized_prompt_tokens(*(message.content for message in messages)) for messages in requests)
        assert initial_tokens < confirmation_tokens
        requests.clear()
        budget = evaluator.numeric_v2_actor_budget
        monkeypatch.setattr(evaluator, "numeric_v2_actor_budget", lambda profile: {
            **budget(profile), "formal_judge_input_max_tokens": initial_tokens})
    with pytest.raises(evaluator.NumericV2EvaluatorError, match="contract_check_input_over_budget"):
        await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(**kwargs)
    assert len(requests) == int(over_budget_stage == "confirmation")
    assert contract == original
