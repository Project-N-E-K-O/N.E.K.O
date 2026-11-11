"""Shared source drafts may share evaluation, but only independent review permits entry."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_options as options, numeric_v2_workflow as wf
from services.theater.numeric_v2_actor_output import NumericV2ActorOutputError
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_shared_history_scheduling import agreement_engine
from tests.unit.test_theater_numeric_v2_source_delivery_routing import KEY


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    pass


def _current(engine):
    session = engine.create_session(session_id="combined-shared", catgirl_binding=_binding(),
        opening_performance={"performance": "先说说你的安排。", "suggested_inputs": []})
    return SimpleNamespace(session=session, ledger_events=())


@pytest.mark.parametrize("case", ["valid", "no_contract_review", "ending", "metric_route", "two_targets",
    "performance_only", "player_only", "multiple_requirements", "not_shared", "not_linked", "deferral",
    "left_scene", "old_invitation", "story_fact", "missing_entry_original", "other_current_fact", "global_fact"])
def test_only_unambiguous_shared_source_is_eligible(monkeypatch, case):
    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    engine = agreement_engine("双方已同意把设备留在原处。",
        owner="catgirl" if case == "not_shared" else "shared",
        delivery="catgirl_dialogue" if case == "not_shared" else "shared_agreement",
        linked=case != "not_linked", source={"performance_only": "performance", "player_only": "player_input"}.get(case))
    node = engine.nodes["start"]
    if case == "ending":
        node["route_gates"][0]["target_node_id"] = next(key for key, row in engine.nodes.items() if row.get("type") == "ending")
    elif case == "metric_route":
        node["route_gates"][0]["conditions"]["all"].append({"type": "metric_compare", "metric_id": "trust", "op": ">=", "value": 1})
    elif case == "two_targets":
        other = deepcopy(node["route_gates"][0]); other["id"] += "_other"
        other["target_node_id"] = next(key for key, row in engine.nodes.items() if row.get("type") == "ending")
        node["route_gates"].append(other)
    elif case == "multiple_requirements":
        node["completion_contract"]["all"].append({"key": KEY, "equals": False})
    elif case == "story_fact": engine.fact_contract[KEY]["visibility"] = "story"
    elif case in {"other_current_fact", "global_fact"}:
        other = "scene:start:player_chose" if case == "other_current_fact" else "player_chose"
        engine.fact_contract[other] = {"value_type": "bool", "visibility": "public", "evidence_source": "player_input"}
    current = _current(engine)
    if case == "missing_entry_original":
        node["story_beat"]["fixed_narrations"] = [{"id": "pending", "text": "尚未播出的必要原文。",
            "trigger": {"type": "entry"}, "after": [], "required_before_exit": True}]
    elif case == "old_invitation": current.session = replace(current.session, transition_offered=True)
    message = {"deferral": "先别处理，我们再商量。", "left_scene": "（我离开了当前房间）"}.get(case, "我建议留在这里，你同意吗？")
    modules = {"evaluator": True, "review_contract": case != "no_contract_review"}
    assert wf._can_combine_ordinary_turn(SimpleNamespace(engine=engine), current,
        TurnRequestV2("proposal", 0, message), modules) is (case == "valid")


@pytest.mark.asyncio
@pytest.mark.parametrize("topic", ["equipment", "archive"])
@pytest.mark.parametrize("mode", ["agree", "reject", "only_request", "no_actor_candidate", "evaluator_claim",
    "evaluator_false", "invalid_evaluation", "target_failure"])
async def test_combined_source_waits_for_independent_proof_and_commits_once(tmp_path, monkeypatch, topic, mode):
    noun = "设备" if topic == "equipment" else "档案"
    engine = agreement_engine(f"双方已同意将{noun}留在原处。")
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id=topic, catgirl_binding=_binding(),
        opening_performance={"performance": "我们还没有约定。", "suggested_inputs": []})
    path = runtime.store._path(topic); original = path.read_bytes()
    message = f"我建议将{noun}留在原处，你同意吗？"
    if mode == "evaluator_false": message = f"我不同意将{noun}留在原处。"
    role = f"我同意，就将{noun}留在原处。" if mode != "only_request" else "你希望我同意吗？"
    approved = mode not in {"reject", "only_request", "invalid_evaluation", "evaluator_false"}
    calls = []

    async def forbidden(*args, **kwargs):
        raise AssertionError("safe combined source must not invoke a separate Evaluator or S")

    async def fallback_review(self, **kwargs):
        assert mode in {"invalid_evaluation", "evaluator_false"}
        assert path.read_bytes() == original and KEY not in kwargs["session"].story_state["facts"]
        calls.append("separate_fallback")
        if mode == "evaluator_false":
            assert kwargs["claims"][0]["value"] is False
            return ev.NumericV2StoryEvidenceReview((0,))
        return ev.NumericV2StoryEvidenceReview()

    async def generate(self, **kwargs):
        assert path.read_bytes() == original
        if kwargs["outcome"].route is None:
            assert KEY not in kwargs["outcome"].session.story_state["facts"]
            assert kwargs["combined_evaluation"] is True
            assert kwargs["source_delivery_task"]["kind"] == "shared_agreement"
            assert kwargs["source_delivery_task"]["pending_results"] == [{"key": KEY, "value": True,
                "description": engine.fact_contract[KEY]["description"]}]
            calls.append("source_with_evaluation")
            candidate = {"key": KEY, "value": True, "evidence_quote": role}
            return {"performance": role, "suggested_inputs": [],
                "fact_candidates": [] if mode in {"no_actor_candidate", "evaluator_false"} else [candidate],
                "_turn_evaluation": {"scene_complete": True,
                    "metric_changes": {"trust": {} if mode == "invalid_evaluation" else {
                        "strength": "weak", "criterion_id": "trust.increase.1"}},
                    "fact_candidates": [{"key": KEY, "value": mode != "evaluator_false", "evidence_quote": message}]
                        if mode in {"evaluator_claim", "evaluator_false"} else []}}
        assert KEY in kwargs["outcome"].session.story_state["facts"]
        assert kwargs["transition_source"]["performance"] == role
        assert not kwargs.get("source_delivery_task") and not kwargs.get("combined_evaluation")
        calls.append("target")
        if mode == "target_failure": raise NumericV2ActorOutputError("test_target_failed")
        return engine.finalize_transition_performance(kwargs["outcome"], {
            "source_performance": role, "bridge_scene_narration": "", "target_scene_narration": "下一项整理开始。",
            "target_performance": "我们按说好的安排继续。", "suggested_inputs": [],
        }, target_opening="下一项整理开始。", bridge_required=False)

    async def contract(self, **kwargs):
        assert path.read_bytes() == original
        if kwargs["actor_performance"].get("segments"):
            calls.append("target_review")
            assert kwargs.get("bound_completion_batch") is None
        else:
            calls.append("source_review")
            batch = kwargs.get("bound_completion_batch")
            if mode in {"invalid_evaluation", "evaluator_false"}:
                assert batch is None
                return ()
            batch.validate()
            claim, = batch.claims
            assert claim["evidence"][0] == {"source": "player_input", "quote": message}
            assert claim["evidence"][-1] == {"source": "actor_performance", "quote": role}
            assert KEY not in kwargs["session"].story_state["facts"]
            kwargs["bound_completion_sink"].append((0,) if approved else ())
        return ()

    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(wf, "aload_theater_module_options", options.aload_theater_module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", forbidden)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "review_story_evidence", fallback_review)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", contract)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "沉稳。")
    kwargs = dict(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("shared-combined", 0, message), ensure_current_binding=lambda _: _binding())
    if mode == "target_failure":
        with pytest.raises(NumericV2ActorOutputError): await wf.execute_numeric_v2_turn(**kwargs)
        assert path.read_bytes() == original
        return
    result = await wf.execute_numeric_v2_turn(**kwargs)
    assert calls == (["source_with_evaluation", "separate_fallback", "source_review"] if mode == "evaluator_false"
        else ["source_with_evaluation", "source_review"] + (["target", "target_review"] if approved
            else ["separate_fallback"] if mode == "invalid_evaluation" else []))
    assert result.diagnostics["evaluator_model_attempts"] == 0
    assert result.diagnostics["ordinary_turn_combined"] is True
    assert result.stored.session.current_node_id == ("work" if approved else "start")
    assert result.stored.session.revision == 1
    assert len([row for row in result.stored.ledger_events if row["from_node_id"] != row["to_node_id"]]) == int(approved)
    if mode == "evaluator_false": assert result.stored.session.story_state["facts"][KEY]["value"] is False
    else: assert (KEY in result.stored.session.story_state["facts"]) is approved
    assert result.stored.session.metrics["trust"] == (current.session.metrics["trust"] if mode == "invalid_evaluation"
        else current.session.metrics["trust"] + 1)
