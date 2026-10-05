"""Explicit contact conditions do not require an unrelated player handoff."""

import json

import pytest

from services.theater.numeric_v2 import NumericV2CompileError
from services.theater.numeric_v2_evaluator import _build_transition_judge_messages
from services.theater.numeric_v2_fixed_narration import apply_triggers
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.unit.test_theater_numeric_v2_fixed_narration import _engine, OPENING, LOG
from tests.unit.test_theater_numeric_v2_runtime import _binding


def _context(required):
    story = _engine().story
    trigger = story['nodes'][0]['story_beat']['fixed_narrations'][1]['trigger']
    trigger['condition'] = '角色实际触碰或接过物件；仅准备递交不算。'
    if required is not None:
        trigger['player_handoff_required'] = required
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id='contact', catgirl_binding=_binding(), opening_performance=OPENING)
    return engine, session


@pytest.mark.parametrize('required,expected', [(None, False), (True, False), (False, True)])
@pytest.mark.parametrize('prop', ['铭牌', '旧照片'])
def test_contact_can_trigger_without_changing_holder(required, expected, prop):
    engine, session = _context(required)
    body = {'performance': f'（指尖触碰你手里的{prop}）表面有痕迹。'}
    claim = ({'id': 'log', 'evidence': f'指尖触碰你手里的{prop}'},)
    result = apply_triggers(engine.nodes['start'], session, body, claim, f'我拿起{prop}走近，仍拿在手里。', known=False)
    assert bool(result.get('fixed_narrations')) is expected
    if expected:
        assert result['fixed_narrations'][0]['text'] == LOG


@pytest.mark.parametrize('required', [None, True, False])
def test_receiving_claim_still_needs_actual_player_handoff(required):
    engine, session = _context(required)
    body = {'performance': '（接过旧照片）谢谢。'}
    claim = ({'id': 'log', 'evidence': '接过旧照片'},)
    assert apply_triggers(engine.nodes['start'], session, body, claim, '我拿起旧照片。', known=False) == body
    result = apply_triggers(engine.nodes['start'], session, body, claim, '我把旧照片递给你。', known=False)
    assert result['fixed_narrations'][0]['text'] == LOG


def test_explicit_handoff_requirement_is_visible_to_same_review():
    engine, session = _context(False)
    messages, _ = _build_transition_judge_messages(engine, session,
        actor_performance={'performance': '（触碰物件）有字迹。'}, player_input='我拿着物件。')
    payload = json.loads(messages[1].content[messages[1].content.index('{'):])
    assert payload['fixed_narration_candidates'][0]['player_handoff_required'] is False
    assert '不授权角色擅自接收物品' in messages[0].content


@pytest.mark.parametrize('value', ['false', 0, None, [], {}])
def test_invalid_handoff_flag_rejected(value):
    story = _engine().story
    story['nodes'][0]['story_beat']['fixed_narrations'][1]['trigger']['player_handoff_required'] = value
    with pytest.raises(NumericV2CompileError):
        NumericV2Engine.from_mapping(story)


@pytest.mark.asyncio
@pytest.mark.parametrize('prop', ['铭牌', '旧照片'])
async def test_contact_delivery_commits_and_restores_without_extra_call(tmp_path, monkeypatch, prop):
    from services.theater import numeric_v2_workflow as workflow
    from services.theater.numeric_v2_evaluator import NumericV2EvaluationResult, NumericV2TransitionOfferReview
    from services.theater.numeric_v2_options import default_options
    from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2

    story = _context(False)[0].story
    story['nodes'][0]['completion_contract'] = {'all': [{'fixed_narration_id': 'log'}]}
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(session_id='handoff', catgirl_binding=_binding(), opening_performance=OPENING)
    body = {'performance': f'（指尖触碰你手里的{prop}）表面有痕迹。',
            'suggested_inputs': [], 'transition_offered': False}
    calls = {'actor': 0, 'review': 0}

    async def options():
        return {**default_options(), 'review': True}

    async def evaluate(self, **kwargs):
        return NumericV2EvaluationResult((), False)

    async def invoke(self, messages, **kwargs):
        calls['actor'] += 1
        assert kwargs['fact_candidates_expected'] is False
        return dict(body)

    async def review(self, **kwargs):
        calls['review'] += 1
        return NumericV2TransitionOfferReview(False, False, (), (),
            fixed_narration_triggers=({'id': 'log', 'evidence': f'指尖触碰你手里的{prop}'},))

    monkeypatch.setattr(workflow, 'aload_theater_module_options', options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(workflow.NumericV2Actor, '_invoke', invoke)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('touch', 0, f'我拿着{prop}让你查看，仍拿在自己手里。'),
        ensure_current_binding=lambda _: _binding())
    assert calls == {'actor': 1, 'review': 1}
    assert runtime.engine.completion_contract_satisfied(result.stored.session)
    assert result.stored.ledger_events[-1].get('fact_operations', []) == []
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session('handoff') == result.stored
