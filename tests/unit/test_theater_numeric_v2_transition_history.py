"""Transition prose must retain actual history rather than be overwritten by static author narration; retest semantics with real models separately."""

import json

import pytest

from services.theater.numeric_v2_actor_output import _parse_output, NumericV2ActorOutputError
from services.theater.numeric_v2_evaluator import _build_messages
from services.theater.numeric_v2_runtime import TurnRequestV2
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


def _candidate():
    # State after transition return; deliberately differs from author's "走回来" to catch static overwrite.
    return dict(source_performance='（接过毛巾）谢谢，消息已经发出。',
                bridge_scene_narration='窗外的雨渐渐停了。',
                target_scene_narration='她擦干头发，把毛巾搭在椅背上。',
                target_performance='（望向窗外）终于可以歇一会儿了。', suggested_inputs=[])


@pytest.mark.asyncio
async def test_compact_transition_preserves_both_generated_narrations_through_commit(tmp_path):
    from services.theater.numeric_v2_runtime import NumericV2Runtime

    engine = _engine()
    runtime = NumericV2Runtime(engine, tmp_path)
    stored = await runtime.start_session(session_id='history', catgirl_binding=_binding(), opening_performance=_opening())
    session = stored.session
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '给你毛巾。'), (),
                                  scene_complete=True, natural_ending_ready=True)
    candidate = _candidate()
    parsed = _parse_output(json.dumps(candidate), transition_required=True)
    assembled = engine.finalize_transition_performance(outcome, parsed,
        target_opening='她从楼梯返回控制室。', bridge_scene_narration='她走回控制室。')
    assert assembled['segments'][1]['scene_narration'] == candidate['bridge_scene_narration']
    assert assembled['segments'][2]['scene_narration'] == candidate['target_scene_narration']
    assert assembled['visible_node_id'] == outcome.session.current_node_id
    committed = await runtime.commit_turn(outcome, assembled)
    assert await runtime.restore_session('history') == committed
    assert committed.session.performance_history[-1]['segments'] == assembled['segments']


@pytest.mark.parametrize('field', ['bridge_scene_narration', 'target_scene_narration'])
def test_new_compact_transition_cannot_silently_fall_back_to_author_text(field):
    candidate = _candidate()
    candidate.pop(field)
    with pytest.raises(NumericV2ActorOutputError):
        _parse_output(json.dumps(candidate), transition_required=True)


def test_evaluator_uses_current_narrative_instead_of_transition_title():
    engine = _engine()
    beat = engine.nodes['start']['story_beat']
    beat.update(narrative_summary='两人选定第一步安顿方案，她回应具体安排后即可收束。',
                transition_goal='新生活')
    session = engine.create_session(session_id='direction', catgirl_binding=_binding(), opening_performance=_opening())
    messages = _build_messages(engine, session, '先去旅店放行李吧。')
    data = json.loads(messages[1].content.split('\n', 1)[1])
    assert data['current_story_beat']['scene_direction'] == beat['narrative_summary']
    # Ending preview must read full direction too, never fall back to another old summary.
    ending = data['transition_preview']['natural_ending_context']
    assert ending['source_direction'] == beat['narrative_summary']


def test_ending_preview_keeps_active_boundaries_and_target_direction():
    """Expired source entrance restrictions do not block ending, while target entrance and both characters' boundaries still require review."""
    engine = _engine()
    source = engine.nodes['start']['story_beat']
    target = engine.nodes['ending_leave']['story_beat']
    source.update(opening_only_boundaries=['玩家尚未提出方案。'],
                  character_state={'scene_boundaries': ['不替玩家确认新的安排。']})
    target.update(narrative_summary='双方约定明确，角色回应后收束。',
                  opening_only_boundaries=['已有双方明确的约定。'],
                  acting_contract={'forbidden_behaviors': ['不追加入住手续。']})
    session = engine.create_session(session_id='boundaries', catgirl_binding=_binding(), opening_performance=_opening())
    data = json.loads(_build_messages(engine, session, '按约定办。')[1].content.split('\n', 1)[1])
    context = data['transition_preview']['natural_ending_context']
    assert context['ending_direction'] == target['narrative_summary']
    assert '不替玩家确认新的安排。' in context['source_boundaries']
    assert '玩家尚未提出方案。' not in context['source_boundaries']
    assert {'已有双方明确的约定。', '不追加入住手续。'} <= set(context['ending_boundaries'])


