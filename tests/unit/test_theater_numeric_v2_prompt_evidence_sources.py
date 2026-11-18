"""Keep author task metadata and future references separate from runtime evidence."""

from copy import deepcopy
import json
from types import SimpleNamespace

from tests.utils.theater_prompt import contract_audit_payload, prompt_payload

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.unit.test_theater_numeric_v2_progress_ownership import _pending_departure_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


_REVIEW = evaluator.NumericV2MetricEvaluator.review_story_evidence
_VERIFY = evaluator.NumericV2MetricEvaluator.verify_contract_boundaries
PLAN = {"transition_direction": "前往检修间开展下一阶段校准。",
        "opening_reference": "检修间的仪表旁出现一张红色结果单。"}
PROGRAM_STATE = "运行时本轮仍留在当前幕，未授权阶段变化。"


def _frame(*, goals=True):
    story = _pending_departure_story()
    route_key = "scene:start:route_choice"
    story["fact_contract"]["facts"][route_key] = {
        "value_type": "bool", "visibility": "public", "description": "玩家选择前往检修间。"}
    story["nodes"][0]["route_gates"][0]["conditions"]["all"].append({
        "type": "fact_equals", "key": route_key, "value": True})
    beat = story["nodes"][0]["story_beat"]
    if goals:
        beat.pop("must_happen", None)
        beat["goals"] = [
            {"id": "show_reading", "owner": "environment", "description": "仪表显示当前接线读数。",
             "evidence": {"mode": "exact", "anchors": ["接线读数"]},
             "delivery": {"type": "environment_fact", "output_field": "scene_update",
                          "source_ids": ["opening.start"]}},
            {"id": "explain_reading", "owner": "catgirl", "description": "角色解释读数与核验结果。",
             "evidence": {"mode": "exact", "anchors": ["核验结果"]},
             "delivery": {"type": "catgirl_dialogue", "output_field": "performance_dialogue",
                          "source_ids": ["goal.show_reading"]}},
        ]
    beat["acting_contract"] = {
        "cognition_state": "normal", "memory_state": "available", "self_reference_mode": "persona_allowed",
        "persona_scope": "full", "dialogue_policy": "required",
        "allowed_behaviors": ["查看仪表并请管事核验当前接线。"], "forbidden_behaviors": [],
    }
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id="prompt_sources", catgirl_binding=_binding(),
                                    opening_performance=_opening())
    return engine, session


def _offline(monkeypatch, answer):
    requests = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages, **kwargs):
            user = messages[1].content
            data = prompt_payload(user)
            requests.append((messages, data, kwargs))
            return SimpleNamespace(content=json.dumps(contract_audit_payload(answer(len(requests), data), messages), ensure_ascii=False))

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"}

    async def client(*args, **kwargs):
        return Client()

    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", client)
    return requests


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["goal", "fallback", "empty", "soft", "soft_freeform", "invalid"])
async def test_selected_author_task_retains_its_exact_permissions_and_dependencies(monkeypatch, mode):
    engine, session = _frame(goals=mode != "fallback")
    blocked = mode not in {"soft", "soft_freeform"}
    selected = {"goal": "explain_reading", "fallback": "scene:start:done", "empty": "",
                "soft": "仪表显示当前接线读数。", "soft_freeform": "先核对当前接线读数。",
                "invalid": "unissued_task"}[mode]
    requests = _offline(monkeypatch, lambda _, data: {"direction": selected})
    source_before = deepcopy(engine.nodes["start"]["story_beat"])
    call = _REVIEW(evaluator.NumericV2MetricEvaluator(object()), engine=engine, session=session,
                   message="我已经到检修间了。", claims=(), timeout_seconds=1,
                   review_progress=True, blocked_departure=blocked)
    if mode == "invalid":
        # A blocked departure's task is the output itself, so it stays strict.
        with pytest.raises(evaluator.NumericV2EvaluatorError):
            await call
        assert len(requests) == 1
        return
    result = await call
    if mode == "soft_freeform":
        # Ordinary progress drops an unknown hint instead of failing the review.
        assert len(requests) == 1
        assert result.direction == "" and result.current_scene_task is None
        return
    assert len(requests) == 1
    _, data, kwargs = requests[0]
    schema = kwargs["response_format"]["json_schema"]["schema"]
    assert set(schema["properties"]) == {"direction"}
    if mode == "empty":
        assert result.direction == selected and result.current_scene_task is None
    else:
        selected_id = "show_reading" if mode == "soft" else selected
        expected_direction = next(row["description"] for row in data["progress"][
            "goals" if mode in {"goal", "soft"} else "pending_results"] if row.get("id", row.get("key")) == selected_id)
        assert result.direction == expected_direction
        assert result.current_scene_task == {
            "selected_id": selected_id, "direction": expected_direction,
            "goals": data["progress"]["goals"],
            "allowed_behaviors": data["progress"]["allowed_behaviors"],
            "pending_results": [row for row in data["progress"]["pending_results"] if row["required_this_scene"]],
        }
        assert any(not row["required_this_scene"] for row in data["progress"]["pending_results"])
        assert all(row["key"] != "scene:start:route_choice" for row in result.current_scene_task["pending_results"])
        if mode == "goal":
            chosen = next(row for row in result.current_scene_task["goals"] if row["id"] == selected)
            assert chosen["owner"] == "catgirl" and chosen["source_ids"] == ["goal.show_reading"]
        captured = deepcopy(data)
        result.current_scene_task["allowed_behaviors"].append("仅修改返回副本。")
        assert data == captured and engine.nodes["start"]["story_beat"] == source_before


