"""A confirmed invitation authorizes the route, not incorrect transition prose."""

from dataclasses import replace
import json

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as wf
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2, MetricChangeV2
from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
from tests.unit.test_theater_numeric_v2_runtime import _binding


async def _invited(tmp_path, target="阅览室"):
    case = initiation_case(target=target)
    engine = case["engine"]
    contract = engine.nodes["start"]["route_gates"][1]["transition_contract"]
    offer, accept = f"我们去{target}聊聊，好吗？", f"好，我们去{target}聊聊。"
    contract.update(fallback_offer=offer, accept_input=accept)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="authorized", catgirl_binding=_binding(),
                                          opening_performance=case["session"].opening_performance)
    outcome = runtime.prepare_turn(current, TurnRequestV2("invite", 0, "接下来呢？"), ())
    outcome, performance = engine.finalize_transition_offer_state(outcome,
        {"performance": f"（转向通道）{offer}", "suggested_inputs": [accept], "transition_offered": True},
        new_offer=True)
    return runtime, await runtime.commit_turn(outcome, performance), accept


@pytest.mark.asyncio
async def test_authored_acceptance_survives_a_committed_followup(tmp_path):
    runtime, current, accept = await _invited(tmp_path)
    outcome = runtime.prepare_turn(current, TurnRequestV2('question', 1, '那里远吗？'), ())
    outcome, performance = runtime.engine.finalize_transition_offer_state(outcome,
        {'performance': '（指向走廊）就在旁边。', 'suggested_inputs': [accept]}, new_offer=False)
    later = await runtime.commit_turn(outcome, performance)
    assert later.session.revision == 2
    assert wf._confirmed_authored_acceptance(runtime.engine, later,
        TurnRequestV2('go', 2, accept, 'suggestion'))


@pytest.mark.asyncio
async def test_disabled_judgement_exposes_authored_pair_for_actor_invitation(tmp_path, monkeypatch):
    runtime, invited, accept = await _invited(tmp_path)
    current = await runtime.start_session(session_id='new-invitation', catgirl_binding=_binding(),
                                          opening_performance=invited.session.opening_performance)
    async def options():
        return {'evaluator': False, 'review': False, 'dispute': False}
    async def generate(self, **kwargs):
        return {'performance': '（指向走廊）我们去那边吧。', 'suggested_inputs': ['好，带路吧。'], 'transition_offered': True}
    monkeypatch.setattr(wf, 'aload_theater_module_options', options)
    monkeypatch.setattr(wf.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(wf.NumericV2Actor, '_character_profile', lambda self: '温和。')
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('invitation', current.session.revision, '接下来呢？'), ensure_current_binding=lambda _: _binding())
    assert accept in result.performance['suggested_inputs']
    assert wf._confirmed_authored_acceptance(runtime.engine, result.stored,
        TurnRequestV2('accept', result.stored.session.revision, accept, 'suggestion'))


def _review(*, delivered=True, rejected=False):
    return ev._parse_transition_judge_output(json.dumps({
        "offer_present": False, "valid": False,
        "body_violations": ["author_boundary"] if not delivered else [],
        "unsafe_suggestion_indexes": [], "delivery_matches_route": delivered,
        "acceptance_authorized": True, "pending_invitation_invalid": rejected,
        "failure_reason": "目标段仍在旧场景。" if not delivered else "",
    }), acceptance_review=True, transition_delivery_review=True)


def test_delivery_failure_is_body_failure_without_revoking_acceptance():
    review = _review(delivered=False)
    assert review.acceptance_authorized is True
    assert review.delivery_matches_route is False
    assert "scene_boundary" in review.body_violations


@pytest.mark.parametrize("value", [None, "false", 0, [], {}])
def test_delivery_field_never_coerces_malformed_values(value):
    payload = {"offer_present": False, "valid": False, "body_violations": [],
               "unsafe_suggestion_indexes": [], "delivery_matches_route": value}
    with pytest.raises(ev.NumericV2EvaluatorOutputError):
        ev._parse_transition_judge_output(json.dumps(payload), transition_delivery_review=True)


def test_ordinary_review_cannot_supply_delivery_authority():
    payload = {"offer_present": False, "valid": False, "body_violations": [],
               "unsafe_suggestion_indexes": [], "delivery_matches_route": True}
    with pytest.raises(ev.NumericV2EvaluatorOutputError):
        ev._parse_transition_judge_output(json.dumps(payload))


def test_requested_delivery_result_must_not_be_missing():
    payload = {"offer_present": False, "valid": False, "body_violations": [], "unsafe_suggestion_indexes": []}
    with pytest.raises(ev.NumericV2EvaluatorOutputError):
        ev._parse_transition_judge_output(json.dumps(payload), transition_delivery_review=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["none", "question", "refusal", "suffix", "not_shown", "old", "different_invitation", "route_changed"])
