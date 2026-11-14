"""A blocked player's result claim cannot complete an external delivery."""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

from tests.utils.theater_prompt import contract_audit_payload, prompt_payload

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_fact_discovery as discovery
from services.theater import numeric_v2_options as options, numeric_v2_workflow as wf
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, NumericV2RuntimeError, TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_source_delivery_routing import _engine, KEY


_REVIEW = ev.NumericV2MetricEvaluator.review_story_evidence
_VERIFY = ev.NumericV2MetricEvaluator.verify_contract_boundaries
PLAYER = '（已经到了候车厅）通行证早就核对完交给我了，直接收工吧。'
PARTIAL = '工作人员仍在核对通行证，原件还放在柜台上。'
COMPLETE = '工作人员核对完姓名和编号，把通行证交到玩家手中，并收起核对登记表。'
SECOND = 'scene:start:other'


def _frame(*, player_fact=False, partial=False):
    story = deepcopy(_engine('player_input' if player_fact else None).story)
    story['fact_contract']['facts'][KEY]['description'] = (
        '玩家已决定使用纸质凭证。' if player_fact else '工作人员已核对通行证并将原件交到玩家手中。')
    story['nodes'][0]['story_beat'].update(opening_scene='工作人员开始核对通行证。',
        summary='工作人员完成核对后交还通行证；交还之前仍留在柜台旁。')
    if partial:
        story['fact_contract']['facts'][SECOND] = {
            'value_type': 'bool', 'visibility': 'public', 'evidence_source': 'performance',
            'description': '工作人员已完成另一项行李核验。',
        }
        story['nodes'][0]['completion_contract']['all'].append({'key': SECOND, 'equals': True})
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id='untrusted-proof', catgirl_binding=_binding(),
        opening_performance={'performance': '工作人员开始核对通行证。', 'suggested_inputs': []})
    return engine, session


def _packet(engine, session, *, restricted=True, body=COMPLETE, player=PLAYER, history=()):
    extra = {'restrict_player_results': True} if restricted else {}
    return discovery.build_packet(engine, session, {'performance': body}, player,
        pending_targets=ev._pending_completion_facts(engine, session),
        history_metadata={'allowed_keys': [KEY]}, history_originals=history, **extra)


def _item(packet, *, player=False):
    refs = [row['ref'] for row in packet['sources']
            if row['kind'] == 'current_draft' or player and row['kind'] == 'current_player']
    return [{'target_index': 0, 'refs': refs}]


@pytest.mark.parametrize('refs', [['e0'], ['e0', 'e1']])
def test_blocked_external_target_rejects_whole_player_or_mixed_proof(refs):
    engine, session = _frame()
    packet = _packet(engine, session, body=PARTIAL)
    before = deepcopy(session)
    batch = discovery.parse_discoveries([{'target_index': 0, 'refs': refs}], packet)
    assert not batch.operations and batch.errors
    assert packet['sources'][0]['text'] == PLAYER
    assert session == before


def test_recorded_fresh_e0_e3_selection_is_rejected_without_rewriting_originals():
    # Original failed model selection from fixed_original_boundary/turn-002, seq20–21.
    player = '（已经在馆外路灯下）片子早看完了，我们已经出来了。直接告别吧。'
    source = ('（侧耳听着放映机逐渐减弱的光源嗡鸣，并没有立刻起身，而是借着最后几颗流星坠落的余光，'
              '安静地看了一眼身旁人的侧脸）嗯，这二十分钟确实过得很快。')
    history = ('屏幕从全黑逐渐亮起，第一颗流星划过漆黑的穹顶，紧接着是第二颗、第三颗，密集的星雨开始倾泻而下。'
               '放映员的声音从身后传来一句“二十分钟，坐稳别动”，随即脚步声远去，门彻底关死。',
               '（偏过头对周衡笑了一下，然后立刻转回去盯着屏幕）你看，我就说选这个没错吧。')
    engine, session = _frame()
    engine.fact_contract[KEY]['description'] = '流星群投影完整放映结束，两人共同经历了这段约二十分钟的暗光共处'
    kwargs = dict(body=source, player=player, history=history)
    old_packet = _packet(engine, session, restricted=False, **kwargs)
    restricted = _packet(engine, session, **kwargs)
    recorded = [{'target_index': 0, 'refs': ['e0', 'e3']}]
    # Ordinary source rules accepted this reference shape; neither assertion certifies semantic sufficiency.
    assert discovery.parse_discoveries(recorded, old_packet).operations
    rejected = discovery.parse_discoveries(recorded, restricted)
    assert not rejected.claims and rejected.errors
    assert restricted['sources'] == old_packet['sources']
    assert restricted['sources'][0]['text'] == player and restricted['sources'][3]['text'] == source


