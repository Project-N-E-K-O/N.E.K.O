"""Contract findings keep the same original evidence through confirmation."""

import json
from types import SimpleNamespace

from tests.utils.theater_prompt import contract_audit_payload, prompt_payload

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding


@pytest.mark.asyncio
async def test_body_authority_and_causality_are_confirmed_together_without_hiding_each_other(monkeypatch):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="batch", catgirl_binding=_binding(),
        opening_performance={"performance": "（设备已交给我）交接完成。", "suggested_inputs": []})
    body = {"scene_narration": "玩家签署了新的许可。设备自行启动。", "performance": "已经启动。",
            "suggested_inputs": ["再次交出设备。", "看看设备状态。"]}
    calls = []

    async def config(_): return {"model": "test", "base_url": "https://test.invalid", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            data = prompt_payload(messages[1].content)
            calls.append(data)
            if len(calls) == 1:
                return SimpleNamespace(content=json.dumps(contract_audit_payload({"violations": [
                    {"rule_index": 3, "candidate_ref": "c0", "quote": "玩家签署了新的许可。", "prior_ref": ""},
                    {"rule_index": 4, "candidate_ref": "c0", "quote": "设备自行启动。", "prior_ref": ""},
                    {"rule_index": 5, "candidate_ref": "s0", "quote": "再次交出设备。", "prior_ref": "h0"},
                ]}, messages), ensure_ascii=False))
            assert len(data["findings"]) == 2 and data["visible_order"]
            return SimpleNamespace(content=json.dumps(contract_audit_payload(json.loads('{"verdicts":[{"applicable":true,"confirmed":true},'
                                           '{"applicable":true,"confirmed":false}]}'), messages)))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    diagnostics = {}
    violations = await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=session, player_input="你先看看情况。", actor_performance=body,
        diagnostics=diagnostics)
    assert len(calls) == 2 and len(violations) == 1
    assert diagnostics["findings"][0]["quote"] == "玩家签署了新的许可。"
    assert diagnostics["unsafe_suggestion_texts"] == ["再次交出设备。"]


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_scope", ["history", "earlier_draft", "same_block"])
@pytest.mark.parametrize("old_result,new_result", [
    ("凭证已交给管理员，交付完成。", "凭证仍在角色手里，尚未交付。"),
    ("本笔费用已支付，交易完成。", "本笔费用尚未支付，需要再付一次。"),
    ("信号已成功发送，任务完成。", "信号尚未发送，需要再发一次。"),
])
async def test_continuity_accepts_actual_predecessor_without_requiring_the_model_to_choose_its_scope(
    monkeypatch, prior_scope, old_result, new_result,
):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="continuity", catgirl_binding=_binding(),
        opening_performance={"scene_narration": old_result, "performance": "我在这里。", "suggested_inputs": []})
    if prior_scope == "earlier_draft":
        performance = {"scene_narration": old_result, "performance": f"（{new_result}）"}
        current_ref, prior_ref = "c1", "c0"
    elif prior_scope == "same_block":
        performance = {"scene_narration": old_result + new_result, "performance": "我在这里。"}
        current_ref = prior_ref = "c0"
    else:
        performance = {"scene_narration": new_result, "performance": "我在这里。"}
        current_ref, prior_ref = "c0", "h0"
    requests = []

    async def config(_): return {"model": "test", "base_url": "https://test.invalid", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            data = prompt_payload(messages[1].content); requests.append(data)
            if len(requests) == 1:
                return SimpleNamespace(content=json.dumps(contract_audit_payload({"violations": [{"rule_index": 0,
                    "candidate_ref": current_ref, "quote": new_result, "prior_ref": prior_ref}]}, messages), ensure_ascii=False))
            assert data["finding"]["candidate"]["id"] == current_ref
            assert data["finding"]["quote"] == new_result
            assert old_result in data["finding"]["prior"]["text"]
            return SimpleNamespace(content=json.dumps(contract_audit_payload(json.loads('{"applicable":true,"confirmed":true}'), messages)))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    diagnostics = {}
    result = await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=session, player_input="按实际结果继续。",
        actor_performance=performance, diagnostics=diagnostics)
    assert len(result) == 1 and len(requests) == 2
    assert diagnostics["findings"][0]["quote"] == new_result


