"""An empty finding list does not prove every authored boundary was checked."""

import json
from tests.utils.theater_prompt import prompt_payload
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_runtime import NumericV2Engine
from services.theater.numeric_v2_structured_output import contract_output_schema
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    pass


def test_body_findings_and_fact_discoveries_have_separate_reference_domains():
    schema = contract_output_schema(grounded=True, author_rule_count=1, rule_count=3,
                                    candidate_refs=["c0", "c1", "s0"], prior_refs=["", "c0", "h0"])
    props = schema["properties"]["violations"]["items"]["properties"]
    assert props["candidate_ref"]["enum"] == ["c0", "c1", "s0"]
    assert props["prior_ref"]["enum"] == ["", "c0", "h0"]
    assert props["rule_index"]["enum"] == [1, 2]
    assert "author_checks" in schema["required"]
    author = schema["properties"]["author_checks"]
    assert author["required"] == ["0"]
    check = author["properties"]["0"]["anyOf"]
    assert check[0]["enum"] == ["clear", "uncertain"]
    assert {"candidate_ref", "quote", "prior_ref"} == set(check[1]["required"])
    assert "author_checks" not in contract_output_schema(grounded=True, confirmation=True)["properties"]
    no_body = contract_output_schema(grounded=True, author_rule_count=1, candidate_refs=["s0"])
    assert no_body["properties"]["author_checks"]["properties"]["0"] == {"type": "string", "enum": ["clear", "uncertain"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("object_name", ["藏品柜", "设备控制柜"])
@pytest.mark.parametrize("case", ["clear", "missing", "duplicate", "uncertain", "wrong_phase", "bad_quote"])
async def test_authored_boundary_requires_complete_grounded_scope(monkeypatch, object_name, case):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="author-coverage", catgirl_binding=_binding(),
                                    opening_performance=_opening())
    rule = f"本幕不得打开{object_name}，只能在柜外讨论。"
    calls = []

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://example.invalid/v1", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            calls.append(messages)
            assert len(calls) == 1
            data = prompt_payload(messages[1].content)
            assert data["author_checks_required"] == [{"rule_index": 0, "origin_node_id": "start",
                "origin_phase": "turn", "check_phases": ["turn"]}]
            checks = [{"rule_index": 0, "status": "clear", "findings": []}]
            if case == "duplicate": checks *= 2
            if case == "uncertain": checks[0]["status"] = "uncertain"
            if case == "wrong_phase": checks[0]["phases"] = ["target_opening"]
            payload = {"violations": [], "author_checks": checks, "player_authority": {
                row["id"]: "none" for row in data["sources"] if row["id"].startswith("c")}}
            if case == "missing": payload.pop("author_checks")
            if case == "bad_quote":
                checks[0].update(status="violation", findings=[{
                    "candidate_ref": "c99", "quote": "不存在的原文", "prior_ref": ""}])
            return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    diagnostics = {}
    before = session.to_dict()
    result = await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": [rule]}}, session=session,
        actor_performance={"performance": "先讨论目录，之后再决定是否申请开柜。", "suggested_inputs": []},
        player_input="先在柜外说清楚。", diagnostics=diagnostics,
    )
    assert result == (() if case == "clear" else (rule,))
    assert diagnostics["author_coverage_complete"] is (case == "clear")
    assert before == session.to_dict()


