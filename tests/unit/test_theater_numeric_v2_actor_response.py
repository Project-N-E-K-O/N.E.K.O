"""Give the transition Actor the complete source-interaction direction rather than making it infer reactions from chapter titles."""

from dataclasses import replace
import json
import pytest

from services.theater.numeric_v2_actor import _turn_messages
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


def _invitation_text(data):
    invitation = data['transition']['pending_invitation']
    if 'content' in invitation:
        return '\n'.join(block['text'] for block in invitation['content'])
    assert invitation['content_reference'] == 'recent_context中同revision的已公开原文'
    return next(row['committed_text'] for row in data['recent_context'] if row['revision'] == invitation['revision'])


@pytest.mark.parametrize('message', [
    '（深吸一口气，闭上眼）……那就动手吧。别犹豫了。',
    '（握紧扶手，慢慢向前挪动一步）你先找个安全处。',
])
def test_actor_rejects_a_whole_copied_player_action_and_short_speech(message):
    from services.theater.numeric_v2_actor import _repeats_player_dialogue

    assert _repeats_player_dialogue({'performance': message}, message)


@pytest.mark.parametrize('message,response', [
    ('（点头）好，我们一起过去。', '（点头）好，我们一起过去。'),
    ('（沿着狭窄通道贴墙蹲下，放缓呼吸）', '（沿着狭窄通道贴墙蹲下，放缓呼吸）'),
    ('（深吸一口气，闭上眼）……那就动手吧。别犹豫了。', '（深吸一口气，闭上眼）收到，开始执行。'),
])
def test_player_copy_guard_preserves_shared_actions_and_brief_agreement(message, response):
    from services.theater.numeric_v2_actor import _repeats_player_dialogue

    assert not _repeats_player_dialogue({'performance': response}, message)


@pytest.mark.asyncio
async def test_whole_player_copy_uses_the_existing_repetition_rejection(monkeypatch):
    from services.theater.numeric_v2_actor import NumericV2Actor, NumericV2ActorOutputError

    engine = _engine()
    session = engine.create_session(session_id='copied-player', catgirl_binding=_binding(), opening_performance=_opening())
    message = '（深吸一口气，闭上眼）……那就动手吧。别犹豫了。'
    outcome = engine.resolve_turn(session, TurnRequestV2('copy', 0, message), ())

    async def invoke(*args, **kwargs):
        return {'performance': message, 'suggested_inputs': [], 'transition_offered': False}

    monkeypatch.setattr(NumericV2Actor, '_invoke', invoke)
    with pytest.raises(NumericV2ActorOutputError, match='repeated_output') as caught:
        await NumericV2Actor(object()).generate_turn(engine=engine, session=session, outcome=outcome,
            player_input=message, character_profile='温和。')
    assert caught.value.repetition_guard == 'player_input'


@pytest.mark.parametrize('offer,old_flag', [('', True), ('明天下午三点一起整理旧箱，好吗？', False)])
def test_ordinary_invitation_is_published_before_its_flag_is_derived(offer, old_flag):
    from services.theater.numeric_v2_actor_output import _parse_output

    parsed = _parse_output(json.dumps({'performance': '今晚的登记已经完成。',
        'transition_offer': offer, 'transition_offered': old_flag,
        'suggested_inputs': ['好，按这个安排。', '我先考虑一下。']}))
    assert parsed['performance'] == '今晚的登记已经完成。' + ('\n' + offer if offer else '')
    assert parsed['transition_offered'] is bool(offer)
    assert parsed['_transition_offer_provided'] is True


def test_ordinary_invitation_normalizes_once_and_does_not_duplicate_visible_text():
    from services.theater.numeric_v2_actor_output import _parse_output

    parsed = _parse_output(json.dumps({'performance': '登记好了。\n明天下午三点见。',
        'transition_offer': '**明天下午三点见。**', 'suggested_inputs': []}))
    assert parsed['performance'] == '登记好了。\n明天下午三点见。'
    assert parsed['transition_offered'] is True


@pytest.mark.parametrize('offer', [None, True, [], {}])
def test_ordinary_invitation_requires_text(offer):
    from services.theater.numeric_v2_actor_output import _parse_output, NumericV2ActorOutputError

    with pytest.raises(NumericV2ActorOutputError, match='transition_offer_invalid'):
        _parse_output(json.dumps({'performance': '登记好了。', 'transition_offer': offer}))


