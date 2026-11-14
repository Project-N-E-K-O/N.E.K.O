"""Only an authorized, self-contained author entry can replace an unusable draft."""

from copy import deepcopy
from dataclasses import replace

import pytest

from services.theater import numeric_v2_options as options, numeric_v2_workflow as wf
from services.theater.numeric_v2_evaluator import NumericV2EvaluationResult, NumericV2EvaluatorError, NumericV2StoryEvidenceReview
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_source_delivery_routing import _engine, KEY, INPUT, AGREED


ENTRY = "作者原文：{{catgirl_name}}打开工作台旁的小灯，清点区域已经准备好。"
TARGET = "清点台另一侧摆着贴好标签的纸箱。"
SPOOF = "scene:start:unreviewed"


async def _case(tmp_path, *, source_first=False, flag=True):
    story = deepcopy(_engine(None if source_first else "player_input").story)
    story["fact_contract"]["facts"][SPOOF] = {
        "value_type": "bool", "visibility": "public", "evidence_source": "performance",
        "description": "尚未获准的秘密结果已经发生。",
    }
    target = next(node for node in story["nodes"] if node["id"] == "work")
    target["story_beat"]["opening_scene"] = TARGET
    target["story_beat"]["acting_contract"] = {
        "cognition_state": "normal", "memory_state": "available", "self_reference_mode": "persona_allowed",
        "persona_scope": "full", "allowed_behaviors": [], "forbidden_behaviors": [], "dialogue_policy": "optional",
    }
    target["story_beat"]["fixed_narrations"] = [{
        "id": "work_entry", "text": ENTRY, "trigger": {"type": "entry"},
        "after": [], "required_before_exit": True,
    }]
    route = story["nodes"][0]["route_gates"][0]
    route["transition_contract"].update(fixed_entry_only=flag,
        bridge_scene_narration="两人走到工作台另一侧。", bridge_required=True)
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="author-recovery", catgirl_binding=_binding(),
        opening_performance={"performance": "先商量设备去向。", "suggested_inputs": []})
    return runtime, current


