"""Production pause keeps full audits disabled and verifies uncertain progression narrowly."""

from copy import deepcopy

import pytest

from services.theater import numeric_v2_options as options, numeric_v2_workflow as workflow
from services.theater.numeric_v2_evaluator import NumericV2EvaluationResult, NumericV2StoryEvidenceReview
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.unit.test_theater_numeric_v2_unified_pacing import story_for


@pytest.fixture
def speed_first(monkeypatch):
    from utils import preferences
    stored = {'theaterModule' + ''.join(part.title() for part in key.split('_')): False
              for key in options.default_options()}
    original = deepcopy(stored)
    async def load():
        raise AssertionError('removed theater switches must not read preferences')
    monkeypatch.setattr(preferences, 'aload_global_entry_flags', load)
    monkeypatch.setattr(options, 'MODEL_REVIEWS_ENABLED', False)
    monkeypatch.setattr(workflow, 'aload_theater_module_options', options.aload_theater_module_options)
    async def forbidden(*args, **kwargs):
        raise AssertionError('speed-first runtime must not call a model audit')
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', forbidden)
    async def contract_check(*args, **kwargs):
        if kwargs.get("proposed_offer") is not None:
            kwargs["diagnostics"]["offer_actionable"] = True
        return ()
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'verify_contract_boundaries', contract_check)
    yield forbidden
    assert stored == original  # Temporary pause never overwrites the user's preferences.


@pytest.mark.asyncio
async def test_production_options_pause_audits_even_when_saved_on(speed_first):
    active = await options.aload_theater_module_options()
    assert all(active[key] is False for key in ('review', 'dispute'))
    assert active['evaluator'] and active['review_delivery'] and active['review_contract']


@pytest.mark.asyncio
async def test_all_modules_default_on_without_legacy_preferences(speed_first, monkeypatch):
    assert all(options.default_options().values())
    monkeypatch.setattr(options, 'MODEL_REVIEWS_ENABLED', True)
    assert await options.aload_theater_module_options() == options.default_options()


def test_removed_settings_page_and_options_api_are_unavailable(tmp_path, monkeypatch):
    from tests.unit.test_theater_numeric_v2_router import _client
    from main_routers.pages_router import router
    with _client(tmp_path, monkeypatch) as client:
        client.app.include_router(router)
        assert client.get('/theater/settings').status_code == 404
        assert client.get('/api/theater-numeric/options').status_code == 404
        assert client.post('/api/theater-numeric/options', json={'modules': {'evaluator': False}}).status_code == 404


@pytest.mark.asyncio
async def test_opening_with_author_boundaries_does_not_call_review(speed_first, monkeypatch):
    story = story_for('山径的避风棚')
    story['nodes'][0]['story_beat']['opening_only_boundaries'] = ['不得在公开开场披露后续身份。']
    async def opening(self, **kwargs):
        return {'performance': '（抬眼）你好。', 'suggested_inputs': []}
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_opening', opening)
    result = await workflow.generate_validated_opening(
        engine=NumericV2Engine.from_mapping(story), config_manager=object(), session_id='speed-opening',
        catgirl_binding=_binding(), actor_budget_profile='balanced')
    assert result['performance'] == '（抬眼）你好。'