async def test_authored_acceptance_requires_current_visible_pair(tmp_path, change):
    runtime, current, accept = await _invited(tmp_path)
    message = {"question": "能去阅览室吗？", "refusal": "先不去了。", "suffix": accept + "不过我还没决定。"}.get(change, accept)
    if change in {"not_shown", "different_invitation"}:
        last = dict(current.session.performance_history[-1])
        if change == "not_shown":
            last["suggested_inputs"] = []
        else:
            last["performance"] = "我们去街市，好吗？"
        current = replace(current, session=replace(current.session, performance_history=(last,)))
    if change == "old":
        outcome = runtime.prepare_turn(current, TurnRequestV2("later", 1, "再想想。"), ())
        outcome, performance = runtime.engine.finalize_transition_offer_state(outcome,
            {"performance": "（点头）可以再想想。", "suggested_inputs": []}, new_offer=False)
        current = await runtime.commit_turn(outcome, performance)
    if change == "route_changed":
        current = replace(current, session=replace(current.session, metrics={"trust": 75}))
    route_id = wf._confirmed_authored_acceptance(runtime.engine, current,
                                                TurnRequestV2("go", current.session.revision, message, "suggestion"))
    assert bool(route_id) is (change == "none")


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["阅信桌", "光学扫描室"])
@pytest.mark.parametrize("mode", ["normal", "repair", "unrepaired", "bad_invitation"])
async def test_confirmed_acceptance_keeps_route_during_body_repair(tmp_path, monkeypatch, target, mode):
    runtime, current, accept = await _invited(tmp_path, target)
    if mode == "bad_invitation":
        runtime.engine.nodes["start"]["route_gates"][1]["transition_contract"]["bridge_scene_narration"] = "两人来到另一处未受邀请的地点。"
        runtime.engine.nodes["ending_leave"]["story_beat"]["opening_scene"] = "两人在另一处未受邀请的地点。"
    calls = {"actor": 0, "review": 0, "evaluator": 0}

    async def options():
        return {"evaluator": True, "review": True, "dispute": False}

    async def evaluate(self, **kwargs):
        calls["evaluator"] += 1
        return ev.NumericV2EvaluationResult((MetricChangeV2("trust", 2, "玩家兑现承诺", accept),),
                                            False, transition_intent="unclear")

    async def generate(self, **kwargs):
        calls["actor"] += 1
        if calls["actor"] == 2 and mode in {"repair", "unrepaired"}:
            # 落点修复只使用历史与当前合同，不把被拒的旧场景再次喂作编辑底稿。
            assert "仍在旧接待台" not in kwargs["retry_hint"]
            assert "目标段仍在旧场景" in kwargs["retry_hint"]
        if kwargs["outcome"].session.current_node_id == "start":
            assert mode == "bad_invitation"
            return {"performance": "刚才的邀请不准确，先留在这里。", "suggested_inputs": ["（等待说明）"],
                    "transition_offered": False}
        assert kwargs["outcome"].session.current_node_id == "ending_leave"
        wrong = mode == "unrepaired" or (mode == "repair" and calls["actor"] == 1)
        return runtime.engine.finalize_transition_performance(kwargs["outcome"], {
            "source_performance": "（点头）好，一起走。",
            "bridge_scene_narration": f"沿通道来到{target}。",
            "target_scene_narration": "仍在旧接待台。" if wrong else f"两人在{target}。",
            "target_performance": "（看向窗边）这里很安静。", "suggested_inputs": [],
        }, target_opening=f"两人在{target}。")

    async def review(self, **kwargs):
        calls["review"] += 1
        if not kwargs["route_changed"]:
            assert mode == "bad_invitation"
            return ev.NumericV2TransitionOfferReview(False, False, (), ())
        assert kwargs.get("confirmed_acceptance") is True
        assert kwargs["route_changed"]
        return _review(delivered=mode in {"normal", "bad_invitation"} or (mode == "repair" and calls["actor"] == 2),
                       rejected=mode == "bad_invitation")

    monkeypatch.setattr(wf, "aload_theater_module_options", options)
    monkeypatch.setattr(ev.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(ev.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "温和。")
    args = dict(config_manager=object(), runtime=runtime, current=current,
                turn=TurnRequestV2("go", 1, accept, "suggestion"), ensure_current_binding=lambda _: _binding())
    if mode == "unrepaired":
        with pytest.raises(wf.NumericV2ActorOutputError, match="transition_review_failed"):
            await wf.execute_numeric_v2_turn(**args)
        assert await runtime.restore_session("authorized") == current
    else:
        result = await wf.execute_numeric_v2_turn(**args)
        assert result.diagnostics["transition_cancellations"] == int(mode == "bad_invitation")
        assert not result.diagnostics["semantic_review_fallback"]
        assert result.stored.session.current_node_id == ("start" if mode == "bad_invitation" else "ending_leave")
        if mode == "bad_invitation":
            assert result.stored.ledger_events[-1]["transition_offer_invalidated"] is True
            assert result.stored.session.transition_offered is False
        assert result.stored.session.metrics["trust"] == 22
        assert len(result.stored.ledger_events) == 2
        assert "仍在旧接待台" not in json.dumps(result.stored.session.to_dict(), ensure_ascii=False)
        assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session("authorized") == result.stored
    assert calls == {"evaluator": 1, "actor": 1 if mode == "normal" else 2,
                     "review": 1 if mode == "normal" else 2}
