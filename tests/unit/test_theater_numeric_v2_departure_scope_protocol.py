"""Whole-draft scope is the sole departure verdict on high-risk turns."""

import json
from types import SimpleNamespace

from tests.utils.theater_prompt import contract_audit_payload, prompt_payload

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding

_VERIFY = evaluator.NumericV2MetricEvaluator.verify_contract_boundaries


@pytest.mark.asyncio
@pytest.mark.parametrize("author_boundaries_only", [False, True])
@pytest.mark.parametrize("final_scope,other_violation,expected_problem", [
    ("current_scene", False, False),
    ("prospective", False, False),
    ("uncertain", False, True),
    ("departed", False, True),
    ("current_scene", True, True),
])
async def test_full_scope_confirmation_can_clear_false_departure_without_clearing_other_violations(
    monkeypatch, final_scope, other_violation, expected_problem, author_boundaries_only,
):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="scope", catgirl_binding=_binding(),
        opening_performance={"performance": "仪表还在桌上，检查尚未完成。", "suggested_inputs": []})
    body = {"scene_narration": "指尖搭着桌沿，仪表的指针仍在缓慢摆动。",
            "performance": "先看完这次读数。你已经替我签了另一份许可。" if other_violation else
                           "先看完这次读数，检查结束后再去检修间。"}
    requests = []

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            data = prompt_payload(messages[1].content)
            requests.append((data, kwargs["response_format"]["json_schema"]["schema"]))
            blocks = [row for row in data.get("sources", data.get("departure_sources", []))
                      if row["id"].startswith("c")]
            assert len(blocks) == 2
            scope = [{"candidate_ref": row["id"], "scope": "current_scene", "quote": row["text"]}
                     for row in blocks]
            if len(requests) == 1:
                scope[1]["scope"] = "departed"  # False initial report, independently rechecked below.
                violations = []
                if other_violation:
                    index = next(row["rule_index"] for row in data["author_boundaries"]
                                 if "不得替玩家" in row["text"])
                    violations = [{"rule_index": index, "candidate_ref": "c1",
                                   "quote": "你已经替我签了另一份许可。", "prior_ref": ""}]
                return SimpleNamespace(content=json.dumps(contract_audit_payload({"violations": violations, "departure_scope": scope}, messages)))
            scope[1]["scope"] = final_scope
            verdicts = [{"applicable": False, "confirmed": False}]
            if other_violation:
                verdicts.append({"applicable": True, "confirmed": True})
            base = {"verdicts": verdicts} if other_violation else verdicts[0]
            return SimpleNamespace(content=json.dumps({**base, "departure_scope": scope}))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    diagnostics = {}
    result = await _VERIFY(evaluator.NumericV2MetricEvaluator(object()),
        node={"story_beat": {}}, session=session, actor_performance=body,
        player_input="我们已经到检修间了，直接结束吧。", departure_attempt_observed=True,
        departure_blocker="completion_pending", exit_mismatch_reason="本轮留在仪表桌旁。",
        diagnostics=diagnostics, author_boundaries_only=author_boundaries_only)
    assert bool(result) is expected_problem
    assert len(requests) == 2
    for _, schema in requests:
        assert "departure_scope" in schema["required"]
        assert {"premature_departure", "departure_clearance"}.isdisjoint(schema["properties"])
    assert diagnostics["departure_scope"] == ("uncertain" if final_scope == "uncertain" else
        "departed" if final_scope == "departed" else "current_scene")
    if other_violation:
        assert any("不得替玩家" in rule for rule in result)


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["empty", "missing_block", "duplicate_block", "unknown_ref", "ungrounded_quote"])
async def test_incomplete_scope_never_becomes_clearance(monkeypatch, defect):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="incomplete", catgirl_binding=_binding(),
        opening_performance={"performance": "检查尚未完成。", "suggested_inputs": []})

    async def config(_): return {"model": "offline", "base_url": "https://test.invalid"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            data = prompt_payload(messages[1].content)
            blocks = [row for row in data["sources"] if row["id"].startswith("c")]
            scope = [{"candidate_ref": row["id"], "scope": "current_scene", "quote": row["text"]}
                     for row in blocks]
            if defect == "empty": scope = []
            elif defect == "missing_block": scope.pop()
            elif defect == "duplicate_block": scope[1] = dict(scope[0])
            elif defect == "unknown_ref": scope[1]["candidate_ref"] = "c99"
            else: scope[1]["quote"] = "这一句不在正文里。"
            return SimpleNamespace(content=json.dumps(contract_audit_payload({"violations": [], "departure_scope": scope}, messages)))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    review = _VERIFY(evaluator.NumericV2MetricEvaluator(object()),
        node={"story_beat": {}}, session=session,
        actor_performance={"scene_narration": "仪表还在桌上。", "performance": "再看一次读数。"},
        player_input="已经走了。", departure_attempt_observed=True,
        departure_blocker="completion_pending", exit_mismatch_reason="留幕。")
    if defect == "ungrounded_quote":
        with pytest.raises(evaluator.NumericV2EvaluatorOutputError):
            await review
    else:
        assert await review