def _install(monkeypatch, runtime, *, failure="numeric_v2_contract_check_timeout", source_first=False,
             bad_tail=False, generation_failure=None, generation_failure_after=0):
    calls = {"actor": 0, "contract": 0}

    async def evaluate(self, **kwargs):
        if kwargs["session"].current_node_id != "start":
            return NumericV2EvaluationResult((), False)
        return NumericV2EvaluationResult((), True,
            fact_operations=({"op": "set", "key": KEY, "value": True, "visibility": "public"},),
            fact_audit=({"key": KEY, "subject": "player", "action": "decide", "object": "equipment",
                         "result": "chosen", "evidence": [{"source": "player_input", "quote": INPUT}]},))

    async def approve(self, **kwargs):
        return NumericV2StoryEvidenceReview(tuple(range(len(kwargs["claims"]))))

    async def generate(self, **kwargs):
        calls["actor"] += 1
        outcome = kwargs["outcome"]
        if outcome.route is None:
            return {"performance": AGREED if source_first else "清点区域的小灯还亮着。", "suggested_inputs": []}
        if generation_failure and calls["actor"] > generation_failure_after:
            error_type = wf.NumericV2ActorOutputError if generation_failure == "numeric_v2_actor_fields_invalid" else wf.NumericV2ActorError
            raise error_type(generation_failure)
        source = kwargs.get("transition_source", {}).get("performance", "未审来源独有内容。")
        return runtime.engine.finalize_transition_performance(outcome, {
            "source_performance": source, "bridge_scene_narration": TARGET if bad_tail else "两人走到工作台另一侧。",
            "target_scene_narration": "", "target_performance": "", "suggested_inputs": ["未审按钮。"],
            "fact_candidates": [{"key": SPOOF, "value": True, "evidence_quote": "未审来源独有内容。"}],
        }, target_opening="", bridge_required=True, target_dialogue_policy=outcome.session.dialogue_policy)

    async def contract(self, **kwargs):
        calls["contract"] += 1
        if kwargs.get("target_node") is None:
            return ()
        candidate = kwargs["actor_performance"]
        if (failure != "fixed_original" and not candidate.get("fact_candidates")
                and not candidate.get("suggested_inputs") and "_authored_recovery" not in candidate):
            # An author candidate still needs its own successful review; only
            # the discarded Actor draft has the configured failure below.
            return ()
        if failure in {"rejected", "fixed_original"}:
            if failure == "fixed_original":
                kwargs["diagnostics"]["findings"] = [{"candidate": {"program_fixed_original": True}}]
            return ("来源稿仍未符合作者约束。",)
        raise NumericV2EvaluatorError(failure)

    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(wf, "aload_theater_module_options", options.aload_theater_module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "review_story_evidence", approve)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", contract)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "温和克制。")
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["numeric_v2_contract_check_timeout", "numeric_v2_contract_check_input_over_budget", "rejected"])
async def test_author_entry_replaces_failed_draft_and_can_continue(tmp_path, monkeypatch, failure):
    runtime, current = await _case(tmp_path)
    calls = _install(monkeypatch, runtime, failure=failure)
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("recover", 0, INPUT), ensure_current_binding=lambda _: _binding())
    assert result.stored.session.current_node_id == "work"
    assert result.stored.session.revision == 1
    assert result.stored.session.story_state["facts"][KEY]["value"] is True
    assert SPOOF not in result.stored.session.story_state["facts"]
    assert result.performance["segments"][0]["performance"] == ""
    assert result.performance["segments"][1]["scene_narration"] == "两人走到工作台另一侧。"
    assert result.performance["segments"][2]["scene_narration"] == ""
    assert result.performance["segments"][2]["performance"] == ""
    assert _binding()["catgirl_name"] in result.performance["segments"][2]["fixed_narrations"][0]["text"]
    assert result.performance["suggested_inputs"] == []
    assert "未审" not in str(result.performance)
    assert calls == {"actor": 2 if failure == "rejected" else 1,
                     "contract": 2}
    restored = await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id)
    assert restored == result.stored
    next_result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=restored,
        turn=TurnRequestV2("continue", 1, "我先看看准备好的清点区。"), ensure_current_binding=lambda _: _binding())
    assert next_result.stored.session.revision == 2
    assert len(next_result.stored.ledger_events) == 2


@pytest.mark.asyncio
async def test_failed_source_first_tail_cannot_add_a_third_author_review(tmp_path, monkeypatch):
    runtime, current = await _case(tmp_path, source_first=True)
    calls = _install(monkeypatch, runtime, source_first=True)
    before = runtime.store._path(current.session.session_id).read_bytes()
    with pytest.raises(wf.NumericV2ActorOutputError, match="contract_check_failed"):
        await wf._execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2("source-first", 0, INPUT), ensure_current_binding=lambda _: _binding())
    assert calls == {"actor": 2, "contract": 2}
    assert runtime.store._path(current.session.session_id).read_bytes() == before


@pytest.mark.asyncio
async def test_bad_source_first_tail_cannot_skip_the_author_candidate_review(tmp_path, monkeypatch):
    runtime, current = await _case(tmp_path, source_first=True)
    calls = _install(monkeypatch, runtime, source_first=True, bad_tail=True)
    before = runtime.store._path(current.session.session_id).read_bytes()
    with pytest.raises(wf.NumericV2ActorOutputError, match="contract_check_failed"):
        await wf._execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2("bad-tail", 0, INPUT), ensure_current_binding=lambda _: _binding())
    assert calls == {"actor": 2, "contract": 2}
    assert runtime.store._path(current.session.session_id).read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("source_first", [False, True])