@pytest.mark.parametrize('reason', [None, '双方已达成约定。', '依据' * 1000])
def test_ending_reason_never_grants_runtime_authorization(reason):
    """A diagnostic claim of completion cannot replace explicit ending authorization; legacy output may omit the reason."""
    from services.theater.numeric_v2_evaluator import _parse_output as parse_evaluation
    from utils.tokenize import count_tokens

    engine = _engine()
    payload = dict(scene_complete=True, metric_changes={})
    if reason is not None:
        payload['ending_reason'] = reason
    evaluation = parse_evaluation(json.dumps(payload), engine, '好。')
    assert not evaluation.natural_ending_ready
    assert count_tokens(evaluation.ending_reason) <= 80
    session = engine.create_session(session_id='diagnostic', catgirl_binding=_binding(), opening_performance=_opening())
    outcome = engine.resolve_turn(session, TurnRequestV2('reason', 0, '好。'), (),
                                  scene_complete=evaluation.scene_complete,
                                  natural_ending_ready=evaluation.natural_ending_ready)
    assert outcome.session.current_node_id == session.current_node_id


@pytest.mark.parametrize('reason', [True, [], {}])
def test_ending_reason_rejects_non_text(reason):
    """Do not turn malformed model fields into readable diagnostics."""
    from services.theater.numeric_v2_evaluator import _parse_output as parse_evaluation, NumericV2EvaluatorOutputError

    with pytest.raises(NumericV2EvaluatorOutputError, match='ending_reason_invalid'):
        parse_evaluation(json.dumps(dict(scene_complete=True, metric_changes={}, ending_reason=reason)), _engine(), '好。')


@pytest.mark.asyncio
@pytest.mark.parametrize('verdict', ['pass', 'repair', 'reject', 'unavailable'])
async def test_dynamic_transition_reviews_whole_candidate_before_atomic_commit(monkeypatch, tmp_path, verdict):
    """Review final nodes even without buttons; persistent semantic conflicts adopt the final draft, while technical failures write no files."""
    from services.theater import numeric_v2_workflow as workflow
    from services.theater.numeric_v2_evaluator import NumericV2EvaluationResult, NumericV2TransitionOfferReview, NumericV2EvaluatorError
    from services.theater.numeric_v2_runtime import NumericV2Runtime

    engine = _engine()
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='review', catgirl_binding=_binding(), opening_performance=_opening())
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    reviews, generations = [], []

    async def evaluate(self, **kwargs):
        return NumericV2EvaluationResult((), True, natural_ending_ready=True, ending_reason='本幕结果已交付。')

    async def generate(self, **kwargs):
        generations.append(kwargs)
        return engine.finalize_transition_performance(kwargs['outcome'], _candidate(), target_opening='旧开场。')

    async def review(self, **kwargs):
        reviews.append(kwargs)
        assert kwargs['session'] == current.session
        assert len(kwargs['actor_performance']['segments']) == 3
        assert kwargs['transition_outcome'].session.status == 'ended'
        if verdict == 'unavailable':
            raise NumericV2EvaluatorError('probe_unavailable')
        # Fast/dispute reviews both confirm same-draft conflict; allow only after actual rewrite.
        bad = verdict == 'reject' or (verdict == 'repair' and len(generations) == 1)
        return NumericV2TransitionOfferReview(False, False,
            body_violations=('author_boundary',) if bad else (), unsafe_suggestion_indexes=(),
            failure_reason='转场事实冲突。' if bad else '')

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    kwargs = dict(config_manager=object(), runtime=runtime, current=current,
                  turn=TurnRequestV2('one', 0, '给你毛巾。'), ensure_current_binding=lambda _: _binding())
    if verdict == 'unavailable':
        with pytest.raises(NumericV2ActorOutputError):
            await workflow.execute_numeric_v2_turn(**kwargs)
        assert await runtime.restore_session('review') == current
        assert {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
    else:
        result = await workflow.execute_numeric_v2_turn(**kwargs)
        assert result.stored.session.status == 'ended'
        assert result.diagnostics['semantic_review_fallback'] is (verdict == 'reject')
        # Diagnostics stay visible but never enter plot facts consumed by later actors.
        assert result.diagnostics['ending_reason'] == '本幕结果已交付。'
        assert 'ending_reason' not in json.dumps(result.stored.ledger_events, ensure_ascii=False)
    assert len(generations) == (2 if verdict in ('repair', 'reject') else 1)
    assert len(reviews) == (3 if verdict in ('repair', 'reject') else 1)


def test_transition_review_keeps_source_history_and_actual_destination():
    from services.theater.numeric_v2_evaluator import _build_transition_judge_messages

    engine = _engine()
    # Source act already passed public opening; only destination opening's temporary restriction applies to this transition.
    engine.nodes['start']['story_beat']['opening_only_boundaries'] = ['来源开场不得披露姓名。']
    ending_direction = '回应获准的最后互动，完整收束本次告别。'
    engine.nodes['ending_leave']['story_beat']['narrative_summary'] = ending_direction
    session = engine.create_session(session_id='review_data', catgirl_binding=_binding(), opening_performance=_opening())
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '谢谢。'), (), scene_complete=True, natural_ending_ready=True)
    candidate = engine.finalize_transition_performance(outcome, _candidate(), target_opening='旧开场。')
    messages = _build_transition_judge_messages(engine, session, player_input='谢谢。',
        actor_performance=candidate, route_changed=True, transition_outcome=outcome)[0]
    data = json.loads(messages[1].content.split('：', 1)[1])
    assert data['terminal'] is True
    assert '来源开场不得披露姓名。' not in data['current_scene']['hard_boundaries']
    assert data['candidate_segments'][0]['performer'] == 'catgirl'
    assert data['candidate_segments'][2]['performer'] == 'catgirl'
    assert [{k: v for k, v in segment.items() if k != 'performer'}
            for segment in data['candidate_segments']] == candidate['segments']
    from services.theater.numeric_v2_context import scene_opening_text
    assert data['target_scene']['opening_situation'] == scene_opening_text(engine.nodes[outcome.session.current_node_id]['story_beat'])
    assert data['target_scene']['story_direction'] == ending_direction
    assert 'story_direction 是作者模板，不是待播正文' in messages[0].content
    assert data['scene_context']
    assert 'next_scene_direction' not in data


