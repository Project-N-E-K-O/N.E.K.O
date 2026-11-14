"""Current player feedback cannot borrow a historical question as an NPC confirmation."""

from dataclasses import replace

import pytest

from services.theater import numeric_v2_evaluator as ev
from services.theater import numeric_v2_fact_discovery as discovery
from services.theater import numeric_v2_options as options, numeric_v2_workflow as wf
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_source_delivery_routing import _engine, KEY, INPUT


HISTORY = '（放下谱页）怎么样？这个衔接点听感上顺不顺？'
FEEDBACK = '我觉得现在衔接顺了，你觉得呢？'
CONFIRMATION = '我也觉得顺，这个衔接点就这样定下来。'


@pytest.mark.parametrize('mode', [
    'four_originals', 'missing_history', 'tampered_packet', 'blocked_existing', 'cross_join',
])
def test_delivery_binding_preserves_full_historical_sources_and_source_permissions(mode):
    engine = _engine()
    session = engine.create_session(session_id='proof-capacity', catgirl_binding=_binding(),
        opening_performance={'performance': HISTORY, 'suggested_inputs': []})
    originals = ['检修员报出读数七。', '复核员确认编号甲。', '记录员已登记测量日期。']
    claim = {'op': 'set', 'key': KEY, 'value': True, 'visibility': 'public',
             'description': engine.fact_contract[KEY]['description'],
             'subject': '双方', 'action': '核对', 'object': '结论', 'result': '双方确认',
             'evidence': [{'source': 'player_input', 'quote': FEEDBACK},
                          *[{'source': 'actor_performance', 'quote': text} for text in originals]]}
    packet = discovery.build_packet(engine, session, {'performance': CONFIRMATION}, FEEDBACK,
        pending_targets=ev._pending_completion_facts(engine, session),
        history_metadata={'allowed_keys': [KEY]}, history_originals=originals)
    if mode == 'missing_history':
        claim['evidence'][1]['quote'] = CONFIRMATION
    if mode == 'tampered_packet':
        packet['sources'][1]['text'] = '另一个历史。'
    if mode == 'blocked_existing':
        # Discovery does not recover an existing key. Source delivery still binds
        # its separately issued update and must retain the blocked source policy.
        session = replace(session, story_state={**session.story_state, 'facts': {
            KEY: {'value': False, 'visibility': 'public'}}})
        packet = discovery.build_packet(engine, session, {'performance': CONFIRMATION}, FEEDBACK,
            pending_targets=ev._pending_completion_facts(engine, session),
            history_metadata={'allowed_keys': [KEY]}, history_originals=originals,
            restrict_player_results=True)
        assert not packet['targets']
    claims = (claim,)
    if mode == 'cross_join':
        other = 'scene:start:other'
        engine.fact_contract[other] = dict(engine.fact_contract[KEY])
        engine.nodes['start']['completion_contract']['all'].append({'key': other, 'equals': True})
        # A previous candidate's presentation bundle is not a new original
        # from which a later candidate may quote across field boundaries.
        invented = {**claim, 'key': other, 'evidence': [
            {'source': 'actor_performance', 'quote': '\n\n'.join(originals[:2])}]}
        claims = (claim, invented)
    batch = discovery.bind_source_delivery(engine, claims, packet,
                                          restrict_player_results=mode == 'blocked_existing')
    if mode in {'missing_history', 'tampered_packet'}:
        assert not batch.claims and batch.errors
        return
    batch.validate()
    assert len(batch.claims) == 1
    if mode == 'cross_join':
        assert batch.errors == ('delivery_history_evidence_missing',)
        return
    table = {row['ref']: row for row in batch.packet['sources']}
    proof = [table[ref] for ref in batch.bindings[0]]
    assert any(row['kind'] == 'current_draft' and row['text'] == CONFIRMATION for row in proof)
    if mode == 'four_originals':
        historical = next(row for row in proof if row['kind'] == 'committed_current_visit')
        assert [table[ref]['text'] for ref in historical['components']] == originals
        assert historical['text'].split('\n\n') == originals
        assert any(row['kind'] == 'current_player' and row['text'] == FEEDBACK for row in proof)
    else:
        assert batch.packet['targets'][0]['allowed_sources'] == ['actor_performance']
        assert all(row['source'] == 'actor_performance' for row in proof)


@pytest.mark.asyncio
@pytest.mark.parametrize('already_false', [False, True])
@pytest.mark.parametrize('mode', ['confirm', 'decline', 'historical_component', 'player_source', 'history_only',
                                  'prior_offer_accepted'])
