"""Program-issued invitation authorization for the approved conservative contract."""

from copy import deepcopy

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as workflow
from services.theater.numeric_v2_options import default_options
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_transition_history import _candidate

OFFER = '手续办妥了，我们现在去阅览室吧。'
ACCEPT = '（点头）好，带路吧。'


async def setup_case(tmp_path, monkeypatch, *, complete=True, mode='off', text='先留在这里。', review=False):
    engine = initiation_case()['engine']
    engine.nodes['start']['route_gates'][1]['transition_contract'].update(
        fallback_offer=OFFER, accept_input=ACCEPT)
    engine.fact_contract['scene:start:done'] = {
        'value_type': 'bool', 'visibility': 'public', 'description': '办妥手续。'}
    engine.nodes['start']['completion_contract'] = {
        'all': [{'key': 'scene:start:done', 'equals': True}]}
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='program-offer', catgirl_binding=_binding(),
        opening_performance={'performance': '先办手续。', 'suggested_inputs': []})
    if complete:
        prepared = runtime.prepare_turn(current, TurnRequestV2('done', 0, '办好了。'), (),
            fact_operations=({'op': 'set', 'key': 'scene:start:done', 'value': True, 'visibility': 'public'},))
        current = await runtime.commit_turn(prepared, {'performance': '办好了。', 'suggested_inputs': []})

    async def options():
        return {**default_options(), 'review': review, 'evaluator': mode != 'off',
                'review_delivery': False, 'review_contract': False, 'actor_retry': False}

    async def evaluate(self, **kwargs):
        if mode == 'failure':
            raise ev.NumericV2EvaluatorError('numeric_v2_evaluator_invalid_json')
        # Deliberately wrong acceptance must not bypass program authorization.
        return ev.NumericV2EvaluationResult((), False, transition_intent='accept')

    async def generate(self, **kwargs):
        outcome = kwargs['outcome']
        if outcome.ledger_event['from_node_id'] != outcome.ledger_event['to_node_id']:
            return engine.finalize_transition_performance(outcome, _candidate(),
                target_opening='两人在阅览室入口，后续操作尚未开始。',
                bridge_scene_narration='两人沿左侧走廊来到阅览室。')
        return {'performance': text, 'suggested_inputs': [ACCEPT, '再等等。'],
                'transition_offered': True,
                'program_invitation': {'route_id': 'fake', 'offer': OFFER, 'accept_input': ACCEPT}}

    async def validate_offer(self, **kwargs):
        return ev.NumericV2TransitionOfferReview(False, True, (), (),
            acceptance_authorized=True if kwargs.get('route_changed') else None)

    monkeypatch.setattr(workflow, 'aload_theater_module_options', options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', validate_offer)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    return runtime, current


async def turn(runtime, current, key, message, source='free'):
    return await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime,
        current=current, turn=TurnRequestV2(key, current.session.revision, message, input_source=source),
        ensure_current_binding=lambda _: _binding())


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['手续办好了。', OFFER, OFFER + '（歪头）等等，先别去了。'])
@pytest.mark.parametrize('mode', ['off', 'on', 'failure'])
async def test_actor_flags_and_literal_quotes_never_issue_authorization(tmp_path, monkeypatch, text, mode):
    runtime, current = await setup_case(tmp_path, monkeypatch, complete=False, mode=mode, text=text)
    offered = await turn(runtime, current, 'offer', '手续怎么样？')
    assert not offered.stored.session.transition_offered
    assert 'program_invitation' not in offered.stored.ledger_events[-1]
    assert offered.performance['suggested_inputs'] == []
    accepted = await turn(runtime, offered.stored, 'accept', ACCEPT, 'suggestion')
    assert accepted.stored.session.current_node_id == 'start'


@pytest.mark.asyncio
@pytest.mark.parametrize('review,mode', [(False, 'off'), (False, 'on'), (False, 'failure'),
                                       (True, 'off'), (True, 'failure')])
async def test_program_invitation_accepts_after_cold_restore(tmp_path, monkeypatch, mode, review):
    runtime, current = await setup_case(tmp_path, monkeypatch, mode=mode, review=review)
    offered = await turn(runtime, current, 'offer', '接下来呢？')
    assert offered.stored.session.transition_offered
    assert offered.performance['suggested_inputs'][0] == ACCEPT
    assert offered.performance['suggested_inputs'] == [ACCEPT]
    assert offered.performance['performance'].endswith(OFFER)
    receipt = offered.stored.ledger_events[-1]['program_invitation']
    assert receipt['performance'] == offered.performance['performance']
    restored = await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id)
    assert restored == offered.stored
    accepted = await turn(runtime, restored, 'accept', ACCEPT, 'suggestion')
    assert accepted.stored.session.current_node_id == 'ending_leave'


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['那里有什么？', '等等，今天不开门，先别去了。'])
@pytest.mark.parametrize('review,mode', [(False, 'on'), (True, 'off'), (True, 'failure')])
async def test_unreviewed_followup_expires_program_invitation(tmp_path, monkeypatch, text, review, mode):
    runtime, current = await setup_case(tmp_path, monkeypatch, text=text, mode=mode, review=review)
    offered = await turn(runtime, current, 'offer', '接下来呢？')
    continued = await turn(runtime, offered.stored, 'followup', '先问个问题。')
    assert not continued.stored.session.transition_offered
    assert continued.performance['suggested_inputs'] == []
    assert 'program_invitation' not in continued.stored.ledger_events[-1]
    restored = await runtime.restore_session(current.session.session_id)
    accepted = await turn(runtime, restored, 'stale-accept', ACCEPT, 'suggestion')
    assert accepted.stored.session.current_node_id == 'start'


