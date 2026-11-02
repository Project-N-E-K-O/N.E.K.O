"""Give the transition Actor the complete source-interaction direction rather than making it infer reactions from chapter titles."""

from dataclasses import replace
import json
import pytest

from services.theater.numeric_v2_actor import _turn_messages
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


@pytest.mark.asyncio
@pytest.mark.parametrize('invitation', ['我们去长街找旧信，好吗？', '我们去轨道站核对星图，好吗？'])
@pytest.mark.parametrize('withdrawn', [False, True])
async def test_transition_binds_original_invitation_beyond_recent_window(tmp_path, invitation, withdrawn):
    from services.theater.numeric_v2_performance import performance_content_blocks
    from tests.unit.test_theater_numeric_v2_pending_offer import _commit

    runtime = NumericV2Runtime(_engine(ordinary=True), tmp_path)
    stored = await runtime.start_session(session_id='authorization_origin', catgirl_binding=_binding(),
                                         opening_performance=_opening())
    stored = await _commit(runtime, stored, invitation, offer=True)
    original = stored.session.performance_history[-1]
    if withdrawn:
        stored = await _commit(runtime, stored, '好，先不去。', intent='reject')
    for _ in range(14):
        stored = await _commit(runtime, stored, '门口还有一张椅子，你也可以坐坐。')
    request = TurnRequestV2('accept_original', stored.session.revision, '好，按原来的安排出发。')
    outcome = runtime.prepare_turn(stored, request, (), transition_intent='accept')
    messages = _turn_messages(runtime.engine, stored.session, outcome, request.message,
                             '克制。', '小岚', '你', recent_ledger_events=stored.ledger_events)
    data = json.loads(messages[1].content.split('\n', 1)[1])
    assert data['transition']['transition_intent'] == 'accept'
    assert data['transition']['pending_invitation'] == {
        'revision': original['revision'], 'content': performance_content_blocks(original),
    }
    assert invitation not in json.dumps(data['recent_context'], ensure_ascii=False)
    assert data['player_input'] == request.message


@pytest.mark.asyncio
async def test_transition_uses_new_offer_after_rejection(tmp_path):
    from tests.unit.test_theater_numeric_v2_pending_offer import _commit

    runtime = NumericV2Runtime(_engine(ordinary=True), tmp_path)
    stored = await runtime.start_session(session_id='authorization_refresh', catgirl_binding=_binding(),
                                         opening_performance=_opening())
    stored = await _commit(runtime, stored, '我们先去旧码头，好吗？', offer=True)
    stored = await _commit(runtime, stored, '好，先不去。', intent='reject')
    stored = await _commit(runtime, stored, '改去门外的平台看看，好吗？', offer=True)
    request = TurnRequestV2('accept_new', stored.session.revision, '好，就去平台。')
    outcome = runtime.prepare_turn(stored, request, (), transition_intent='accept')
    messages = _turn_messages(runtime.engine, stored.session, outcome, request.message,
                             '克制。', '小岚', '你', recent_ledger_events=stored.ledger_events)
    data = json.loads(messages[1].content.split('\n', 1)[1])
    assert data['transition']['pending_invitation']['revision'] == stored.session.revision
    assert '改去门外的平台看看' in json.dumps(data['transition']['pending_invitation'], ensure_ascii=False)
    assert '旧码头' not in json.dumps(data['transition']['pending_invitation'], ensure_ascii=False)


@pytest.mark.asyncio
async def test_transition_preserves_runtime_names_in_committed_invitation(tmp_path):
    from services.theater.numeric_v2_performance import performance_content_blocks
    from tests.unit.test_theater_numeric_v2_pending_offer import _commit

    engine = _engine(ordinary=True)
    engine.story['intro'].update(player_name='小岚', catgirl_name='叶晴')
    runtime = NumericV2Runtime(engine, tmp_path)
    stored = await runtime.start_session(session_id='authorization_names',
        catgirl_binding={**_binding(), 'catgirl_name': '小岚'}, opening_performance=_opening())
    stored = await _commit(runtime, stored, '（小岚抬眼）我们去走廊看看，好吗？', offer=True)
    original = stored.session.performance_history[-1]
    request = TurnRequestV2('accept_names', stored.session.revision, '好，就去走廊。')
    outcome = runtime.prepare_turn(stored, request, (), transition_intent='accept')
    data = json.loads(_turn_messages(engine, stored.session, outcome, request.message,
        '克制。', '小岚', '哥哥', recent_ledger_events=stored.ledger_events)[1].content.split('\n', 1)[1])
    assert data['transition']['pending_invitation']['content'] == performance_content_blocks(original)
    assert '小岚抬眼' in json.dumps(data['transition']['pending_invitation'], ensure_ascii=False)
    assert '哥哥抬眼' not in json.dumps(data['transition']['pending_invitation'], ensure_ascii=False)


def test_formal_authorization_does_not_invent_a_pending_invitation():
    engine = _engine(ordinary=True)
    session = engine.create_session(session_id='authorization_missing', catgirl_binding=_binding(),
                                    opening_performance=_opening())
    session = replace(session, transition_offered=True)
    outcome = engine.resolve_turn(session, TurnRequestV2('accept', 0, '好。'), (), transition_intent='accept')
    data = json.loads(_turn_messages(engine, session, outcome, '好。', '克制。', '小岚', '你')[1].content.split('\n', 1)[1])
    assert data['transition']['pending_invitation'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['initiate', 'natural_ending'])
