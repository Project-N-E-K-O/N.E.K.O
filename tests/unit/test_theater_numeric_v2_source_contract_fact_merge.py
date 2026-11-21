"""An independent source contract can approve prebound facts without a second request."""

import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_options as options, numeric_v2_workflow as wf
from services.theater.numeric_v2_performance import performance_content_blocks
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_source_delivery_routing import _engine, KEY
from tests.utils.theater_prompt import contract_audit_payload, prompt_payload


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['approve', 'reject', 'skipped', 'stale_body', 'rewrite', 'real_approve', 'real_reject', 'tail_rewrite'])
@pytest.mark.parametrize('topic,agreement', [
    ('equipment', '同意，设备保存在工作间。'),
    ('archive', '同意，档案保存在馆内。'),
])
async def test_source_fact_approval_is_bound_and_not_resampled(tmp_path, monkeypatch, mode, topic, agreement):
    engine = _engine()
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id=topic, catgirl_binding=_binding(),
        opening_performance={'performance': '你可以先说明安排。', 'suggested_inputs': []})
    path = runtime.store._path(topic)
    original = path.read_bytes()
    message = '我提议留在原处，你同意吗？'
    calls = []
    source_checks = 0
    source_generations = 0
    transport_calls = []
    rejected = mode in {'reject', 'real_reject'}
    real_contract = wf.NumericV2MetricEvaluator.verify_contract_boundaries

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), False,
            fact_operations=({'op': 'set', 'key': KEY, 'value': True, 'visibility': 'public'},),
            fact_audit=({'key': KEY, 'subject': '双方', 'action': '同意', 'object': '去向', 'result': '已商定',
                         'evidence': [{'source': 'player_input', 'quote': message}]},))

    async def generate(self, **kwargs):
        nonlocal source_generations
        assert path.read_bytes() == original
        if kwargs['outcome'].route is None:
            source_generations += 1
            text = agreement + ('先把条件说清楚。' if mode == 'rewrite' and source_generations > 1 else '')
            calls.append('source')
            return {'performance': text, 'suggested_inputs': [], 'fact_candidates': [
                {'key': KEY, 'value': True, 'evidence_quote': text}]}
        calls.append('tail')
        assert KEY in kwargs['outcome'].session.story_state['facts']
        if mode == 'tail_rewrite' and calls.count('tail') == 2:
            repair = kwargs['draft_repair_context']
            assert repair['draft']['target_scene_narration'] == '接下来进入整理阶段。'
            assert repair['issues'][0]['rule'] == '程序原文必须具备直接前因。'
            assert repair['issues'][0]['quote'] == '接下来进入整理阶段。'
        return engine.finalize_transition_performance(kwargs['outcome'], {
            'source_performance': kwargs['transition_source']['performance'], 'bridge_scene_narration': '',
            'target_scene_narration': '接下来进入整理阶段。',
            'target_performance': '前因已补齐，按安排继续。' if calls.count('tail') > 1 else '就按这个安排继续。',
            'suggested_inputs': [],
        }, target_opening='接下来进入整理阶段。', bridge_required=False)

    async def contract(self, **kwargs):
        nonlocal source_checks
        assert path.read_bytes() == original
        if kwargs['actor_performance'].get('segments'):
            assert not kwargs.get('bound_completion_batch')
            calls.append('tail_check')
            if mode == 'tail_rewrite' and calls.count('tail_check') == 1:
                kwargs['diagnostics']['findings'] = [{
                    'rule': '程序原文必须具备直接前因。', 'quote': '接下来进入整理阶段。',
                    'candidate': {'id': 'c1', 'phase': 'target_opening', 'type': 'narration',
                                  'text': '接下来进入整理阶段。'}, 'prior': None,
                }]
                return ('程序原文必须具备直接前因。',)
            return await real_contract(self, **kwargs) if mode.startswith('real_') else ()
        calls.append('source_check')
        source_checks += 1
        batch = kwargs['bound_completion_batch']
        batch.validate()
        assert len(batch.claims) == 1 and batch.claims[0]['key'] == KEY
        assert batch.claims[0]['evidence'][-1]['quote'] == kwargs['actor_performance']['performance']
        if mode.startswith('real_'):
            return await real_contract(self, **kwargs)
        kwargs['diagnostics'].update(player_authority_complete=True,
            player_authority_blocks={f'c{i}': 'none' for i, _ in enumerate(
                performance_content_blocks(kwargs['actor_performance']))})
        if mode == 'rewrite' and source_checks == 1:
            # A bad whole body cannot publish even an affirmative fact decision.
            kwargs['bound_completion_sink'].append((0,))
            kwargs['diagnostics']['findings'] = [{'rule': '须修正本稿。', 'quote': agreement}]
            return ('须修正本稿。',)
        if mode != 'skipped':
            kwargs['bound_completion_sink'].append(() if rejected else (0,))
        if mode == 'stale_body':
            kwargs['actor_performance']['performance'] += '还需要重新核对。'
        return ()

    async def approve(self, **kwargs):
        assert mode in {'skipped', 'stale_body'}
        assert path.read_bytes() == original
        assert agreement in kwargs['actor_evidence']
        if mode == 'stale_body':
            assert '还需要重新核对' in kwargs['actor_evidence']
        calls.append('separate_approval')
        return ev.NumericV2StoryEvidenceReview((0,))

    monkeypatch.setattr(options, 'MODEL_REVIEWS_ENABLED', False)
    monkeypatch.setattr(wf, 'aload_theater_module_options', options.aload_theater_module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'review_story_evidence', approve)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'verify_contract_boundaries', contract)
    monkeypatch.setattr(wf.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(wf.NumericV2Actor, '_character_profile', lambda self: '自然。')
    if mode.startswith('real_'):
        async def config(_):
            return {'model': 'qwen3.8-flash', 'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'api_key': 'test'}

        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def ainvoke(self, messages, **kwargs):
                data = prompt_payload(messages[1].content)
                transport_calls.append(data)
                payload = contract_audit_payload({'violations': []}, messages)
                properties = kwargs['response_format']['json_schema']['schema']['properties']
                if 'premature_departure' in properties:
                    payload['premature_departure'] = []
                if 'route_conflicts' in properties:
                    payload['route_conflicts'] = []
                if 'completion_check' in data:
                    payload['fact_discoveries'] = []
                if 'bound_completion_check' in data:
                    assert data['bound_completion_check']['claims'][0]['key'] == KEY
                    payload['bound_completion_approved_indexes'] = [] if rejected else [0]
                return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))

        async def factory(*args, **kwargs): return Client()
        monkeypatch.setattr(ev, '_model_config', config)
        monkeypatch.setattr(ev, 'create_chat_llm_async', factory)
    diagnostics = {}
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('merged-source', 0, message), ensure_current_binding=lambda _: _binding(),
        diagnostics_sink=diagnostics)
    assert result.stored.session.revision == 1
    assert result.stored.session.current_node_id == ('start' if rejected else 'work')
    assert (KEY in result.stored.session.story_state['facts']) is not rejected
    assert calls.count('separate_approval') == int(mode in {'skipped', 'stale_body'})
    assert diagnostics.get('source_completion_reviews_merged', 0) == int(mode not in {'skipped', 'stale_body'})
    assert source_checks == (2 if mode == 'rewrite' else 1)
    assert calls.count('tail') == (2 if mode == 'tail_rewrite' else int(not rejected))
    if mode.startswith('real_'):
        assert len(transport_calls) == 1 + int(not rejected)