@pytest.mark.asyncio
@pytest.mark.parametrize("review_fails,new_invitation", [(False, False), (True, False), (False, True)])
async def test_progress_review_runs_once_before_the_first_over_budget_actor_draft(
    speed_first, tmp_path, monkeypatch, review_fails, new_invitation,
):
    from services.theater.numeric_v2_evaluator import NumericV2EvaluatorError

    story = story_for("长街")
    story["nodes"][0]["recommended_turns"] = 3
    story["nodes"][0].pop("completion_contract", None)
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="checkpoint", catgirl_binding=_binding(), opening_performance=_opening())
    calls = []
    advice = "确认已经交付的线索，回应玩家眼前的行动。"

    async def evaluate(self, **kwargs):
        return NumericV2EvaluationResult((), False)

    async def review(self, **kwargs):
        count = kwargs["session"].node_turn_count
        assert kwargs["review_progress"] and not kwargs["claims"]
        assert bool(kwargs["pending_offer"]) is (new_invitation and count == 7)
        calls.append(("review", count))
        if review_fails:
            raise NumericV2EvaluatorError("timeout")
        return NumericV2StoryEvidenceReview(direction=advice)

    async def generate(self, **kwargs):
        calls.append(("actor", kwargs["session"].node_turn_count, kwargs.get("progress_direction")))
        return {"performance": f"（点头）我听到第{kwargs['session'].revision + 1}个问题了。", "suggested_inputs": []}

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "review_story_evidence", review)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "verify_contract_boundaries", speed_first)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "克制。")
    for i in range(9 if new_invitation else 5):
        if new_invitation and i == 4:
            from tests.unit.test_theater_numeric_v2_pending_offer import _commit
            current = await _commit(runtime, current, "接下来呢？", offer=True, narration="现在一起去长街吗？")
            continue
        result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2(f"turn-{i}", i, f"第{i + 1}个问题。"), ensure_current_binding=lambda _: _binding())
        assert result.diagnostics.get("progress_review_calls", 0) == int(i == 3 or new_invitation and i == 7)
        current = await runtime.restore_session(current.session.session_id)
        assert current == result.stored
    assert calls[:5] == [
        ("actor", 0, None), ("actor", 1, None), ("actor", 2, None), ("review", 3),
        ("actor", 3, None if review_fails else advice),
    ]
    assert calls[5:] == (
        [("actor", 5, None), ("actor", 6, None), ("review", 7), ("actor", 7, advice), ("actor", 8, None)]
        if new_invitation else [("actor", 4, None)]
    )
    assert advice not in str(current.session.performance_history)


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [False, True])
async def test_delayed_paraphrase_reaches_independent_approval_before_the_checkpoint(
    speed_first, tmp_path, monkeypatch, approved,
):
    from services.theater.numeric_v2_evaluator import _parse_output
    from tests.unit.test_theater_numeric_v2_pending_offer import _commit
    from tests.unit.test_theater_numeric_v2_transition_history import _candidate

    story = story_for("长街")
    story["nodes"][0]["recommended_turns"] = 3
    story["nodes"][0].pop("completion_contract", None)
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="paraphrase", catgirl_binding=_binding(), opening_performance=_opening())
    offer = "你愿意现在跟我沿长街去取旧信吗？"
    current = await _commit(runtime, current, "接下来呢？", offer=True, narration=offer)
    current = await _commit(runtime, current, "我再想想。")
    message = "（迈步与你同行）这就去拿那封信。"
    calls = []

    async def evaluate(self, **kwargs):
        result = _parse_output(
            '{"scene_complete":true,"metric_changes":{},"exit_matches":true,"exit_comparison":"同一安排",'
            '"transition_intent":"accept","transition_reply_target":"pending_transition"}',
            engine, message, current.session, current.ledger_events,
        )
        assert result.transition_intent == "unclear" and result.needs_offer_review
        return result

    async def review(self, **kwargs):
        assert offer in kwargs["pending_offer"] and kwargs["pending_offer_revision"] == 1
        assert not kwargs["review_progress"]
        calls.append("review")
        return NumericV2StoryEvidenceReview(offer_decision="accept" if approved else "hold")

    async def generate(self, **kwargs):
        calls.append("actor")
        outcome = kwargs["outcome"]
        if outcome.route is not None:
            return engine.finalize_transition_performance(outcome, _candidate(), target_opening="长街到了。")
        return {"performance": "先说清楚再走。", "suggested_inputs": []}

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "review_story_evidence", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "克制。")
    result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("paraphrase", current.session.revision, message), ensure_current_binding=lambda _: _binding())
    assert (result.stored.session.current_node_id != current.session.current_node_id) is approved
    assert calls == ["review", "actor"]