def test_published_invitation_uses_the_same_speech_contract_as_the_body():
    from services.theater.numeric_v2_actor_output import _parse_output, NumericV2ActorOutputError

    with pytest.raises(NumericV2ActorOutputError, match='dialogue_forbidden'):
        _parse_output(json.dumps({'performance': '（点头）', 'transition_offer': '一起过去吗？'}),
            dialogue_policy='forbidden')
    silent = _parse_output(json.dumps({'performance': '（点头）', 'transition_offer': ''}),
        dialogue_policy='forbidden')
    assert silent['performance'] == '（点头）' and not silent['transition_offered']


def test_role_keeps_author_extras_without_repeating_structured_entry_states():
    from services.theater.numeric_v2_actor import _author_entry_prompt_text

    state = {'catgirl_state': '女主站在窗边。', 'player_state': '男主坐在桌旁。',
             'environment_state': '环境为明亮的工作室。'}
    text = _author_entry_prompt_text(acting_context={
        'current_scene_state': ''.join(state.values()) + '信号灯只用于指路。', 'character_state': state})
    assert all(text.count(value) == 1 for value in state.values())
    assert '信号灯只用于指路。' in text


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['performance', 'scene_narration'])
async def test_actor_rejects_copying_current_player_dialogue(monkeypatch, field):
    from services.theater.numeric_v2_actor import NumericV2Actor, NumericV2ActorOutputError
    from services.theater.numeric_v2_runtime import NumericV2Engine
    from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id='player_echo', catgirl_binding=_binding(),
        opening_performance={'performance': '灯光指向站旁的小屋。', 'suggested_inputs': []})
    message = '（顺着灯光看去）信号小屋？那里会有什么？'
    outcome = engine.resolve_turn(session, TurnRequestV2('ask', 0, message), ())
    candidate = {'performance': '那里是旧车站的控制室。', 'suggested_inputs': [], 'transition_offered': False}
    candidate[field] = message

    async def invoke(self, messages, **kwargs):
        return dict(candidate)

    monkeypatch.setattr(NumericV2Actor, '_invoke', invoke)
    with pytest.raises(NumericV2ActorOutputError, match='repeated_output') as caught:
        await NumericV2Actor(object()).generate_turn(engine=engine, session=session, outcome=outcome,
            player_input=message, character_profile='温和。', defer_dialogue_repetition=True)
    assert caught.value.repetition_guard == 'player_input'


@pytest.mark.asyncio
@pytest.mark.parametrize('message,reply', [
    ('好。', '好。'),
    ('（拿起信号灯，走向门口）', '（拿起信号灯，走向门口）'),
    ('信号小屋？那里会有什么？', '你问那里有什么？那里是旧车站的控制室。'),
])
async def test_player_echo_guard_allows_short_confirmations_actions_and_answers(monkeypatch, message, reply):
    from services.theater.numeric_v2_actor import NumericV2Actor
    from services.theater.numeric_v2_runtime import NumericV2Engine
    from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id='player_echo_allowed', catgirl_binding=_binding(),
        opening_performance={'performance': '灯光指向站旁的小屋。', 'suggested_inputs': []})
    outcome = engine.resolve_turn(session, TurnRequestV2('reply', 0, message), ())

    async def invoke(self, messages, **kwargs):
        return {'performance': reply, 'suggested_inputs': [], 'transition_offered': False}

    monkeypatch.setattr(NumericV2Actor, '_invoke', invoke)
    result = await NumericV2Actor(object()).generate_turn(engine=engine, session=session, outcome=outcome,
        player_input=message, character_profile='温和。')
    assert result['performance'] == reply


