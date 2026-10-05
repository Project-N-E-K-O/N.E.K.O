"""Optional suggestions must not hold up a reply that already has usable choices."""

import json

import pytest

from services.theater import numeric_v2_actor as actor_module
from services.theater import numeric_v2_evaluator as evaluator
from services.theater import numeric_v2_workflow as workflow
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


@pytest.mark.asyncio
@pytest.mark.parametrize('scene_changed', [False, True])
@pytest.mark.parametrize('suggestion', [
    '（收起雨伞）我先听你说。', '（查看终端）请解释这条读数。',
    '（打开课本开始复习）', '（仔细观察终端上的读数）',
])
async def test_one_parsed_choice_never_requests_a_top_up(monkeypatch, scene_changed, suggestion):
    actor = actor_module.NumericV2Actor(object())

    async def unexpected_call(*args, **kwargs):
        pytest.fail('A usable choice must not trigger an auxiliary model request')

    monkeypatch.setattr(actor, '_invoke', unexpected_call)
    candidate = actor_module._parse_output(json.dumps({
        'performance': '（点头）我来说明。', 'suggested_inputs': [suggestion],
        'transition_offered': False,
    }, ensure_ascii=False))
    result = await actor._ensure_suggestions(
        performance=candidate, player_input='请继续。', catgirl_name='测试猫娘',
        max_input_tokens=900, scene_changed=scene_changed,
    )
    assert result == [suggestion]
    assert actor.suggestion_fill_attempt_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('fill_enabled', [False, True])
async def test_single_offer_option_keeps_structured_acceptance_fill(monkeypatch, fill_enabled):
    actor = actor_module.NumericV2Actor(object())
    decline = '（摆手）先不出发，我想再谈谈。'
    accept = '（点头）好，我们现在就出发。'
    calls = []

    async def fill(*args, **kwargs):
        calls.append(kwargs)
        return {'suggested_inputs': [accept, decline]}

    monkeypatch.setattr(actor, '_invoke', fill)
    suggestions = await actor._ensure_suggestions(
        allow_fill=fill_enabled,
        performance={'performance': '（指向门外）我们现在出发，好吗？',
                     'transition_offered': True, 'suggested_inputs': [decline]},
        player_input='接下来怎么安排？', catgirl_name='测试猫娘', max_input_tokens=900,
    )
    assert suggestions == ([accept, decline] if fill_enabled else [decline])
    assert len(calls) == int(fill_enabled)
    if calls:
        assert calls[0]['transition_suggestions_only'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('remaining', [False, True])
@pytest.mark.parametrize('fill_enabled', [False, True])
async def test_repeated_input_is_removed_before_deciding_to_fill(monkeypatch, remaining, fill_enabled):
    actor = actor_module.NumericV2Actor(object())
    calls = []

    async def fill(*args, **kwargs):
        calls.append(kwargs)
        return {'suggested_inputs': ['（点头）请详细说说。']}

    monkeypatch.setattr(actor, '_invoke', fill)
    current_input = '（看向她）请继续。'
    usable = '（抬头）接下来有什么安排？'
    result = await actor._ensure_suggestions(
        allow_fill=fill_enabled,
        performance={'performance': '（点头）好的。',
                     'suggested_inputs': [current_input] + ([usable] if remaining else [])},
        player_input=current_input, catgirl_name='测试猫娘', max_input_tokens=900,
    )
    assert len(calls) == int(fill_enabled and not remaining)
    assert result == ([usable] if remaining else ['（点头）请详细说说。'] if fill_enabled else [])
    assert current_input not in result


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['ordinary', 'formal', 'cancelled'])
@pytest.mark.parametrize('kept_count', [0, 1, 2, 3])
@pytest.mark.parametrize('fill_enabled', [False, True])
@pytest.mark.parametrize('action_only', [False, True])
async def test_review_filter_never_generates_unreviewed_replacements(
    monkeypatch, tmp_path, phase, kept_count, fill_enabled, action_only,
):
    case = initiation_case()
    engine = case['engine']
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id='suggestion_wait', catgirl_binding=_binding(),
        opening_performance=case['session'].opening_performance,
    )
    suggestions = ['（点头）请继续说。', '（低头）让我想一想。', '（摆手）我先不处理。']
    if action_only:
        suggestions = ['（翻开课本）', '（观察桌上的资料）', '（静静等待）']
    generations, reviews = [], []

    async def options():
        return {'evaluator': True, 'review': True, 'suggestion_fill': fill_enabled,
                'dispute': False, 'actor_retry': False, 'history_lookup': False,
                'review_delivery': False, 'review_contract': False}

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult(
            (), False, transition_intent='unclear' if phase == 'ordinary' else 'initiate',
            public_destination_quote=case['session'].opening_performance['performance'],
        )

    async def generate(self, **kwargs):
        generations.append(kwargs)
        outcome = kwargs['outcome']
        if outcome.ledger_event['from_node_id'] != outcome.ledger_event['to_node_id']:
            return engine.finalize_transition_performance(outcome, {
                **_candidate(), 'suggested_inputs': list(suggestions),
            }, target_opening='两人在阅览室入口。')
        return {'performance': '（点头）我们先确认眼前的安排。',
                'suggested_inputs': list(suggestions), 'transition_offered': False}

    async def review(self, **kwargs):
        reviews.append(kwargs)
        cancelled = phase == 'cancelled' and len(reviews) == 1
        return evaluator.NumericV2TransitionOfferReview(
            False, False, ('player_action',) if cancelled else (),
            tuple(range(kept_count, len(suggestions))),
            '候选新增了未授权的玩家行动。' if cancelled else '仅指定推荐不安全。',
            initiation_authorized=False if cancelled else None,
        )

    async def unexpected_fill(*args, **kwargs):
        pytest.fail('Do not put new model-written buttons after their final safety review')

    monkeypatch.setattr(workflow, 'aload_theater_module_options', options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_ensure_suggestions', unexpected_fill)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('one', 0, '（翻开课本）' if phase == 'ordinary' else '带路吧。'),
        ensure_current_binding=lambda _: _binding(),
    )
    assert len(generations) == len(reviews) == (2 if phase == 'cancelled' else 1)
    assert result.performance['suggested_inputs'] == (suggestions if kept_count == len(suggestions) else [])
    assert result.diagnostics['actor_suggestion_refill_after_review_attempts'] == 0
    assert result.stored.session.current_node_id == ('ending_leave' if phase == 'formal' else 'start')
    assert result.stored.session.revision == 1
    assert len(result.stored.ledger_events) == 1
    if phase == 'ordinary':
        assert generations[0]['player_input'] == '（翻开课本）'
        assert result.stored.ledger_events[0]['input_text'] == '（翻开课本）'
    assert await NumericV2Runtime(engine, tmp_path).restore_session('suggestion_wait') == result.stored
