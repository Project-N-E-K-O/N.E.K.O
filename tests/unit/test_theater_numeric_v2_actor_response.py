"""转场演员要获得来源互动的完整方向，不能只凭章节标题猜角色回应。"""

import json

from services.theater.numeric_v2_actor import _turn_messages
from services.theater.numeric_v2_runtime import TurnRequestV2
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


def test_transition_keeps_source_direction_separate_from_history_and_target():
    # 使用与章节标题不同的具体叙事，捕获紧凑装箱遗漏来源方向的问题。
    engine = _engine()
    direction = '回应玩家逐船核对救援名单的帮助，人员安全后收束。'
    engine.nodes['start']['story_beat'].update(narrative_summary=direction)
    session = engine.create_session(session_id='response', catgirl_binding=_binding(), opening_performance=_opening())
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '最后一条船也安全了。'), (),
                                  scene_complete=True, natural_ending_ready=True)
    messages = _turn_messages(engine, session, outcome, '最后一条船也安全了。',
                             '克制，重视具体事实。', '小岚', '你', deterministic_transition=True)
    data = json.loads(messages[1].content.split('\n', 1)[1])
    assert data['transition']['source_scene']['story_direction'] == direction
    assert direction not in json.dumps(data['recent_context'], ensure_ascii=False)
    assert data['transition']['target_scene']['story_direction'] != direction
    assert data['acting_context']['core_persona'] == '克制，重视具体事实。'