async def test_other_transition_modes_do_not_reuse_an_old_invitation(tmp_path, mode):
    from tests.unit.test_theater_numeric_v2_pending_offer import _commit

    runtime = NumericV2Runtime(_engine(ordinary=mode == 'initiate'), tmp_path)
    stored = await runtime.start_session(session_id='authorization_mode', catgirl_binding=_binding(),
                                         opening_performance=_opening())
    stored = await _commit(runtime, stored, '我们去走廊看看，好吗？', offer=True)
    stored = await _commit(runtime, stored, '好，先不去。', intent='reject')
    request = TurnRequestV2('other_mode', stored.session.revision,
                           '现在去走廊。' if mode == 'initiate' else '最后一条船也安全了。')
    outcome = runtime.prepare_turn(stored, request, (),
        transition_intent='initiate' if mode == 'initiate' else 'unclear',
        scene_complete=mode == 'natural_ending', natural_ending_ready=mode == 'natural_ending')
    messages = _turn_messages(runtime.engine, stored.session, outcome, request.message,
                             '克制。', '小岚', '你', recent_ledger_events=stored.ledger_events)
    data = json.loads(messages[1].content.split('\n', 1)[1])
    assert data['transition']['transition_intent'] == ('initiate' if mode == 'initiate' else 'unclear')
    assert 'pending_invitation' not in data['transition']


def test_transition_keeps_source_direction_separate_from_history_and_target():
    # Use concrete narrative distinct from the chapter title to catch compact packing that omits the source direction.
    engine = _engine()
    direction = '回应玩家逐船核对救援名单的帮助，人员安全后收束。'
    engine.nodes['start']['story_beat'].update(narrative_summary=direction)
    session = engine.create_session(session_id='response', catgirl_binding=_binding(), opening_performance=_opening())
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '最后一条船也安全了。'), (),
                                  scene_complete=True, natural_ending_ready=True)
    ending_direction = '回应最后一艘船获救的消息，感谢玩家的帮助，完整收束本次救援。'
    engine.nodes[outcome.session.current_node_id]['story_beat']['narrative_summary'] = ending_direction
    messages = _turn_messages(engine, session, outcome, '最后一条船也安全了。',
                             '克制，重视具体事实。', '小岚', '你')
    data = json.loads(messages[1].content.split('\n', 1)[1])
    assert data['transition']['source_scene']['story_direction'] == direction
    assert direction not in json.dumps(data['recent_context'], ensure_ascii=False)
    assert data['transition']['target_scene']['story_direction'] == ending_direction
    assert data['acting_context']['core_persona'] == '克制，重视具体事实。'


@pytest.mark.asyncio
async def test_interactive_target_direction_waits_until_after_its_opening(tmp_path):
    # Ordinary transitions establish only the entry; subsequent material for the whole act should appear only in ordinary turns after the opening is committed.
    case = initiation_case()
    engine = case['engine']
    target = engine.nodes['ending_leave']['story_beat']
    future = '之后共同修复破损的星图，完成修复后发现背面的航行记录。'
    target['summary'] = future
    target['opening_only_boundaries'] = ['开场时星图尚未修复。']
    target['character_state'] = {'catgirl_state': '猫娘站在阅览室入口，双手空着。'}
    runtime = NumericV2Runtime(engine, tmp_path)
    stored = await runtime.start_session(session_id='entry_direction', catgirl_binding=_binding(),
                                         opening_performance=case['session'].opening_performance)
    request = TurnRequestV2('enter', 0, case['message'])
    outcome = runtime.prepare_turn(stored, request, (), transition_intent='initiate')
    messages = _turn_messages(engine, stored.session, outcome, request.message, '克制。', '小岚', '你')
    data = json.loads(messages[1].content.split('\n', 1)[1])
    assert future not in '\n'.join(message.content for message in messages)
    assert 'story_direction' not in data['transition']['target_scene']
    assert data['transition']['target_scene']['opening_situation'] == target['opening_scene']
    assert '开场时星图尚未修复。' in messages[0].content
    assert '猫娘站在阅览室入口，双手空着。' in json.dumps(data['acting_context'], ensure_ascii=False)
    assert data['transition']['bridge_scene_narration'] == '两人沿左侧走廊来到阅览室。'
    assert data['transition']['must_preserve'] == ['后续操作尚未开始。']

    performance = engine.finalize_transition_performance(outcome, {
        'source_performance': '（点头）跟我来。',
        'bridge_scene_narration': '两人沿左侧走廊来到阅览室。',
        'target_scene_narration': '两人在阅览室入口，后续操作尚未开始。',
        'target_performance': '（看向书架）先看看目录吗？',
        'suggested_inputs': [],
    }, target_opening=target['opening_scene'])
    committed = await runtime.commit_turn(outcome, performance)
    next_request = TurnRequestV2('look', 1, '这里有哪些可以查看的资料？')
    next_outcome = runtime.prepare_turn(committed, next_request, ())
    next_messages = _turn_messages(engine, committed.session, next_outcome, next_request.message,
                                   '克制。', '小岚', '你')
    assert future in '\n'.join(message.content for message in next_messages)
    assert '开场时星图尚未修复。' not in next_messages[0].content


def test_extra_suggestions_are_trimmed_instead_of_discarded():
    """Keep the first three when there are more than three: deterministic trimming replaces an extra recommendation-model call."""  # noqa: DOCSTRING_CJK

    from services.theater.numeric_v2_actor_output import _parse_actor_suggestions

    counts: dict[str, int] = {}
    payload = [
        '（走向窗边）外面下雨了吗',
        '（放下杯子）我们等一下再走',
        '（看向她）你想聊点什么',
        '（起身）我该走了',
    ]
    parsed = _parse_actor_suggestions(payload, diagnostics=counts)
    assert parsed == payload[:3]
    assert counts.get('too_many_items') == 1
