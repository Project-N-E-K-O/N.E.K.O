"""A confirmed bad immutable source cannot be repaired by regenerating only its tail."""

from copy import deepcopy

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_options as options, numeric_v2_workflow as wf
from services.theater.numeric_v2_actor_output import NumericV2ActorOutputError
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_source_delivery_routing import _engine, KEY
from tests.unit.test_theater_numeric_v2_runtime import _binding


@pytest.mark.asyncio
@pytest.mark.parametrize('topic,description,message,agreement,forbidden_action,boundary', [
    ('equipment', '双方已同意设备留在工作间。', '我建议把设备留在工作间，先商定再搬。',
     '同意，设备先留在工作间。', '（搬起设备走出工作间）', '本幕不得将设备搬出工作间。'),
    ('archive', '双方已同意档案保存在馆内。', '这份档案保存在馆内，先说清楚归档安排。',
     '同意，档案保存在馆内。', '（把档案带出馆外）', '本幕不得将档案带出馆外。'),
])
@pytest.mark.parametrize('finding_phase', ['source_response', 'target_opening', 'cleared_future'])
async def test_source_violation_aborts_instead_of_resampling_tail_verdict(
    tmp_path, monkeypatch, topic, description, message, agreement, forbidden_action, boundary, finding_phase,
):
    story = deepcopy(_engine().story)
    story['fact_contract']['facts'][KEY]['description'] = description
    story['nodes'][0]['story_beat'].update(opening_scene=description, summary=description, must_not_happen=[boundary])
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id=topic, catgirl_binding=_binding(),
        opening_performance={'performance': '你先说说安排。', 'suggested_inputs': []})
    original = runtime.store._path(topic).read_bytes()
    calls = []
    tail_checks = 0
    source_body = agreement + (forbidden_action if finding_phase == 'source_response'
                               else '等你准备好，我们之后再决定何时搬动。' if finding_phase == 'cleared_future' else '')

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), False,
            fact_operations=({'op': 'set', 'key': KEY, 'value': True, 'visibility': 'public'},),
            fact_audit=({'key': KEY, 'subject': '双方', 'action': '同意', 'object': '去向', 'result': '已有安排',
                         'evidence': [{'source': 'player_input', 'quote': message}]},))

    async def review(self, **kwargs):
        if not kwargs['claims']:
            return ev.NumericV2StoryEvidenceReview()
        assert kwargs['actor_evidence'] == source_body
        return ev.NumericV2StoryEvidenceReview((0,))

    async def generate(self, **kwargs):
        assert runtime.store._path(topic).read_bytes() == original
        if kwargs['outcome'].route is None:
            calls.append('source')
            return {'performance': source_body, 'suggested_inputs': [], 'fact_candidates': []}
        calls.append('tail')
        return engine.finalize_transition_performance(kwargs['outcome'], {
            'source_performance': source_body, 'bridge_scene_narration': '',
            'target_scene_narration': '新阶段开始。',
            'target_performance': '按新安排继续。' if calls.count('tail') == 1 else '继续整理。',
            'suggested_inputs': [],
        }, target_opening='新阶段开始。', bridge_required=False)

    async def contract(self, **kwargs):
        nonlocal tail_checks
        if not kwargs['actor_performance'].get('segments'):
            return ()
        tail_checks += 1
        if tail_checks != 1:
            return ()  # A changed tail must never make the locked source's confirmed finding disappear.
        kwargs['diagnostics'].update(reported_findings=1, grounded_findings=1,
            confirmed_findings=0 if finding_phase == 'cleared_future' else 1,
            findings=[] if finding_phase == 'cleared_future' else [{
                'rule': boundary, 'quote': source_body,
                'candidate': {'id': 'c0', 'phase': finding_phase, 'type': 'dialogue', 'text': source_body},
                'prior': None,
            }])
        return () if finding_phase == 'cleared_future' else (boundary,)

    monkeypatch.setattr(options, 'MODEL_REVIEWS_ENABLED', False)
    monkeypatch.setattr(wf, 'aload_theater_module_options', options.aload_theater_module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'review_story_evidence', review)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'verify_contract_boundaries', contract)
    monkeypatch.setattr(wf.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(wf.NumericV2Actor, '_character_profile', lambda self: '自然。')
    kwargs = dict(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('delivery', 0, message), ensure_current_binding=lambda _: _binding())
    if finding_phase == 'source_response':
        with pytest.raises(NumericV2ActorOutputError, match='numeric_v2_contract_violation'):
            await wf.execute_numeric_v2_turn(**kwargs)
        assert calls == ['source', 'tail']
        assert runtime.store._path(topic).read_bytes() == original
    else:
        result = await wf.execute_numeric_v2_turn(**kwargs)
        assert result.stored.session.current_node_id == 'work'
        assert calls == (['source', 'tail'] if finding_phase == 'cleared_future' else ['source', 'tail', 'tail'])
