"""Regressions for committed choices and author plans found by two-genre stress."""

import json

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater import numeric_v2_workflow as workflow
from services.theater.numeric_v2_options import default_options
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_transition_history import _candidate
from tests.unit.theater_workshop.test_numeric_v2_generation import _generation_setup, _idea_outline
from theater_workshop.sdk.generation.numeric_v2 import NumericV2Generator


@pytest.mark.asyncio
@pytest.mark.parametrize('accepted', ['（收好文书起身）趁天黑前再赶一段路。', '（把通行卡装进口袋）现在去轨道站。'])
async def test_new_verified_invitation_does_not_reinsert_this_turns_consumed_action(tmp_path, monkeypatch, accepted):
    story = numeric_v2_story()
    for route in story['nodes'][0]['route_gates']:
        route['transition_contract']['accept_input'] = accepted
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(session_id='consumed_new_offer', catgirl_binding=_binding(), opening_performance=_opening())

    async def options():
        return {**default_options(), 'review': True}

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        return {'performance': '（点头）既然收好了，我们出发吧。', 'suggested_inputs': [], 'transition_offered': True}

    async def review(self, **kwargs):
        return evaluator.NumericV2TransitionOfferReview(True, True, (), ())

    monkeypatch.setattr(workflow, 'aload_theater_module_options', options)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('act', 0, accepted), ensure_current_binding=lambda _: _binding())

    assert result.performance['suggested_inputs'] == []
    assert result.stored.session.transition_offered is True
    assert result.diagnostics['verified_offer_acceptance_suggestions_inserted'] == 0
    assert await runtime.restore_session('consumed_new_offer') == result.stored


@pytest.mark.parametrize('name,purpose,state', [
    ('旧信', '核对日期', '由女主保管在抽屉里'),
    ('晶片', '验证入口权限', '由玩家保管在腰包里'),
])
def test_carrying_prop_does_not_turn_author_plan_into_permanent_state(name, purpose, state):
    candidate = _idea_outline()
    candidate['key_props'][0].update(name=name, purpose=purpose)
    candidate['key_props'][0]['states'][0].update(owner='player', state=state)
    explicit = '已经公开的调查结果不能被改写'
    candidate['mainline_chapters'][0]['exit_plan']['preserve_facts'].append(explicit)
    generator = NumericV2Generator()
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(candidate, ensure_ascii=False)
    result = generator.generate(title='carry plan', setup=_generation_setup())
    preserved = result['story']['nodes'][0]['route_gates'][0]['transition_contract']['must_preserve']

    assert explicit in preserved
    assert any(name in item and purpose in item for item in preserved)
    assert not any(state in item for item in preserved)
    assert result['key_props'][0]['states'][0]['state'] == state


@pytest.mark.asyncio
@pytest.mark.parametrize('text,requires_reply,timeout,full_review_rejects', [
    ('（点头）哟，你？好久不见。一路辛苦了。', False, False, False),
    ('（点头）谁能想到这一路这么远？终于送到了。', False, False, False),
    ('（点头）这一趟送到了。', False, False, False),
    ('（点头）你接下来要留在这里，还是回去？', True, False, False),
    ('（点头）哟，你？好久不见。', False, True, False),
    ('（点头）哟，你？好久不见。', False, False, True),
])
@pytest.mark.parametrize('question_field', ['source_performance', 'bridge_scene_narration', 'target_performance'])
@pytest.mark.parametrize('long_source', [False, True])
async def test_terminal_question_check_does_not_replace_full_review_or_commit_unknown(
    tmp_path, monkeypatch, text, requires_reply, timeout, full_review_rejects, question_field, long_source,
):
    engine = _engine()
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='terminal_question', catgirl_binding=_binding(), opening_performance=_opening())
    calls = {'actor': 0, 'narrow': 0, 'review': 0}

    async def options():
        return {**default_options(), 'review': True}

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((), True, natural_ending_ready=True)

    async def generate(self, **kwargs):
        calls['actor'] += 1
        candidate = _candidate()
        if long_source:
            candidate['source_performance'] = ''.join(f'（点头）已经确认第{i}条消息。' for i in range(8))
        candidate[question_field] = text
        return engine.finalize_transition_performance(kwargs['outcome'], candidate, target_opening='雨后的长街。')

    async def narrow(self, **kwargs):
        calls['narrow'] += 1
        boundaries = kwargs['node']['story_beat']['must_not_happen']
        assert len(boundaries) == 1
        assert '下一轮回答' in boundaries[0]
        # Inspect the real model-input projection, not merely the raw candidate.
        assert kwargs['include_all_segments'] is True
        visible = json.dumps(evaluator._context_content(
            kwargs['actor_performance'], include_all_segments=kwargs['include_all_segments'],
        ), ensure_ascii=False)
        assert text.split('）')[-1] in visible
        if timeout:
            raise evaluator.NumericV2EvaluatorError('numeric_v2_contract_check_timeout')
        return tuple(boundaries) if requires_reply else ()

    async def review(self, **kwargs):
        calls['review'] += 1
        return evaluator.NumericV2TransitionOfferReview(False, False,
            ('scene_boundary',) if full_review_rejects else (), (),
            '目标落点错误。' if full_review_rejects else '', delivery_matches_route=not full_review_rejects)

    monkeypatch.setattr(workflow, 'aload_theater_module_options', options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'verify_contract_boundaries', narrow)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    kwargs = dict(config_manager=object(), runtime=runtime, current=current,
                  turn=TurnRequestV2('finish', 0, '就到这里吧。'), ensure_current_binding=lambda _: _binding())
    if requires_reply or timeout or full_review_rejects:
        with pytest.raises(workflow.NumericV2ActorOutputError):
            await workflow.execute_numeric_v2_turn(**kwargs)
        assert await runtime.restore_session('terminal_question') == current
        assert calls['actor'] == (1 if timeout else 2)
        assert calls['narrow'] == (1 if timeout else 2)
        assert calls['review'] == (2 if full_review_rejects else 0)
    else:
        result = await workflow.execute_numeric_v2_turn(**kwargs)
        assert result.stored.session.status == 'ended'
        assert calls == {'actor': 1, 'narrow': int('？' in text), 'review': 1}
        assert result.diagnostics['terminal_question_review_calls'] == calls['narrow']
        assert await runtime.restore_session('terminal_question') == result.stored