@pytest.mark.asyncio
@pytest.mark.parametrize("object_name", ["信件", "展品说明"])
async def test_source_boundary_cannot_be_cleared_by_only_checking_target(monkeypatch, object_name):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="author-phase", catgirl_binding=_binding(),
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
            assert data["author_checks_required"] == [{
                "rule_index": 0, "origin_node_id": "start", "origin_phase": "source_response",
                "check_phases": ["source_response", "transition_bridge", "target_opening"],
            }]
            return SimpleNamespace(content=json.dumps({"violations": [], "player_authority": {
                row["id"]: "none" for row in data["sources"] if row["id"].startswith("c")}, "author_checks": [{
                "rule_index": 0, "status": "clear", "phases": ["target_opening"], "findings": [],
            }]}))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    result = await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": [f"本幕只讨论处理{object_name}的安排，不得拆阅。"]}},
        target_node={"story_beat": {}}, session=session, include_all_segments=True,
        actor_performance={"segments": [
            {"phase": "source_response", "performance": "好，我们说定了。"},
            {"phase": "target_opening", "performance": "现在看看内容。"},
        ]}, player_input="同意先处理。",
    )
    assert len(result) == 1
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("behavior", ["在本幕内拆封录音带或播放内容", "未经借展方同意签署借展协议"])
async def test_role_forbidden_behaviors_are_individually_checked(monkeypatch, behavior):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="role-rule", catgirl_binding=_binding(), opening_performance=_opening())

    async def config(_):
        return {"model": "test", "base_url": "https://example.invalid", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            data = prompt_payload(messages[1].content)
            assert data["author_boundaries"][0]["text"] == "当前角色禁止行为：" + behavior
            assert data["author_checks_required"] == [{"rule_index": 0, "origin_node_id": "start",
                "origin_phase": "turn", "check_phases": ["turn"]}]
            return SimpleNamespace(content=json.dumps({"author_checks": [{
                "rule_index": 0, "status": "clear", "findings": [],
            }], "violations": [], "player_authority": {
                row["id"]: "none" for row in data["sources"] if row["id"].startswith("c")}}))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    diagnostics = {}
    result = await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"acting_contract": {"forbidden_behaviors": [behavior]}}},
        session=session, actor_performance={"performance": "我先整理这里的材料，之后再商量下一步。"},
        player_input="先讨论。", diagnostics=diagnostics,
    )
    assert result == () and diagnostics["author_coverage_complete"] is True


@pytest.mark.asyncio
async def test_plain_turn_with_only_role_prohibitions_is_scheduled_for_audit(tmp_path, monkeypatch):
    from services.theater import numeric_v2_workflow as workflow
    from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    beat = engine.nodes["start"]["story_beat"]
    beat["must_not_happen"] = []
    beat["character_state"] = {"scene_boundaries": []}
    beat.setdefault("acting_contract", {})["forbidden_behaviors"] = ["未经同意执行下一项工作"]
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="role-only", catgirl_binding=_binding(), opening_performance=_opening())
    calls = []

    async def options():
        return {"evaluator": True, "review": False, "review_contract": True, "actor_retry": False}

    async def evaluate(self, **kwargs): return evaluator.NumericV2EvaluationResult((), False)
    async def generate(self, **kwargs): return {"performance": "先把方案讨论清楚。", "suggested_inputs": []}
    async def audit(self, **kwargs):
        calls.append(kwargs)
        assert kwargs["author_boundaries_only"] is True
        return ()

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "自然克制。")
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "verify_contract_boundaries", audit)
    result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("one", 0, "先讨论方案。"), ensure_current_binding=lambda _: _binding())
    assert len(calls) == 1 and result.stored.session.revision == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 3])
@pytest.mark.parametrize("case", ["clear", "missing", "duplicate", "overlap", "unknown_rule", "invalid_ref", "uncertain", "extra_field"])
async def test_compact_author_checks_preserve_exact_coverage(monkeypatch, count, case):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="compact-coverage", catgirl_binding=_binding(), opening_performance=_opening())
    rules = [f"本幕不得执行第{i}项受限操作。" for i in range(count)]

    async def config(_): return {"model": "test", "base_url": "https://example.invalid", "api_key": "test"}
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            wire = prompt_payload(messages[1].content)
            checks = {"clear": list(range(count)), "uncertain": [], "violations": []}
            if case == "missing": checks["clear"].pop()
            if case == "duplicate": checks["clear"].append(0)
            if case == "overlap": checks["uncertain"] = [0]
            if case == "unknown_rule": checks["clear"][-1] = 999
            if case == "uncertain": checks["uncertain"] = [checks["clear"].pop()]
            if case == "extra_field": checks["unexpected"] = True
            if case == "invalid_ref":
                index = checks["clear"].pop()
                checks["violations"] = [{"rule_index": index, "candidate_ref": "c999", "quote": "不存在的执行。", "prior_ref": ""}]
            return SimpleNamespace(content=json.dumps({"violations": [], "author_checks": checks,
                "player_authority": {row["id"]: "none" for row in wire["sources"] if row["id"].startswith("c")}}))
    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    diagnostics = {}
    result = await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": rules}}, session=session,
        actor_performance={"performance": "先讨论。"}, player_input="先等等。", diagnostics=diagnostics)
    assert bool(result) is (case != "clear")
    assert diagnostics["author_coverage_complete"] is (case == "clear")
    assert isinstance(diagnostics["author_checks"], list)