@pytest.mark.asyncio
async def test_new_visible_result_reopens_review_and_expires_the_same_old_offer(
    speed_first, tmp_path, monkeypatch,
):
    from services.theater.numeric_v2_context import pending_transition_record
    from tests.unit.test_theater_numeric_v2_pending_offer import _commit

    story = story_for("长街")
    story["nodes"][0]["recommended_turns"] = 3
    story["nodes"][0].pop("completion_contract", None)
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="obsolete", catgirl_binding=_binding(), opening_performance=_opening())
    offer = "我们现在一起去长街交接吧？"
    current = await _commit(runtime, current, "接下来呢？", offer=True, narration=offer)
    current = await _commit(runtime, current, "请先等等。")
    current = await _commit(runtime, current, "我在等对方回复。")
    checks = []

    async def evaluate(self, **kwargs):
        return NumericV2EvaluationResult((), False)

    async def review(self, **kwargs):
        count = kwargs["session"].node_turn_count
        checks.append(count)
        assert kwargs["review_progress"] and kwargs["pending_offer_revision"] == 1
        if count == 3:
            return NumericV2StoryEvidenceReview()
        return NumericV2StoryEvidenceReview(
            offer_decision="obsolete", offer_quote="对方已经离开，交接取消了。")

    async def generate(self, **kwargs):
        count = kwargs["session"].node_turn_count
        invalidated = kwargs["outcome"].ledger_event.get("transition_offer_invalidated", False)
        assert invalidated == (count == 5)
        return {"performance": f"（望向窗外）这里是第{count + 1}轮。", "suggested_inputs": [],
                **({"scene_narration": "对方已经离开，交接取消了。"} if count == 4 else {})}

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "review_story_evidence", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "克制。")
    for i in (3, 4, 5, 6):
        result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2(f"turn-{i}", i, f"第{i + 1}个问题。"), ensure_current_binding=lambda _: _binding())
        current = await runtime.restore_session(current.session.session_id)
        assert current == result.stored
        assert current.session.transition_offered is (i < 5)
    assert checks == [3, 5]
    assert current.ledger_events[5]["transition_offer_invalidated"] is True
    forked = await runtime.fork_session_for_test("obsolete", session_id="forked", through_revision=6)
    assert pending_transition_record(forked.session, ledger_events=forked.ledger_events, include_withdrawn=True) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("actionable", [False, True])