@pytest.mark.asyncio
@pytest.mark.parametrize('repeated_input,metric_change', [(True, False), (False, False), (True, True)])
async def test_stable_confirmation_is_checked_before_older_history_guard(tmp_path, monkeypatch, repeated_input, metric_change):
    from services.theater.numeric_v2_actor import NumericV2Actor, NumericV2ActorOutputError
    from services.theater.numeric_v2_runtime import NumericV2Engine, MetricChangeV2
    from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    message = '（保持沉睡，呼吸均匀）'
    reply = '（维持原来的姿态，静静注视着床边）监测中。'
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='stable_older', catgirl_binding=_binding(), opening_performance=_opening())
    for index, (text, body) in enumerate([(message, reply), ('（继续休息）', '我留在这里。')]):
        previous = runtime.prepare_turn(current, TurnRequestV2(f'previous_{index}', current.session.revision, text), ())
        current = await runtime.commit_turn(previous, {'performance': body, 'suggested_inputs': [], 'transition_offered': False})
    session = current.session
    current_input = message if repeated_input else '（睁开眼）现在几点了？'
    changes = (MetricChangeV2('trust', 1, '新的进展', current_input),) if metric_change else ()
    outcome = engine.resolve_turn(session, TurnRequestV2('stable', 2, current_input), changes)

    async def invoke(self, messages, **kwargs):
        return {'performance': reply, 'suggested_inputs': [], 'transition_offered': False}

    monkeypatch.setattr(NumericV2Actor, '_invoke', invoke)
    call = NumericV2Actor(object()).generate_turn(engine=engine, session=session, outcome=outcome,
        player_input=current_input, character_profile='温和。')
    if repeated_input and not metric_change:
        assert (await call)['performance'] == reply
    else:
        with pytest.raises(NumericV2ActorOutputError, match='repeated_output'):
            await call


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['dialogue', 'long', 'action', 'narration', 'offer', 'fact'])
@pytest.mark.parametrize('defer', [False, True])
async def test_only_plain_dialogue_repetition_can_defer_to_the_next_turn(monkeypatch, kind, defer):
    from services.theater.numeric_v2_actor import NumericV2Actor, NumericV2ActorOutputError
    from services.theater.numeric_v2_runtime import NumericV2Engine
    from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story

    text = '我会在这里等你，你可以慢慢考虑自己的决定。'
    if kind == 'long':
        text *= 8
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id='soft_repeat', catgirl_binding=_binding(),
        opening_performance={'performance': text, 'suggested_inputs': []})
    request = TurnRequestV2('reply', 0, '我还想再想想。')
    outcome = engine.resolve_turn(session, request, ())
    candidate = {'performance': text, 'suggested_inputs': ['谢谢你。'], 'transition_offered': False}
    if kind == 'action':
        candidate['performance'] = '（拿起钥匙）' + text
    elif kind == 'narration':
        candidate['scene_narration'] = '窗边的灯忽然熄灭了。'
    elif kind == 'offer':
        candidate['transition_offered'] = True
    elif kind == 'fact':
        candidate['fact_candidates'] = [{'key': 'scene:start:ready', 'value': True, 'evidence_quote': text}]
    calls = []

    async def invoke(self, messages, **kwargs):
        calls.append(messages)
        return dict(candidate)

    monkeypatch.setattr(NumericV2Actor, '_invoke', invoke)
    call = NumericV2Actor(object()).generate_turn(engine=engine, session=session, outcome=outcome,
        player_input=request.message, character_profile='温和。', allow_suggestion_fill=False,
        defer_dialogue_repetition=defer)
    if defer and kind == 'dialogue':
        assert (await call)['performance'] == text
    else:
        with pytest.raises(NumericV2ActorOutputError, match='repeated_output'):
            await call
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_next_turn_rephrases_committed_dialogue_without_replaying_actions(tmp_path):
    from services.theater.numeric_v2_runtime import NumericV2Engine
    from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    text = '我会在这里等你，你可以慢慢考虑自己的决定。'
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='soft_repeat_hint', catgirl_binding=_binding(),
        opening_performance={'performance': '先看看这些花。', 'suggested_inputs': []})
    for index in range(2):
        prepared = runtime.prepare_turn(current, TurnRequestV2('old'+str(index), index, '我想再考虑一下。'), ())
        current = await runtime.commit_turn(prepared, {'performance': text, 'suggested_inputs': []})
    session = (await runtime.restore_session('soft_repeat_hint')).session
    records = session.performance_history
    outcome = engine.resolve_turn(session, TurnRequestV2('next', 2, '我决定了，先留在这里。'), ())
    messages = _turn_messages(engine, session, outcome, '我决定了，先留在这里。', '温和。', 'Lan', '哥哥')
    assert '上一轮有重复措辞' in messages[0].content
    assert '不重演动作、不撤销事实' in messages[0].content
    assert session.performance_history == records


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
    data = prompt_payload(messages[1].content)
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
    data = prompt_payload(messages[1].content)
    assert data['transition']['pending_invitation']['revision'] == stored.session.revision
    assert '改去门外的平台看看' in _invitation_text(data)
    assert '旧码头' not in _invitation_text(data)


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
    data = prompt_payload(_turn_messages(engine, stored.session, outcome, request.message, '克制。', '小岚', '哥哥', recent_ledger_events=stored.ledger_events)[1].content)
    assert 'content' not in data['transition']['pending_invitation']
    assert all(block['text'] in _invitation_text(data) for block in performance_content_blocks(original))
    assert '小岚抬眼' in _invitation_text(data)
    assert '哥哥抬眼' not in _invitation_text(data)