@pytest.mark.asyncio
@pytest.mark.parametrize('review,mode', [(False, 'on'), (True, 'off'), (True, 'failure')])
@pytest.mark.parametrize('text,valid', [(OFFER, True), ('（收好凭据）' + OFFER, False),
                                     ('这句话是假的：' + OFFER, False),
                                     ('这句话是假的。（摇头）' + OFFER, False),
                                     (OFFER + '（歪头）等等，先别去了。', False),
                                     (OFFER + '（望向窗外）', False)])
async def test_final_author_blocks_are_issued_without_duplication(tmp_path, monkeypatch, review, mode, text, valid):
    runtime, current = await setup_case(tmp_path, monkeypatch, review=review, mode=mode, text=text)
    result = await turn(runtime, current, 'quote', '接下来呢？')
    assert result.performance['performance'] == text
    assert result.performance['performance'].count(OFFER) == 1
    assert result.stored.session.transition_offered is valid
    assert result.performance['suggested_inputs'] == ([ACCEPT] if valid else [])
    assert ('program_invitation' in result.stored.ledger_events[-1]) is valid
    accepted = await turn(runtime, result.stored, 'accept', ACCEPT, 'suggestion')
    assert accepted.stored.session.current_node_id == ('ending_leave' if valid else 'start')


@pytest.mark.asyncio
@pytest.mark.parametrize('message,intent', [('那里有什么？', 'unclear'), ('再等等，先不走。', 'reject')])
@pytest.mark.parametrize('review,mode', [(False, 'on'), (True, 'off')])
async def test_expired_program_offer_is_not_reissued_every_other_turn(tmp_path, monkeypatch, message, intent, review, mode):
    runtime, current = await setup_case(tmp_path, monkeypatch, review=review, mode=mode)
    offered = await turn(runtime, current, 'offer', '接下来呢？')
    current = offered.stored
    async def evaluate_reply(self, **kwargs):
        return ev.NumericV2EvaluationResult((), False, transition_intent=intent)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate_reply)
    for index in range(4):
        result = await turn(runtime, current, f'followup-{index}', message)
        assert not result.stored.session.transition_offered
        assert OFFER not in result.performance['performance']
        assert 'program_invitation' not in result.stored.ledger_events[-1]
        current = result.stored


@pytest.mark.asyncio
async def test_consumed_acceptance_does_not_leave_an_unusable_invitation(tmp_path, monkeypatch):
    runtime, current = await setup_case(tmp_path, monkeypatch)
    result = await turn(runtime, current, 'uninvited-accept', ACCEPT)
    assert result.stored.session.current_node_id == 'start'
    assert not result.stored.session.transition_offered
    assert result.performance['suggested_inputs'] == []
    assert OFFER not in result.performance['performance']
    assert 'program_invitation' not in result.stored.ledger_events[-1]
    assert await runtime.restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['route', 'text', 'visible', 'receipt', 'input'])
async def test_program_acceptance_rejects_changed_evidence(tmp_path, monkeypatch, mutation):
    from dataclasses import replace
    runtime, current = await setup_case(tmp_path, monkeypatch)
    offered = await turn(runtime, current, 'offer', '接下来呢？')
    current = offered.stored
    if mutation == 'route':
        runtime.engine.nodes['start']['route_gates'][1]['id'] = 'changed'
    elif mutation == 'text':
        history = deepcopy(current.session.performance_history)
        history[-1]['performance'] += '等等，先别去了。'
        current = replace(current, session=replace(current.session, performance_history=history))
    elif mutation == 'visible':
        history = deepcopy(current.session.performance_history)
        history[-1]['scene_narration'] = '邀请已经撤销。'
        current = replace(current, session=replace(current.session, performance_history=history))
    elif mutation == 'receipt':
        events = deepcopy(current.ledger_events)
        events[-1].pop('program_invitation')
        current = replace(current, ledger_events=events)
    request = TurnRequestV2('accept', current.session.revision,
        '好。' if mutation == 'input' else ACCEPT, input_source='suggestion')
    assert workflow._confirmed_authored_acceptance(
        runtime.engine, current, request, require_program_invitation=True) == ''