async def test_post_draft_contract_checks_publication_without_removing_valid_body(
    speed_first, tmp_path, monkeypatch, actionable,
):
    story = story_for("长街")
    story["nodes"][0].pop("completion_contract", None)
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="publication", catgirl_binding=_binding(), opening_performance=_opening())
    text = "我们现在一起去长街吗？" if actionable else "外面的人越来越多。"
    checks = []

    async def evaluate(self, **kwargs):
        return NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        return {"performance": "先把眼前的话说完。\n" + text, "suggested_inputs": ["我现在就和你去长街。"],
                "transition_offered": True, "_published_transition_offer": text, "_transition_offer_provided": True}

    async def contract(self, **kwargs):
        proposed = kwargs.get("proposed_offer")
        checks.append(proposed["text"] if proposed else "")
        if proposed:
            kwargs["diagnostics"]["offer_actionable"] = actionable
        return ()

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "verify_contract_boundaries", contract)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "克制。")
    result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("publish", 0, "现在呢？"), ensure_current_binding=lambda _: _binding())
    assert checks == [text]
    assert result.performance["performance"] == "先把眼前的话说完。\n" + text
    assert bool(result.performance["suggested_inputs"]) is actionable
    assert result.stored.session.transition_offered is actionable


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["publish", "empty", "defer", "same_turn_unlock"])
async def test_invalid_offer_recovers_only_the_offer_once_per_visit(speed_first, tmp_path, monkeypatch, mode):
    story = story_for("档案室")
    key = "scene:start:done"
    route = story["nodes"][0]["route_gates"][1]
    same_turn_unlock = mode == "same_turn_unlock"
    publishes = mode in {"publish", "same_turn_unlock"}
    if same_turn_unlock:
        route["conditions"] = {"all": [{"type": "fact_equals", "key": key, "value": True}]}
        route["transition_contract"]["fallback_offer"] = "催促声迫使我立刻查看时间。"
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="offer-repair", catgirl_binding=_binding(), opening_performance=_opening())
    original = "我们已经把材料理齐了。"
    rejected = "催促声迫使我立刻查看时间。"
    assert workflow.is_pressure_only_offer(rejected)
    offer, accept = "现在一起去档案室核对记录吗？", "好，我和你去档案室核对记录。"
    if not same_turn_unlock:
        ready = runtime.prepare_turn(current, TurnRequestV2("ready", 0, "材料已经理齐。"), (),
            fact_operations=({"op": "set", "key": key, "value": True, "visibility": "public"},))
        current = await runtime.commit_turn(ready, {"performance": original, "suggested_inputs": []})
    calls = []

    async def evaluate(self, **kwargs):
        return NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        return {"performance": original + "\n" + rejected, "suggested_inputs": ["马上动手。"],
                "transition_offered": True, "_published_transition_offer": rejected, "_transition_offer_provided": True,
                "fact_candidates": [{"key": key, "value": True, "evidence_quote": original}] if same_turn_unlock else []}

    async def approve(self, **kwargs):
        assert kwargs["claims"][0]["key"] == key
        return NumericV2StoryEvidenceReview((0,))

    async def contract(self, **kwargs):
        if proposed := kwargs.get("proposed_offer"):
            # A role's own reaction to pressure remains valid prose, but a
            # model's true flag cannot turn it into a player's invitation.
            kwargs["diagnostics"]["offer_actionable"] = True
            if proposed["text"] == offer:
                assert proposed["accept_input"] == accept
        return ()

    async def repair(self, **kwargs):
        calls.append(kwargs["visible_body"])
        assert kwargs["rejected_offer"] == rejected
        return {"offer": offer, "accept_input": accept, "stay_input": "先留在这里。"} if publishes else {
            "offer": "", "accept_input": "", "stay_input": ""}

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "verify_contract_boundaries", contract)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "repair_public_offer", repair)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "review_story_evidence", approve)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "克制。")
    result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("respond", current.session.revision, "稍等，我想想。" if mode == "defer" else "接下来怎么安排？"),
        ensure_current_binding=lambda _: _binding())
    assert calls == ([] if mode == "defer" else [original])
    assert result.performance["performance"] == original + "\n" + (offer if publishes else rejected)
    assert result.stored.session.transition_offered is publishes
    assert (accept in result.performance["suggested_inputs"]) is publishes
    restored = await runtime.restore_session("offer-repair")
    assert restored == result.stored
    if same_turn_unlock:
        issued = restored.ledger_events[-1]
        assert issued["routing_fact_operations"] == []
        assert restored.session.story_state["facts"][key]["value"] is True
        request = TurnRequestV2("accept", restored.session.revision, accept, "suggestion")
        accepted = runtime.prepare_turn(restored, request, (), transition_intent="accept")
        assert accepted.ledger_event["transition_intent"] == "accept"
        assert accepted.route["id"] == route["id"]
        assert "transition_offer_invalidated" not in accepted.ledger_event
        assert workflow._confirmed_authored_acceptance(engine, restored, request, require_program_invitation=True) == route["id"]
        held = runtime.prepare_turn(restored, TurnRequestV2("hold", restored.session.revision, "先留在这里。"), ())
        assert held.route is None and held.session.transition_offered
        blocked = runtime.prepare_turn(restored, request, (), transition_intent="accept",
            fact_operations=({"op": "set", "key": key, "value": False, "visibility": "public"},))
        assert blocked.route is None and blocked.ledger_event["transition_offer_invalidated"] is True
    if mode == "empty":
        forked = await runtime.fork_session_for_test("offer-repair", session_id="same-visit", through_revision=2)
        result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=forked,
            turn=TurnRequestV2("again", 2, "我们接下来怎么安排？"), ensure_current_binding=lambda _: _binding())
        assert calls == [original]
        assert result.performance["performance"] == original + "\n" + rejected
        assert not result.stored.session.transition_offered
        assert result.stored.ledger_events[-1].get("program_invitation") is None


