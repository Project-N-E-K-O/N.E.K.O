"""Player permission satisfies literal conditions without waiving scene limits."""

import json
from types import SimpleNamespace

from tests.utils.theater_prompt import contract_audit_payload, prompt_payload

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    # These tests exercise the real narrow-audit adapter, not the orchestration stub.
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("object_name,local_action", [
    ("藏品柜", "整理展品目录"),
    ("设备控制柜", "收好检修清单"),
])
@pytest.mark.parametrize("case", ["unconditional", "permission_exception", "future", "actor_local"])
async def test_native_audit_keeps_permission_and_stage_prohibitions_distinct(
    monkeypatch, object_name, local_action, case,
):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="permission-scope", catgirl_binding=_binding(),
                                    opening_performance=_opening())
    rule = (f"取得玩家明确同意之前不得打开{object_name}。" if case == "permission_exception"
            else f"本幕不得打开{object_name}，只能在柜外讨论。")
    message = f"我同意你打开{object_name}，请你来操作。"
    action = f"取出钥匙，打开{object_name}"
    body = f"（{action}）请看这里。"
    if case == "future":
        body = f"等这项讨论结束，我们再商量要不要打开{object_name}，好吗？"
    elif case == "actor_local":
        body = f"（{local_action}）先把眼前的问题说清楚。"
    candidate = {"performance": body, "suggested_inputs": []}
    requests = []

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://example.invalid/v1", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            requests.append(messages)
            data = prompt_payload(messages[1].content)
            if len(requests) == 1:
                assert "仅在原禁令明确附有许可条件时，玩家同意才满足该条件" in messages[0].content
                assert "玩家主动行动或授权不解除没有此例外的作者阶段禁令" in messages[0].content
                assert data["author_boundaries"][0]["text"] == rule
                assert data["player_input"] == message
                c = [row for row in data["sources"] if row["id"].startswith("c")]
                assert "".join(row["text"] for row in c) == body.replace("（", "").replace("）", "")
                findings = ([{"rule_index": 0, "candidate_ref": "c0", "quote": action, "prior_ref": ""}]
                            if case == "unconditional" else [])
                return SimpleNamespace(content=json.dumps(contract_audit_payload({"violations": findings}, messages), ensure_ascii=False))
            finding = data["finding"]
            assert finding["rule"] == rule
            assert finding["candidate"]["type"] == "action"
            assert finding["quote"] == action
            assert data["player_input"] == message
            assert "玩家说可以开始，均不能解除本阶段禁令" in messages[0].content
            return SimpleNamespace(content=json.dumps(contract_audit_payload(json.loads('{"applicable":true,"confirmed":true}'), messages)))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    before = session.to_dict()
    problems = await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": [rule]}}, session=session,
        actor_performance=candidate, player_input=message,
    )
    assert problems == ((rule,) if case == "unconditional" else ())
    assert len(requests) == (2 if case == "unconditional" else 1)
    assert before == session.to_dict()


