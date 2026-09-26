"""Give the transition Actor the complete source-interaction direction rather than making it infer reactions from chapter titles."""

import json
import pytest

from services.theater.numeric_v2_actor import _turn_messages
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


def test_transition_keeps_source_direction_separate_from_history_and_target():
    # 使用与章节标题不同的具体叙事，捕获紧凑装箱遗漏来源方向的问题。
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
    # 普通转场只建立入口；整幕后续素材应在开场提交后的普通回合才可见。
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
    """超过三条时保留前三条：确定性裁剪替代一次补推荐模型调用。"""  # noqa: DOCSTRING_CJK

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