def test_completed_anchor_only_drops_an_explicit_redo():
    story = story_for("档案室")
    key = "scene:start:done"
    story["fact_contract"]["facts"][key].update(evidence_source="performance", evidence_anchor="签署登记单")
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id="anchor", catgirl_binding=_binding(), opening_performance=_opening())
    outcome = engine.resolve_turn(session, TurnRequestV2("ready", 0, "办好了。"), (),
        fact_operations=({"op": "set", "key": key, "value": True, "visibility": "public"},))
    choices = ["（重新签署登记单）", "登记单签署完成了吗？", "（重新核对日期）"]
    assert workflow._drop_completed_action_suggestions(choices, engine, session) == choices
    assert workflow._drop_completed_action_suggestions(choices, engine, outcome.session) == choices[1:]


@pytest.mark.asyncio
@pytest.mark.parametrize('fact_source', ['evaluator', 'evaluator_denied', 'actor', 'actor_denied'])
async def test_ordinary_turn_reuses_already_committed_performance_evidence(speed_first, tmp_path, monkeypatch, fact_source):
    engine = NumericV2Engine.from_mapping(story_for('山径的避风棚'))
    runtime = NumericV2Runtime(engine, tmp_path)
    calls = []
    text = '眼前的事情已经处理完。'
    current = await runtime.start_session(session_id='speed-turn', catgirl_binding=_binding(),
        opening_performance={'performance': text, 'suggested_inputs': []})
    key = 'scene:start:done'
    async def evaluate(self, **kwargs):
        calls.append('evaluator')
        if not fact_source.startswith('evaluator'):
            return NumericV2EvaluationResult((), False)
        return NumericV2EvaluationResult((), False,
            fact_operations=({'op': 'set', 'key': key, 'value': True, 'visibility': 'public'},),
            fact_audit=({'key': key, 'evidence': [{'source': 'actor_performance', 'quote': text}]},))
    async def generate(self, **kwargs):
        calls.append('actor')
        candidates = [{'key': key, 'value': True, 'evidence_quote': text}] if fact_source.startswith('actor') else []
        return {'performance': text, 'suggested_inputs': [], 'fact_candidates': candidates}
    async def approve(self, **kwargs):
        calls.append('fact_review')
        assert kwargs['actor_evidence'] == (text if fact_source.startswith('actor') else '')
        assert kwargs['claims'][0]['key'] == key
        assert key not in kwargs['session'].story_state['facts']
        return NumericV2StoryEvidenceReview(() if fact_source.endswith('_denied') else (0,))
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'review_story_evidence', approve)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和克制。')
    diagnostics = {}
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('one', 0, text), ensure_current_binding=lambda _: _binding(),
        diagnostics_sink=diagnostics)
    assert calls == (['evaluator', 'fact_review', 'actor'] if fact_source.startswith('evaluator')
                     else ['evaluator', 'actor', 'fact_review'])
    assert result.stored.session.revision == 1
    assert (key in result.stored.session.story_state['facts']) is (not fact_source.endswith('_denied'))
    assert result.performance['performance'].startswith(text)
    assert diagnostics['transition_judge_calls'] == 1
    assert diagnostics['completion_fact_review_calls'] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("approved,deferred", [(False, False), (True, False), (True, True)])
