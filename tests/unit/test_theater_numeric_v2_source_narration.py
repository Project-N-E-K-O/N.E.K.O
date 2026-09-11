"""Carry source NPC responses through parsing, review, commit and recovery while retaining compatibility with older three-segment records."""

from copy import deepcopy
import json

import pytest

from services.theater.numeric_v2_actor import _turn_messages
from services.theater.numeric_v2_actor_output import _parse_output, NumericV2ActorOutputError
from services.theater.numeric_v2_evaluator import _build_transition_judge_messages
from services.theater.numeric_v2_context import history_evidence
from services.theater.numeric_v2_performance import performance_content_blocks, performance_dialogue
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


NPC_REPLY = '值班员回答：“编号是丙七，登记已经完成。”'


@pytest.mark.parametrize('compact', [True, False])
@pytest.mark.parametrize('narration', [None, '', NPC_REPLY])
def test_source_narration_is_optional_and_preserved_by_actor_parser(compact, narration):
    candidate = _candidate() if compact else {
        'segments': [
            {'phase': 'source_response', 'performance': '（点头）记住了。'},
            {'phase': 'transition_bridge', 'scene_narration': '两人来到门边。'},
            {'phase': 'target_opening', 'performance': '（停步）这里安静些。'},
        ],
        'suggested_inputs': [],
    }
    source = candidate if compact else candidate['segments'][0]
    key = 'source_scene_narration' if compact else 'scene_narration'
    if narration is not None:
        source[key] = narration
    parsed = _parse_output(json.dumps(candidate, ensure_ascii=False), transition_required=True,
                           deterministic_transition=compact)
    result = parsed if compact else parsed['segments'][0]
    if narration:
        assert result[key] == narration
    else:
        assert key not in result


@pytest.mark.parametrize('compact', [True, False])
@pytest.mark.parametrize('invalid', [None, [], {}, 123])
def test_source_narration_rejects_non_text(compact, invalid):
    candidate = _candidate() if compact else {
        'segments': [
            {'phase': 'source_response', 'performance': '（点头）记住了。'},
            {'phase': 'transition_bridge', 'scene_narration': '两人来到门边。'},
            {'phase': 'target_opening', 'performance': '（停步）这里安静些。'},
        ],
    }
    source = candidate if compact else candidate['segments'][0]
    source['source_scene_narration' if compact else 'scene_narration'] = invalid
    with pytest.raises(NumericV2ActorOutputError, match='scene_narration_invalid'):
        _parse_output(json.dumps(candidate), transition_required=True, deterministic_transition=compact)


@pytest.mark.asyncio
@pytest.mark.parametrize('with_narration', [False, True])
async def test_source_reply_survives_commit_cold_restore_and_fork(tmp_path, with_narration):
    runtime = NumericV2Runtime(_engine(), tmp_path)
    current = await runtime.start_session(session_id='source_reply', catgirl_binding=_binding(),
                                          opening_performance=_opening())
    message = '（询问值班员）编号是多少？'
    outcome = runtime.prepare_turn(current, TurnRequestV2('one', 0, message), (),
                                   scene_complete=True, natural_ending_ready=True)
    candidate = _candidate()
    if with_narration:
        candidate['source_scene_narration'] = NPC_REPLY
    original = deepcopy(candidate)
    assembled = runtime.engine.finalize_transition_performance(outcome, candidate, target_opening='旧开场。')
    assert candidate == original
    assert [s['phase'] for s in assembled['segments']] == ['source_response', 'transition_bridge', 'target_opening']
    assert assembled['segments'][0].get('scene_narration') == (NPC_REPLY if with_narration else None)
    assert 'source_scene_narration' not in assembled

    reviewed = _build_transition_judge_messages(runtime.engine, current.session,
        actor_performance=assembled, player_input=message, transition_outcome=outcome)
    review_data = json.loads(reviewed[1].content.split('：', 1)[1])
    assert review_data['candidate_segments'][0].get('scene_narration') == (NPC_REPLY if with_narration else None)
    committed = await runtime.commit_turn(outcome, assembled)
    restored = await NumericV2Runtime(runtime.engine, tmp_path).restore_session('source_reply')
    assert restored == committed
    assert restored.session.revision == len(restored.ledger_events) == 1
    record = restored.session.performance_history[-1]
    assert record['segments'] == assembled['segments']
    if with_narration:
        blocks = performance_content_blocks(record)
        assert blocks[0] == {'type': 'narration', 'text': NPC_REPLY}
        assert blocks[1]['type'] == 'action'
        assert all(NPC_REPLY not in line['text'] for line in performance_dialogue(record))
        evidence = history_evidence(restored.session, '值班员说的编号是什么？')
        assert NPC_REPLY in json.dumps(evidence, ensure_ascii=False)
    fork = await runtime.fork_session_for_test('source_reply', session_id='fork', through_revision=1)
    assert fork.session.performance_history == restored.session.performance_history
    with pytest.raises(ValueError, match='session_already_ended'):
        runtime.prepare_turn(restored, TurnRequestV2('two', 1, '继续'), ())


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['', None, [], 123])
async def test_invalid_source_narration_cannot_be_committed_or_cold_restored(tmp_path, invalid):
    runtime = NumericV2Runtime(_engine(), tmp_path)
    current = await runtime.start_session(session_id='invalid_source', catgirl_binding=_binding(),
                                          opening_performance=_opening())
    outcome = runtime.prepare_turn(current, TurnRequestV2('one', 0, '谢谢。'), (),
                                   scene_complete=True, natural_ending_ready=True)
    assembled = runtime.engine.finalize_transition_performance(outcome, _candidate(), target_opening='旧开场。')
    damaged = deepcopy(assembled)
    damaged['segments'][0]['scene_narration'] = invalid
    with pytest.raises(ValueError, match='numeric_transition_performance_invalid'):
        await runtime.commit_turn(outcome, damaged)
    assert await runtime.restore_session('invalid_source') == current
    await runtime.commit_turn(outcome, assembled)
    path = runtime.store._path('invalid_source')
    payload = json.loads(path.read_text())
    payload['session']['performance_history'][-1]['segments'][0]['scene_narration'] = invalid
    path.write_text(json.dumps(payload, ensure_ascii=False))
    with pytest.raises(ValueError, match='numeric_transition_performance_invalid'):
        await NumericV2Runtime(runtime.engine, tmp_path).restore_session('invalid_source')


def test_actor_and_guard_agree_on_source_narration_scope():
    engine = _engine()
    session = engine.create_session(session_id='source_contract', catgirl_binding=_binding(),
                                    opening_performance=_opening())
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '编号是多少？'), (),
                                  scene_complete=True, natural_ending_ready=True)
    messages = _turn_messages(engine, session, outcome, '编号是多少？', '温和。', '小葵', '你',
                              deterministic_transition=True)
    assert 'source_scene_narration' in messages[0].content
    assert '明确拒绝或说明未知' in messages[0].content
    candidate = engine.finalize_transition_performance(outcome, _candidate(), target_opening='旧开场。')
    review = _build_transition_judge_messages(engine, session, actor_performance=candidate,
        player_input='编号是多少？', transition_outcome=outcome)
    assert 'source_response.scene_narration' in review[0].content
    assert 'performer 只标记 performance' in review[0].content