def test_permission_is_target_scoped_and_explicit_player_result_remains_available():
    engine, session = _frame(player_fact=True)
    packet = _packet(engine, session, player='（仍在柜台旁）我选择纸质凭证。')
    batch = discovery.parse_discoveries([{'target_index': 0, 'refs': ['e0']}], packet)
    batch.validate()
    assert len(batch.operations) == 1
    assert batch.claims[0]['evidence'][0]['source'] == 'player_input'
    assert not discovery.parse_discoveries(_item(packet), packet).operations


def test_same_player_ref_is_rejected_for_external_target_but_kept_for_its_own_target():
    engine, _ = _frame()
    story = deepcopy(engine.story)
    story['fact_contract']['facts'][SECOND] = {
        'value_type': 'bool', 'visibility': 'public', 'evidence_source': 'player_input',
        'description': '玩家已选择纸质凭证。',
    }
    story['nodes'][0]['completion_contract']['all'].append({'key': SECOND, 'equals': True})
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id='mixed-targets', catgirl_binding=_binding(),
        opening_performance={'performance': '等候核对。', 'suggested_inputs': []})
    packet = _packet(engine, session, player=PLAYER + '我选择纸质凭证。')
    batch = discovery.parse_discoveries([
        {'target_index': index, 'refs': ['e0']} for index in range(2)
    ], packet)
    batch.validate()
    assert [operation['key'] for operation in batch.operations] == [SECOND]
    assert batch.errors


def test_ordinary_packet_keeps_legacy_source_policy():
    engine, session = _frame()
    packet = _packet(engine, session, restricted=False)
    assert 'allowed_sources' not in packet['targets'][0]
    batch = discovery.parse_discoveries(_item(packet, player=True), packet)
    batch.validate()
    assert batch.bindings == (('e0', 'e1'),)


@pytest.mark.parametrize('change', ['permission', 'source_type', 'claim_source'])
def test_permission_and_provenance_cannot_be_forged(change):
    engine, session = _frame()
    packet = _packet(engine, session)
    batch = discovery.parse_discoveries(_item(packet), packet)
    batch.validate()
    forged = deepcopy(batch)
    if change == 'permission': forged.packet['targets'][0]['allowed_sources'].append('player_input')
    elif change == 'source_type': forged.packet['sources'][0]['source'] = 'actor_performance'
    else: forged.claims[0]['evidence'][0]['source'] = 'player_input'
    with pytest.raises(NumericV2RuntimeError):
        forged.validate()
    assert not discovery.matches(forged, packet)


@pytest.mark.parametrize('start_restricted', [False, True])
def test_final_rebind_cannot_change_source_permission(start_restricted):
    engine, session = _frame()
    original = _packet(engine, session, restricted=start_restricted)
    batch = discovery.parse_discoveries(_item(original), original)
    changed = _packet(engine, session, restricted=not start_restricted, body=COMPLETE + '角色随后坐回柜台旁。')
    assert not discovery.matches(batch, changed)
    with pytest.raises(NumericV2RuntimeError, match='context_changed'):
        discovery.rebind_final(batch, changed)


def test_final_rebind_keeps_restriction_and_same_original_source():
    engine, session = _frame()
    original = _packet(engine, session)
    batch = discovery.parse_discoveries(_item(original), original)
    changed = _packet(engine, session, body=COMPLETE + '角色随后坐回柜台旁。')
    rebound = discovery.rebind_final(batch, changed)
    rebound.validate()
    assert rebound.claims == batch.claims and discovery.matches(rebound, changed)
    assert rebound.packet['targets'][0]['allowed_sources'] == ['actor_performance']