@pytest.mark.asyncio
@pytest.mark.parametrize('place,destination', [('档案接待台', '阅览室'), ('维修平台', '观察舱')])
@pytest.mark.parametrize('intent', ['accept', 'initiate'])
async def test_review_target_direction_waits_until_after_nonterminal_opening(tmp_path, place, destination, intent):
    """Review the authorized entrance with all safety evidence, then restore the full direction after its native commit."""
    from copy import deepcopy
    from services.theater.numeric_v2_evaluator import _build_transition_judge_messages
    from services.theater.numeric_v2_performance import performance_content_blocks
    from services.theater.numeric_v2_runtime import NumericV2Runtime
    from tests.unit.test_theater_numeric_v2_pending_offer import _commit
    from tests.unit.test_theater_numeric_v2_player_transition import initiation_case

    case = initiation_case(place, destination, message=f'请带我到{destination}入口。')
    engine = case['engine']
    target = engine.nodes['ending_leave']['story_beat']
    future = f'之后由玩家选择是否查看{destination}中的资料，确认后才开始操作。'
    target.update(summary=future, narrative_summary=future,
        opening_only_boundaries=['入幕时后续操作尚未开始。'],
        character_state={'catgirl_state': '猫娘站在入口，拿着自己的包。',
                         'player_state': '玩家仍持有自己的登记卡。',
                         'scene_boundaries': ['不得改变玩家登记卡的持有者。']},
        acting_contract={'allowed_behaviors': ['猫娘可以介绍入口标识。'],
                         'assertable_self_facts': ['猫娘保留自己的包。'],
                         'forbidden_behaviors': ['不得替玩家选择查阅内容。']})
    runtime = NumericV2Runtime(engine, tmp_path)
    stored = await runtime.start_session(session_id='review_entry', catgirl_binding=_binding(),
                                        opening_performance=case['session'].opening_performance)
    invitation = f'我们去{destination}入口，好吗？'
    if intent == 'accept':
        # Latch/commit real invitation; setting Session boolean alone cannot supply the original public invitation.
        stored = await _commit(runtime, stored, invitation, offer=True)
    message = f'好，我们去{destination}入口。' if intent == 'accept' else case['message']
    outcome = runtime.prepare_turn(stored, TurnRequestV2('enter', stored.session.revision, message), (),
                                   transition_intent=intent)
    assert outcome.session.status != 'ended'
    candidate = engine.finalize_transition_performance(outcome, {
        'source_performance': '（拿好自己的包）跟我来。',
        'bridge_scene_narration': f'两人沿左侧走廊来到{destination}。',
        'target_scene_narration': target['opening_scene'],
        'target_performance': '（看向入口标识）这里就是入口。', 'suggested_inputs': [],
    }, target_opening=target['opening_scene'])
    before = deepcopy((engine.story, stored.session, outcome, candidate))
    public_quote = f'左侧走廊通往{destination}，通道已经开放。'
    messages = _build_transition_judge_messages(engine, stored.session, player_input=message,
        actor_performance=candidate, transition_outcome=outcome, public_destination_quote=public_quote)[0]
    data = json.loads(messages[1].content.split('：', 1)[1])
    assert 'story_direction' not in data['target_scene']
    assert future not in '\n'.join(part.content for part in messages)
    # An excluded template cannot consume packing budget or evict history.
    target.update(summary=future * 10000, narrative_summary=future * 10000)
    try:
        enlarged = _build_transition_judge_messages(engine, stored.session, player_input=message,
            actor_performance=candidate, transition_outcome=outcome, public_destination_quote=public_quote)[0]
        assert [part.content for part in enlarged] == [part.content for part in messages]
    finally:
        target.update(summary=future, narrative_summary=future)
    context = data['target_scene']
    assert context['opening_situation'] == target['opening_scene']
    assert context['character_state']['catgirl_state'] == target['character_state']['catgirl_state']
    assert context['character_state']['player_state'] == target['character_state']['player_state']
    assert context['acting_contract']['allowed_behaviors'] == target['acting_contract']['allowed_behaviors']
    assert context['acting_contract']['assertable_self_facts'] == target['acting_contract']['assertable_self_facts']
    assert {'入幕时后续操作尚未开始。', '不得改变玩家登记卡的持有者。',
            '不得替玩家选择查阅内容。'} <= set(context['hard_boundaries'])
    assert data['player_input'] == message
    assert data['transition_contract'] == outcome.transition_contract
    assert data['transition_authorization']['transition_intent'] == intent
    if intent == 'accept':
        original = data['transition_authorization']['pending_invitation']
        assert original['revision'] == stored.session.revision
        assert invitation in json.dumps(original, ensure_ascii=False)
    else:
        assert data['transition_authorization']['public_destination_quote'] == public_quote
    assert data['scene_context'][-1]['revision'] == stored.session.revision
    latest = (stored.session.performance_history[-1] if stored.session.performance_history
              else stored.session.opening_performance)
    assert [(part['type'], part['text']) for part in data['scene_context'][-1]['content']] == [
        (part['type'], part['text']) for part in performance_content_blocks(latest)]
    assert [{key: value for key, value in segment.items() if key != 'performer'}
            for segment in data['candidate_segments']] == candidate['segments']
    assert (engine.story, stored.session, outcome, candidate) == before

    # Construct committed entry history to test next normal turn packing, without presetting model review verdicts.
    committed = await runtime.commit_turn(outcome, candidate)
    restored = await NumericV2Runtime(engine, tmp_path).restore_session('review_entry')
    assert restored == committed
    ordinary = _build_transition_judge_messages(engine, restored.session, player_input='先看看入口标识。',
        actor_performance={'performance': '（指向标识）可以先看看这里。', 'suggested_inputs': []})[0]
    ordinary_data = json.loads(ordinary[1].content.split('：', 1)[1])
    assert ordinary_data['current_scene']['story_direction'] == future
    assert 'target_scene' not in ordinary_data
    assert (engine.story, stored.session, outcome, candidate) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('requirement', ['optional', 'authored_bridge', 'independent_delivery'])