@pytest.mark.asyncio
async def test_explicit_acceptance_control_binds_invitation_without_inventing_player_words(tmp_path):
    from tests.unit.test_theater_numeric_v2_pending_offer import _commit

    runtime = NumericV2Runtime(_engine(ordinary=True), tmp_path)
    stored = await runtime.start_session(session_id='authorization_control', catgirl_binding=_binding(),
                                         opening_performance=_opening())
    invitation = '我们按刚才说定的分工去走廊，好吗？'
    stored = await _commit(runtime, stored, invitation, offer=True)
    # 复用已经合法的来源/目标结果；控制输入只改变解释方式，不能合成玩家台词。
    outcome = runtime.prepare_turn(stored, TurnRequestV2('accept_control', stored.session.revision, '好。'),
                                   (), transition_intent='accept')
    messages = _turn_messages(runtime.engine, stored.session, outcome, '', '克制。', '小岚', '你',
                             input_source='accept_invitation', recent_ledger_events=stored.ledger_events)
    data = prompt_payload(messages[1].content)
    assert data['player_input'] == ''
    assert invitation in _invitation_text(data)
    assert '程序已确认玩家接受刚展示的邀请' in messages[0].content
    assert '不是玩家台词' in messages[0].content
    assert '不新增玩家动作、分工或重要操作的授权' in messages[0].content
    assert 'player_input 是玩家刚结束的发言' not in messages[1].content


def test_formal_authorization_does_not_invent_a_pending_invitation():
    engine = _engine(ordinary=True)
    session = engine.create_session(session_id='authorization_missing', catgirl_binding=_binding(),
                                    opening_performance=_opening())
    session = replace(session, transition_offered=True)
    outcome = engine.resolve_turn(session, TurnRequestV2('accept', 0, '好。'), (), transition_intent='accept')
    data = prompt_payload(_turn_messages(engine, session, outcome, '好。', '克制。', '小岚', '你')[1].content)
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
    data = prompt_payload(messages[1].content)
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
    data = prompt_payload(messages[1].content)
    assert data['transition']['source_scene']['story_direction'] == direction
    assert direction not in json.dumps(data['recent_context'], ensure_ascii=False)
    assert data['transition']['target_scene']['story_direction'] == ending_direction
    # Unlabelled legacy prose remains expressive; only explicitly labelled traits
    # acquire the wider, bounded personality role.
    assert data['acting_context']['core_persona'].endswith('：克制，重视具体事实。')
    assert '语言氛围参考（只影响措辞' in data['acting_context']['core_persona']


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
    data = prompt_payload(messages[1].content)
    assert future not in '\n'.join(message.content for message in messages)
    assert 'story_direction' not in data['transition']['target_scene']
    assert data['transition']['target_scene']['opening_situation'] == target['opening_scene']
    assert '开场时星图尚未修复。' in messages[0].content
    assert data['transition']['target_scene']['author_entry_plan']['catgirl_state'] == '猫娘站在阅览室入口，双手空着。'
    assert 'target_character_state' not in data['acting_context']
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


