"""Explicit invitation recovery preserves consent and cold replay."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.theater import numeric_v2_workflow as workflow
from services.theater.numeric_v2_runtime import NumericV2Runtime, NumericV2RuntimeError, TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_review_round8_invitation import ACCEPT, OFFER, setup_case, turn


async def reinvite(runtime, current, key='reinvite'):
    return await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime,
        current=current, turn=TurnRequestV2(key, current.session.revision, '重新邀请', 'reinvite'),
        ensure_current_binding=lambda _: _binding())


@pytest.mark.asyncio
@pytest.mark.parametrize('review,mode', [(False, 'on'), (True, 'off'), (True, 'failure')])
async def test_expired_invitation_can_be_reissued_and_accepted_after_cold_restore(tmp_path, monkeypatch, review, mode):
    runtime, current = await setup_case(tmp_path, monkeypatch, mode=mode, review=review)
    offered = await turn(runtime, current, 'offer', '接下来呢？')
    followed = await turn(runtime, offered.stored, 'followup', '先问个问题。')
    assert not followed.stored.session.transition_offered
    recovered = await reinvite(runtime, followed.stored)
    assert recovered.performance['performance'] == OFFER
    assert recovered.performance['suggested_inputs'] == []
    assert recovered.stored.session.current_node_id == followed.stored.session.current_node_id
    assert recovered.stored.session.metrics == followed.stored.session.metrics
    assert recovered.stored.session.node_turn_count == followed.stored.session.node_turn_count
    assert recovered.stored.ledger_events[-1]['program_invitation']['offer'] == OFFER
    assert recovered.stored.ledger_events[-1]['input_text'] == ''
    assert recovered.stored.session.performance_history[-1]['input_text'] == ''
    assert recovered.stored.session.performance_history[-1]['input_source'] == 'reinvite'
    scope = recovered.stored.ledger_events[-1]['timeline_projection']['scene_scope']
    assert scope['visit_id'] == followed.stored.ledger_events[-1]['timeline_projection']['scene_scope']['visit_id']
    forked = await runtime.fork_session_for_test(current.session.session_id,
        session_id='control-fork', through_revision=recovered.stored.session.revision)
    assert forked.session.node_turn_count == recovered.stored.session.node_turn_count
    assert forked.session.performance_history[-1]['input_text'] == ''
    fork_accepted = await turn(runtime, forked, 'fork-accept', '', 'accept_invitation')
    assert fork_accepted.stored.session.current_node_id == 'ending_leave'
    restored = await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id)
    assert restored == recovered.stored
    accepted = await turn(runtime, restored, 'accept-new', '', 'accept_invitation')
    assert accepted.stored.session.current_node_id == 'ending_leave'


@pytest.mark.asyncio
async def test_multiple_reinvitations_preserve_visit_and_model_history(tmp_path, monkeypatch):
    import json
    from services.theater.numeric_v2_archive import build_numeric_v2_public_archive
    from services.theater.numeric_v2_actor import _repeats_earlier_session_performance, _turn_messages
    from services.theater.numeric_v2_evaluator import _current_scene_context, _build_transition_judge_messages
    from services.theater.numeric_v2_context import performance_history_records
    from services.theater.numeric_v2_runtime import current_visit_started_revision

    runtime, current = await setup_case(tmp_path, monkeypatch)
    for index in range(2):
        offered = await reinvite(runtime, current, f'reinvite-{index}')
        continued = await turn(runtime, offered.stored, f'follow-{index}', '先问个问题。')
        current = continued.stored
        assert current_visit_started_revision(current.session) == 0
        assert current.ledger_events[-1]['timeline_projection']['scene_scope']['visit_id'] == 'start:r0'
    assert all(row['player_input'] != '重新邀请' for row in _current_scene_context(current.session))
    assert '重新邀请' not in str(performance_history_records(current.session))
    prepared = runtime.prepare_turn(current, TurnRequestV2('next', current.session.revision, '继续聊。'), ())
    messages = _turn_messages(runtime.engine, current.session, prepared, '继续聊。', '温和。', '猫娘', '你')
    assert '重新邀请' not in str(messages)
    assert _repeats_earlier_session_performance({'performance': OFFER}, current.session, route_changed=False)
    archive = build_numeric_v2_public_archive(title='测试', session=current.session, ending=None)
    assert all(row['player_input'] != '重新邀请' for row in archive['turns'])
    judge = _build_transition_judge_messages(runtime.engine, current.session,
        actor_performance={'performance': '继续聊。'}, player_input='继续聊。')[0]
    payload = json.loads(judge[1].content.split('：', 1)[1])
    assert payload['current_visit_history_complete'] is True


@pytest.mark.asyncio
async def test_dead_authored_quote_recovery_has_no_model_calls(tmp_path, monkeypatch):
    runtime, current = await setup_case(tmp_path, monkeypatch, text=OFFER + '（摇头）先别去了。')
    blocked = await turn(runtime, current, 'quote', '接下来呢？')
    assert not blocked.stored.session.transition_offered

    async def forbidden(*args, **kwargs):
        raise AssertionError('reinvitation must not call a model')

    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', forbidden)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', forbidden)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', forbidden)
    recovered = await reinvite(runtime, blocked.stored)
    assert recovered.performance['performance'] == OFFER
    assert not workflow.invitation_recovery_contract(runtime, recovered.stored)


@pytest.mark.asyncio
@pytest.mark.parametrize('guard', ['incomplete', 'forbidden', 'missing_offer', 'missing_accept', 'terminal'])
async def test_unavailable_reinvitation_does_not_commit(tmp_path, monkeypatch, guard):
    runtime, current = await setup_case(tmp_path, monkeypatch, complete=guard != 'incomplete')
    if guard == 'forbidden':
        current = replace(current, session=replace(current.session, dialogue_policy='forbidden'))
    if guard in {'missing_offer', 'missing_accept'}:
        runtime.engine.nodes['start']['route_gates'][1]['transition_contract'][
            'fallback_offer' if guard == 'missing_offer' else 'accept_input'] = ''
    if guard == 'terminal':
        runtime.engine.nodes['ending_leave']['terminal'] = True
    assert workflow.invitation_recovery_contract(runtime, current) is None
    with pytest.raises(NumericV2RuntimeError, match='numeric_reinvitation_not_available'):
        await reinvite(runtime, current)
    restored = await runtime.restore_session(current.session.session_id)
    assert restored.session.revision == current.session.revision


@pytest.mark.asyncio
async def test_router_reinvitation_replay_and_stale_revision(tmp_path, monkeypatch):
    from main_routers import numeric_theater_router as router

    runtime, current = await setup_case(tmp_path, monkeypatch)

    async def get_runtime(*args):
        return runtime

    async def no_forget(*args):
        return False

    async def commit_allowed(*args):
        return None

    monkeypatch.setattr(router, 'get_config_manager', object)
    monkeypatch.setattr(router, '_runtime_for_story', get_runtime)
    monkeypatch.setattr(router, '_ensure_current_catgirl', lambda *args: _binding())
    monkeypatch.setattr(router, '_forget_pending', no_forget)
    monkeypatch.setattr(router, '_assert_commit_allowed', commit_allowed)
    payload = {'client_turn_id': 'control', 'base_revision': current.session.revision,
               'message': '重新邀请', 'input_source': 'reinvite'}
    first = await router._submit_numeric_input_once(payload, current.session.story_package_id, current.session.session_id)
    assert first.status_code == 422
    assert json.loads(first.body)['reason'] == 'numeric_reinvitation_not_available'
    assert (await runtime.restore_session(current.session.session_id)).session.revision == current.session.revision

@pytest.mark.asyncio
@pytest.mark.parametrize('review', [False, True])
async def test_reinvitation_respects_required_narration_and_review_switch(tmp_path, monkeypatch, review):
    from main_routers import numeric_theater_router as router
    from tests.unit.test_theater_numeric_v2_fixed_narration import _piece

    runtime, current = await setup_case(tmp_path, monkeypatch, review=review)
    runtime.engine.nodes['start']['story_beat']['fixed_narrations'] = [_piece('pending', '必显原文。')]
    async def options():
        return {'review': review}
    monkeypatch.setattr('services.theater.numeric_v2_options.aload_theater_module_options', options)
    monkeypatch.setattr(workflow, 'aload_theater_module_options', options)
    payload = await router._numeric_payload(runtime, current)
    assert 'invitation_recovery_available' not in payload
    if review:
        with pytest.raises(NumericV2RuntimeError, match='numeric_reinvitation_not_available'):
            await reinvite(runtime, current)
        assert (await runtime.restore_session(current.session.session_id)).session.revision == current.session.revision
        piece = {'node_id': 'start', 'id': 'pending', 'text': '必显原文。', 'bindings': {}, 'position': 'after'}
        current = replace(current, session=replace(current.session,
            performance_history=(*current.session.performance_history, {'fixed_narrations': [piece]})))
        assert 'invitation_recovery_available' not in await router._numeric_payload(runtime, current)
        # Persisted delivery is checked by the same helper used at submission.
        assert workflow.invitation_recovery_contract(runtime, current)
    else:
        assert (await reinvite(runtime, current)).stored.session.transition_offered


@pytest.mark.asyncio
@pytest.mark.parametrize('review,mode', [(False, 'on'), (True, 'off'), (True, 'on')])
async def test_non_silent_reinvitation_reviews_complete_candidate_once(tmp_path, monkeypatch, review, mode):
    from services.theater import numeric_v2_evaluator as ev

    runtime, current = await setup_case(tmp_path, monkeypatch, review=review, mode=mode)
    seen = []
    original = deepcopy(current)

    async def review_offer(self, **kwargs):
        seen.append(deepcopy(kwargs))
        assert kwargs['session'] == current.session
        assert kwargs['message'] == ''
        assert kwargs['actor_performance'] == {'performance': OFFER, 'suggested_inputs': []}
        assert kwargs['completion_fact_review_only'] is True
        assert kwargs['invitation_proposal_only'] is True
        assert 0 < kwargs['timeout_seconds'] <= 8.0
        return ev.NumericV2TransitionOfferReview(True, True, (), (), offer_quote=OFFER,
            offer_kind='invitation', approved_suggestion_texts=(ACCEPT,), offer_acceptance_valid=True)

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review_offer)
    result = await reinvite(runtime, current)
    assert len(seen) == 1
    assert current == original
    assert result.performance['performance'] == OFFER
    assert result.performance['suggested_inputs'] == []
    assert result.stored.session.metrics == current.session.metrics
    assert result.stored.session.node_turn_count == current.session.node_turn_count
    assert result.stored.ledger_events[-1]['input_text'] == ''
    assert result.stored.ledger_events[-1]['program_invitation']['offer'] == OFFER


@pytest.mark.asyncio
@pytest.mark.parametrize('verdict', [
    'not_present', 'missing_quote', 'wrong_quote', 'exit_only', 'invalid', 'body', 'unsafe',
    'error', 'timeout',
])
async def test_non_silent_reinvitation_rejection_keeps_store_unchanged(tmp_path, monkeypatch, verdict):
    import asyncio
    from services.theater import numeric_v2_evaluator as ev

    runtime, current = await setup_case(tmp_path, monkeypatch, mode='on')
    before = runtime.store._path(current.session.session_id).read_bytes()
    calls = []

    async def review_offer(self, **kwargs):
        calls.append(kwargs)
        if verdict == 'error':
            raise ev.NumericV2EvaluatorError('numeric_v2_evaluator_invalid_json')
        if verdict == 'timeout':
            await asyncio.sleep(0.1)
        changes = {
            'not_present': {'offer_present': False},
            'missing_quote': {'offer_quote': ''},
            'wrong_quote': {'offer_quote': '我们去另一处吧。'},
            'exit_only': {'offer_kind': 'exit_mention_only'},
            'invalid': {'valid': False},
            'body': {'body_violations': ('author_boundary',)},
            'unsafe': {'unsafe_suggestion_indexes': (0,)},
        }.get(verdict, {})
        return replace(ev.NumericV2TransitionOfferReview(True, True, (), (), offer_quote=OFFER,
            offer_kind='invitation', approved_suggestion_texts=(ACCEPT,)), **changes)

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review_offer)
    if verdict == 'timeout':
        monkeypatch.setattr(workflow, 'NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS', 0.01)
    with pytest.raises(workflow.NumericV2ActorOutputError, match='^numeric_v2_transition_review_failed$'):
        await reinvite(runtime, current)
    assert len(calls) == 1
    assert runtime.store._path(current.session.session_id).read_bytes() == before
    assert await runtime.restore_session(current.session.session_id) == current


@pytest.mark.asyncio
async def test_authored_invitation_review_keeps_full_body_but_only_original_acceptance(monkeypatch):
    from services.theater import numeric_v2_evaluator as ev

    performance = {'performance': '（收起自己的笔）已经办妥。' + OFFER,
        'scene_narration': '管事收好了文书。', 'suggested_inputs': ['再等等。', ACCEPT]}
    before = deepcopy(performance)
    seen = []

    class Evaluator:
        async def validate_transition_offer(self, **kwargs):
            seen.append(kwargs)
            return ev.NumericV2TransitionOfferReview(True, True, (), (), offer_quote=OFFER,
                offer_kind='invitation', approved_suggestion_texts=(ACCEPT,))

    assert await workflow._review_authored_invitation(evaluator=Evaluator(), engine=object(),
        session=object(), message='手续怎么样？', performance=performance, offer=OFFER,
        accept_input=ACCEPT, timeout_seconds=20.0)
    assert performance == before
    assert len(seen) == 1
    assert seen[0]['actor_performance'] == {**performance, 'suggested_inputs': [ACCEPT]}
    assert seen[0]['timeout_seconds'] == 8.0


@pytest.mark.asyncio
@pytest.mark.parametrize('mixed_quote', [False, True])
async def test_dynamic_reinvitation_binds_actual_acceptance_after_cold_restore(tmp_path, monkeypatch, mixed_quote):
    from services.theater import numeric_v2_evaluator as ev
    runtime, current = await setup_case(tmp_path, monkeypatch, mode='on')
    new_offer = '事情都交代清楚了，接下来一起去阅览室吧？'
    if mixed_quote:
        new_offer = '（收好自己的笔）' + new_offer
    calls = []

    async def generate(self, **kwargs):
        calls.append(kwargs)
        assert kwargs['player_input'] == '' and kwargs['input_source'] == 'reinvite'
        assert kwargs['recent_ledger_events'] == current.ledger_events
        assert kwargs['allow_suggestion_fill'] is False
        return {'performance': new_offer, 'suggested_inputs': [], 'transition_offered': True}

    async def review(self, **kwargs):
        assert kwargs['invitation_proposal_only'] is True
        assert 'require_offer_acceptance' not in kwargs
        assert kwargs['actor_performance']['suggested_inputs'] == []
        return ev.NumericV2TransitionOfferReview(True, True, (), (), offer_quote=new_offer,
            offer_kind='invitation')

    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    result = await reinvite(runtime, current)
    receipt = result.stored.ledger_events[-1]['program_invitation']
    assert receipt['kind'] == 'reviewed_proposal' and receipt['offer'] == new_offer
    assert 'accept_input' not in receipt and receipt['revision'] == result.stored.session.revision
    restored = await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id)
    request = TurnRequestV2('accept-real', restored.session.revision, '', 'accept_invitation')
    assert workflow._confirmed_authored_acceptance(runtime.engine, restored, request, require_program_invitation=True)
    assert not workflow._confirmed_authored_acceptance(runtime.engine, restored,
        replace(request, message='先等等。'), require_program_invitation=True)
    assert not workflow._confirmed_authored_acceptance(runtime.engine, restored,
        replace(request, input_source='freeform'), require_program_invitation=True)
    for field, value in [('revision', -1), ('target_node_id', 'other'), ('performance', OFFER), ('visible_blocks', [])]:
        ledger = deepcopy(restored.ledger_events)
        ledger[-1]['program_invitation'][field] = value
        assert not workflow._confirmed_authored_acceptance(runtime.engine,
            replace(restored, ledger_events=ledger), request, require_program_invitation=True)
    # Runtime仍独立选择同一路线；控制不完成移动，只有下一轮实际点选后才能换幕。
    prepared = runtime.prepare_turn(restored, request, (), transition_intent='accept')
    assert prepared.session.current_node_id == 'ending_leave'
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('offer,quote,accepted', [
    ('（让开门口）我们一起去观测台吧。', '（让开门口）我们一起去观测台吧。', True),
    ('可以了。（收好记录板）一起去观测台吧。', '（收好记录板）一起去观测台吧。', True),
    ('(指向连廊)Let us go to the observatory.', '(指向连廊)Let us go to the observatory.', True),
    ('（让开门口）我们一起去观测台吧。', '我们一起去观测台吧。', True),
    # 相同对白不证明伪造的动作引文来自本次稿；不能先丢动作再查出处。
    ('（让开门口）我们一起去观测台吧。', '（已经打开星图）我们一起去观测台吧。', False),
    ('（让开门口）我们一起去观测台吧。', '（让开门口）', False),
    ('（让开门口）我们一起去观测台吧。', '让开门口）我们一起去观测台吧。', False),
    ('（让开门口）我们一起去观测台吧。', '（让开门口）我们一起去机房吧。', False),
])
async def test_invitation_quote_provenance_preserves_mixed_delivery(offer, quote, accepted):
    from services.theater import numeric_v2_evaluator as ev

    acceptance = '好，我们现在过去。'
    candidate = {'performance': offer, 'suggested_inputs': [acceptance]}
    original = deepcopy(candidate)
    calls = []

    class Evaluator:
        async def validate_transition_offer(self, **kwargs):
            calls.append(kwargs)
            assert kwargs['actor_performance'] == original
            assert kwargs['require_offer_acceptance'] is True
            return ev.NumericV2TransitionOfferReview(True, True, (), (), offer_quote=quote,
                offer_kind='invitation', approved_suggestion_texts=(acceptance,), offer_acceptance_valid=True)

    result = await workflow._review_authored_invitation(evaluator=Evaluator(), engine=object(),
        session=object(), message='', performance=candidate, offer=offer,
        accept_input=acceptance, timeout_seconds=8, require_offer_acceptance=True)
    assert result is accepted
    assert len(calls) == 1
    assert candidate == original


@pytest.mark.asyncio
async def test_legacy_deferral_still_needs_separate_acceptance_approval(tmp_path, monkeypatch):
    from services.theater import numeric_v2_evaluator as ev
    runtime, current = await setup_case(tmp_path, monkeypatch, mode='on')
    before = runtime.store._path(current.session.session_id).read_bytes()

    async def review(self, **kwargs):
        return ev.NumericV2TransitionOfferReview(True, True, (), (), offer_quote=OFFER,
            offer_kind='invitation', approved_suggestion_texts=('先等等。',), offer_acceptance_valid=False)

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    assert not await workflow._review_authored_invitation(
        evaluator=workflow.NumericV2MetricEvaluator(object()), engine=runtime.engine,
        session=current.session, message='', performance={'performance': OFFER},
        offer=OFFER, accept_input='先等等。', timeout_seconds=8, require_offer_acceptance=True)
    assert runtime.store._path(current.session.session_id).read_bytes() == before


@pytest.mark.asyncio
async def test_dynamic_reinvitation_does_not_commit_after_persona_changes(tmp_path, monkeypatch):
    from services.theater import numeric_v2_evaluator as ev
    runtime, current = await setup_case(tmp_path, monkeypatch, mode='on')
    before = runtime.store._path(current.session.session_id).read_bytes()
    profile = ['温和，简短回应。']
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: profile[0])

    async def generate(self, **kwargs):
        assert kwargs['character_profile'] == '温和，简短回应。'
        profile[0] = '冷静，认真回答。'
        return {'performance': OFFER, 'suggested_inputs': [], 'transition_offered': True}

    async def review(self, **kwargs):
        return ev.NumericV2TransitionOfferReview(True, True, (), (), offer_quote=OFFER,
            offer_kind='invitation', approved_suggestion_texts=(ACCEPT,), offer_acceptance_valid=True)

    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    with pytest.raises(ValueError, match='catgirl_profile_changed_requires_retry'):
        await reinvite(runtime, current)
    assert runtime.store._path(current.session.session_id).read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('review_enabled', [False, True])
async def test_proposal_control_accepts_without_player_text_or_evaluator(tmp_path, monkeypatch, review_enabled):
    from services.theater import numeric_v2_evaluator as ev

    runtime, current = await setup_case(tmp_path, monkeypatch, mode='on', review=review_enabled)
    generated = workflow.NumericV2Actor.generate_turn
    calls = []

    async def generate(self, **kwargs):
        if kwargs['input_source'] == 'reinvite':
            return {'performance': OFFER, 'suggested_inputs': [], 'transition_offered': True}
        calls.append(kwargs)
        return await generated(self, **kwargs)

    async def no_evaluation(*args, **kwargs):
        raise AssertionError('an explicit program acceptance is not prose to score or classify')

    async def review(self, **kwargs):
        if kwargs.get('invitation_proposal_only'):
            assert kwargs['actor_performance'] == {'performance': OFFER, 'suggested_inputs': []}
            assert 'require_offer_acceptance' not in kwargs
            return ev.NumericV2TransitionOfferReview(True, True, (), (),
                offer_quote=OFFER, offer_kind='invitation')
        assert kwargs['confirmed_acceptance'] is True
        return ev.NumericV2TransitionOfferReview(False, True, (), (), acceptance_authorized=True,
            approved_suggestion_texts=tuple(kwargs['actor_performance'].get('suggested_inputs') or ()))

    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', no_evaluation)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    offered = await reinvite(runtime, current)
    assert offered.performance['suggested_inputs'] == []
    receipt = offered.stored.ledger_events[-1]['program_invitation']
    assert receipt['kind'] == 'reviewed_proposal' and 'accept_input' not in receipt
    assert runtime.engine.invitation_acceptance_route(offered.stored.session,
        ledger_events=offered.stored.ledger_events)
    accepted = await turn(runtime, offered.stored, 'accept-control', '', 'accept_invitation')
    assert accepted.stored.session.current_node_id == 'ending_leave'
    assert accepted.stored.session.metrics == current.session.metrics
    assert accepted.stored.ledger_events[-1]['input_source'] == 'accept_invitation'
    assert accepted.stored.ledger_events[-1]['input_text'] == ''
    assert accepted.diagnostics['evaluator_model_attempts'] == 0
    assert accepted.diagnostics['evaluator_skip_reason'] == 'invitation_control'
    assert len(calls) == 1 and calls[0]['player_input'] == ''
    restored = await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id)
    assert restored == accepted.stored
    forked = await runtime.fork_session_for_test(current.session.session_id,
        session_id='accepted-control-fork', through_revision=accepted.stored.session.revision)
    assert forked.session.current_node_id == accepted.stored.session.current_node_id


@pytest.mark.asyncio
@pytest.mark.parametrize('button', ['先完成原任务，下一阶段等会再说。', '你去核对，我继续留在这里。'])
async def test_actor_buttons_cannot_acquire_program_acceptance_even_if_review_allows(tmp_path, monkeypatch, button):
    from services.theater import numeric_v2_evaluator as ev

    runtime, current = await setup_case(tmp_path, monkeypatch, mode='on')
    before = runtime.store._path(current.session.session_id).read_bytes()
    seen = []

    async def generate(self, **kwargs):
        return {'performance': OFFER, 'suggested_inputs': [button], 'transition_offered': True}

    async def approve_everything(self, **kwargs):
        seen.append(kwargs)
        return ev.NumericV2TransitionOfferReview(True, True, (), (), offer_quote=OFFER,
            offer_kind='invitation', approved_suggestion_texts=(button,), offer_acceptance_valid=True)

    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', approve_everything)
    with pytest.raises(workflow.NumericV2ActorOutputError):
        await reinvite(runtime, current)
    assert seen == []
    assert runtime.store._path(current.session.session_id).read_bytes() == before


@pytest.mark.asyncio
async def test_accept_control_without_receipt_is_rejected_before_any_model(tmp_path, monkeypatch):
    runtime, current = await setup_case(tmp_path, monkeypatch, mode='on')
    before = runtime.store._path(current.session.session_id).read_bytes()

    async def forbidden(*args, **kwargs):
        raise AssertionError('invalid controls must be rejected before model work')

    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', forbidden)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', forbidden)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', forbidden)
    with pytest.raises(NumericV2RuntimeError, match='numeric_invitation_acceptance_not_available'):
        await turn(runtime, current, 'forged-control', '', 'accept_invitation')
    assert runtime.store._path(current.session.session_id).read_bytes() == before


@pytest.mark.asyncio
async def test_router_accept_control_is_current_revision_bound_and_idempotent(tmp_path, monkeypatch):
    from main_routers import numeric_theater_router as router

    runtime, current = await setup_case(tmp_path, monkeypatch, mode='on')
    offered = await reinvite(runtime, current)

    async def get_runtime(*args):
        return runtime

    async def no_forget(*args):
        return False

    async def commit_allowed(*args):
        return None

    async def forbidden(*args, **kwargs):
        raise AssertionError('accepted controls do not call the ordinary Evaluator')

    monkeypatch.setattr(router, 'get_config_manager', object)
    monkeypatch.setattr(router, '_runtime_for_story', get_runtime)
    monkeypatch.setattr(router, '_ensure_current_catgirl', lambda *args: _binding())
    monkeypatch.setattr(router, '_forget_pending', no_forget)
    monkeypatch.setattr(router, '_assert_commit_allowed', commit_allowed)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', forbidden)
    assert (await router._numeric_payload(runtime, offered.stored))['invitation_acceptance_available']
    payload = {'client_turn_id': 'accept-control', 'base_revision': offered.stored.session.revision,
        'message': '', 'input_source': 'accept_invitation'}
    first = await router._submit_numeric_input_once(payload, current.session.story_package_id, current.session.session_id)
    assert first['ok'] and first['resolved_turn']['route_changed']
    assert first['invitation_acceptance_available'] is False
    replay = await router._submit_numeric_input_once(payload, current.session.story_package_id, current.session.session_id)
    assert replay['ok'] and replay['idempotent_replay']
    assert replay['session']['revision'] == first['session']['revision']
    stale = await router._submit_numeric_input_once({**payload, 'client_turn_id': 'stale-control'},
        current.session.story_package_id, current.session.session_id)
    assert stale.status_code == 409
    unavailable = await router._submit_numeric_input_once({**payload, 'client_turn_id': 'used-control',
        'base_revision': first['session']['revision']}, current.session.story_package_id, current.session.session_id)
    assert unavailable.status_code == 409
    assert (await runtime.restore_session(current.session.session_id)).session.revision == first['session']['revision']


@pytest.mark.asyncio
async def test_review_can_withdraw_a_proposal_after_explicit_acceptance(tmp_path, monkeypatch):
    from services.theater import numeric_v2_evaluator as ev

    runtime, current = await setup_case(tmp_path, monkeypatch, mode='on', review=True)
    offered = await reinvite(runtime, current)
    seen = []

    async def review(self, **kwargs):
        seen.append(kwargs)
        if kwargs.get('route_changed'):
            assert kwargs['confirmed_acceptance'] is True
            # 原 Parser 会把明确撤回接受授权归入 player_action，Mock 保持这个真实结果合同。
            return ev.NumericV2TransitionOfferReview(False, True, ('player_action',), (),
                acceptance_authorized=False, pending_invitation_invalid=True)
        return ev.NumericV2TransitionOfferReview(False, True, (), (),
            approved_suggestion_texts=tuple(kwargs['actor_performance'].get('suggested_inputs') or ()))

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    result = await turn(runtime, offered.stored, 'accept-invalid-proposal', '', 'accept_invitation')
    assert result.stored.session.current_node_id == current.session.current_node_id
    assert result.stored.session.transition_offered is False
    assert result.stored.ledger_events[-1]['transition_offer_invalidated'] is True
    assert result.stored.ledger_events[-1]['transition_intent'] == 'unclear'
    assert result.stored.ledger_events[-1]['input_text'] == ''
    assert result.diagnostics['evaluator_model_attempts'] == 0
    assert any(row.get('route_changed') for row in seen)
    assert await runtime.restore_session(current.session.session_id) == result.stored
