"""A confirmation-only declaration never replaces current invitation and exit gates."""

from copy import deepcopy
from dataclasses import replace

import pytest

from services.theater import numeric_v2_workflow as wf
from services.theater.numeric_v2_options import default_options
from services.theater.numeric_v2_runtime import TurnRequestV2
from tests.unit import test_theater_numeric_v2_same_turn_invitation as invitation
from tests.unit.test_theater_numeric_v2_confirmation_only import _offered, _turn
from tests.unit.test_theater_numeric_v2_fixed_narration import _piece
from tests.unit.test_theater_numeric_v2_pending_offer import _commit


class EvaluationReached(RuntimeError):
    """Stop at the real evaluation boundary without making a provider request."""


@pytest.mark.asyncio
async def test_reviewed_dynamic_receipt_does_not_inherit_author_confirmation_only_skip(tmp_path, monkeypatch):
    runtime, current, calls = await _offered(tmp_path, monkeypatch)
    current = await _commit(runtime, current, "好，先留在柜台旁。", intent="reject")
    # 直接重建旧版真实回执形状；新重邀已不生成玩家台词，不能拿新 kind 冒充旧协议兼容。
    outcome = runtime.prepare_turn(current,
        TurnRequestV2('legacy-dynamic', current.session.revision, '', 'reinvite'), ())
    performance = {'performance': invitation.OFFER, 'suggested_inputs': [invitation.ACCEPT]}
    outcome, performance = runtime.engine.finalize_transition_offer_state(outcome, performance, new_offer=True)
    route = runtime.engine.preview_route(current.session.current_node_id, current.session.metrics,
        facts=current.session.story_state['facts'])
    outcome = replace(outcome, ledger_event={**outcome.ledger_event, 'program_invitation': {
        'kind': 'reviewed_dynamic', 'revision': outcome.session.revision,
        'route_id': route['id'], 'target_node_id': route['target_node_id'],
        'offer': invitation.OFFER, 'performance': invitation.OFFER, 'accept_input': invitation.ACCEPT,
        'visible_blocks': wf.performance_content_blocks(performance)}})
    current = await runtime.commit_turn(outcome, performance)
    assert current.ledger_events[-1]["program_invitation"]["kind"] == "reviewed_dynamic"
    turn = _turn(current)
    # 相同按钮仍可正常确认动态邀请；旧作者 opt-in 不授予动态回执跳过原判定的权限。
    assert wf._confirmed_authored_acceptance(runtime.engine, current, turn, require_program_invitation=True)
    before = deepcopy(current)
    persisted = runtime.store._path(current.session.session_id).read_bytes()
    assert not wf._confirmation_only_acceptance(runtime, current, turn,
        history_lookup_enabled=False, review_enabled=False)
    calls.update(evaluator=0, actor=0, review=0)

    async def evaluate(self, **kwargs):
        calls["evaluator"] += 1
        assert kwargs["message"] == invitation.ACCEPT
        raise EvaluationReached("full_evaluator")

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    diagnostics = {}
    with pytest.raises(EvaluationReached, match="full_evaluator"):
        await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=turn, ensure_current_binding=lambda _: invitation._binding(), diagnostics_sink=diagnostics)
    assert calls == {"evaluator": 1, "actor": 0, "review": 0}
    assert diagnostics["evaluator_model_attempts"] == 1
    assert "evaluator_skip_reason" not in diagnostics
    assert current == before
    assert runtime.store._path(current.session.session_id).read_bytes() == persisted


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "missing_receipt", "receipt_shape", "wrong_receipt_route", "wrong_receipt_accept", "wrong_receipt_blocks",
    "changed_body", "hidden_button", "replaced_button", "old", "withdrawn", "invalidated",
    "freeform", "added_question", "wrong_revision", "processed_turn", "inactive", "not_offered",
    "metric_drift", "fact_route_drift", "fact_drift", "fact_missing", "completion_missing", "history_lookup", "forbidden",
    "terminal_target", "silent_contract",
])
async def test_ineligible_confirmation_is_read_only_and_reaches_full_evaluator(tmp_path, monkeypatch, case):
    if case in {"metric_drift", "fact_route_drift"}:
        original = invitation.story_for

        def with_alternative_route(destination):
            story = original(destination)
            alternate = deepcopy(story["nodes"][2])
            alternate["id"] = "alternate_scene"
            alternate["route_gates"][0]["id"] = "alternate_to_leave"
            alternate["route_gates"][0]["target_node_id"] = "ending_stay"
            story["nodes"].append(alternate)
            route = story["nodes"][0]["route_gates"][0]
            route["target_node_id"] = alternate["id"]
            route["transition_contract"] = deepcopy(story["nodes"][0]["route_gates"][1]["transition_contract"])
            route["transition_contract"]["accept_input_confirmation_only"] = True
            if case == "fact_route_drift":
                story["fact_contract"]["facts"]["scene:start:detour"] = {
                    "value_type": "bool", "visibility": "public", "description": "绕行路线已获准。"}
                route["conditions"] = {"all": [{"type": "fact_equals",
                    "key": "scene:start:detour", "value": True}]}
            return story

        monkeypatch.setattr(invitation, "story_for", with_alternative_route)
    runtime, current, calls = await _offered(tmp_path, monkeypatch)
    if case in {"old", "withdrawn", "invalidated"}:
        current = await _commit(runtime, current, "（留在柜台旁）先说清楚你的问题。",
            intent="reject" if case == "withdrawn" else "unclear", invalidate=case == "invalidated")
    current = deepcopy(current)
    turn = _turn(current)
    if case == "missing_receipt":
        current.ledger_events[-1].pop("program_invitation")
    elif case == "receipt_shape":
        current.ledger_events[-1]["program_invitation"] = "没有程序签发记录"
    elif case.startswith("wrong_receipt_"):
        receipt = current.ledger_events[-1]["program_invitation"]
        key = {"wrong_receipt_route": "route_id", "wrong_receipt_accept": "accept_input",
               "wrong_receipt_blocks": "visible_blocks"}[case]
        receipt[key] = [] if key == "visible_blocks" else "伪造回执"
    elif case == "changed_body":
        current.session.performance_history[-1]["performance"] += "不过现在不能出发。"
    elif case in {"hidden_button", "replaced_button"}:
        current.session.performance_history[-1]["suggested_inputs"] = [] if case == "hidden_button" else ["先等等。"]
    elif case == "freeform":
        turn = _turn(current, source="freeform")
    elif case == "added_question":
        turn = _turn(current, message=invitation.ACCEPT + "但请先告诉我还要带什么？")
    elif case == "wrong_revision":
        turn = replace(turn, base_revision=current.session.revision - 1)
    elif case == "processed_turn":
        turn = replace(turn, client_turn_id=current.session.processed_client_turn_ids[-1])
    elif case == "inactive":
        current = replace(current, session=replace(current.session, status="ended"))
    elif case == "not_offered":
        current = replace(current, session=replace(current.session, transition_offered=False))
    elif case == "metric_drift":
        current.session.metrics["trust"] = 75
    elif case == "fact_route_drift":
        current.session.story_state["facts"]["scene:start:detour"] = deepcopy(
            current.session.story_state["facts"][invitation.KEY])
    elif case == "fact_drift":
        current.session.story_state["facts"][invitation.KEY]["value"] = False
    elif case == "fact_missing":
        current.session.story_state["facts"].pop(invitation.KEY)
    elif case == "completion_missing":
        runtime.engine.nodes["start"].pop("completion_contract")
    elif case == "forbidden":
        current = replace(current, session=replace(current.session, dialogue_policy="forbidden"))
    elif case in {"terminal_target", "silent_contract"}:
        # The compiler rejects these authored combinations; retain Runtime defense
        # when an integration passes a stale or externally changed engine object.
        route = runtime.engine.preview_route("start", current.session.metrics,
            facts=current.session.story_state["facts"])
        if case == "terminal_target":
            runtime.engine.nodes[route["target_node_id"]]["terminal"] = True
        else:
            authored = next(item for item in runtime.engine.nodes["start"]["route_gates"]
                if item["id"] == route["id"])
            authored["transition_contract"]["silent_choice"] = {"destination": "棚边", "arrangement": "避风"}

    if case in {"metric_drift", "fact_route_drift"}:
        # Completion is still true and the newly selected route is itself eligible;
        # the old receipt must not authorize that different route, even with identical buttons.
        assert runtime.engine.completion_contract_satisfied(current.session) is True
        route = runtime.engine.preview_route("start", current.session.metrics,
            facts=current.session.story_state["facts"])
        assert route["target_node_id"] == "alternate_scene"
        assert route["transition_contract"]["accept_input_confirmation_only"] is True
        assert runtime.engine.nodes[route["target_node_id"]]["type"] == "scene"
        assert not runtime.engine.nodes[route["target_node_id"]].get("terminal")
        assert route["id"] != current.ledger_events[-1]["program_invitation"]["route_id"]

    before = deepcopy(current)
    persisted = runtime.store._path(current.session.session_id).read_bytes()
    assert not wf._confirmation_only_acceptance(runtime, current, turn,
        history_lookup_enabled=case == "history_lookup", review_enabled=False)
    assert current == before
    assert runtime.store._path(current.session.session_id).read_bytes() == persisted

    async def options():
        return {**{key: key == 'evaluator' for key in default_options()}, "history_lookup": case == "history_lookup"}

    async def evaluate(self, **kwargs):
        calls["evaluator"] += 1
        assert kwargs["message"] == turn.message
        raise EvaluationReached("full_evaluator")

    monkeypatch.setattr(wf, "aload_theater_module_options", options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    diagnostics = {}
    with pytest.raises(EvaluationReached, match="full_evaluator"):
        await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=turn, ensure_current_binding=lambda _: invitation._binding(), diagnostics_sink=diagnostics)
    assert calls == {"evaluator": 1, "actor": 0, "review": 0}
    assert diagnostics["evaluator_model_attempts"] == 1
    assert "evaluator_skip_reason" not in diagnostics
    assert current == before
    assert runtime.store._path(current.session.session_id).read_bytes() == persisted