def test_ordinary_actor_puts_actual_continuity_before_planned_direction():
    engine = _engine()
    session = engine.create_session(session_id='actual_start', catgirl_binding=_binding(),
                                    opening_performance=_opening())
    message = '我已按眼前安排完成操作，现在等对方回应。'
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, message), ())
    messages = _turn_messages(engine, session, outcome, message, '温和。', '小岚', '你')
    prefix, body = messages[1].content.split('\n', 1)
    assert '最新已提交原文和本轮独立预审证据确定当前起点' in prefix
    assert '承接 player_input 的实际原话，再交付尚缺的回应' in prefix
    assert '作者禁令不证明当前未发生，也不撤销实际历史' in prefix
    data = prompt_payload(body)
    assert list(data) == ["role", "current_scene", "pacing", "next_scene", "story_so_far", "player_input"]
    assert data['player_input'] == message


def _repair_material():
    return {
        'draft': {'performance': '尚未交付的旧答复。', 'scene_narration': '尚未提交的外部回应。',
                  'suggested_inputs': ['尚未选择的旧推荐。'], 'transition_offered': False},
        'issues': [{'field': 'performance', 'quote': '旧答复', 'violations': ['actor_boundary'],
                    'code': 'actor_issue'}],
        'body_violations': ['actor_boundary'],
    }


def test_formal_repair_uses_spare_budget_without_displacing_transition_contract(monkeypatch):
    from copy import deepcopy
    from services.theater import numeric_v2_actor as actor

    engine = _engine()
    session = engine.create_session(session_id='formal-repair-budget', catgirl_binding=_binding(),
                                    opening_performance=_opening())
    outcome = engine.resolve_turn(session, TurnRequestV2('end', 0, '谢谢。'), (),
        scene_complete=True, natural_ending_ready=True)
    material = {'draft': {'source_performance': '旧稿的来源回应。',
                         'bridge_scene_narration': '旧稿错误地把目标开场放在桥段。',
                         'target_scene_narration': '旧稿的目标开场。',
                         'target_performance': '旧稿的结局回应。', 'suggested_inputs': []},
                'issues': [{'field': 'bridge_scene_narration', 'quote': '目标开场'}]}
    original = deepcopy((session, outcome, material))
    diagnostics = []
    monkeypatch.setattr(actor, '_log_prompt_diagnostics', lambda _session, values: diagnostics.append(dict(values)))

    def messages(context=None):
        return _turn_messages(engine, session, outcome, '谢谢。', '温和。', '小岚', '你',
            retry_hint='只修正桥段提前包含的目标开场，其他段也须重审。', draft_repair_context=context)

    base = messages()
    admitted = messages(material)
    data = prompt_payload(admitted[1].content)
    assert data.pop('draft_repair') == material
    assert data == prompt_payload(base[1].content)
    assert admitted[0].content == base[0].content
    assert '旧稿的来源回应' not in admitted[0].content
    assert list(prompt_payload(admitted[1].content))[-1] == 'player_input'
    assert diagnostics[-1]['draft_repair'] == 'admitted'
    admitted_tokens = actor._messages_tokens(admitted)
    budget = actor.numeric_v2_actor_budget(session.actor_budget_profile)
    monkeypatch.setattr(actor, 'numeric_v2_actor_budget', lambda _: {**budget, 'input_max_tokens': admitted_tokens})
    assert messages(material) == admitted
    monkeypatch.setattr(actor, 'numeric_v2_actor_budget', lambda _: {**budget, 'input_max_tokens': admitted_tokens - 1})
    omitted = messages(material)
    assert [m.content for m in omitted] == [m.content for m in messages()]
    assert diagnostics[-2]['draft_repair'] == 'omitted_budget'
    assert (session, outcome, material) == original