async def test_completion_continuation_uses_verified_action_without_a_second_choice(
    speed_first, tmp_path, monkeypatch, approved, deferred,
):
    story = story_for("档案室")
    key = "scene:start:done"
    story["fact_contract"]["facts"][key].update(
        evidence_source="player_input", description="玩家已整理好手边的材料并盖好盖子。",
    )
    route = story["nodes"][0]["route_gates"][1]
    contract = route["transition_contract"]
    del contract["fallback_offer"], contract["accept_input"]
    contract["advance_on_completion"] = True
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="continue", catgirl_binding=_binding(), opening_performance=_opening())
    text = "（盖上盖子）都整理好了。" if approved else "等一下，我打算一会儿再整理。"
    if deferred:
        text = "（抬手）等一下，东西已经整理好了，我想先在这里待一会儿。"
    advanced = approved and not deferred
    calls = []

    async def evaluate(self, **kwargs):
        return NumericV2EvaluationResult((), False,
            fact_operations=({"op": "set", "key": key, "value": True, "visibility": "public"},),
            fact_audit=({"key": key, "evidence": [{"source": "player_input", "quote": text}]},))

    async def approve(self, **kwargs):
        assert kwargs["claims"][0]["evidence"][0]["quote"] == text
        assert not kwargs["pending_offer"]
        calls.append("evidence")
        return NumericV2StoryEvidenceReview((0,) if approved else ())

    async def generate(self, **kwargs):
        calls.append("actor")
        outcome = kwargs["outcome"]
        if not advanced:
            assert outcome.route is None
            return {"performance": "好，我等你。", "suggested_inputs": []}
        assert outcome.route["id"] == route["id"]
        return engine.finalize_transition_performance(outcome, {
            "source_performance": "好。", "bridge_scene_narration": "",
            "target_scene_narration": "整理后的空间变得宽敞。", "target_performance": "我们来看看吧。",
            "suggested_inputs": [],
        }, target_opening="整理后的空间变得宽敞。")

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "review_story_evidence", approve)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "克制。")
    result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("action", 0, text), ensure_current_binding=lambda _: _binding())
    assert calls == ["evidence", "actor"]
    assert result.stored.session.current_node_id == (route["target_node_id"] if advanced else "start")
    assert not result.stored.session.transition_offered
    assert (key in result.stored.session.story_state["facts"]) is approved
    cold = NumericV2Runtime(engine, tmp_path)
    assert await cold.restore_session("continue") == result.stored
    fork = await cold.fork_session_for_test("continue", session_id="fork", through_revision=1)
    assert fork.session.current_node_id == result.stored.session.current_node_id
    if deferred:
        assert fork.ledger_events[-1]["transition_intent"] == "reject"
        next_turn = cold.prepare_turn(fork, TurnRequestV2("continue-now", 1, "可以继续了。"), ())
        assert next_turn.session.current_node_id == route["target_node_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [False, True])
async def test_player_fact_asserted_by_actor_repairs_body_before_commit(speed_first, tmp_path, monkeypatch, approved):
    story = story_for("档案室")
    key = "scene:start:done"
    story["fact_contract"]["facts"][key]["evidence_source"] = "player_input"
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="source", catgirl_binding=_binding(), opening_performance=_opening())
    drafts = []
    quote = "玩家把材料都收好了。"
    player_input = "我已经把材料收好了。" if approved else "你先看看标签。"

    async def evaluate(self, **kwargs):
        if not approved:
            return NumericV2EvaluationResult((), False)
        return NumericV2EvaluationResult((), False,
            fact_operations=({"op": "set", "key": key, "value": True, "visibility": "public"},),
            fact_audit=({"key": key, "evidence": [{"source": "player_input", "quote": player_input}]},))

    async def approve(self, **kwargs):
        return NumericV2StoryEvidenceReview((0,))

    async def generate(self, **kwargs):
        drafts.append(kwargs.get("draft_repair_context"))
        if len(drafts) == 1:
            return {"scene_narration": quote, "performance": "我来帮忙。", "suggested_inputs": [],
                    "fact_candidates": [{"key": key, "value": True, "evidence_quote": quote}]}
        assert drafts[-1]["issues"][0]["quote"] == quote
        return {"performance": "我来查看标签。", "suggested_inputs": [], "fact_candidates": []}

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "review_story_evidence", approve)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "克制。")
    result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("ask", 0, player_input), ensure_current_binding=lambda _: _binding())
    assert len(drafts) == (1 if approved else 2)
    assert result.performance["performance"].splitlines()[0] == ("我来帮忙。" if approved else "我来查看标签。")
    assert (key in result.stored.session.story_state["facts"]) is approved