@pytest.mark.asyncio
@pytest.mark.parametrize("focused_over_budget", [False, True])
async def test_confirmation_checks_the_reported_proposal_instead_of_searching_for_another_violation(
    monkeypatch, focused_over_budget,
):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="proposal", catgirl_binding=_binding(),
        opening_performance={"performance": "我们先商量。", "suggested_inputs": []})
    requests = []

    async def config(_): return {"model": "test", "base_url": "https://test.invalid", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            data = prompt_payload(messages[1].content); requests.append(data)
            if len(requests) == 1:
                if focused_over_budget:
                    # Only the follow-up request exceeds the fixed 10k review capacity.
                    monkeypatch.setattr(evaluator, "count_tokens", lambda text: 10001)
                return SimpleNamespace(content=json.dumps(contract_audit_payload({"violations": [{"rule_index": 0,
                    "candidate_ref": "c0", "quote": "请先写下建议，我们再商量。", "prior_ref": ""}]}, messages)))
            assert data["finding"]["quote"] == "请先写下建议，我们再商量。"
            assert "sources" not in data
            return SimpleNamespace(content=json.dumps(contract_audit_payload(json.loads('{"applicable":true,"confirmed":false}'), messages)))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    check = evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": ["未达成共识前不得执行分配。"]}},
        session=session, player_input="我愿意一起承担后果。",
        actor_performance={"performance": "请先写下建议，我们再商量。"})
    if focused_over_budget:
        with pytest.raises(evaluator.NumericV2EvaluatorError, match="input_over_budget"):
            await check
        assert len(requests) == 1
    else:
        assert await check == () and len(requests) == 2