async def test_compact_bridge_contract_through_actor_commit_and_restore(monkeypatch, tmp_path, requirement):
    """The real Actor parser accepts contract-permitted output; missing required bridges cannot create partial turns, and empty bridges consume no playback or TTS index."""
    from services.theater import numeric_v2_actor as actor_module
    from services.theater.numeric_v2_performance import performance_content_blocks
    from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime
    from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story

    story = numeric_v2_story()
    target_opening = '雨后的长街安静下来。'
    for node in story['nodes']:
        if node['type'] == 'ending':
            node['story_beat']['opening_scene'] = target_opening
        for route in node.get('route_gates', []):
            route['transition_contract']['must_deliver'] = [
                '两人沿楼梯抵达楼下大厅。' if requirement == 'independent_delivery' else target_opening
            ]
            if requirement == 'authored_bridge':
                route['transition_contract']['bridge_scene_narration'] = '一夜过去，天已亮。'
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(session_id='bridge', catgirl_binding=_binding(), opening_performance=_opening())
    outcome = runtime.prepare_turn(current, TurnRequestV2('one', 0, '谢谢。'), (),
                                   scene_complete=True, natural_ending_ready=True)
    candidate = _candidate()
    candidate['bridge_scene_narration'] = ''
    messages_seen = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, messages):
            messages_seen.append(messages)
            return type('Response', (), {'content': json.dumps(candidate, ensure_ascii=False)})()

    async def model_config(_config_manager):
        return {'model': 'test', 'base_url': 'http://test.invalid'}

    async def client(*_args, **_kwargs):
        return Client()

    monkeypatch.setattr(actor_module, '_model_config', model_config)
    monkeypatch.setattr(actor_module, 'create_chat_llm_async', client)
    actor = actor_module.NumericV2Actor(object())
    kwargs = dict(engine=runtime.engine, session=current.session, outcome=outcome,
                  player_input='谢谢。', character_profile='温和克制。')
    if requirement != 'optional':
        before = runtime.store._path('bridge').read_bytes()
        with pytest.raises(NumericV2ActorOutputError, match='scene_narration_invalid'):
            await actor.generate_turn(**kwargs)
        assert runtime.store._path('bridge').read_bytes() == before
        candidate['bridge_scene_narration'] = '天亮后，两人沿楼梯抵达大厅。'
    performance = await actor.generate_turn(**kwargs)
    data = json.loads(messages_seen[-1][1].content.split('\n', 1)[1])
    assert data['transition']['bridge_required'] is (requirement != 'optional')
    assert 'bridge_required' in messages_seen[-1][0].content
    assert performance['segments'][1]['scene_narration'] == candidate['bridge_scene_narration']
    committed = await runtime.commit_turn(outcome, performance)
    restored = await NumericV2Runtime(runtime.engine, tmp_path).restore_session('bridge')
    assert restored == committed
    assert restored.session.revision == len(restored.ledger_events) == 1
    blocks = performance_content_blocks(restored.session.performance_history[-1])
    assert all(b['text'].strip() for b in blocks)
    assert len(blocks) == (5 if requirement == 'optional' else 6)
    fork = await runtime.fork_session_for_test('bridge', session_id='bridge_fork', through_revision=1)
    assert fork.session.performance_history == restored.session.performance_history
    with pytest.raises(ValueError, match='session_already_ended'):
        runtime.prepare_turn(restored, TurnRequestV2('two', 1, '继续'), ())