@pytest.mark.asyncio
async def test_reinvitation_uses_author_contract_without_generation_or_review(speed_first, tmp_path, monkeypatch):
    from tests.unit.test_theater_review_round8_invitation import setup_case, OFFER
    runtime, current = await setup_case(tmp_path, monkeypatch, mode='on')
    monkeypatch.setattr(workflow, 'aload_theater_module_options', options.aload_theater_module_options)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', speed_first)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', speed_first)
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('reinvite', current.session.revision, '', 'reinvite'),
        ensure_current_binding=lambda _: _binding())
    assert result.performance['performance'] == OFFER
    assert result.stored.session.current_node_id == current.session.current_node_id


@pytest.mark.asyncio
async def test_ending_question_uses_only_narrow_check_while_full_audits_stay_paused(speed_first, tmp_path, monkeypatch):
    from tests.unit.test_theater_numeric_v2_terminal_question_default import _setup, CHOICE
    _, _, calls, diagnostics, execute = await _setup(
        tmp_path, monkeypatch, drafts=(CHOICE, '（点头）故事到这里落幕。'))
    monkeypatch.setattr(workflow, 'aload_theater_module_options', options.aload_theater_module_options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', speed_first)
    result = await execute()
    assert result.stored.session.status == 'ended'
    assert calls['actor'] == 2 and len(calls['terminal']) == 1
    assert diagnostics['terminal_question_review_calls'] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('reply,intent,advance', [
    ('好，明早见。', 'accept', True),
    ('先签署文件吧，签完再说。', 'accept', False),
    ('先不决定，聊一会儿。', 'reject', False),
    ('你打算几点出发？', 'unclear', False),
])
async def test_unconfirmed_acceptance_and_optional_facts_share_one_evidence_check(
    speed_first, tmp_path, monkeypatch, reply, intent, advance,
):
    story = story_for('关外院落')
    story['nodes'][0].pop('completion_contract')
    key = 'scene:start:signed'
    story['fact_contract'] = {'facts': {
        key: {'value_type': 'bool', 'visibility': 'public', 'description': '双方已核对并签署文件。'},
    }}
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='sleep-offer', catgirl_binding=_binding(),
                                          opening_performance=_opening())
    calls = []
    offer = '今晚先休息，明早一起去关外院落交付，好吗？'

    async def evaluate(self, **kwargs):
        calls.append('evaluator')
        if kwargs['session'].revision and intent == 'accept' and not advance:
            return NumericV2EvaluationResult((), False, transition_intent='accept',
                transition_reply_target='pending_transition',
                fact_operations=({'op': 'set', 'key': key, 'value': True, 'visibility': 'public'},),
                fact_audit=({'key': key, 'evidence': [{'source': 'player_input', 'quote': reply}]},))
        return NumericV2EvaluationResult((), True,
            transition_intent='unclear' if kwargs['session'].revision == 0 else intent,
            transition_reply_target='pending_transition')

    async def generate(self, **kwargs):
        calls.append('actor')
        if kwargs['session'].revision == 0:
            return {'performance': offer, 'suggested_inputs': ['好，明早见。', '先不决定。'],
                    'transition_offered': True}
        if advance:
            from tests.unit.test_theater_numeric_v2_transition_history import _candidate
            return engine.finalize_transition_performance(kwargs['outcome'], _candidate(),
                                                          target_opening='关外的院落。')
        return {'performance': '那就先在这里聊聊。', 'suggested_inputs': [], 'transition_offered': False}

    async def approve(self, **kwargs):
        calls.append('evidence_review')
        assert kwargs['pending_offer'] == offer
        assert [claim['key'] for claim in kwargs['claims']] == ([] if advance else [key])
        return NumericV2StoryEvidenceReview(offer_decision="accept" if advance else "hold")

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'review_story_evidence', approve)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和克制。')
    offered = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime,
        current=current, turn=TurnRequestV2('offer', 0, '接下来呢？'), ensure_current_binding=lambda _: _binding())
    assert offered.stored.session.transition_offered
    assert offered.performance['performance'] == offer
    assert offered.performance.get('transition_offer_source') != 'reviewed'
    restored = await runtime.restore_session(current.session.session_id)
    assert restored == offered.stored
    result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime,
        current=restored, turn=TurnRequestV2('reply', 1, reply), ensure_current_binding=lambda _: _binding())
    assert (result.stored.session.current_node_id != current.session.current_node_id) is advance
    assert result.stored.session.transition_offered is (not advance and intent != 'reject')
    assert key not in result.stored.session.story_state['facts']
    assert calls == ['evaluator', 'actor', 'evaluator'] + (['evidence_review'] if intent == 'accept' else []) + ['actor']
    assert result.diagnostics['transition_judge_calls'] == int(intent == 'accept')
    assert 'narrative_model_attempts' not in result.diagnostics