@pytest.mark.asyncio
@pytest.mark.parametrize("rule,body", [
    ("本幕只讨论借展方案，不得联系外部借展方或交付借展方答复。",
     "借展方接通电话，明确同意将藏品借给本馆。"),
    ("本幕不得打开设备柜，只能在柜外讨论。", "柜门被打开，里面的设备启动了。"),
])
@pytest.mark.parametrize("confirmation", [False, True])
async def test_direct_author_finding_does_not_require_a_redundant_self_prior(
    monkeypatch, rule, body, confirmation,
):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="self-prior", catgirl_binding=_binding(), opening_performance=_opening())
    requests = []

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://example.invalid/v1", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            requests.append(messages)
            data = prompt_payload(messages[1].content)
            if len(requests) == 1:
                return SimpleNamespace(content=json.dumps(contract_audit_payload({"violations": [{
                    "rule_index": 0, "candidate_ref": "c0", "quote": body, "prior_ref": "c0",
                }]}, messages), ensure_ascii=False))
            assert data["finding"]["rule"] == rule
            assert data["finding"]["quote"] == body
            assert data["finding"]["prior"] is None
            assert body in json.dumps(data["visible_order"], ensure_ascii=False)
            return SimpleNamespace(content=json.dumps(contract_audit_payload({"applicable": True, "confirmed": confirmation}, messages)))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    diagnostics = {}
    problems = await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": [rule]}}, session=session,
        actor_performance={"scene_narration": body, "performance": "请核对。", "suggested_inputs": []},
        player_input="请按当前安排继续。", diagnostics=diagnostics,
    )
    assert len(requests) == 2
    assert diagnostics["grounded_findings"] == 1
    assert problems == ((rule,) if confirmation else ())


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["repeat_without_prior", "author_plan_without_prior", "future_prior", "empty_quote", "unknown_prior"])
async def test_missing_or_invalid_predecessors_cannot_clear_an_authored_rule(monkeypatch, case):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="invalid-prior", catgirl_binding=_binding(), opening_performance=_opening())
    rule = "本幕不得打开设备柜。"
    body = "设备柜已经打开。"
    author = case not in {"repeat_without_prior", "author_plan_without_prior"}
    index = 1 if case == "author_plan_without_prior" else 0
    prior_ref = {"future_prior": "c1", "unknown_prior": "c999"}.get(case, "c0")
    quote = "" if case == "empty_quote" else body
    requests = []

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://example.invalid/v1", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            requests.append(messages)
            assert len(requests) == 1
            return SimpleNamespace(content=json.dumps(contract_audit_payload({"violations": [{
                "rule_index": index, "candidate_ref": "c0", "quote": quote, "prior_ref": prior_ref,
            }]}, messages), ensure_ascii=False))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    diagnostics = {}
    result = await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": [rule] if author else [], "summary": "只在柜外讨论。"}},
        session=session, actor_performance={"scene_narration": body, "performance": "我留在柜外。"},
        player_input="继续讨论。", diagnostics=diagnostics,
    )
    assert result == ((rule,) if author else ())
    assert diagnostics["grounded_findings"] == 0
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("first_rule,second_rule,future,actual", [
    ("本幕不得完成节目编排。", "本幕不得联系外部机构。",
     "如果信息核实了，我会调整节目编排。", "社区值班人员接通电话，确认了住户信息。"),
    ("本幕不得确定借展协议。", "本幕不得联系借展方。",
     "如果细节说定，我会整理借展协议。", "借展方接通电话，同意外借这件藏品。"),
])
async def test_one_cleared_author_finding_does_not_replace_other_rule_checks(
    monkeypatch, first_rule, second_rule, future, actual,
):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="independent-author-rules", catgirl_binding=_binding(),
                                    opening_performance=_opening())
    requests = []

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://example.invalid/v1", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            requests.append(messages)
            data = prompt_payload(messages[1].content)
            if len(requests) == 1:
                assert "每类最多报告一项" not in messages[0].content
                assert "优先逐条核对作者明确禁令" in messages[0].content
                assert "单项发现不能代替或跳过其他禁令" in messages[0].content
                assert [row["text"] for row in data["author_boundaries"][:2]] == [first_rule, second_rule]
                return SimpleNamespace(content=json.dumps(contract_audit_payload({"violations": [
                    {"rule_index": 0, "candidate_ref": "c1", "quote": future, "prior_ref": ""},
                    {"rule_index": 1, "candidate_ref": "c0", "quote": actual, "prior_ref": "c0"},
                ]}, messages), ensure_ascii=False))
            assert [row["rule"] for row in data["findings"]] == [first_rule, second_rule]
            assert future in json.dumps(data["visible_order"], ensure_ascii=False)
            assert actual in json.dumps(data["visible_order"], ensure_ascii=False)
            return SimpleNamespace(content=json.dumps(contract_audit_payload({"verdicts": [
                {"applicable": True, "confirmed": False},
                {"applicable": True, "confirmed": True},
            ]}, messages)))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    diagnostics = {}
    problems = await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": [first_rule, second_rule]}}, session=session,
        actor_performance={"scene_narration": actual, "performance": future},
        player_input="先把当前事项商量清楚。", diagnostics=diagnostics,
    )
    assert len(requests) == 2
    assert diagnostics["grounded_findings"] == 2
    assert diagnostics["confirmed_findings"] == 1
    assert problems == (second_rule,)