@pytest.mark.asyncio
async def test_review_switch_keeps_real_required_condition_piece_as_exit_gate(tmp_path, monkeypatch):
    original = invitation.story_for

    def with_required_condition(destination):
        story = original(destination)
        source = story["nodes"][0]
        source["story_beat"]["fixed_narrations"] = [_piece("record", "完整核验记录。")]
        assert source["completion_contract"]["all"] == [{"key": invitation.KEY, "equals": True}]
        return story

    monkeypatch.setattr(invitation, "story_for", with_required_condition)
    runtime, current, calls = await _offered(tmp_path, monkeypatch)
    turn = _turn(current)
    before = deepcopy(current)
    assert runtime.engine.completion_contract_satisfied(current.session) is True
    assert wf._confirmed_authored_acceptance(runtime.engine, current, turn, require_program_invitation=True)
    assert wf._confirmation_only_acceptance(runtime, current, turn,
        history_lookup_enabled=False, review_enabled=False)
    assert not wf._confirmation_only_acceptance(runtime, current, turn,
        history_lookup_enabled=False, review_enabled=True)
    assert current == before
    assert await runtime.restore_session(current.session.session_id) == current

    async def review_on():
        return {**{key: key == 'evaluator' for key in default_options()}, "review": True}

    monkeypatch.setattr(wf, "aload_theater_module_options", review_on)
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=turn, ensure_current_binding=lambda _: invitation._binding())
    assert calls == {"evaluator": 1, "actor": 1, "review": 1}
    assert result.stored.session.current_node_id == "start"
    assert not result.performance.get("transition_delivered")
    assert "fixed_narrations" not in result.performance
    assert "evaluator_skip_reason" not in result.diagnostics
    assert current == before
    assert await runtime.restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize("valid_button", [True, False])
async def test_disabled_evaluator_keeps_original_fallback_before_new_opt_in(tmp_path, monkeypatch, valid_button):
    runtime, current, calls = await _offered(tmp_path, monkeypatch)
    turn = _turn(current, source="suggestion" if valid_button else "freeform")

    async def options():
        return {**{key: key == 'evaluator' for key in default_options()}, "evaluator": False}

    def forbidden_new_helper(*args, **kwargs):
        raise AssertionError("disabled evaluator must keep the original path")

    monkeypatch.setattr(wf, "aload_theater_module_options", options)
    monkeypatch.setattr(wf, "_confirmation_only_acceptance", forbidden_new_helper)
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=turn, ensure_current_binding=lambda _: invitation._binding())
    assert calls == {"evaluator": 0, "actor": 1, "review": 0}
    assert result.diagnostics["evaluator_skipped"] is True
    assert "evaluator_skip_reason" not in result.diagnostics
    assert result.stored.session.current_node_id == ("ending_leave" if valid_button else "start")
    assert result.stored.session.metrics == current.session.metrics
