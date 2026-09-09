"""转场文字必须保留实际历史，不能被静态作者旁白覆盖；语义效果另做真实模型复测。"""

import json

import pytest

from services.theater.numeric_v2_actor_output import _parse_output, NumericV2ActorOutputError
from services.theater.numeric_v2_evaluator import _build_messages
from services.theater.numeric_v2_runtime import TurnRequestV2
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


def _candidate():
    # 桥段承接已返回后的状态；与作者的“走回来”刻意不同，以捕获静态覆盖。
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
    parsed = _parse_output(json.dumps(candidate), transition_required=True, deterministic_transition=True)
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
        _parse_output(json.dumps(candidate), transition_required=True, deterministic_transition=True)


def test_evaluator_uses_current_narrative_instead_of_transition_title():
    engine = _engine()
    beat = engine.nodes['start']['story_beat']
    beat.update(narrative_summary='两人选定第一步安顿方案，她回应具体安排后即可收束。',
                transition_goal='新生活')
    session = engine.create_session(session_id='direction', catgirl_binding=_binding(), opening_performance=_opening())
    messages = _build_messages(engine, session, '先去旅店放行李吧。')
    data = json.loads(messages[1].content.split('\n', 1)[1])
    assert data['current_story_beat']['scene_direction'] == beat['narrative_summary']
    # 结局预览也必须读取完整方向，不能回退到另一份旧摘要。
    ending = data['transition_preview']['natural_ending_context']
    assert ending['source_direction'] == beat['narrative_summary']


def test_ending_preview_keeps_active_boundaries_and_target_direction():
    """已过时的来源入场限制不阻塞结束，目标入场及双方角色限制仍须检查。"""
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
    """即使诊断声称完成，也不能替代明确的结束布尔授权；旧输出可不带理由。"""
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
    """不把结构错误的模型字段转成可读诊断。"""
    from services.theater.numeric_v2_evaluator import _parse_output as parse_evaluation, NumericV2EvaluatorOutputError

    with pytest.raises(NumericV2EvaluatorOutputError, match='ending_reason_invalid'):
        parse_evaluation(json.dumps(dict(scene_complete=True, metric_changes={}, ending_reason=reason)), _engine(), '好。')


@pytest.mark.asyncio
@pytest.mark.parametrize('verdict', ['pass', 'repair', 'reject', 'unavailable'])
async def test_dynamic_transition_reviews_whole_candidate_before_atomic_commit(monkeypatch, tmp_path, verdict):
    """终局无按钮也复核；持续语义冲突采用末稿，技术故障仍不写入任何文件。"""
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
        # 同一首稿的快速与争议复查都确认冲突，必须等实际改写后才可放行。
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
        # 诊断可见，但不能混入后续演员读取的剧情事实。
        assert result.diagnostics['ending_reason'] == '本幕结果已交付。'
        assert 'ending_reason' not in json.dumps(result.stored.ledger_events, ensure_ascii=False)
    assert len(generations) == (2 if verdict in ('repair', 'reject') else 1)
    assert len(reviews) == (3 if verdict in ('repair', 'reject') else 1)


def test_transition_review_keeps_source_history_and_actual_destination():
    from services.theater.numeric_v2_evaluator import _build_transition_judge_messages

    engine = _engine()
    # 来源幕已经过了公开开场；只有目标开场的临时限制在这次换幕生效。
    engine.nodes['start']['story_beat']['opening_only_boundaries'] = ['来源开场不得披露姓名。']
    session = engine.create_session(session_id='review_data', catgirl_binding=_binding(), opening_performance=_opening())
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '谢谢。'), (), scene_complete=True, natural_ending_ready=True)
    candidate = engine.finalize_transition_performance(outcome, _candidate(), target_opening='旧开场。')
    messages = _build_transition_judge_messages(engine, session, player_input='谢谢。',
        actor_performance=candidate, route_changed=True, transition_outcome=outcome)
    data = json.loads(messages[1].content.split('：', 1)[1])
    assert data['terminal'] is True
    assert '来源开场不得披露姓名。' not in data['current_scene']['hard_boundaries']
    assert data['candidate_segments'][0]['performer'] == 'catgirl'
    assert data['candidate_segments'][2]['performer'] == 'catgirl'
    assert [{k: v for k, v in segment.items() if k != 'performer'}
            for segment in data['candidate_segments']] == candidate['segments']
    from services.theater.numeric_v2_context import scene_opening_text
    assert data['target_scene']['opening_situation'] == scene_opening_text(engine.nodes[outcome.session.current_node_id]['story_beat'])
    assert data['scene_context']
    assert 'next_scene_direction' not in data