@pytest.mark.parametrize("failure", ["numeric_v2_actor_timeout", "numeric_v2_actor_fields_invalid"])
async def test_actor_generation_failure_uses_the_same_author_receipt(tmp_path, monkeypatch, source_first, failure):
    runtime, current = await _case(tmp_path, source_first=source_first)
    calls = _install(monkeypatch, runtime, source_first=source_first, generation_failure=failure)
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("actor-failed", 0, INPUT), ensure_current_binding=lambda _: _binding())
    assert result.stored.session.current_node_id == "work"
    assert result.performance["segments"][0]["performance"] == (AGREED if source_first else "")
    assert calls["contract"] == 1 + int(source_first)
    if source_first:
        assert result.stored.ledger_events[-1]["routing_fact_operations"][0]["key"] == KEY
        assert result.stored.session.story_state["facts"][KEY]["value"] is True
    assert result.diagnostics["authored_transition_recoveries"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("source_first", [False, True])
async def test_contract_rejection_then_actor_rewrite_failure_keeps_author_delivery(tmp_path, monkeypatch, source_first):
    runtime, current = await _case(tmp_path, source_first=source_first)
    calls = _install(monkeypatch, runtime, source_first=source_first, failure="rejected",
        generation_failure="numeric_v2_actor_timeout", generation_failure_after=1 + int(source_first))
    if source_first:
        before = runtime.store._path(current.session.session_id).read_bytes()
        with pytest.raises(wf.NumericV2ActorError, match="actor_timeout"):
            await wf._execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
                turn=TurnRequestV2("rewrite-failed", 0, INPUT), ensure_current_binding=lambda _: _binding())
        assert calls == {"actor": 3, "contract": 2}
        assert runtime.store._path(current.session.session_id).read_bytes() == before
        return
    result = await wf._execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("rewrite-failed", 0, INPUT), ensure_current_binding=lambda _: _binding())
    assert result.stored.session.current_node_id == "work"
    assert result.performance["segments"][0]["performance"] == (AGREED if source_first else "")
    assert result.diagnostics["semantic_rewrite_attempts"] == 1
    assert result.diagnostics["authored_transition_recoveries"] == 1
    assert calls == {"actor": 2, "contract": 2}


@pytest.mark.asyncio
async def test_confirmed_source_violation_cannot_be_hidden_by_an_author_tail(tmp_path, monkeypatch):
    runtime, current = await _case(tmp_path, source_first=True)
    _install(monkeypatch, runtime, source_first=True)
    before = runtime.store._path(current.session.session_id).read_bytes()

    async def contract(self, **kwargs):
        if kwargs.get("target_node") is None:
            return ()
        kwargs["diagnostics"]["findings"] = [{"candidate": {"phase": "source_response"}}]
        return ("已绑定来源正文存在明确违规。",)

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", contract)
    with pytest.raises(wf.NumericV2ActorOutputError, match="contract_violation"):
        await wf._execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2("bad-source", 0, INPUT), ensure_current_binding=lambda _: _binding())
    assert runtime.store._path(current.session.session_id).read_bytes() == before


@pytest.mark.asyncio
async def test_no_author_mark_cannot_turn_a_rejected_dynamic_draft_into_approval(tmp_path, monkeypatch):
    runtime, current = await _case(tmp_path, flag=False)
    _install(monkeypatch, runtime)
    before = runtime.store._path(current.session.session_id).read_bytes()
    with pytest.raises(wf.NumericV2ActorOutputError, match="contract_check_failed"):
        await wf._execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2("no-authority", 0, INPUT), ensure_current_binding=lambda _: _binding())
    assert runtime.store._path(current.session.session_id).read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("guard", ["identity", "write_fence"])
async def test_recovery_still_obeys_identity_and_write_fences(tmp_path, monkeypatch, guard):
    runtime, current = await _case(tmp_path)
    _install(monkeypatch, runtime)
    before = runtime.store._path(current.session.session_id).read_bytes()
    binding_checks = []

    def binding(_):
        binding_checks.append(True)
        if guard == "identity" and len(binding_checks) > 1:
            raise ValueError("character_changed_requires_retry")
        return _binding()

    async def fence():
        if guard == "write_fence":
            raise RuntimeError("storage_maintenance")

    with pytest.raises((ValueError, RuntimeError), match="character_changed|storage_maintenance"):
        await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2("guard", 0, INPUT), ensure_current_binding=binding, before_commit=fence)
    assert runtime.store._path(current.session.session_id).read_bytes() == before
    assert len(binding_checks) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["suggested_inputs", "fact_candidates", "_authored_recovery"])