def test_future_plan_is_separate_from_program_and_committed_source_blocks():
    sources = [{"id": "c0", "type": "dialogue", "text": "先核对眼前接线。", "phase": "turn"},
               {"id": "d0", "type": "route_decision", "text": PROGRAM_STATE,
                "transition_authorized": False}]
    kwargs = dict(required=["本轮不能进入后续阶段。"], candidate_text="先核对眼前接线。",
                  player_input="我已经到检修间了。", source_blocks=sources, check_departure_scope=True)
    old_messages = evaluator._build_contract_check_messages(**kwargs)
    messages = evaluator._build_contract_check_messages(**kwargs, departure_plan=PLAN)
    data = prompt_payload(messages[1].content)
    assert data["departure_plan"] == PLAN and data["sources"] == sources
    assert all(value not in json.dumps(data["sources"], ensure_ascii=False) for value in PLAN.values())
    assert "不是已发生证据" in messages[0].content and "d0程序权限" in messages[0].content
    assert "departure_plan" not in old_messages[0].content
    assert "departure_plan" not in prompt_payload(old_messages[1].content)
    assert [message.content for message in old_messages] == [message.content for message in
        evaluator._build_contract_check_messages(**kwargs, departure_plan=None)]


@pytest.mark.asyncio
@pytest.mark.parametrize("complete_scope", [False, True])
async def test_departure_confirmation_retains_future_plan_without_clearing_unchecked_body(monkeypatch, complete_scope):
    engine, session = _frame()
    body = "（指向桌边）我们先检查当前仪表。"
    candidates = []

    def answer(index, data):
        assert data["departure_plan"] == PLAN
        if index == 1:
            candidates.extend(row for row in data["sources"] if row["id"].startswith("c"))
            d0, = [row for row in data["sources"] if row["id"] == "d0"]
            assert d0["text"] == PROGRAM_STATE
            return {"violations": [], "departure_scope": [
                {"scope": "departed" if row["id"] == "c1" else "current_scene",
                 "candidate_ref": row["id"], "quote": row["text"]} for row in candidates]}
        assert index == 2
        d0, = [row for row in data["departure_sources"] if row["id"] == "d0"]
        assert d0["text"] == PROGRAM_STATE
        assert [row for row in data["departure_sources"] if row["id"].startswith("c")] == candidates
        scope = candidates if complete_scope else candidates[1:]
        return {"applicable": False, "confirmed": False,
                "departure_scope": [{"scope": "current_scene", "candidate_ref": row["id"], "quote": row["text"]}
                                    for row in scope]}

    requests = _offline(monkeypatch, answer)
    diagnostics = {}
    problems = await _VERIFY(evaluator.NumericV2MetricEvaluator(object()), node=engine.nodes["start"],
        actor_performance={"performance": body, "suggested_inputs": []}, player_input="我已经到检修间了。",
        session=session, include_all_segments=True, exit_mismatch_reason=PROGRAM_STATE,
        departure_attempt_observed=True, departure_plan=PLAN, diagnostics=diagnostics)
    assert len(requests) == 2 and bool(problems) is not complete_scope
    for messages, _, kwargs in requests:
        assert "departure_plan" in messages[0].content and "不是已发生证据" in messages[0].content
        assert "departure_scope" in kwargs["response_format"]["json_schema"]["schema"]["required"]
    assert diagnostics["departure_scope"] == ("current_scene" if complete_scope else "uncertain")


@pytest.mark.asyncio
async def test_future_plan_cannot_be_referenced_as_a_committed_prior(monkeypatch):
    engine, session = _frame()
    body = "（指向桌边）我们先检查当前仪表。"

    def answer(index, data):
        assert index == 1 and data["departure_plan"] == PLAN
        assert not any(row["id"] == "departure_plan" for row in data["sources"])
        causal_index = next(row["rule_index"] for row in data["author_boundaries"] if "必要的直接前因" in row["text"])
        candidates = [row for row in data["sources"] if row["id"].startswith("c")]
        return {"violations": [{"rule_index": causal_index, "candidate_ref": "c1", "quote": candidates[1]["text"],
                                "prior_ref": "departure_plan"}],
                "departure_scope": [{"scope": "current_scene", "candidate_ref": row["id"], "quote": row["text"]}
                                    for row in candidates]}

    requests = _offline(monkeypatch, answer)
    diagnostics = {}
    problems = await _VERIFY(evaluator.NumericV2MetricEvaluator(object()), node=engine.nodes["start"],
        actor_performance={"performance": body, "suggested_inputs": []}, player_input="我已经到检修间了。",
        session=session, include_all_segments=True, exit_mismatch_reason=PROGRAM_STATE,
        departure_attempt_observed=True, departure_plan=PLAN, diagnostics=diagnostics)
    assert not problems and len(requests) == 1 and diagnostics["grounded_findings"] == 0