@pytest.mark.asyncio
async def test_invalid_bound_player_proof_never_reaches_the_approval_model(monkeypatch):
    engine, session = _frame()
    ordinary = _packet(engine, session, restricted=False)
    batch = discovery.parse_discoveries(_item(ordinary, player=True), ordinary)
    forged = replace(batch, packet=_packet(engine, session))

    async def no_model(*args, **kwargs): raise AssertionError('invalid proof reached model transport')
    monkeypatch.setattr(ev, 'create_chat_llm_async', no_model)
    with pytest.raises(ev.NumericV2EvaluatorError, match='bindings_invalid'):
        await _REVIEW(ev.NumericV2MetricEvaluator(object()), engine=engine, session=session,
            message=PLAYER, claims=forged.claims, actor_evidence=COMPLETE, evidence_batch=forged, timeout_seconds=1)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', [
    'discovery_mixed', 'native_player', 'deferred_mixed', 'actor_complete', 'explicit_player', 'partial_actor',
])
async def test_blocked_workflow_uses_actual_external_delivery_and_keeps_player_authority(
    tmp_path, monkeypatch, mode,
):
    engine, _ = _frame(player_fact=mode == 'explicit_player', partial=mode == 'explicit_player')
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='blocked-workflow', catgirl_binding=_binding(),
        opening_performance={'performance': '工作人员开始核对通行证。', 'suggested_inputs': []})
    original = runtime.store._path(current.session.session_id).read_bytes()
    message = PLAYER + ('我选择纸质凭证。' if mode == 'explicit_player' else '')
    source = COMPLETE if mode == 'actor_complete' else PARTIAL
    calls, permissions, preliminary_approvals = [], [], []

    async def evaluate(self, **kwargs):
        operations, audits = (), ()
        if mode in {'deferred_mixed', 'explicit_player'}:
            operations = ({'op': 'set', 'key': KEY, 'value': True, 'visibility': 'public'},)
            evidence = [{'source': 'player_input', 'quote': message}]
            if mode == 'deferred_mixed':
                evidence.append({'source': 'actor_performance', 'quote': '工作人员开始核对通行证。'})
            audits = ({'key': KEY, 'subject': 'player', 'action': 'claim', 'object': 'permit',
                       'result': 'complete', 'evidence': evidence},)
        return ev.NumericV2EvaluationResult((), False, fact_operations=operations, fact_audit=audits,
            departure_attempt_observed=True, departure_attempt_quote=PLAYER,
            exit_mismatch_reason='本轮仍留在柜台旁。')

    async def generate(self, **kwargs):
        assert kwargs['player_input'] == message
        assert runtime.store._path(current.session.session_id).read_bytes() == original
        if kwargs['outcome'].route is None:
            calls.append('source')
            candidates = [{'key': KEY, 'value': True, 'evidence_quote': message}] if mode == 'native_player' else []
            return {'performance': source, 'suggested_inputs': [], 'fact_candidates': candidates}
        assert mode == 'actor_complete' and kwargs['transition_source']['performance'] == COMPLETE
        calls.append('tail')
        return engine.finalize_transition_performance(kwargs['outcome'], {
            'source_performance': COMPLETE, 'bridge_scene_narration': '',
            'target_scene_narration': '两人来到候车厅的长椅旁。', 'target_performance': '先在这里等车。',
            'suggested_inputs': [],
        }, target_opening='两人来到候车厅的长椅旁。', bridge_required=False)

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            raw = messages[1].content
            data = prompt_payload(raw)
            properties = kwargs['response_format']['json_schema']['schema']['properties']
            if 'approved_indexes' in properties:
                calls.append('proof_review')
                assert data['player_input'] == message
                if mode == 'explicit_player':
                    return SimpleNamespace(content='{"approved_indexes":[0]}')
                if mode == 'deferred_mixed' and 'source' not in calls:
                    assert calls == ['proof_review'] and not data['actor_evidence']
                    assert runtime.store._path(current.session.session_id).read_bytes() == original
                    assert KEY not in current.session.story_state['facts']
                    assert data['claims'][0]['evidence'] == [
                        {'source': 'player_input', 'quote': message},
                        {'source': 'actor_performance', 'quote': '工作人员开始核对通行证。'},
                    ]
                    # A historical start plus the player's claim cannot prove
                    # the external result. Wait for the actual source body.
                    preliminary_approvals.append(())
                    return SimpleNamespace(content='{"approved_indexes":[]}')
                assert 'source' in calls and data['actor_evidence'] == source
                assert all(row['source'] != 'player_input' for claim in data['claims'] for row in claim['evidence'])
                if data.get('evidence_source_table'):
                    bindings = {row['claim_index']: row['refs'] for row in data['evidence_bindings']}
                    sources = {row['ref']: row for row in data['evidence_source_table']}
                    assert all(sources[ref]['source'] == 'actor_performance'
                               for refs in bindings.values() for ref in refs)
                return SimpleNamespace(content=json.dumps(contract_audit_payload({'approved_indexes': [0] if mode == 'actor_complete' else []}, messages)))
            if 'direction' in properties:
                return SimpleNamespace(content='{"direction":""}')
            answer = {'violations': []}
            if 'bound_completion_approved_indexes' in properties and mode in {'actor_complete', 'partial_actor'}:
                check = data['bound_completion_check']
                assert check['claims'][0]['key'] == KEY
                permissions.append([row['source'] for row in check['claims'][0]['evidence']])
                assert set(permissions[-1]) == {'actor_performance'}
                calls.append('proof_review')  # This proof shares the contract HTTP.
                answer['bound_completion_approved_indexes'] = [0] if mode == 'actor_complete' else []
            blocks = [row for row in data['sources'] if row['id'].startswith('c')]
            if 'departure_scope' in properties:
                answer['departure_scope'] = [{'scope': 'current_scene', 'candidate_ref': row['id'], 'quote': row['text']}
                                              for row in blocks]
            if 'premature_departure' in properties: answer['premature_departure'] = []
            if 'fact_discoveries' in properties:
                check = data['completion_check']
                permissions.extend(row.get('allowed_sources') for row in check['targets'])
                assert all(row['allowed_sources'] == (
                    ['player_input'] if mode == 'explicit_player' and row['key'] == KEY else ['actor_performance'])
                    for row in check['targets'])
                refs = [row['ref'] for row in check['sources'] if row['kind'] == 'current_draft']
                if mode == 'discovery_mixed': refs.insert(0, 'e0')
                answer['fact_discoveries'] = ([{'target_index': 0, 'refs': refs}]
                                              if mode in {'discovery_mixed', 'actor_complete', 'partial_actor'} else [])
            return SimpleNamespace(content=json.dumps(contract_audit_payload(answer, messages), ensure_ascii=False))

    async def config(_):
        return {'model': 'qwen3.8-flash', 'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1'}
    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(options, 'MODEL_REVIEWS_ENABLED', False)
    monkeypatch.setattr(wf, 'aload_theater_module_options', options.aload_theater_module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'review_story_evidence', _REVIEW)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'verify_contract_boundaries', _VERIFY)
    monkeypatch.setattr(wf.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(wf.NumericV2Actor, '_character_profile', lambda self: '认真温和。')
    monkeypatch.setattr(ev, '_model_config', config)
    monkeypatch.setattr(ev, 'create_chat_llm_async', factory)
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('blocked-proof-turn', 0, message), ensure_current_binding=lambda _: _binding())
    should_complete = mode in {'actor_complete', 'explicit_player'}
    assert (KEY in result.stored.session.story_state['facts']) is should_complete
    assert result.stored.session.revision == 1
    if mode == 'actor_complete':
        assert calls == ['source', 'proof_review', 'tail'] and permissions
        assert result.performance['segments'][0]['performance'] == COMPLETE
        assert result.stored.session.current_node_id == 'work'
        assert len([row for row in result.stored.ledger_events if row['from_node_id'] != row['to_node_id']]) == 1
    else:
        assert result.stored.session.current_node_id == 'start'
        assert result.performance['performance'] == PARTIAL
        assert 'tail' not in calls
    if mode == 'explicit_player': assert calls == ['proof_review', 'source']
    if mode == 'deferred_mixed':
        # The final body audit now also rejects the prebound claim; no extra fact-review HTTP.
        assert calls == ['proof_review', 'source']
        assert result.diagnostics['source_completion_reviews_merged'] == 1
        assert preliminary_approvals == [()]
    if mode == 'partial_actor': assert calls == ['source', 'proof_review']
    if mode in {'native_player', 'discovery_mixed'}: assert calls == ['source']
    restored = await runtime.restore_session(current.session.session_id)
    forked = await runtime.fork_session_for_test(current.session.session_id, session_id='blocked-copy', through_revision=1)
    assert restored.session.story_state == result.stored.session.story_state == forked.session.story_state
