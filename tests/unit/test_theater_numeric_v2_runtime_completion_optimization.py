"""Regressions from complete routes: fixed completion items and receipt acceptance."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_actor as actor, numeric_v2_evaluator as ev
from services.theater import numeric_v2_options as options, numeric_v2_workflow as wf
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from services.theater.numeric_v2_compact_review import compact_schema, expand_report
from services.theater.numeric_v2_structured_output import contract_output_schema
from services.theater.numeric_v2_usage import invoke_with_usage
from tests.unit.test_theater_numeric_v2_fact_discovery import _story
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_authored_transition_recovery import _case, _install, KEY
from tests.unit.test_theater_numeric_v2_bound_completion_contract import _install as _audit_transport
from tests.unit.test_theater_numeric_v2_latency_optimization import _clear


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    pass


@pytest.mark.asyncio
async def test_soft_budget_handles_mixed_fact_and_fixed_narration_requirements(tmp_path, monkeypatch):
    story = _story()
    node = story["nodes"][0]
    node["recommended_turns"] = 2
    node["story_beat"]["fixed_narrations"] = [{
        "id": "queue_notice", "text": "柜台已经开始办理通行证核对。", "trigger": {"type": "entry"},
        "after": [], "required_before_exit": True}]
    node["completion_contract"]["all"].append({"fixed_narration_id": "queue_notice"})
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(session_id="mixed-completion", catgirl_binding=_binding(),
        opening_performance={"performance": "我在柜台旁等候。", "suggested_inputs": []})
    for index in range(node["recommended_turns"]):
        outcome = runtime.prepare_turn(current, TurnRequestV2(f"queued-{index}", index, "先等候。"), (),
            condition_narrations_enabled=False)
        current = await runtime.commit_turn(outcome, {"performance": "仍在等候。", "suggested_inputs": []})
    tasks = []

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        tasks.append(kwargs.get("current_scene_task"))
        return {"performance": "（留在柜台旁）核对还没完成，我们先等候。", "suggested_inputs": []}

    async def contract(self, **kwargs):
        return ()

    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(wf, "aload_theater_module_options", options.aload_theater_module_options)
    monkeypatch.setattr(wf, "_can_combine_ordinary_turn", lambda *args: False)
    monkeypatch.setattr(actor.NumericV2Actor, "_character_profile", lambda self: "认真。")
    monkeypatch.setattr(actor.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(ev.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(ev.NumericV2MetricEvaluator, "verify_contract_boundaries", contract)
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("wait", current.session.revision, "还要等多久？"), ensure_current_binding=lambda _: _binding())
    assert not result.diagnostics.get("safe_delivery_fallback")
    assert tasks and [row["key"] for row in tasks[0]["pending_results"]] == ["scene:start:done"]
    assert result.stored.session.current_node_id == "start"
    assert result.stored.session.story_state["facts"] == current.session.story_state["facts"]


async def _signed_case(tmp_path, monkeypatch, *, legacy=False):
    runtime, current = await _case(tmp_path)
    calls = _install(monkeypatch, runtime)
    source = runtime.engine.nodes[current.session.current_node_id]
    source["route_gates"][0]["transition_contract"].pop("advance_on_completion")
    source["route_gates"][0]["transition_contract"].update(
        fallback_offer="要一起到工作台另一侧继续吗？", accept_input="好，一起到工作台另一侧。")
    if legacy:
        source.pop("completion_contract")
        runtime.engine.fact_contract.clear()
    for index in range(source["min_turns"] - 1):
        waiting = runtime.prepare_turn(current, TurnRequestV2(f"wait-{index}", index, "先等等。"), (),
            condition_narrations_enabled=False)
        current = await runtime.commit_turn(waiting, {"performance": "我先在这里等候。", "suggested_inputs": []})
    ready = runtime.prepare_turn(current, TurnRequestV2("ready", current.session.revision, "我决定把设备留在这里。"), (),
        scene_complete=True, condition_narrations_enabled=False,
        fact_operations=() if legacy else ({"op": "set", "key": KEY, "value": True, "visibility": "public"},))
    performance = {"performance": "要一起到工作台另一侧继续吗？", "suggested_inputs": ["好，一起到工作台另一侧。"]}
    ready, performance = runtime.engine.finalize_transition_offer_state(ready, performance, new_offer=True)
    route = source["route_gates"][0]
    receipt = {"kind": "reviewed_dynamic", "revision": ready.session.revision, "route_id": route["id"],
        "target_node_id": route["target_node_id"], "offer": performance["performance"],
        "accept_input": performance["suggested_inputs"][0], "performance": performance["performance"],
        "visible_blocks": wf.performance_content_blocks(performance)}
    ready = replace(ready, ledger_event={**ready.ledger_event, "program_invitation": receipt})
    current = await runtime.commit_turn(ready, performance)
    current = await runtime.restore_session(current.session.session_id)
    return runtime, current, calls, receipt


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_current_signed_acceptance_uses_one_final_audit_without_another_intent_vote(tmp_path, monkeypatch, legacy):
    runtime, current, calls, receipt = await _signed_case(tmp_path, monkeypatch, legacy=legacy)

    async def no_model_intent(self, **kwargs):
        raise AssertionError("the current verified click already supplies acceptance")

    monkeypatch.setattr(ev.NumericV2MetricEvaluator, "evaluate", no_model_intent)
    monkeypatch.setattr(ev.NumericV2MetricEvaluator, "review_story_evidence", no_model_intent)
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("accept", current.session.revision, receipt["accept_input"], "suggestion"), ensure_current_binding=lambda _: _binding())
    assert result.diagnostics["evaluator_skip_reason"] == "verified_invitation_acceptance"
    assert result.stored.session.current_node_id == "work"
    assert calls == {"actor": 0, "contract": 1}
    assert result.stored.session.metrics == current.session.metrics
    assert await runtime.restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["not_ready", "branch", "pending_player_fact", "stale", "freeform"])
async def test_receipt_shortcut_keeps_completion_choice_and_current_input_guards(tmp_path, monkeypatch, changed):
    runtime, current, _, receipt = await _signed_case(tmp_path, monkeypatch)
    source = runtime.engine.nodes[current.session.current_node_id]
    turn = TurnRequestV2("accept", current.session.revision, receipt["accept_input"], "suggestion")
    assert wf._receipt_acceptance_only(runtime, current, turn, source["route_gates"][0]["id"])
    if changed == "branch":
        source["route_gates"].append(deepcopy(source["route_gates"][0]))
    elif changed == "stale":
        turn = replace(turn, base_revision=0)
    elif changed == "freeform":
        turn = replace(turn, input_source="freeform")
    elif changed == "pending_player_fact":
        source["route_gates"][0]["conditions"]["all"].append({"type": "fact_equals", "key": KEY, "value": False})
    elif changed == "not_ready":
        current = replace(current, session=replace(current.session, story_state={**current.session.story_state, "facts": {}}))
    assert not wf._receipt_acceptance_only(runtime, current, turn, source["route_gates"][0]["id"])


@pytest.mark.asyncio
async def test_combined_plain_body_still_gets_one_player_authority_audit(tmp_path, monkeypatch):
    runtime, current = await _case(tmp_path)
    source = runtime.engine.nodes[current.session.current_node_id]
    source["route_gates"][0]["transition_contract"].pop("advance_on_completion")
    source["route_gates"][0]["transition_contract"]["accept_input"] = "好，一起过去。"
    runtime.engine.fact_contract[KEY]["evidence_source"] = "performance"
    outcome = runtime.prepare_turn(current, TurnRequestV2("wait", 0, "我先在这里等候。"), (),
        condition_narrations_enabled=False)
    current = await runtime.commit_turn(outcome, {"performance": "好，我们先留在这里。", "suggested_inputs": []})

    async def no_separate_evaluation(self, **kwargs):
        raise AssertionError("this ordinary source shares the Actor call")

    async def generate(self, **kwargs):
        assert kwargs["combined_evaluation"] is True
        async def invoke(messages):
            return SimpleNamespace(content="draft")
        await invoke_with_usage(SimpleNamespace(ainvoke=invoke), [], stage="actor")
        return {"performance": "（指向工作台上的标签）这张标签写的是工具名称。", "suggested_inputs": [],
            "_turn_evaluation": {"metric_changes": {}, "scene_complete": False, "fact_candidates": []}}

    calls = _audit_transport(monkeypatch, lambda messages, data, kwargs: _clear(kwargs))
    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(wf, "aload_theater_module_options", options.aload_theater_module_options)
    monkeypatch.setattr(ev.NumericV2MetricEvaluator, "evaluate", no_separate_evaluation)
    monkeypatch.setattr(ev.NumericV2MetricEvaluator, "review_story_evidence", no_separate_evaluation)
    monkeypatch.setattr(actor.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(actor.NumericV2Actor, "_character_profile", lambda self: "认真。")
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("label", 1, "这张标签写的是什么？"), ensure_current_binding=lambda _: _binding())
    assert not result.diagnostics.get("safe_delivery_fallback")
    assert result.diagnostics["ordinary_turn_combined"] is True
    assert result.diagnostics["model_call_stages"] == ["actor", "contract"], result.diagnostics
    assert len(calls) == 1
    assert result.stored.session.current_node_id == current.session.current_node_id


@pytest.mark.parametrize("quote", ["我从工作台上坐起，双脚落地时发出轻微的金属撞击声。",
    "我拉着你走出了工作室。", "我替阿岚答应了委托。", "我让修理工签下了委托。"])
def test_first_person_or_default_actor_cannot_downgrade_an_explicit_violation(quote):
    schema = compact_schema(contract_output_schema(grounded=True, check_player_authority=True),
        rule_count=2, candidate_refs=["c0"], prior_refs=[""])
    source = {"type": "action", "default_actor_id": "active_catgirl", "text": "我望向你推近的椅子。" + quote}
    row = {"verdict": "violation", "rule_index": 1, "candidate_ref": "c0", "quote": quote, "prior_ref": ""}
    report = expand_report({"verdict": "violation", "findings": [row]}, schema,
        author_count=0, player_rule=1, fixed_rule=None, sources={"c0": source}, fixed_refs=[])
    assert report["player_authority"]["c0"] == [{"quote": quote, "authorization_ref": ""}]


def test_actor_context_cannot_clear_another_player_action_or_author_conflict():
    schema = compact_schema(contract_output_schema(grounded=True, check_player_authority=True),
        rule_count=3, candidate_refs=["c0"], prior_refs=[""])
    quotes = ["我从工作台上坐起。", "你签下了新协议。", "终端公开了后续剧情。"]
    source = {"type": "action", "default_actor_id": "active_catgirl", "text": "".join(quotes)}
    payload = {"verdict": "violation", "findings": [
        {"verdict": "violation", "rule_index": index, "candidate_ref": "c0", "quote": quote, "prior_ref": ""}
        for index, quote in zip((1, 1, 0), quotes)]}
    report = expand_report(payload, schema, author_count=1, player_rule=1, fixed_rule=None,
        sources={"c0": source}, fixed_refs=[])
    assert report["player_authority"]["c0"] == [{"quote": quote, "authorization_ref": ""} for quote in quotes[:2]]
    assert report["author_checks"]["violations"][0]["quote"] == quotes[2]


@pytest.mark.asyncio
async def test_no_author_prohibition_still_blocks_a_grounded_player_action(monkeypatch):
    from tests.unit.test_theater_numeric_v2_fact_discovery import _frame
    engine, session = _frame()
    quote = "你签下了委托协议。"
    def respond(messages, data, kwargs):
        payload = _clear(kwargs)
        player_rule = next(row["rule_index"] for row in data["author_boundaries"] if row["text"] == ev._PLAYER_AUTHORITY_RULE)
        payload.update(verdict="violation", findings=[{"verdict": "violation", "rule_index": player_rule,
            "candidate_ref": "c0", "quote": quote, "prior_ref": ""}])
        return payload
    calls = _audit_transport(monkeypatch, respond)
    problems = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=session, actor_performance={"performance": "（" + quote + "）"},
        player_input="先给我看看协议。", single_pass=True)
    assert problems and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["ready", "later_uncertain", "hold", "refused", "before_min",
    "fixed_pending", "explicit_deferral", "typed_unfinished", "different_visit"])
async def test_legacy_soft_budget_uses_only_current_committed_readiness(tmp_path, monkeypatch, case):
    runtime, current = await _case(tmp_path)
    calls = _install(monkeypatch, runtime)
    source = runtime.engine.nodes[current.session.current_node_id]
    definitions = deepcopy(runtime.engine.fact_contract)
    source.pop("completion_contract")
    runtime.engine.fact_contract.clear()
    source["recommended_turns"] = source["min_turns"]
    source["route_gates"][0]["transition_contract"].pop("advance_on_completion")
    source["route_gates"][0]["transition_contract"].update(
        fallback_offer="要一起到工作台另一侧继续吗？", accept_input="好，一起到工作台另一侧。")
    count = source["min_turns"] - (case == "before_min")
    for index in range(count):
        outcome = runtime.prepare_turn(current, TurnRequestV2(f"local-{index}", index, "这一项已经说清了。"), (),
            scene_complete=True, condition_narrations_enabled=False)
        current = await runtime.commit_turn(outcome, {"performance": "我已说清这一项。", "suggested_inputs": []})
    if case in {"later_uncertain", "hold", "refused"}:
        outcome = runtime.prepare_turn(current, TurnRequestV2("later", current.session.revision,
            "现在不去，我想再聊聊。" if case == "refused" else "再说一会儿。"), (),
            scene_complete=case == "refused", transition_intent="reject" if case == "refused" else "unclear",
            hold_current_scene=case == "hold", condition_narrations_enabled=False)
        current = await runtime.commit_turn(outcome, {"performance": "我先留在这里。", "suggested_inputs": []})
    if case == "fixed_pending":
        source["story_beat"]["fixed_narrations"] = [{"id": "undelivered", "text": "仍须播出的必要原文。",
            "trigger": {"type": "entry"}, "after": [], "required_before_exit": True}]
    if case == "typed_unfinished":
        runtime.engine.fact_contract.update(definitions)
        source["completion_contract"] = {"all": [{"key": KEY, "equals": True}]}
    if case == "different_visit":
        current.ledger_events[-1]["visit_started_revision"] = -1
    turn = TurnRequestV2("checkpoint", current.session.revision,
        "现在先别走，我想再聊聊。" if case == "explicit_deferral" else "接下来呢？")
    proposed = wf._checkpoint_invitation(runtime, current, turn)
    assert bool(proposed) is (case == "ready")
    if case != "ready":
        return
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=turn, ensure_current_binding=lambda _: _binding())
    assert result.stored.session.current_node_id == current.session.current_node_id
    assert calls == {"actor": 0, "contract": 0}
    assert result.stored.ledger_events[-1]["scene_complete"] is True
    restored = await runtime.restore_session(current.session.session_id)
    accepted = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=restored,
        turn=TurnRequestV2("accept", restored.session.revision, result.performance["suggested_inputs"][0], "suggestion"),
        ensure_current_binding=lambda _: _binding())
    assert accepted.stored.session.current_node_id == "work"
    assert accepted.diagnostics["evaluator_skip_reason"] == "verified_invitation_acceptance"
    assert calls == {"actor": 0, "contract": 1}
    assert accepted.stored.session.story_state["facts"].get(KEY) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("agreed", [True, False])
async def test_evaluator_and_body_audit_cannot_write_one_fact_twice_or_reverse_it(tmp_path, monkeypatch, agreed):
    from tests.unit.test_theater_numeric_v2_fact_discovery import _frame, KEY as FIRST, OTHER
    engine, _ = _frame(source="player_input", two=True)
    engine.fact_contract[FIRST]["description"] = "玩家已明确同意当前登记安排。"
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="overlapping-approval", catgirl_binding=_binding(),
        opening_performance={"performance": "请确认这份登记安排。", "suggested_inputs": []})
    message = "我同意现在的登记安排。" if agreed else "我不同意现在的登记安排。"

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), False,
            fact_operations=({"op": "set", "key": FIRST, "value": agreed, "visibility": "public"},),
            fact_audit=({"key": FIRST, "subject": "玩家", "action": "同意" if agreed else "拒绝",
                "object": "登记安排", "result": "已作决定",
                "evidence": [{"source": "player_input", "quote": message}]},))

    async def evidence(self, **kwargs):
        return ev.NumericV2StoryEvidenceReview(tuple(range(len(kwargs["claims"]))))

    async def generate(self, **kwargs):
        return {"scene_narration": "工作人员已完成独立的行李检查。", "performance": "我听清你的决定了。",
            "suggested_inputs": []}

    def audit(messages, data, kwargs):
        payload = _clear(kwargs)
        if "bound_completion_check" in data:
            payload["bound_completion_approved_indexes"] = [row["index"] for row in data["bound_completion_check"]["claims"]]
        return payload

    _audit_transport(monkeypatch, audit)
    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(wf, "aload_theater_module_options", options.aload_theater_module_options)
    monkeypatch.setattr(ev.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(ev.NumericV2MetricEvaluator, "review_story_evidence", evidence)
    monkeypatch.setattr(actor.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(actor.NumericV2Actor, "_character_profile", lambda self: "认真。")
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("decide", 0, message), ensure_current_binding=lambda _: _binding())
    if not agreed:
        assert result.diagnostics["safe_delivery_fallback"] is True
        assert result.stored.session.story_state["facts"] == current.session.story_state["facts"]
    else:
        assert not result.diagnostics.get("safe_delivery_fallback")
        operations = result.stored.ledger_events[-1]["fact_operations"]
        assert [row["key"] for row in operations].count(FIRST) == 1
        assert result.stored.session.story_state["facts"][OTHER]["value"] is True
        assert await runtime.restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize("handed", [False, True])
async def test_body_audit_cannot_grant_a_program_blocked_player_handoff(tmp_path, monkeypatch, handed):
    from tests.unit.test_theater_numeric_v2_handoff_suggestion import _start, KEY as HANDOFF
    runtime, current = await _start(tmp_path, "剑匣", [])
    message = "（我把剑匣递向保管员）请接好。" if handed else "信物已留，我们走吧。"
    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), False)
    async def generate(self, **kwargs):
        return {"performance": "我在旁边等候。", "suggested_inputs": [],
            "scene_narration": "保管员已伸手接稳剑匣。" if handed else "保管员仍在等候亲手交接。"}
    def audit(messages, data, kwargs):
        assert ("bound_completion_check" in data) is handed
        payload = _clear(kwargs)
        if handed:
            payload["bound_completion_approved_indexes"] = [0]
        return payload
    _audit_transport(monkeypatch, audit)
    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(wf, "aload_theater_module_options", options.aload_theater_module_options)
    monkeypatch.setattr(ev.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(actor.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(actor.NumericV2Actor, "_character_profile", lambda self: "认真。")
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("handoff", 0, message), ensure_current_binding=lambda _: _binding())
    assert not result.diagnostics.get("safe_delivery_fallback")
    assert (HANDOFF in result.stored.session.story_state["facts"]) is handed
    assert await runtime.restore_session(current.session.session_id) == result.stored
