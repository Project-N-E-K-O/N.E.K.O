"""A shared agreement can be proved before Actor without producer-selected role quotes."""

from copy import deepcopy

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_options as options, numeric_v2_workflow as wf
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_source_delivery_routing import _engine, KEY


CASES = [
    ('equipment', '双方已同意把设备留在工作间。', '我想把设备留在工作间，你同意吗？', '我同意，就留在工作间。'),
    ('archive', '双方已同意将档案保存在馆内。', '我们把档案保存在馆内，好不好？', '可以，就按你说的保存在馆内。'),
]


@pytest.fixture(autouse=True)
def _separate_evaluator_path(monkeypatch):
    # These cases verify pre-Actor history proof through the retained standalone Evaluator path.
    monkeypatch.setattr(wf, "_can_combine_ordinary_turn", lambda *args: False)


def agreement_engine(description, *, owner='shared', delivery='shared_agreement', linked=True, source=None, entered=False):
    story = deepcopy(_engine().story)
    definition = story['fact_contract']['facts'][KEY]
    definition['description'] = description
    if source:
        definition['evidence_source'] = source
    node = story['nodes'][0]
    node['story_beat'].pop('must_happen', None)
    node['story_beat']['goals'] = [{
        'id': 'agree', 'owner': owner, 'description': description,
        'evidence': {'mode': 'semantic', 'anchors': []},
        'delivery': {'type': delivery, 'output_field': 'shared' if owner == 'shared' else 'performance_dialogue',
                     'source_ids': ['runtime.player_input'], 'timing': 'turn'},
    }]
    route = node['route_gates'][0]
    # The completion contract is the actual typed gate; no duplicate route fact condition is needed.
    route['conditions'] = {'all': []}
    route['transition_contract']['source_ids'] = ['goal.agree'] if linked else []
    if entered:
        node['type'] = 'scene'
        before = deepcopy(story['nodes'][1])
        before.update(id='before', type='start', chapter='前一现场')
        before['route_gates'][0].update(id='before_to_start', target_node_id='start', conditions={'all': []})
        story['nodes'].insert(0, before)
        story['start_node_id'] = 'before'
    return NumericV2Engine.from_mapping(story)


async def enter_scene(runtime, current, role_text):
    outcome = runtime.prepare_turn(current, TurnRequestV2('enter', current.session.revision, '到工作台核对吧。'),
                                   (), transition_intent='initiate')
    assert outcome.session.current_node_id == 'start'
    performance = runtime.engine.finalize_transition_performance(outcome, {
        'source_performance': '过去看看。', 'bridge_scene_narration': '',
        'target_scene_narration': '两人来到工作台前。', 'target_performance': role_text,
        'suggested_inputs': [],
    }, target_opening='两人来到工作台前。', bridge_required=False)
    return await runtime.commit_turn(outcome, performance)


@pytest.mark.asyncio
@pytest.mark.parametrize('topic,description,history,message', CASES)
@pytest.mark.parametrize('decision', ['approve', 'reject'])
@pytest.mark.parametrize('history_origin', ['ordinary', 'explicit_opening', 'explicit_entry'])
async def test_canonical_shared_history_is_bound_before_actor_and_still_independently_approved(
    tmp_path, monkeypatch, topic, description, history, message, decision, history_origin,
):
    engine = agreement_engine(description, entered=history_origin == 'explicit_entry')
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id=topic, catgirl_binding=_binding(),
        opening_performance={'performance': history if history_origin == 'explicit_opening' else '先商量。', 'suggested_inputs': []})
    if history_origin == 'ordinary':
        prepared = runtime.prepare_turn(current, TurnRequestV2('proposal', current.session.revision, '你先说说想法。'), ())
        current = await runtime.commit_turn(prepared, {'performance': history, 'suggested_inputs': []})
    elif history_origin == 'explicit_entry':
        current = await enter_scene(runtime, current, history)
    original = runtime.store._path(topic).read_bytes()
    calls = []

    async def evaluate(self, **kwargs):
        evidence = [{'source': 'player_input', 'quote': message}]
        if history_origin != 'ordinary':
            evidence.append({'source': 'actor_performance', 'quote': history})
        return ev.NumericV2EvaluationResult((), False,
            fact_operations=({'op': 'set', 'key': KEY, 'value': True, 'visibility': 'public'},),
            fact_audit=({'key': KEY, 'subject': '双方', 'action': '同意并执行', 'object': '安排',
                         'result': '完成了后续搬运', 'evidence': evidence},))

    async def review(self, **kwargs):
        if not kwargs['claims']:
            return ev.NumericV2StoryEvidenceReview()
        assert runtime.store._path(topic).read_bytes() == original
        claim, = kwargs['claims']
        assert claim['description'] == description
        assert {'source': 'actor_performance', 'quote': history} in claim['evidence']
        assert {'source': 'player_input', 'quote': message} in claim['evidence']
        if not kwargs.get('actor_evidence'):
            assert calls == []
            calls.append('pre_review')
            return ev.NumericV2StoryEvidenceReview((0,) if decision == 'approve' else ())
        assert calls == ['pre_review', 'source']
        kwargs['evidence_batch'].validate()
        calls.append('source_review')
        return ev.NumericV2StoryEvidenceReview()

    async def generate(self, **kwargs):
        assert runtime.store._path(topic).read_bytes() == original
        if kwargs['outcome'].route is None:
            assert calls == ['pre_review']
            calls.append('source')
            return {'performance': '我还需要想想这个安排。', 'suggested_inputs': [], 'fact_candidates': []}
        assert calls == ['pre_review']
        assert 'transition_source' not in kwargs
        assert kwargs['approved_completion_facts'][0]['evidence'][-1]['quote'] == history
        calls.append('transition')
        return engine.finalize_transition_performance(kwargs['outcome'], {
            'source_performance': '好，我们按这个共同决定继续。', 'bridge_scene_narration': '',
            'target_scene_narration': '下一项工作开始。', 'target_performance': '接着来吧。', 'suggested_inputs': [],
        }, target_opening='下一项工作开始。', bridge_required=False)

    async def contract(*args, **kwargs):
        return ()

    monkeypatch.setattr(options, 'MODEL_REVIEWS_ENABLED', False)
    monkeypatch.setattr(wf, 'aload_theater_module_options', options.aload_theater_module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'review_story_evidence', review)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'verify_contract_boundaries', contract)
    monkeypatch.setattr(wf.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(wf.NumericV2Actor, '_character_profile', lambda self: '自然。')
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('response', current.session.revision, message), ensure_current_binding=lambda _: _binding())
    assert calls == (['pre_review', 'transition'] if decision == 'approve'
                     else ['pre_review', 'source', 'source_review'])
    assert result.stored.session.current_node_id == ('work' if decision == 'approve' else 'start')
    assert (result.stored.session.story_state['facts'].get(KEY, {}).get('value') is True) == (decision == 'approve')
    assert len([event for event in result.stored.ledger_events[len(current.ledger_events):]
                if event['from_node_id'] != event['to_node_id']]) == int(decision == 'approve')