async def test_metadata_never_grants_state_or_skips_body_review(tmp_path, monkeypatch, field):
    runtime, current = await _case(tmp_path)
    calls = _install(monkeypatch, runtime)
    finalize_original = runtime.engine.finalize_transition_offer_state
    tampered = []

    def tamper(outcome, candidate, **kwargs):
        outcome, result = finalize_original(outcome, candidate, **kwargs)
        if result.get("segments") and result["segments"][0].get("performance") == "":
            result = deepcopy(result)
            result[field] = (["新按钮。"] if field == "suggested_inputs" else
                [{"key": SPOOF, "value": True, "evidence_quote": "作者原文"}] if field == "fact_candidates" else True)
            tampered.append(result[field])
        return outcome, result

    monkeypatch.setattr(runtime.engine, "finalize_transition_offer_state", tamper)
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("tamper", 0, INPUT), ensure_current_binding=lambda _: _binding())
    assert tampered
    assert result.stored.session.current_node_id == "work"
    assert result.stored.session.revision == 1
    assert SPOOF not in result.stored.session.story_state["facts"]
    assert calls["contract"] == 2
    assert result.diagnostics["authored_transition_recoveries"] == 1


@pytest.mark.asyncio
async def test_author_original_conflict_cannot_be_reinstalled_as_its_own_recovery(tmp_path, monkeypatch):
    runtime, current = await _case(tmp_path)
    _install(monkeypatch, runtime, failure="fixed_original")
    before = runtime.store._path(current.session.session_id).read_bytes()
    with pytest.raises(wf.NumericV2ActorOutputError, match="contract_violation"):
        await wf._execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2("fixed-conflict", 0, INPUT), ensure_current_binding=lambda _: _binding())
    assert runtime.store._path(current.session.session_id).read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["route", "entry", "condition_only", "already_displayed", "required_policy"])
async def test_recovery_requires_an_authorized_pending_independent_entry(tmp_path, missing):
    runtime, current = await _case(tmp_path)
    outcome = runtime.prepare_turn(current, TurnRequestV2("ready", 0, INPUT), (), scene_complete=True,
        fact_operations=({"op": "set", "key": KEY, "value": True, "visibility": "public"},))
    assert outcome.route is not None
    target = runtime.engine.nodes["work"]
    if missing == "route":
        outcome = replace(outcome, route=None)
    elif missing == "entry":
        target["story_beat"].pop("fixed_narrations")
    elif missing == "condition_only":
        target["story_beat"]["fixed_narrations"][0]["trigger"] = {"type": "condition", "condition": "玩家确认后展示。"}
    elif missing == "required_policy":
        outcome = replace(outcome, session=replace(outcome.session, dialogue_policy="required"))
    else:
        # The original was displayed during an earlier visit, not a new entry grant.
        current = replace(current, session=replace(current.session, performance_history=({
            "fixed_narrations": [{"node_id": "work", "id": "work_entry"}],
        },)))
    assert wf._authored_transition_recovery(runtime.engine, current, outcome) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("text,allowed", [
    ("作者原文：工作已经结束，灯光映着整理好的清单。", True),
    ("作者原文：你接下来还想做什么？", False),
    ("作者原文：请把清单送到门口的信箱。", False),
])
async def test_terminal_recovery_rejects_new_questions_and_tasks_in_author_text(tmp_path, text, allowed):
    runtime, current = await _case(tmp_path)
    outcome = runtime.prepare_turn(current, TurnRequestV2("terminal", 0, INPUT), (), scene_complete=True,
        fact_operations=({"op": "set", "key": KEY, "value": True, "visibility": "public"},))
    runtime.engine.nodes["work"]["type"] = "ending"
    runtime.engine.nodes["work"]["story_beat"]["fixed_narrations"][0]["text"] = text
    outcome = replace(outcome, session=replace(outcome.session, status="ended"))
    assert (wf._authored_transition_recovery(runtime.engine, current, outcome) is not None) is allowed