def test_fact_reference_prompt_enumerates_the_exact_ids_without_repeating_the_originals():
    data = {"player_input": "我愿意等候。", "scene_context": [{"revision": 1, "content": []}],
            "committed_fact_evidence": {"content_refs": ["scene_context[0].content"]}}
    bank = evaluator._pack_fact_evidence_refs(data, ["设备已经停机。指示灯归零。"],
        human_prefix="数据：", system_tokens=0, max_tokens=7000)
    ids = [key for group in data["fact_evidence_refs"].values() for key in group["ids"]]
    assert set(ids) == set(bank)
    assert "设备已经停机" not in json.dumps(data["fact_evidence_refs"], ensure_ascii=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("rule_index,body,quote,expected_calls", [
    (0, "（独自抵达车站，开始办理托运）", "独自抵达车站，开始办理托运", 1),
    (3, "（独自抵达车站，开始办理托运）", "独自抵达车站，开始办理托运", 2),
    (3, "要不要改成一起去车站？", "要不要改成一起去车站？", 1),
])
async def test_route_decision_restricts_execution_but_cannot_serve_as_historical_evidence(
    monkeypatch, rule_index, body, quote, expected_calls,
):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="route-proof", catgirl_binding=_binding(),
        opening_performance={"performance": "先核对去哪边。", "suggested_inputs": []})
    calls = []

    async def config(_): return {"model": "test", "base_url": "https://test.invalid", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            data = prompt_payload(messages[1].content); calls.append(data)
            if len(calls) == 1:
                decision = next(row for row in data["sources"] if row["id"] == "d0")
                assert decision["transition_authorized"] is False
                return SimpleNamespace(content=json.dumps(contract_audit_payload({"violations": [{"rule_index": rule_index,
                    "candidate_ref": "c0", "quote": quote, "prior_ref": "d0"}]}, messages)))
            assert data["finding"]["prior"]["type"] == "route_decision"
            return SimpleNamespace(content=json.dumps(contract_audit_payload(json.loads('{"applicable":true,"confirmed":true}'), messages)))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    result = await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=session, player_input="我去车站，你留在这里。",
        actor_performance={"performance": body},
        exit_mismatch_reason="玩家要求分头行动，入口是双方同行，未授权该转场。")
    assert len(calls) == expected_calls
    assert bool(result) is (expected_calls == 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("performance,executed", [
    ("下一步要一起签署文件吗？", False),
    ("（落笔签署文件并交出）", True),
    ("我已经签署并交出文件了。", True),
])
async def test_discussion_only_phase_restriction_distinguishes_an_invitation_from_execution(monkeypatch, performance, executed):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="phase-proof", catgirl_binding=_binding(),
        opening_performance={"performance": "本轮先讨论方案。", "suggested_inputs": []})
    calls = []

    async def config(_): return {"model": "test", "base_url": "https://test.invalid", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            calls.append(messages)
            return SimpleNamespace(content=(json.dumps(contract_audit_payload({"violations": [{"rule_index": 1,
                "candidate_ref": "c0", "quote": performance.strip("（）"), "prior_ref": "a0"}]}, messages))
                if len(calls) == 1 else json.dumps(contract_audit_payload(json.loads('{"applicable":true,"confirmed":true}'), messages))))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    result = await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"summary": "本幕只讨论和决定，不实际签署文件。"}}, session=session,
        actor_performance={"performance": performance}, player_input="先讨论方案。")
    assert bool(result) is executed
    assert len(calls) == (2 if executed else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("scene,quote,delivered", [
    ("来访者笑着回答：“入口很清楚。”", "来访者笑着回答：“入口很清楚。”", True),
    ("来访者刚才回答：“入口很清楚。”", "入口很清楚。", False),
    ("来访者站起身，指向门口说道：‘刚才那个入口很清楚。’", "刚才那个入口很清楚。", True),
    ("来访者笑着回答：“入口很清楚。”另一人此前也说这里拥挤。", "此前也说这里拥挤", False),
])
async def test_contract_distinguishes_live_scene_speech_from_reported_history(monkeypatch, scene, quote, delivered):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="scene-speaker", catgirl_binding=_binding(),
        opening_performance={"performance": "来访者站在入口前。"})
    rule = "禁止编造来访者尚未实际给出的评价"
    calls = []

    async def config(_):
        return {"model": "test", "base_url": "https://test.invalid", "api_key": "test"}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def ainvoke(self, messages, **kwargs):
            data = prompt_payload(messages[1].content)
            calls.append(data)
            if len(calls) == 1:
                assert ("delivery" in data["sources"][0]) is ("刚才回答" not in scene)
                return SimpleNamespace(content=json.dumps(contract_audit_payload({"violations": [
                    {"rule_index": 0, "candidate_ref": "c0", "quote": quote, "prior_ref": ""},
                ]}, messages)))
            return SimpleNamespace(content=json.dumps(contract_audit_payload(json.loads('{"applicable":true,"confirmed":true}'), messages)))

    async def factory(*args, **kwargs):
        return Client()

    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    diagnostics = {}
    result = await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": [rule]}}, session=session,
        actor_performance={"scene_narration": scene}, player_input="请说说感受。", diagnostics=diagnostics,
    )
    assert result == (() if delivered else (rule,))
    assert len(calls) == (1 if delivered else 2)
    assert diagnostics.get("direct_scene_deliveries_kept", 0) == int(delivered)