@pytest.mark.asyncio
@pytest.mark.parametrize('topic,description,opening,message,source', [
    ('letter', '双方已同意今晚打开这封信。', '这里有封一直没有登记的信。',
     '如果收件人是频道，我们就按流程打开看看。', '封面写的是我们频道；我们按这个条件打开看看。'),
    ('archive', '双方已同意在馆内接收这份档案。', '桌上有份还未登记的档案。',
     '若封面注明交给本馆，我们就按交接流程接收。', '封面明确交给本馆，我同意按交接流程接收。'),
])
@pytest.mark.parametrize('history_origin', ['initial_opening', 'scene_entry'])
async def test_unreferenced_entry_information_cannot_become_pre_actor_agreement_proof(
    tmp_path, monkeypatch, topic, description, opening, message, source, history_origin,
):
    engine = agreement_engine(description, entered=history_origin == 'scene_entry')
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id=topic, catgirl_binding=_binding(),
        opening_performance={'performance': opening, 'suggested_inputs': []})
    if history_origin == 'scene_entry':
        current = await enter_scene(runtime, current, opening)
    original = runtime.store._path(topic).read_bytes()
    calls = []

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), False,
            fact_operations=({'op': 'set', 'key': KEY, 'value': True, 'visibility': 'public'},),
            fact_audit=({'key': KEY, 'subject': '双方', 'action': '有条件安排', 'object': '物品',
                         'result': '同意', 'evidence': [{'source': 'player_input', 'quote': message}]},))

    async def review(self, **kwargs):
        if not kwargs['claims']:
            return ev.NumericV2StoryEvidenceReview()
        assert runtime.store._path(topic).read_bytes() == original
        if not kwargs.get('actor_evidence'):
            # A permissive verdict cannot help: merely displayed entry information must not be sent as agreement proof.
            calls.append('premature_review')
            return ev.NumericV2StoryEvidenceReview((0,))
        assert calls == ['source']
        assert kwargs['actor_evidence'] == source
        calls.append('source_review')
        return ev.NumericV2StoryEvidenceReview((0,))

    async def generate(self, **kwargs):
        assert runtime.store._path(topic).read_bytes() == original
        if kwargs['outcome'].route is None:
            assert calls == []
            assert KEY not in kwargs['outcome'].session.story_state['facts']
            calls.append('source')
            return {'performance': source, 'suggested_inputs': [], 'fact_candidates': []}
        assert calls == ['source', 'source_review']
        assert kwargs['transition_source']['performance'] == source
        calls.append('tail')
        return engine.finalize_transition_performance(kwargs['outcome'], {
            'source_performance': source, 'bridge_scene_narration': '',
            'target_scene_narration': '下一项工作开始。', 'target_performance': '接着来吧。', 'suggested_inputs': [],
        }, target_opening='下一项工作开始。', bridge_required=False)

    async def contract(*args, **kwargs):
        return ()

    monkeypatch.setattr(options, 'MODEL_REVIEWS_ENABLED', False)
    monkeypatch.setattr(wf, 'aload_theater_module_options', options.aload_theater_module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'review_story_evidence', review)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'verify_contract_boundaries', contract)
    monkeypatch.setattr(wf.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(wf.NumericV2Actor, '_character_profile', lambda self: '自然。')
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('respond', current.session.revision, message), ensure_current_binding=lambda _: _binding())
    assert calls == ['source', 'source_review', 'tail']
    assert result.stored.session.story_state['facts'][KEY]['value'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['experience', 'npc_result', 'unlinked', 'no_role', 'explicit_source', 'reject', 'wrong_type'])
