"""Exercise literal delivery, trigger evidence and persistence as one transaction."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.theater.numeric_v2 import NumericV2CompileError
from services.theater.numeric_v2_actor import _opening_messages, _turn_messages
from services.theater.numeric_v2_actor_output import _parse_output, NumericV2ActorOutputError
from services.theater.numeric_v2_archive import _performance_memory_parts
from services.theater.numeric_v2_context import history_evidence
from services.theater.numeric_v2_evaluator import (
    NumericV2EvaluationResult, NumericV2TransitionOfferReview,
    NumericV2EvaluatorOutputError,
    _build_transition_judge_messages, _parse_transition_judge_output,
)
from services.theater.numeric_v2_fixed_narration import apply_triggers, displayed_ids, validate_delivery
from services.theater.numeric_v2_performance import performance_content_blocks, performance_dialogue
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from services.theater import numeric_v2_workflow as workflow
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


REPORT = '[REPORT]\n损坏率：87.4%\n序列号00-Aoi（不可改写）'
LOG = '0042\n“永远优先应答你的呼叫。”\n[EOF]'
ACTION = '（将铭牌放入读取器）已经接好了。'
OPENING = {'scene_narration': '修理台亮起。', 'performance': '（看向你）你是谁？', 'suggested_inputs': []}


def _piece(piece_id, text, *, entry=False, after=(), required=True):
    return {'id': piece_id, 'text': text,
            'trigger': {'type': 'entry'} if entry else {'type': 'condition', 'condition': '铭牌已经放入读取器。'},
            'after': list(after), 'required_before_exit': required}


def _engine():
    story = numeric_v2_story()
    story['nodes'][0]['story_beat']['fixed_narrations'] = [
        _piece('report', REPORT, entry=True), _piece('log', LOG, after=['report'])]
    return NumericV2Engine.from_mapping(story)


def _claims():
    return ({'id': 'log', 'evidence': '将铭牌放入读取器'},)


@pytest.mark.parametrize('mutation', [
    lambda rows: rows.append(deepcopy(rows[0])),
    lambda rows: rows[0].update(after=['log']),
    lambda rows: rows[1].update(after=['missing']),
    lambda rows: rows[1].update(required_before_exit='true'),
    lambda rows: rows[1].update(text='{{unknown_name}}'),
    lambda rows: rows[1].update(text='太长的日志。' * 3000),
    lambda rows: rows[1].update(trigger={'type': 'turn_number', 'number': 3}),
])
def test_compiler_rejects_invalid_fixed_narration_contract(mutation):
    story = deepcopy(_engine().story)
    mutation(story['nodes'][0]['story_beat']['fixed_narrations'])
    with pytest.raises(NumericV2CompileError):
        NumericV2Engine.from_mapping(story)


def test_actor_cannot_supply_runtime_owned_text():
    with pytest.raises(NumericV2ActorOutputError):
        _parse_output(json.dumps({'performance': ACTION, 'suggested_inputs': [], 'transition_offered': False,
                                 'fixed_narrations': [{'id': 'log', 'text': 'forged'}]}), opening_required=False)


def test_public_payload_keeps_literal_text_but_hides_internal_metadata():
    from main_routers.numeric_theater_router import _public_performance

    engine = _engine()
    session = engine.create_session(session_id='public-fixed', catgirl_binding=_binding(), opening_performance=OPENING)
    public = _public_performance({'segments': [session.opening_performance]})
    assert public['segments'][0]['fixed_narrations'] == [{'text': REPORT, 'position': 'before'}]
    assert session.opening_performance['fixed_narrations'][0]['node_id'] == 'start'


@pytest.mark.asyncio
async def test_target_entry_is_mandatory_and_survives_committed_transition(tmp_path):
    story = numeric_v2_story()
    for node in story['nodes'][1:]:
        node['story_beat']['fixed_narrations'] = [_piece('entry', REPORT, entry=True)]
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='target-entry', catgirl_binding=_binding(), opening_performance=OPENING)
    outcome = runtime.prepare_turn(current, TurnRequestV2('one', 0, '就到这里吧。'), (),
                                   scene_complete=True, natural_ending_ready=True)
    assert outcome.session.current_node_id != current.session.current_node_id
    parsed = _parse_output(json.dumps(_candidate()), transition_required=True, deterministic_transition=True)
    performance = engine.finalize_transition_performance(outcome, parsed, target_opening='修理台亮起。')
    missing = deepcopy(performance)
    missing['segments'][2].pop('fixed_narrations')
    with pytest.raises(ValueError, match='entry_missing'):
        await runtime.commit_turn(outcome, missing)
    assert (await runtime.restore_session(current.session.session_id)).session.revision == 0
    saved = await runtime.commit_turn(outcome, performance)
    assert await runtime.restore_session(current.session.session_id) == saved
    assert performance['segments'][2]['fixed_narrations'][0]['text'] == REPORT


def test_name_placeholders_do_not_rewrite_serials_or_nested_values():
    engine = _engine()
    engine.story['initial_state']['player_address_known'] = False
    engine.nodes['start']['story_beat']['fixed_narrations'][0]['text'] = '{{catgirl_name}}／{{player_name}}／00-Aoi'
    binding = {**_binding(), 'catgirl_name': '新猫娘{{player_name}}'}
    session = engine.create_session(session_id='names', catgirl_binding=binding, opening_performance=OPENING)
    assert session.opening_performance['fixed_narrations'][0]['text'] == '新猫娘{{player_name}}／你／00-Aoi'
    renamed = replace(session, catgirl_binding={**binding, 'catgirl_name': '又一个名字'})
    # Recovery uses the names captured with the delivered text, never today's name.
    validate_delivery(engine.story, renamed.opening_performance)


def test_prompt_hides_untriggered_text_and_preserves_delivered_text():
    engine = _engine()
    session = engine.create_session(session_id='prompts', catgirl_binding=_binding(), opening_performance=OPENING)
    opening = '\n'.join(m.content for m in _opening_messages(engine, '温和。', 'Lan', '你', False, max_tokens=10000))
    assert REPORT in opening and LOG not in opening
    review_messages = _build_transition_judge_messages(engine, session, actor_performance={'performance': ACTION}, player_input='帮我接上。')
    packed = '\n'.join(m.content for m in review_messages)
    assert 'fixed_narration_candidates' in packed and '铭牌已经放入读取器' in packed
    assert '固定五字段' not in packed
    assert '"fixed_narration_triggers":[]' in packed
    assert LOG not in packed
    engine.story['intro']['catgirl_name'] = '原来的姓名'
    engine.nodes['start']['story_beat']['fixed_narrations'][1]['trigger']['condition'] = '原来的姓名实际拿起铭牌。'
    renamed = replace(session, catgirl_binding={**session.catgirl_binding, 'catgirl_name': '新角色'})
    messages = _build_transition_judge_messages(engine, renamed, actor_performance={'performance': ACTION}, player_input='帮我接上。')
    payload = json.loads(messages[1].content.split('：', 1)[1])
    assert payload['fixed_narration_candidates'][0]['condition'] == '新角色实际拿起铭牌。'
    opening = '\n'.join(m.content for m in _opening_messages(engine, '温和。', '新角色', '你', False, max_tokens=10000))
    assert '新角色实际拿起铭牌。' in opening


@pytest.mark.asyncio
async def test_literal_order_cold_recovery_fork_and_exit_gate(tmp_path):
    engine = _engine()
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='fixed', catgirl_binding=_binding(), opening_performance=OPENING)
    opening_blocks = performance_content_blocks(current.session.opening_performance)
    assert [b['text'] for b in opening_blocks][:2] == ['修理台亮起。', REPORT]
    request = TurnRequestV2('one', 0, '帮我接上。')
    outcome = runtime.prepare_turn(current, request, (), scene_complete=True, natural_ending_ready=True)
    assert outcome.session.status == 'active'
    raw = {'performance': ACTION, 'suggested_inputs': [], 'transition_offered': False}
    performance = apply_triggers(engine.nodes['start'], current.session, raw, _claims(), request.message, known=False)
    assert performance_content_blocks(performance)[-1] == {'type': 'narration', 'text': LOG}
    assert all(LOG not in line['text'] for line in performance_dialogue(performance))
    committed = await runtime.commit_turn(outcome, performance)
    restored = await NumericV2Runtime(engine, tmp_path).restore_session('fixed')
    assert restored == committed
    assert displayed_ids(restored.session) == {('start', 'report'), ('start', 'log')}
    forked = await runtime.fork_session_for_test('fixed', session_id='fork', through_revision=1)
    assert forked.session.performance_history == restored.session.performance_history
    assert apply_triggers(engine.nodes['start'], restored.session, raw, _claims(), request.message, known=False) == raw
    parts, text = _performance_memory_parts(performance, phase='ordinary')
    assert parts[-1]['kind'] == 'scene_narration' and parts[-1]['text'] == LOG
    assert text.index(ACTION) < text.index(LOG)
    assert LOG in json.dumps(history_evidence(restored.session, '日志0042写了什么？'), ensure_ascii=False).replace('\\n', '\n')
    end = runtime.prepare_turn(restored, TurnRequestV2('two', 1, '从现在开始。'), (), scene_complete=True, natural_ending_ready=True)
    assert end.session.status == 'ended'
    actor_messages = _turn_messages(engine, restored.session, end, '从现在开始。', '温和。', 'Lan', '你', False)
    assert LOG in '\n'.join(m.content for m in actor_messages).replace('\\n', '\n')
    closing = engine.finalize_transition_performance(end, _candidate(), target_opening='记录继续。')
    await runtime.commit_turn(end, closing)
    assert (await NumericV2Runtime(engine, tmp_path).restore_session('fixed')).session.status == 'ended'


@pytest.mark.parametrize('claims', [({'id': 'log', 'evidence': '没有发生的原话'},),
                                  ({'id': 'unknown', 'evidence': '将铭牌放入读取器'},)])
def test_uncited_or_unknown_triggers_do_not_display(claims):
    engine = _engine()
    session = engine.create_session(session_id='evidence', catgirl_binding=_binding(), opening_performance=OPENING)
    raw = {'performance': ACTION}
    assert apply_triggers(engine.nodes['start'], session, raw, claims, '接上。', known=False) == raw


@pytest.mark.asyncio
async def test_forged_literal_is_rejected_before_persistence(tmp_path):
    runtime = NumericV2Runtime(_engine(), tmp_path)
    current = await runtime.start_session(session_id='tamper', catgirl_binding=_binding(), opening_performance=OPENING)
    outcome = runtime.prepare_turn(current, TurnRequestV2('one', 0, '接上。'), ())
    performance = apply_triggers(runtime.engine.nodes['start'], current.session, {'performance': ACTION}, _claims(), '接上。', known=False)
    performance['fixed_narrations'][0]['text'] = '被改写了'
    with pytest.raises(ValueError, match='numeric_fixed_narration_invalid'):
        await runtime.commit_turn(outcome, performance)
    assert await runtime.restore_session('tamper') == current


@pytest.mark.asyncio
@pytest.mark.parametrize('rewrite', [False, True])
async def test_workflow_uses_final_review_and_rollback_keeps_piece_pending(tmp_path, monkeypatch, rewrite):
    runtime = NumericV2Runtime(_engine(), tmp_path)
    current = await runtime.start_session(session_id='workflow', catgirl_binding=_binding(), opening_performance=OPENING)
    calls = {'actor': 0, 'review': 0}

    async def evaluate(self, **kwargs):
        return NumericV2EvaluationResult((), True, natural_ending_ready=True)

    async def actor(self, **kwargs):
        calls['actor'] += 1
        return {'performance': ACTION if calls['actor'] == 1 else '（看着铭牌）还没有接上。', 'suggested_inputs': [], 'transition_offered': False}

    async def review(self, **kwargs):
        calls['review'] += 1
        bad = rewrite and calls['actor'] == 1
        return NumericV2TransitionOfferReview(False, False, ('author_boundary',) if bad else (), (),
            fixed_narration_triggers=_claims() if calls['actor'] == 1 else ())

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', actor)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    kwargs = dict(config_manager=object(), runtime=runtime, current=current,
                  turn=TurnRequestV2('one', 0, '接上。'), ensure_current_binding=lambda _: _binding())
    committed = await workflow.execute_numeric_v2_turn(**kwargs)
    assert (('start', 'log') in displayed_ids(committed.stored.session)) is not rewrite
    assert committed.stored.session.revision == len(committed.stored.ledger_events) == 1
    assert calls['actor'] == (2 if rewrite else 1)
    assert calls['review'] == (3 if rewrite else 1)

    # A different uncommitted session exercises the same storage-failure boundary.
    second = await runtime.start_session(session_id='failure', catgirl_binding=_binding(), opening_performance=OPENING)
    outcome = runtime.prepare_turn(second, TurnRequestV2('retry', 0, '接上。'), ())
    performance = apply_triggers(runtime.engine.nodes['start'], second.session, {'performance': ACTION}, _claims(), '接上。', known=False)
    original_commit = runtime.store.commit

    async def fail(*args, **kwargs):
        raise OSError('injected storage failure')

    monkeypatch.setattr(runtime.store, 'commit', fail)
    with pytest.raises(OSError):
        await runtime.commit_turn(outcome, performance)
    monkeypatch.setattr(runtime.store, 'commit', original_commit)
    assert await runtime.restore_session('failure') == second
    saved = await runtime.commit_turn(outcome, performance)
    assert ('start', 'log') in displayed_ids(saved.session)


def test_review_extension_is_optional_and_does_not_change_other_stories():
    raw = {'offer_present': False, 'valid': False, 'body_violations': [], 'unsafe_suggestion_indexes': []}
    assert _parse_transition_judge_output(json.dumps(raw)).fixed_narration_triggers == ()
    with pytest.raises(NumericV2EvaluatorOutputError):
        _parse_transition_judge_output(json.dumps(raw), fixed_narration_review=True)
    raw['fixed_narration_triggers'] = list(_claims())
    result = _parse_transition_judge_output(json.dumps(raw), fixed_narration_review=True)
    assert result.fixed_narration_triggers == _claims()
    with pytest.raises(NumericV2EvaluatorOutputError):
        _parse_transition_judge_output(json.dumps(raw))