@pytest.mark.parametrize('invalid', [None, 0, [], {}])
def test_optional_compact_bridge_does_not_accept_invalid_type(invalid):
    candidate = _candidate()
    candidate['bridge_scene_narration'] = invalid
    with pytest.raises(NumericV2ActorOutputError, match='scene_narration_invalid'):
        _parse_output(json.dumps(candidate), transition_required=True,
                      bridge_required=False)


@pytest.mark.parametrize('field', ['source_performance', 'target_performance', 'target_scene_narration'])
def test_optional_bridge_does_not_relax_other_transition_text(field):
    candidate = _candidate()
    candidate[field] = ''
    with pytest.raises(NumericV2ActorOutputError):
        _parse_output(json.dumps(candidate), transition_required=True,
                      bridge_required=False)


def test_compact_bridge_stays_required_without_explicit_contract_permission():
    candidate = _candidate()
    candidate['bridge_scene_narration'] = ''
    with pytest.raises(NumericV2ActorOutputError, match='scene_narration_invalid'):
        _parse_output(json.dumps(candidate), transition_required=True)


def test_runtime_optional_bridge_matches_legacy_array_permission():
    engine = _engine()
    session = engine.create_session(session_id='bridge_paths', catgirl_binding=_binding(), opening_performance=_opening())
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '谢谢。'), (),
                                  scene_complete=True, natural_ending_ready=True)
    candidate = _candidate()
    candidate['bridge_scene_narration'] = ''
    compact = engine.finalize_transition_performance(outcome, candidate, target_opening='旧开场。', bridge_required=False)
    legacy = engine.finalize_transition_performance(outcome, {'segments': [
        {'phase': 'source_response', 'performance': candidate['source_performance']},
        {'phase': 'transition_bridge', 'scene_narration': ''},
        {'phase': 'target_opening', 'performance': candidate['target_performance']},
    ], 'suggested_inputs': []}, target_opening=candidate['target_scene_narration'], bridge_required=False)
    assert compact == legacy
    with pytest.raises(ValueError, match='numeric_transition_performance_invalid'):
        engine.finalize_transition_performance(outcome, candidate, target_opening='旧开场。', bridge_required=True)