async def test_history_scheduling_does_not_promote_requested_delivery_or_unlinked_history(
    tmp_path, monkeypatch, mode,
):
    description = ('玩家已经亲自体验完整画面。' if mode == 'experience' else
                   '馆员已当场签收玩家归还的图书。' if mode == 'npc_result' else '双方已同意同一项安排。')
    engine = agreement_engine(description, owner='catgirl' if mode == 'npc_result' else 'shared',
        delivery='catgirl_dialogue' if mode == 'npc_result' else 'shared_agreement',
        linked=mode != 'unlinked', source='performance' if mode == 'explicit_source' else None)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id=mode, catgirl_binding=_binding(),
        opening_performance=({'scene_narration': '资料放在桌上。', 'performance': '', 'suggested_inputs': []}
                             if mode == 'no_role' else {'performance': '要不要看这幅画？', 'suggested_inputs': []}))
    if mode == 'experience':
        prepared = runtime.prepare_turn(current, TurnRequestV2('offer', current.session.revision, '请先说说可看的内容。'), ())
        current = await runtime.commit_turn(prepared, {'performance': '要不要看这幅画？', 'suggested_inputs': []})
    message = '不，我先不决定。' if mode == 'reject' else '请先展示画面或给出签收答复。'
    calls = []

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), False, transition_intent='reject' if mode == 'reject' else 'unclear',
            fact_operations=({'op': 'set', 'key': KEY, 'value': 1 if mode == 'wrong_type' else True, 'visibility': 'public'},),
            fact_audit=({'key': KEY, 'subject': '双方', 'action': '请求', 'object': '结果',
                         'result': '已有结果', 'evidence': [{'source': 'player_input', 'quote': message}]},))

    async def review(self, **kwargs):
        if not kwargs['claims']:
            return ev.NumericV2StoryEvidenceReview()
        if not kwargs.get('actor_evidence'):
            # Misleading legacy shared metadata only schedules a review; the actual experience is absent.
            assert mode in {'experience', 'wrong_type'} and not calls
            assert (any(row['source'] == 'actor_performance' for row in kwargs['claims'][0]['evidence'])) is (mode == 'experience')
            calls.append('pre_review')
            return ev.NumericV2StoryEvidenceReview()
        assert calls[-1] == 'source'
        calls.append('source_review')
        return ev.NumericV2StoryEvidenceReview()

    async def generate(self, **kwargs):
        assert kwargs['outcome'].route is None
        assert not kwargs['outcome'].ledger_event.get('fact_operations')
        assert calls == (['pre_review'] if mode in {'experience', 'wrong_type'} else [])
        calls.append('source')
        return {'performance': '我先说明尚待确认的内容。', 'suggested_inputs': [], 'fact_candidates': []}

    async def contract(*args, **kwargs):
        return ()

    monkeypatch.setattr(options, 'MODEL_REVIEWS_ENABLED', False)
    monkeypatch.setattr(wf, 'aload_theater_module_options', options.aload_theater_module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'review_story_evidence', review)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'verify_contract_boundaries', contract)
    monkeypatch.setattr(wf.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(wf.NumericV2Actor, '_character_profile', lambda self: '自然。')
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('request', current.session.revision, message), ensure_current_binding=lambda _: _binding())
    assert result.stored.session.current_node_id == 'start'
    assert KEY not in result.stored.session.story_state['facts']


def test_new_visit_pool_cannot_rebind_the_previous_scene_role_proposal():
    # The runtime context, not an old actor field or the producer, decides eligible visit originals.
    from dataclasses import replace
    engine = agreement_engine('双方已同意保留档案。')
    session = engine.create_session(session_id='new-visit', catgirl_binding=_binding(),
        opening_performance={'performance': '我们把旧档案保留，好不好？', 'suggested_inputs': []})
    session = replace(session, revision=1, current_node_id='work', performance_history=[{
        'revision': 1, 'from_node_id': 'start', 'to_node_id': 'work',
        'segments': [
            {'phase': 'source_response', 'performance': '我们把旧档案保留，好不好？'},
            {'phase': 'transition_bridge', 'scene_narration': '两人换到新桌前。'},
            {'phase': 'target_opening', 'performance': '这里先谈新的资料。'},
        ],
    }])
    engine.fact_contract['scene:work:agreed'] = dict(engine.fact_contract[KEY])
    engine.nodes['work']['completion_contract'] = {'all': [{'key': 'scene:work:agreed', 'equals': True}]}
    _, originals = ev._committed_completion_evidence(engine, session)
    assert '我们把旧档案保留，好不好？' not in originals
    assert '这里先谈新的资料。' in originals
