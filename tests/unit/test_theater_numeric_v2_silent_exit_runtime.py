"""Silent exits expose an explicit player choice without adding character prose."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as wf
from services.theater.numeric_v2_options import default_options
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, NumericV2RuntimeError, TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_silent_completion_offer import _story
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


async def setup(tmp_path, monkeypatch, *, genre="scifi", review=True, mode="on", complete=True,
                binding=None, silent_choice=None):
    story = _story(genre)
    destination = "观测舱入口" if genre == "scifi" else "石桥入口"
    accept = f"我同意前往{destination}，安排是：继续旅程。"
    contract = story["nodes"][0]["route_gates"][1]["transition_contract"]
    contract["accept_input"] = "好，就按刚才的安排。"
    contract["silent_choice"] = silent_choice or {"destination": destination, "arrangement": "继续旅程"}
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="silent", catgirl_binding=binding or _binding(),
        opening_performance={"performance": "（望向出口）", "suggested_inputs": []})
    if complete:
        prepared = runtime.prepare_turn(current, TurnRequestV2("done", 0, "办好了。"), (),
            fact_operations=({"op": "set", "key": "scene:start:done", "value": True, "visibility": "public"},))
        current = await runtime.commit_turn(prepared, {"performance": "（放下工具）", "suggested_inputs": []})
    calls = []

    async def options():
        return {**default_options(), "review": review, "evaluator": mode != "off",
                "actor_retry": False, "suggestion_fill": False}

    async def evaluate(self, **kwargs):
        if mode == "failure":
            raise ev.NumericV2EvaluatorError("numeric_v2_evaluator_invalid_json")
        return ev.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        outcome = kwargs["outcome"]
        if outcome.ledger_event["from_node_id"] != outcome.ledger_event["to_node_id"]:
            candidate = {**_candidate(), "source_performance": "（望向出口）"}
            return engine.finalize_transition_performance(outcome, candidate, target_opening="到达入口。",
                source_dialogue_policy="forbidden")
        return {"performance": "（看向远处的灯光）", "suggested_inputs": ["那我们这就出发吧。"], "transition_offered": False}

    async def review_offer(self, **kwargs):
        calls.append(kwargs)
        return ev.NumericV2TransitionOfferReview(False, False, (), (),
            reviewed_suggestion_texts=tuple(kwargs["actor_performance"].get("suggested_inputs", [])),
            pending_invitation_invalid=False if kwargs.get("confirmed_acceptance") else None,
            acceptance_authorized=True if kwargs.get("route_changed") else None,
            delivery_matches_route=True if kwargs.get("route_changed") else None)

    async def propose(self, **kwargs):
        return []

    monkeypatch.setattr(wf, "aload_theater_module_options", options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "validate_transition_offer", review_offer)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "温和。")
    monkeypatch.setattr(wf.NumericV2NarrativeProposer, "propose", propose)
    return runtime, current, accept, calls


async def turn(runtime, current, key, message, source="freeform"):
    return await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2(key, current.session.revision, message, source),
        ensure_current_binding=lambda _: _binding())


@pytest.mark.asyncio
@pytest.mark.parametrize("genre", ["scifi", "fantasy"])
@pytest.mark.parametrize("review,mode", [(True, "on"), (False, "on"), (True, "failure"), (False, "off"), (True, "off")])
async def test_silent_button_is_visible_and_click_survives_restore_and_fork(tmp_path, monkeypatch, genre, review, mode):
    runtime, current, accept, calls = await setup(tmp_path, monkeypatch, genre=genre, review=review, mode=mode)
    offered = await turn(runtime, current, "offer", "接下来呢？")
    assert offered.performance["performance"] == "（看向远处的灯光）"
    assert offered.performance["suggested_inputs"] == [accept]
    assert offered.stored.ledger_events[-1]["transition_offer_source"] == "program_silent"
    assert offered.stored.session.transition_offered
    assert offered.stored.session.dialogue_policy == "optional"
    receipt = offered.stored.ledger_events[-1]["program_invitation"]
    assert receipt["silent"] is True and receipt["offer"] == ""
    assert receipt["revision"] == offered.stored.session.revision
    assert len(calls) == int(review)
    restored = await NumericV2Runtime(runtime.engine, tmp_path).restore_session("silent")
    assert restored == offered.stored
    fork = await runtime.fork_session_for_test("silent", session_id="silent-fork", through_revision=restored.session.revision)
    assert fork.ledger_events[-1]["program_invitation"] == receipt
    assert fork.ledger_events[-1]["transition_offer_source"] == "program_silent"
    assert fork.session.performance_history[-1]["suggested_inputs"] == restored.session.performance_history[-1]["suggested_inputs"]
    for stored, key in [(restored, "accept"), (fork, "fork-accept")]:
        accepted = await turn(runtime, stored, key, accept, "suggestion")
        assert accepted.stored.session.current_node_id == "ending_leave"
        assert accepted.stored.ledger_events[-1]["source_transition_dialogue_policy"] == "forbidden"
        assert accepted.performance["segments"][0]["performance"] == "（望向出口）"
        assert await runtime.restore_session(stored.session.session_id) == accepted.stored
    if review:
        assert calls[-1]["confirmed_silent_acceptance"] == accept


@pytest.mark.asyncio
@pytest.mark.parametrize("genre", ["scifi", "fantasy"])
async def test_fixed_narration_completed_this_turn_publishes_button_immediately(tmp_path, monkeypatch, genre):
    from tests.unit.test_theater_numeric_v2_fixed_narration import _piece

    runtime, current, accept, _ = await setup(tmp_path, monkeypatch, genre=genre)
    node = runtime.engine.nodes["start"]
    node["story_beat"]["fixed_narrations"] = [_piece("closing", "记录已封存。")]
    node["completion_contract"] = {"all": [{"fixed_narration_id": "closing"}]}

    async def review(self, **kwargs):
        return ev.NumericV2TransitionOfferReview(False, False, (), (),
            reviewed_suggestion_texts=tuple(kwargs["actor_performance"].get("suggested_inputs", [])),
            fixed_narration_triggers=({"id": "closing", "evidence": "记录封存。"},))

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "validate_transition_offer", review)
    result = await turn(runtime, current, "complete-fixed", "记录封存。")
    assert result.performance["fixed_narrations"][0]["text"] == "记录已封存。"
    assert runtime.engine.completion_contract_satisfied(result.stored.session) is True
    assert result.performance["suggested_inputs"][0] == accept
    assert result.stored.session.transition_offered
    restored = await runtime.restore_session("silent")
    assert restored == result.stored


@pytest.mark.asyncio
async def test_completion_confirmed_this_turn_publishes_button_immediately(tmp_path, monkeypatch):
    runtime, current, accept, _ = await setup(tmp_path, monkeypatch, review=False, complete=False)

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), False, fact_operations=(
            {"op": "set", "key": "scene:start:done", "value": True, "visibility": "public"},))

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    result = await turn(runtime, current, "complete-now", "办好了。")
    assert result.performance["suggested_inputs"][0] == accept
    assert result.stored.session.transition_offered


@pytest.mark.asyncio
@pytest.mark.parametrize("review", [False, True])
async def test_expiry_never_auto_reissues_but_explicit_reinvitation_restores_only_button(tmp_path, monkeypatch, review):
    runtime, current, accept, _ = await setup(tmp_path, monkeypatch, review=review)
    offered = await turn(runtime, current, "offer", "接下来呢？")
    expired = await turn(runtime, offered.stored, "follow", "先问个问题。")
    assert not expired.stored.session.transition_offered
    assert accept not in expired.performance["suggested_inputs"]
    assert "program_invitation" not in expired.stored.ledger_events[-1]
    again = await turn(runtime, expired.stored, "again", "继续等。")
    assert not again.stored.session.transition_offered
    assert "program_invitation" not in again.stored.ledger_events[-1]
    revived = await turn(runtime, again.stored, "reinvite", "重新邀请", "reinvite")
    assert revived.performance["performance"] == ""
    assert revived.performance["suggested_inputs"] == [accept]
    assert revived.stored.session.node_turn_count == again.stored.session.node_turn_count
    restored = await runtime.restore_session("silent")
    assert restored == revived.stored
    fork = await runtime.fork_session_for_test("silent", session_id="revived-fork", through_revision=restored.session.revision)
    for stored, key in [(restored, "accept"), (fork, "fork-accept")]:
        accepted = await turn(runtime, stored, key, accept, "suggestion")
        assert accepted.stored.session.current_node_id == "ending_leave"
        assert accepted.stored.ledger_events[-1]["source_transition_dialogue_policy"] == "forbidden"
        assert accepted.performance["segments"][0]["performance"] == "（望向出口）"
        assert await runtime.restore_session(stored.session.session_id) == accepted.stored


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["missing", "route", "revision", "accept", "body", "button", "source"])
async def test_silent_acceptance_rejects_stale_or_modified_authorization(tmp_path, monkeypatch, mutation):
    runtime, current, accept, _ = await setup(tmp_path, monkeypatch)
    offered = await turn(runtime, current, "offer", "接下来呢？")
    current = offered.stored
    events, history = deepcopy(current.ledger_events), deepcopy(current.session.performance_history)
    receipt = events[-1]["program_invitation"]
    if mutation == "missing":
        events[-1].pop("program_invitation")
    elif mutation == "route":
        receipt["route_id"] = "wrong-route"
    elif mutation == "revision":
        receipt["revision"] -= 1
    elif mutation == "accept":
        receipt["accept_input"] = "好。"
    elif mutation == "body":
        history[-1]["performance"] = "（转向另一个出口）"
    elif mutation == "button":
        history[-1]["suggested_inputs"] = []
    current = replace(current, ledger_events=events, session=replace(current.session, performance_history=history))
    request = TurnRequestV2("accept", current.session.revision, accept, "freeform" if mutation == "source" else "suggestion")
    assert wf._confirmed_authored_acceptance(runtime.engine, current, request) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("guard", ["incomplete", "speaking", "ending", "leaving", "reject", "body"])
async def test_ineligible_turn_never_issues_silent_button(tmp_path, monkeypatch, guard):
    runtime, current, accept, _ = await setup(tmp_path, monkeypatch, complete=guard != "incomplete")
    if guard == "speaking":
        runtime.engine.nodes["start"]["story_beat"]["goals"][-1]["delivery"]["state_effects"]["dialogue_policy"] = "optional"
    if guard == "ending":
        runtime.engine.nodes["ending_leave"]["terminal"] = True
    if guard == "reject":
        async def evaluate(self, **kwargs):
            return ev.NumericV2EvaluationResult((), False, transition_intent="reject")
        monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    if guard == "body":
        async def review(self, **kwargs):
            return ev.NumericV2TransitionOfferReview(False, False, ("author_boundary",), (), failure_reason="越界。")
        monkeypatch.setattr(wf.NumericV2MetricEvaluator, "validate_transition_offer", review)
    result = await turn(runtime, current, "guard", "我们走向门口。" if guard == "leaving" else "接下来呢？")
    assert "program_invitation" not in result.stored.ledger_events[-1]
    assert accept not in result.performance["suggested_inputs"]


def test_review_distinguishes_the_program_button_from_character_dialogue():
    from services.theater.numeric_v2_evaluator import _build_transition_judge_messages
    from tests.unit.test_theater_numeric_v2_review_capacity import _fixture
    engine, session, outcome, candidate = _fixture(1)
    outcome = replace(outcome, ledger_event={**outcome.ledger_event, "transition_intent": "accept"})
    messages, _ = _build_transition_judge_messages(engine, session, actor_performance=candidate,
        player_input="好，我们去入口。", transition_outcome=outcome,
        confirmed_acceptance=True, confirmed_silent_acceptance="好，我们去入口。")
    data = json.loads(messages[1].content.split("：", 1)[1])
    assert data["transition_authorization"]["pending_invitation"]["kind"] == "silent_exit"
    assert "不是角色台词或已经完成的玩家行动" in messages[0].content


@pytest.mark.asyncio
@pytest.mark.parametrize("review", [True, False])
async def test_freeform_copy_cannot_accept_even_when_evaluator_claims_acceptance(tmp_path, monkeypatch, review):
    runtime, current, accept, _ = await setup(tmp_path, monkeypatch, review=review)
    offered = await turn(runtime, current, "offer", "接下来呢？")

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), False, transition_intent="accept")

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    result = await turn(runtime, offered.stored, "typed", accept)
    assert result.stored.session.current_node_id == "start"


@pytest.mark.asyncio
async def test_program_button_does_not_whitelist_other_rejected_suggestions(tmp_path, monkeypatch):
    runtime, current, accept, _ = await setup(tmp_path, monkeypatch)

    async def review(self, **kwargs):
        return ev.NumericV2TransitionOfferReview(False, False, (), (0,), reviewed_suggestion_texts=())

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "validate_transition_offer", review)
    result = await turn(runtime, current, "offer", "接下来呢？")
    assert result.performance["suggested_inputs"] == [accept]


@pytest.mark.asyncio
async def test_empty_body_exception_requires_exact_program_control_receipt(tmp_path, monkeypatch):
    from services.theater.numeric_v2_performance import is_silent_invitation_control
    runtime, current, _, _ = await setup(tmp_path, monkeypatch)
    offered = await turn(runtime, current, "offer", "接下来呢？")
    expired = await turn(runtime, offered.stored, "expire", "先等等。")
    revived = await turn(runtime, expired.stored, "reinvite", "重新邀请", "reinvite")
    event = revived.stored.ledger_events[-1]
    assert is_silent_invitation_control(event, revived.performance)
    for key, value in [("input_source", "freeform"), ("program_invitation", {}),
                       ("to_node_id", "other-node")]:
        assert not is_silent_invitation_control({**event, key: value}, revived.performance)
    assert not is_silent_invitation_control(event, {**revived.performance, "scene_narration": "新旁白"})
    assert not is_silent_invitation_control(event, {**revived.performance, "suggested_inputs": ["其他选择"]})


def test_workshop_prompts_make_silent_button_self_contained():
    from theater_workshop.sdk.generation.numeric_v2 import _BRANCH_PATH_PROMPT
    from theater_workshop.sdk.generation import numeric_v2
    from theater_workshop.sdk.generation import runtime_rules
    assert "Runtime 据此生成完整按钮、不补台词" in _BRANCH_PATH_PROMPT
    assert "Runtime 由两项生成完整按钮" in numeric_v2._MAINLINE_PROMPT
    assert "Runtime 由两项生成完整按钮" in numeric_v2._MAINLINE_CONTINUATION_PROMPT
    assert "独立说明具体去向和安排" in runtime_rules.SCORING_RUNTIME_RULES


@pytest.mark.asyncio
@pytest.mark.parametrize("genre", ["scifi", "fantasy"])
async def test_silent_transition_rejects_source_speech_with_review_disabled(tmp_path, monkeypatch, genre):
    runtime, current, accept, _ = await setup(tmp_path, monkeypatch, genre=genre, review=False)
    offered = await turn(runtime, current, "offer", "接下来呢？")

    async def generate(self, **kwargs):
        # Bypass Actor parsing to check the independent commit boundary.
        return runtime.engine.finalize_transition_performance(kwargs["outcome"], _candidate(),
            target_opening="到达入口。")

    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    with pytest.raises(NumericV2RuntimeError, match="numeric_transition_performance_invalid"):
        await turn(runtime, offered.stored, "accept-speech", accept, "suggestion")
    assert await runtime.restore_session("silent") == offered.stored


@pytest.mark.asyncio
async def test_real_actor_receives_receipt_policy_without_mutating_session(tmp_path, monkeypatch):
    original_generate = wf.NumericV2Actor.generate_turn
    runtime, current, accept, _ = await setup(tmp_path, monkeypatch, review=False)
    offered = await turn(runtime, current, "offer", "接下来呢？")
    captured = []

    async def invoke(self, messages, **kwargs):
        captured.append(kwargs)
        return {**_candidate(), "source_performance": "（垂下手，望向出口）"}

    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", original_generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_invoke", invoke)
    accepted = await turn(runtime, offered.stored, "actor-accept", accept, "suggestion")
    assert offered.stored.session.dialogue_policy == "optional"
    assert captured[0]["source_dialogue_policy"] == "forbidden"
    assert captured[0]["target_dialogue_policy"] == accepted.stored.session.dialogue_policy
    assert accepted.performance["segments"][2]["performance"] == _candidate()["target_performance"]
    fork = await runtime.fork_session_for_test("silent", session_id="accepted-fork",
        through_revision=accepted.stored.session.revision)
    assert fork.ledger_events[-1]["source_transition_dialogue_policy"] == "forbidden"
    assert await runtime.restore_session("accepted-fork") == fork


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [("source_transition_dialogue_policy",),
    ("input_source",), ("input_source", "source_transition_dialogue_policy")])
@pytest.mark.parametrize("source_speech", [False, True])
async def test_restore_rejects_missing_receipt_derived_source_policy(tmp_path, monkeypatch, missing, source_speech):
    from services.theater.numeric_v2_store import NumericV2StoreError
    runtime, current, accept, _ = await setup(tmp_path, monkeypatch, review=False)
    offered = await turn(runtime, current, "offer", "接下来呢？")
    await turn(runtime, offered.stored, "accept", accept, "suggestion")
    path = runtime.store.root / "silent.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for field in missing:
        payload["ledger_events"][-1].pop(field)
    if source_speech:
        payload["session"]["performance_history"][-1]["segments"][0]["performance"] = "（望向出口）我们走吧。"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(NumericV2StoreError, match="numeric_ledger_replay_mismatch"):
        await runtime.restore_session("silent")


@pytest.mark.asyncio
async def test_legacy_acceptance_without_source_policy_still_restores(tmp_path, monkeypatch):
    from tests.unit.test_theater_review_round8_invitation import ACCEPT, setup_case, turn as ordinary_turn

    runtime, current = await setup_case(tmp_path, monkeypatch, review=False)
    offered = await ordinary_turn(runtime, current, "offer", "接下来呢？")
    receipt = offered.stored.ledger_events[-1].get("program_invitation") or {}
    assert receipt.get("silent") is not True
    await ordinary_turn(runtime, offered.stored, "accept", ACCEPT, "suggestion")
    path = runtime.store.root / f"{current.session.session_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    # Before source-policy receipts, ordinary acceptances did not persist input_source.
    payload["ledger_events"][-1].pop("source_transition_dialogue_policy", None)
    payload["ledger_events"][-1].pop("input_source", None)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    restored = await runtime.restore_session(current.session.session_id)
    assert restored.session.current_node_id == "ending_leave"
    assert "source_transition_dialogue_policy" not in restored.ledger_events[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["missing", "silent_flag", "not_mapping"])
@pytest.mark.parametrize("unrelated_dialogue", [False, True])
async def test_full_review_cannot_accept_silent_exit_without_valid_receipt(tmp_path, monkeypatch, mutation, unrelated_dialogue):
    runtime, current, accept, _ = await setup(tmp_path, monkeypatch, review=True)
    if unrelated_dialogue:
        original_generate = wf.NumericV2Actor.generate_turn

        async def generate(self, **kwargs):
            if kwargs["outcome"].ledger_event["from_node_id"] != kwargs["outcome"].ledger_event["to_node_id"]:
                return await original_generate(self, **kwargs)
            return {"performance": "（收好工具）记录已经封存。", "suggested_inputs": [],
                    "transition_offered": False}

        monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    offered = await turn(runtime, current, "offer", "接下来呢？")
    path = runtime.store.root / "silent.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    event = payload["ledger_events"][-1]
    if mutation == "missing":
        event.pop("program_invitation")
    elif mutation == "silent_flag":
        event["program_invitation"]["silent"] = False
    else:
        event["program_invitation"] = "damaged"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    current = await runtime.restore_session("silent")

    async def accept_evaluation(self, **kwargs):
        return ev.NumericV2EvaluationResult((), False, transition_intent="accept")

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", accept_evaluation)
    result = await turn(runtime, current, "tampered-accept", accept, "suggestion")
    assert result.stored.session.current_node_id == current.session.current_node_id
    assert result.stored.ledger_events[-1]["transition_intent"] != "accept"


@pytest.mark.asyncio
@pytest.mark.parametrize("genre", ["scifi", "fantasy"])
async def test_legacy_opaque_silent_button_is_not_signed_or_reinvited(tmp_path, monkeypatch, genre):
    runtime, current, _, _ = await setup(tmp_path, monkeypatch, genre=genre, review=False)
    runtime.engine.nodes["start"]["route_gates"][1]["transition_contract"].pop("silent_choice")
    result = await turn(runtime, current, "old-package", "接下来呢？")
    assert not result.stored.session.transition_offered
    assert "好，就按刚才的安排。" not in result.performance["suggested_inputs"]
    assert wf.invitation_recovery_contract(runtime, result.stored) is None


@pytest.mark.parametrize("choice", [{}, {"destination": "门口"},
    {"destination": "", "arrangement": "会面"},
    {"destination": "门口", "arrangement": "会面\n等待"}])
def test_invalid_structured_silent_choices_fail_compilation(choice):
    from services.theater.numeric_v2 import NumericV2Compiler, NumericV2CompileError
    story = _story("scifi")
    story["nodes"][0]["route_gates"][1]["transition_contract"]["silent_choice"] = choice
    with pytest.raises(NumericV2CompileError):
        NumericV2Compiler().compile(story)


@pytest.mark.parametrize("destination", ["观测舱", "山间石桥"])
def test_branch_export_preserves_structured_silent_choice(destination):
    from theater_workshop.sdk.numeric_v2_branch import NumericV2BranchService
    choice = {"destination": destination, "arrangement": "查看封存记录"}
    transition = {"reason": "完成交接", "bridge_scene_narration": "来到下一处。",
        "must_preserve": [], "tone": "平静", "silent_choice": choice}
    contract = NumericV2BranchService._transition_contract(transition,
        source_ids=[], accept_input="按刚才的安排。")
    assert contract["silent_choice"] == choice
    choice["destination"] = "别处"
    assert contract["silent_choice"]["destination"] == destination


@pytest.mark.parametrize("field", ["destination", "arrangement"])
def test_compiler_and_runtime_reject_carriage_returns_in_silent_choice(tmp_path, field):
    from services.theater.numeric_v2 import NumericV2CompileError, NumericV2Compiler
    story = _story("scifi")
    contract = story["nodes"][0]["route_gates"][1]["transition_contract"]
    contract["silent_choice"] = {"destination": "观测舱", "arrangement": "查看记录"}
    contract["silent_choice"][field] += "\r更多内容"
    with pytest.raises(NumericV2CompileError):
        NumericV2Compiler().compile(story)
    assert wf._silent_choice_input(None, None, contract) == ""


@pytest.mark.parametrize("fallback,accept", [("一起去下一处？", "好，我们去。"), (None, None)])
def test_branch_export_drops_unused_silent_choice(fallback, accept):
    from theater_workshop.sdk.numeric_v2_branch import NumericV2BranchService
    transition = {"reason": "完成交接", "bridge_scene_narration": "来到下一处。",
        "must_preserve": [], "tone": "平静", "silent_choice": {"destination": "unused"}}
    contract = NumericV2BranchService._transition_contract(transition,
        source_ids=[], fallback_offer=fallback, accept_input=accept)
    assert "silent_choice" not in contract


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["freeform", "reinvite"])
async def test_projected_silent_choice_overflow_is_explicit_and_rolls_back(tmp_path, monkeypatch, source):
    from utils.tokenize import count_tokens

    choice = {"destination": "男女主的观测舱", "arrangement": "查看记录"}
    assert all(count_tokens(value) <= 30 for value in choice.values())
    binding = {**_binding(), "catgirl_name": "longname " * 160}
    runtime, current, _, _ = await setup(tmp_path, monkeypatch, binding=binding, silent_choice=choice)
    with pytest.raises(NumericV2RuntimeError, match="numeric_silent_choice_input_too_long"):
        await turn(runtime, current, "overflow", "重新邀请" if source == "reinvite" else "接下来呢？", source)
    assert await runtime.restore_session("silent") == current


@pytest.mark.parametrize("tokens", [140, 141])
def test_silent_choice_checks_final_projected_input_budget(monkeypatch, tokens):
    text = "a " * (tokens - 1) + "a"
    monkeypatch.setattr(wf, "_project_authored_transition_text", lambda *_args: text)
    contract = {"silent_choice": {"destination": "observatory", "arrangement": "read records"}}
    if tokens > 140:
        with pytest.raises(NumericV2RuntimeError, match="numeric_silent_choice_input_too_long"):
            wf._silent_choice_input(None, None, contract)
    else:
        assert wf._silent_choice_input(None, None, contract) == text


@pytest.mark.asyncio
async def test_readonly_session_payload_survives_projected_choice_overflow(tmp_path, monkeypatch):
    from main_routers import numeric_theater_router as router
    from services.theater import numeric_v2_options

    async def options():
        return default_options()

    monkeypatch.setattr(numeric_v2_options, "aload_theater_module_options", options)
    runtime, current, _, _ = await setup(tmp_path, monkeypatch,
        binding={**_binding(), "catgirl_name": "longname " * 160},
        silent_choice={"destination": "男女主的观测舱", "arrangement": "查看记录"})
    payload = await router._numeric_payload(runtime, current)
    assert payload["session"]["session_id"] == current.session.session_id
    assert payload["invitation_recovery_available"] is False
    assert await runtime.restore_session("silent") == current
    with pytest.raises(NumericV2RuntimeError, match="numeric_silent_choice_input_too_long"):
        await turn(runtime, current, "reinvite-after-read", "重新邀请", "reinvite")


@pytest.mark.asyncio
@pytest.mark.parametrize("new_name", ["新名字", "longname " * 160], ids=["rename", "oversized-rename"])
async def test_signed_silent_button_survives_rename_before_commit(tmp_path, monkeypatch, new_name):
    runtime, current, _, _ = await setup(tmp_path, monkeypatch,
        silent_choice={"destination": "男女主的观测舱", "arrangement": "查看记录"})
    bindings = iter([_binding(), {**_binding(), "catgirl_name": new_name}])
    offered = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("rename-offer", current.session.revision, "接下来呢？"),
        ensure_current_binding=lambda _: next(bindings))
    button = offered.performance["suggested_inputs"][0]
    assert new_name not in button
    assert offered.stored.session.catgirl_binding["catgirl_name"] == new_name
    restored = await runtime.restore_session("silent")
    fork = await runtime.fork_session_for_test("silent", session_id="rename-fork",
        through_revision=restored.session.revision)
    for stored, key in [(restored, "rename-accept"), (fork, "rename-fork-accept")]:
        accepted = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=stored,
            turn=TurnRequestV2(key, stored.session.revision, button, "suggestion"),
            ensure_current_binding=lambda _: {**_binding(), "catgirl_name": new_name})
        assert accepted.stored.session.current_node_id == "ending_leave"
        assert accepted.stored.ledger_events[-1]["source_transition_dialogue_policy"] == "forbidden"
        assert await runtime.restore_session(stored.session.session_id) == accepted.stored


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("medium,legacy_provenance", [("dialogue", False), ("dialogue", True),
    ("narration", False), ("action", False)])
async def test_reviewed_offer_before_silence_still_accepts_freeform(tmp_path, monkeypatch, legacy, medium, legacy_provenance):
    runtime, current, _, _ = await setup(tmp_path, monkeypatch, review=True)
    if legacy:
        runtime.engine.nodes["start"]["route_gates"][1]["transition_contract"].pop("silent_choice")
    original_generate = wf.NumericV2Actor.generate_turn

    async def generate(self, **kwargs):
        if kwargs["outcome"].ledger_event["from_node_id"] != kwargs["outcome"].ledger_event["to_node_id"]:
            return await original_generate(self, **kwargs)
        performance = {"performance": "我们待会去观测舱吧。", "suggested_inputs": ["好，走吧。"],
                       "transition_offered": True}
        if medium == "narration":
            performance.update(performance="（等待你的决定）", scene_narration="前往观测舱的入口已经打开，等待你的决定。")
        elif medium == "action":
            performance["performance"] = "（指向观测舱的入口，伸手等待你的决定）"
        return performance

    async def review_offer(self, **kwargs):
        return ev.NumericV2TransitionOfferReview(not kwargs.get("route_changed"), True, (), (),
            pending_invitation_invalid=False, acceptance_authorized=True,
            delivery_matches_route=True)

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), False,
            transition_intent="accept" if kwargs["session"].transition_offered else "unclear")

    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "validate_transition_offer", review_offer)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    offered = await turn(runtime, current, "spoken-offer", "接下来呢？")
    assert offered.stored.session.transition_offered
    assert "program_invitation" not in offered.stored.ledger_events[-1]
    assert offered.stored.ledger_events[-1]["transition_offer_source"] == "reviewed"
    if legacy_provenance:
        path = runtime.store.root / "silent.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["ledger_events"][-1].pop("transition_offer_source")
        payload["session"]["performance_history"][-1].pop("transition_offer_source")
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    stored = await runtime.restore_session("silent")
    accepted = await turn(runtime, stored, "spoken-accept", "好，走吧。")
    assert accepted.stored.session.current_node_id == "ending_leave"
    assert await runtime.restore_session("silent") == accepted.stored


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["event_missing", "history_missing", "mismatch", "bad_type"])
async def test_restore_rejects_mismatched_offer_provenance(tmp_path, monkeypatch, mutation):
    from services.theater.numeric_v2_store import NumericV2StoreError

    runtime, current, _, _ = await setup(tmp_path, monkeypatch)
    await turn(runtime, current, "offer", "接下来呢？")
    path = runtime.store.root / "silent.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    event, history = payload["ledger_events"][-1], payload["session"]["performance_history"][-1]
    if mutation == "event_missing":
        event.pop("transition_offer_source")
    elif mutation == "history_missing":
        history.pop("transition_offer_source")
    elif mutation == "mismatch":
        event["transition_offer_source"] = "reviewed"
    else:
        event["transition_offer_source"] = history["transition_offer_source"] = {}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(NumericV2StoreError, match="numeric_ledger_replay_mismatch"):
        await runtime.restore_session("silent")