async def test_mixed_history_requires_actual_current_delivery(tmp_path, monkeypatch, mode, already_false):
    engine = _engine('player_input' if mode == 'player_source' else None)
    runtime = NumericV2Runtime(engine, tmp_path)
    history = ('我同意把设备留在这里。你也同意的话，我们就按这个去向办。'
               if mode == 'prior_offer_accepted' else '检修员已报出仪表读数为七。请你说出自己的核对结论。'
               if mode == 'historical_component' else CONFIRMATION if mode == 'history_only' else HISTORY)
    if mode == 'historical_component':
        engine.fact_contract[KEY]['description'] = '检修员已报出读数，玩家已作出核对结论，角色已确认收到该结论。'
    current = await runtime.start_session(session_id='mixed-proof', catgirl_binding=_binding(),
        opening_performance={'performance': history, 'suggested_inputs': []})
    if already_false:
        prior = runtime.prepare_turn(current, TurnRequestV2('prior', 0, '先等等。'), (),
            fact_operations=({'op': 'set', 'key': KEY, 'value': False, 'visibility': 'public'},))
        current = await runtime.commit_turn(prior, {'performance': history, 'suggested_inputs': []})
    before = runtime.store._path(current.session.session_id).read_bytes()
    revision = current.session.revision
    calls = []
    message = ('可以，就按你刚才提出的安排，把设备留在这里。' if mode == 'prior_offer_accepted'
               else INPUT if mode == 'player_source' else FEEDBACK)
    source = ('我不同意，这个衔接还得再试。' if mode == 'decline'
              else '我收到你的核对结论了。' if mode == 'historical_component' else CONFIRMATION)

    async def evaluate(self, **kwargs):
        evidence = [{'source': 'actor_performance', 'quote': history}]
        if mode == 'player_source':
            evidence = []
        if mode != 'history_only':
            evidence.insert(0, {'source': 'player_input', 'quote': message})
        return ev.NumericV2EvaluationResult((), False,
            fact_operations=({'op': 'set', 'key': KEY, 'value': True, 'visibility': 'public'},),
            fact_audit=({'key': KEY, 'subject': '双方', 'action': '核对', 'object': '结论',
                         'result': '双方确认', 'evidence': evidence},))

    async def approve(self, **kwargs):
        if not kwargs['claims']:
            return ev.NumericV2StoryEvidenceReview()
        assert runtime.store._path(current.session.session_id).read_bytes() == before
        if not kwargs.get('actor_evidence'):
            assert calls == [] and not kwargs.get('actor_evidence')
            calls.append('early_review')
            return ev.NumericV2StoryEvidenceReview((0,) if mode in {
                'player_source', 'history_only', 'prior_offer_accepted'} else ())
        assert calls == ['early_review', 'source']
        assert kwargs['actor_evidence'] == source
        batch = kwargs['evidence_batch']
        batch.validate()
        assert batch.claims == kwargs['claims']
        bound = {row['ref']: row for row in batch.packet['sources']}
        proofs = [(row['quote'], bound[ref]) for row, ref in
                  zip(batch.claims[0]['evidence'], batch.bindings[0])]
        assert any(quote == history and row['kind'] == 'committed_current_visit' for quote, row in proofs)
        assert any(quote == message and row['kind'] == 'current_player' for quote, row in proofs)
        assert any(quote == source and row['kind'] == 'current_draft' for quote, row in proofs)
        calls.append('source_review')
        return ev.NumericV2StoryEvidenceReview(() if mode == 'decline' else (0,))

    async def generate(self, **kwargs):
        assert runtime.store._path(current.session.session_id).read_bytes() == before
        if kwargs['outcome'].route is None:
            assert calls == ['early_review']
            assert not kwargs['outcome'].ledger_event.get('fact_operations')
            calls.append('source')
            return {'performance': source, 'suggested_inputs': [], 'fact_candidates': []}
        calls.append('tail')
        if mode not in {'player_source', 'history_only', 'prior_offer_accepted'}:
            assert calls == ['early_review', 'source', 'source_review', 'tail']
            assert kwargs['transition_source']['performance'] == source
        return engine.finalize_transition_performance(kwargs['outcome'], {
            'source_performance': source, 'bridge_scene_narration': '',
            'target_scene_narration': '工作台的灯熄灭。', 'target_performance': '收工。',
            'suggested_inputs': [],
        }, target_opening='工作台的灯熄灭。', bridge_required=False)

    async def contract(*args, **kwargs):
        return ()

    monkeypatch.setattr(options, 'MODEL_REVIEWS_ENABLED', False)
    monkeypatch.setattr(wf, 'aload_theater_module_options', options.aload_theater_module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'review_story_evidence', approve)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'verify_contract_boundaries', contract)
    monkeypatch.setattr(wf.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(wf.NumericV2Actor, '_character_profile', lambda self: '自然克制。')
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('mixed-current', revision, message), ensure_current_binding=lambda _: _binding())
    assert result.stored.session.revision == revision + 1
    assert result.stored.session.story_state['facts'].get(KEY, {}).get('value') is (
        False if already_false and mode == 'decline' else True if mode != 'decline' else None)
    transitions = [row for row in result.stored.ledger_events if row['from_node_id'] != row['to_node_id']]
    assert len(transitions) == int(mode != 'decline')
    assert calls == (['early_review', 'tail'] if mode in {'player_source', 'history_only', 'prior_offer_accepted'}
                     else ['early_review', 'source', 'source_review'] if mode == 'decline'
                     else ['early_review', 'source', 'source_review', 'tail'])
    restored = await runtime.restore_session(current.session.session_id)
    assert restored == result.stored