@pytest.mark.asyncio
async def test_draft_repair_uses_only_remaining_budget_and_never_replaces_history(monkeypatch, tmp_path):
    from copy import deepcopy
    from services.theater import numeric_v2_actor as actor
    from tests.unit.test_theater_numeric_v2_pending_offer import _commit

    engine = _engine()
    runtime = NumericV2Runtime(engine, tmp_path)
    stored = await runtime.start_session(session_id='repair_capacity', catgirl_binding=_binding(),
                                         opening_performance=_opening())
    stored = await _commit(runtime, stored, '管事已经把原件交还给你，后续手续仍待答复。')
    stored = await _commit(runtime, stored, '我记得原件还在你手里，先听眼前的答复。')
    session = stored.session
    outcome = engine.resolve_turn(session, TurnRequestV2('one', session.revision, '请按实际情况接着说。'), ())
    material = _repair_material()
    original = deepcopy((session, outcome, material))
    diagnostics = []
    monkeypatch.setattr(actor, '_log_prompt_diagnostics', lambda _session, values: diagnostics.append(dict(values)))
    def messages(context=None):
        return _turn_messages(engine, session, outcome, '请按实际情况接着说。', '温和。', '小岚', '你',
                              retry_hint='修正未提交答复，全部内容仍须复核。', draft_repair_context=context)

    base = messages()
    admitted = messages(material)
    data = prompt_payload(admitted[1].content)
    base_data = prompt_payload(base[1].content)
    assert '管事已经把原件交还给你' in base_data['story_so_far']
    assert '原件还在你手里' in base_data['story_so_far']
    assert data.pop('draft_repair') == material
    assert data == base_data and list(data)[-1] == 'player_input'
    assert '未提交编辑材料' in admitted[1].content and '不是历史或批准' in admitted[1].content
    assert '全部重审' in admitted[1].content
    assert diagnostics[-1]['draft_repair'] == 'admitted'
    admitted_tokens = actor._messages_tokens(admitted)
    budget = actor.numeric_v2_actor_budget(session.actor_budget_profile)
    monkeypatch.setattr(actor, 'numeric_v2_actor_budget', lambda _: {**budget, 'input_max_tokens': admitted_tokens})
    assert messages(material) == admitted
    assert diagnostics[-1]['final_tokens'] == admitted_tokens
    # 只差一个 token 时整体撤回编辑附件；不能挤掉一轮原文以装下旧稿。
    monkeypatch.setattr(actor, 'numeric_v2_actor_budget', lambda _: {**budget, 'input_max_tokens': admitted_tokens - 1})
    omitted = messages(material)
    assert [item.content for item in omitted] == [item.content for item in messages()]
    assert diagnostics[-2]['draft_repair'] == 'omitted_budget'
    assert (session, outcome, material) == original


@pytest.mark.parametrize('mode', ['initial', 'reinvite', 'transition'])
def test_draft_repair_cannot_enter_other_actor_phases(mode):
    engine = _engine()
    session = engine.create_session(session_id='repair_scope', catgirl_binding=_binding(),
                                    opening_performance=_opening())
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '好。'), (),
        scene_complete=mode == 'transition', natural_ending_ready=mode == 'transition')
    messages = _turn_messages(engine, session, outcome, '好。', '温和。', '小岚', '你',
        retry_hint='' if mode == 'initial' else '修正未提交答复。',
        input_source='reinvite' if mode == 'reinvite' else 'freeform',
        draft_repair_context=_repair_material())
    assert '尚未交付的旧答复' not in ''.join(item.content for item in messages)
    assert 'draft_repair' not in prompt_payload(messages[1].content)


@pytest.mark.asyncio
async def test_generate_turn_forwards_draft_repair_without_committing_it(monkeypatch):
    from copy import deepcopy
    from services.theater import numeric_v2_actor as actor

    engine = _engine()
    session = engine.create_session(session_id='repair_forward', catgirl_binding=_binding(),
                                    opening_performance=_opening())
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '请继续。'), ())
    original = deepcopy((session, outcome))
    generator = actor.NumericV2Actor(object())
    captured = []
    async def invoke(messages, **kwargs):
        captured.extend(messages)
        return {'performance': '我明白你的安排了。', 'suggested_inputs': ['那就按这个安排来。'],
                'transition_offered': False}
    monkeypatch.setattr(generator, '_invoke', invoke)
    result = await generator.generate_turn(engine=engine, session=session, outcome=outcome,
        player_input='请继续。', character_profile='温和。', allow_suggestion_fill=False,
        retry_hint='按编辑材料修正，全部重审。', draft_repair_context=_repair_material())
    assert prompt_payload(captured[1].content)['draft_repair'] == _repair_material()
    assert 'draft_repair' not in result and (session, outcome) == original