@pytest.mark.asyncio
@pytest.mark.parametrize('requested,intent,expect_author_offer', [
    (True, 'unclear', True), (False, 'unclear', True), (True, 'reject', False),
])
async def test_completed_actor_offer_has_the_actual_author_arrangement(
    speed_first, tmp_path, monkeypatch, requested, intent, expect_author_offer,
):
    engine = NumericV2Engine.from_mapping(story_for('山径的避风棚'))
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='completed-offer', catgirl_binding=_binding(),
        opening_performance=_opening())
    prepared = runtime.prepare_turn(current, TurnRequestV2('complete', 0, '完成眼前工作。'), (),
        fact_operations=({'op': 'set', 'key': 'scene:start:done', 'value': True, 'visibility': 'public'},))
    current = await runtime.commit_turn(prepared, {'performance': '眼前的事情已经处理完。', 'suggested_inputs': []})
    calls = []
    body = '好，我们试着把手里的东西轻轻转一下。'
    async def evaluate(self, **kwargs):
        calls.append('evaluator')
        return NumericV2EvaluationResult((), True, transition_intent=intent)
    async def generate(self, **kwargs):
        calls.append('actor')
        return {'performance': body, 'suggested_inputs': ['再转一次。', '先停一下。'],
                'transition_offered': requested}
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和克制。')
    result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('respond', 1, '再试着转一下。'), ensure_current_binding=lambda _: _binding())
    contract = engine.preview_route('start', current.session.metrics)['transition_contract']
    assert (contract['fallback_offer'] in result.performance['performance']) is expect_author_offer
    assert body in result.performance['performance']
    if expect_author_offer:
        assert result.performance['suggested_inputs'][0] == contract['accept_input']
        assert '先停一下。' in result.performance['suggested_inputs']
        assert result.stored.session.transition_offered
    assert calls == ['evaluator', 'actor']
    assert result.stored.session.current_node_id == 'start'
    assert await runtime.restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize('declared,complete,intent,expected', [
    (False, True, 'unclear', True),
    (False, False, 'unclear', False),
    (False, True, 'reject', False),
    (True, True, 'unclear', False),
])
async def test_legacy_completed_scene_can_offer_without_overriding_explicit_conditions(
    speed_first, tmp_path, monkeypatch, declared, complete, intent, expected,
):
    story = story_for('山径的避风棚')
    if not declared:
        story['nodes'][0].pop('completion_contract')
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='legacy-completion', catgirl_binding=_binding(),
        opening_performance=_opening())
    calls = []

    async def evaluate(self, **kwargs):
        calls.append('evaluator')
        return NumericV2EvaluationResult((), complete, transition_intent=intent)

    async def generate(self, **kwargs):
        calls.append('actor')
        return {'performance': '眼前的事情已经处理完。', 'suggested_inputs': ['先聊一会儿。'],
                'transition_offered': False}

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和克制。')
    result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime,
        current=current, turn=TurnRequestV2('ask', 0, '接下来呢？'),
        ensure_current_binding=lambda _: _binding())
    contract = engine.preview_route('start', current.session.metrics)['transition_contract']
    assert (contract['fallback_offer'] in result.performance['performance']) is expected
    assert result.stored.session.transition_offered is expected
    assert result.stored.session.current_node_id == 'start'
    assert calls == ['evaluator', 'actor']
    if expected:
        assert result.performance['suggested_inputs'][0] == contract['accept_input']
        assert '先聊一会儿。' in result.performance['suggested_inputs']
    assert await runtime.restore_session(current.session.session_id) == result.stored
