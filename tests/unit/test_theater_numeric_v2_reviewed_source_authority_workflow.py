"""Reuse only an independently reviewed unchanged source's player absence finding."""

from copy import deepcopy

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_options as options, numeric_v2_workflow as wf
from services.theater.numeric_v2_actor_output import NumericV2ActorOutputError
from services.theater.numeric_v2_performance import performance_content_blocks
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_source_delivery_routing import _engine, KEY


@pytest.mark.asyncio
@pytest.mark.parametrize('topic,agreement', [
    ('equipment', '（点头，看向工作台）同意，设备留在这里。'),
    ('archive', '（点头，看向档案架）同意，档案保存在馆内。'),
])
@pytest.mark.parametrize('mode', ['none', 'missing', 'uncertain', 'granted', 'author_violation', 'tail_repair'])
async def test_only_successful_complete_absence_survives_source_delivery(
    tmp_path, monkeypatch, topic, agreement, mode,
):
    engine = _engine()
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id=topic, catgirl_binding=_binding(),
        opening_performance={'performance': '先说清楚安排。', 'suggested_inputs': []})
    path = runtime.store._path(topic)
    before = path.read_bytes()
    source = {'performance': agreement, 'suggested_inputs': [], 'fact_candidates': []}
    source_blocks = performance_content_blocks(source)
    source_checked = False
    tail_checks = []
    actor_calls = []
    message = '我建议东西留在原处，你同意吗？'

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), False,
            fact_operations=({'op': 'set', 'key': KEY, 'value': True, 'visibility': 'public'},),
            fact_audit=({'key': KEY, 'subject': '双方', 'action': '同意', 'object': '去向', 'result': '已有安排',
                         'evidence': [{'source': 'player_input', 'quote': message}]},))

    async def approve(self, **kwargs):
        assert source_checked and path.read_bytes() == before
        assert kwargs['actor_evidence'] == agreement
        return ev.NumericV2StoryEvidenceReview((0,))

    async def generate(self, **kwargs):
        assert path.read_bytes() == before
        if kwargs['outcome'].route is None:
            actor_calls.append('source')
            return deepcopy(source)
        actor_calls.append('tail')
        assert performance_content_blocks(kwargs['transition_source']) == source_blocks
        return engine.finalize_transition_performance(kwargs['outcome'], {
            'source_performance': agreement, 'bridge_scene_narration': '',
            'target_scene_narration': '新阶段开始。',
            'target_performance': '按新安排继续。' if actor_calls.count('tail') == 1 else '接着整理。',
            'suggested_inputs': [],
        }, target_opening='新阶段开始。', bridge_required=False)

    async def contract(self, **kwargs):
        nonlocal source_checked
        assert path.read_bytes() == before
        candidate = kwargs['actor_performance']
        if not candidate.get('segments'):
            source_checked = True
            report = {f'c{i}': 'none' for i in range(len(source_blocks))}
            if mode == 'uncertain':
                report['c0'] = 'uncertain'
            elif mode == 'granted':
                report['c0'] = [{'quote': source_blocks[0]['text'], 'authorization_ref': 'p0'}]
            elif mode == 'missing':
                report.pop('c0')
            kwargs['diagnostics'].update(player_authority_complete=mode != 'uncertain',
                                          player_authority_blocks=report)
            return ()
        # The full source remains in the final independent author/causal check.
        assert performance_content_blocks(candidate['segments'][0]) == source_blocks
        receipt = kwargs.get('reviewed_player_absence', ())
        assert tuple(receipt) == (tuple(source_blocks) if mode not in {'missing', 'uncertain', 'granted'} else ())
        assert kwargs['player_input'] == message
        tail_checks.append(deepcopy(receipt))
        if mode == 'author_violation' or mode == 'tail_repair' and len(tail_checks) == 1:
            kwargs['diagnostics']['findings'] = [{
                'rule': '阶段禁令仍须检查。', 'quote': agreement if mode == 'author_violation' else '按新安排继续。',
                'candidate': {'phase': 'source_response' if mode == 'author_violation' else 'target_opening'},
                'prior': None,
            }]
            return ('阶段禁令仍须检查。',)
        return ()

    monkeypatch.setattr(options, 'MODEL_REVIEWS_ENABLED', False)
    monkeypatch.setattr(wf, 'aload_theater_module_options', options.aload_theater_module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'review_story_evidence', approve)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'verify_contract_boundaries', contract)
    monkeypatch.setattr(wf.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(wf.NumericV2Actor, '_character_profile', lambda self: '自然。')
    kwargs = dict(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('reviewed-source', 0, message), ensure_current_binding=lambda _: _binding())
    if mode == 'author_violation':
        with pytest.raises(NumericV2ActorOutputError, match='numeric_v2_contract_violation'):
            await wf.execute_numeric_v2_turn(**kwargs)
        assert path.read_bytes() == before and actor_calls == ['source', 'tail']
        return
    result = await wf.execute_numeric_v2_turn(**kwargs)
    assert result.stored.session.current_node_id == 'work'
    assert len([row for row in result.stored.ledger_events if row['from_node_id'] != row['to_node_id']]) == 1
    assert actor_calls == (['source', 'tail', 'tail'] if mode == 'tail_repair' else ['source', 'tail'])
    assert len(tail_checks) == (2 if mode == 'tail_repair' else 1)