@pytest.mark.asyncio
async def test_scene_speaker_cannot_validate_unselected_approach(monkeypatch):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="route-feedback", catgirl_binding=_binding(),
        opening_performance={"performance": "我们先按纸质方式核对。"})
    choice = [{"value": "纸质核对", "player_input": "只用纸质记录。"}]
    quote = "刚才的电子读数很清楚。"
    calls = []

    async def config(_):
        return {"model": "test", "base_url": "https://test.invalid", "api_key": "test"}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def ainvoke(self, messages, **kwargs):
            data = prompt_payload(messages[1].content)
            assert data["player_fact_evidence"] == choice
            calls.append(data)
            if len(calls) == 1:
                return SimpleNamespace(content=json.dumps(contract_audit_payload({
                    "route_conflicts": [{"candidate_ref": "c0", "quote": quote}], "violations": [],
                }, messages)))
            assert data["finding"]["quote"] == quote
            assert data["finding"]["candidate"]["delivery"] == "current_scene_speech"
            return SimpleNamespace(content=json.dumps(contract_audit_payload(json.loads('{"applicable":true,"confirmed":true}'), messages)))

    async def factory(*args, **kwargs):
        return Client()

    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    result = await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": ["不得擅自换方案。"]}},
        session=session, player_input="你看到什么？",
        actor_performance={"scene_narration": f"来访者笑着回答：“{quote}”"},
        player_fact_evidence=choice, author_boundaries_only=True,
    )
    assert len(result) == 1 and "未选方案" in result[0]
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("author_boundaries_only", [False, True])
@pytest.mark.parametrize("rule,quote,suffix,expected_calls,use_causal_rule", [
    ("禁止在本幕实际完成签署", "我来签署文件", "。我们要不要现在就开始？", 1, False),
    ("禁止在本幕实际完成签署", "我来签署了文件", "。", 2, False),
    ("禁止讨论签署", "我来签署文件", "。我们要不要现在就开始？", 2, False),
    ("不得更改表单", "参与者试用过了，对结果很满意", "。", 2, True),
])
async def test_explicit_prospective_assignment_does_not_trigger_a_rewrite_for_execution(
    monkeypatch, rule, quote, suffix, expected_calls, author_boundaries_only, use_causal_rule,
):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="proposed-assignment", catgirl_binding=_binding(),
        opening_performance={"performance": "先把分工定好。"})
    calls = []

    async def config(_):
        return {"model": "test", "base_url": "https://test.invalid", "api_key": "test"}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def ainvoke(self, messages, **kwargs):
            calls.append(messages)
            if len(calls) == 1:
                wire = prompt_payload(messages[1].content)
                boundaries = [entry["text"] for entry in wire["author_boundaries"]]
                if author_boundaries_only:
                    assert len(boundaries) == 3 and boundaries[0] == rule
                    assert "未授权的新主动动作" in boundaries[1]
                    assert "关键结果" in boundaries[2]
                index = next(i for i, text in enumerate(boundaries) if "首次宣告不可逆结果" in text) if use_causal_rule else 0
                return SimpleNamespace(content=json.dumps(contract_audit_payload({"violations": [{
                    "rule_index": index, "candidate_ref": "c0", "quote": quote, "prior_ref": "",
                }]}, messages)))
            context = prompt_payload(messages[1].content)
            assert context["visible_order"] and context["latest_committed"]
            return SimpleNamespace(content=json.dumps(contract_audit_payload(json.loads('{"applicable":true,"confirmed":true}'), messages)))

    async def factory(*args, **kwargs):
        return Client()

    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    result = await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"character_state": {"scene_boundaries": [rule]}}}, session=session,
        actor_performance={"performance": quote + suffix}, player_input="先定分工。",
        author_boundaries_only=author_boundaries_only,
    )
    assert len(calls) == expected_calls
    assert bool(result) is (expected_calls == 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("applicable,confirmed,rejected", [
    (False, True, False), (False, False, False), (True, False, False), (True, True, True),
])
async def test_confirmation_requires_both_applicable_scope_and_actual_violation(
    monkeypatch, applicable, confirmed, rejected,
):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="conditional-rule", catgirl_binding=_binding(),
        opening_performance={"scene_narration": "两人已到达档案室。", "performance": "先核对文书。"})
    calls = []

    async def config(_): return {"model": "test", "base_url": "https://test.invalid", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            data = prompt_payload(messages[1].content); calls.append(data)
            if len(calls) == 1:
                return SimpleNamespace(content=json.dumps(contract_audit_payload({"violations": [{"rule_index": 0,
                    "candidate_ref": "c0", "quote": "拆开文书封口", "prior_ref": "h0"}]}, messages)))
            assert data["finding"]["prior"]["text"] == "两人已到达档案室。"
            return SimpleNamespace(content=json.dumps(contract_audit_payload({"applicable": applicable, "confirmed": confirmed}, messages)))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    result = await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": ["不得在到达档案室前拆开文书。"]}},
        session=session, player_input="请按现场情况处理。", actor_performance={"performance": "（拆开文书封口）"})
    assert bool(result) is rejected
    assert len(calls) == 2
