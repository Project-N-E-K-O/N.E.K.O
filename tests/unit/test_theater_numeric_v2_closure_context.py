"""Regress complete closure scope and subsequent direction projections; reuse the fixtures for real-model positive and negative comparisons."""
from dataclasses import replace
import json
import pytest
from services.theater import numeric_v2_evaluator as ev
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_prompt_contract import _session


SCENES = (
    ('借阅柜台', '交还书本并核对归还登记', '书本已归还，归还登记已核对', '书本已归还，但归还登记尚未核对', '阅览室', '挑选下一本书'),
    ('乐器排练间', '更换断弦并完成调音', '断弦已更换，调音已完成', '断弦已更换，但还没有调音', '舞台入口', '登台演奏'),
    ('灯具修理间', '接好灯线并验证灯能稳定点亮', '灯线接好，试亮稳定', '灯线接好，但尚未验证能否点亮', '庭院门口', '布置今晚的活动'),
)


def closure_cases():
    """Vary only the submitted result within each genre and do not expose expected labels to the model."""
    rows = []
    for place, task, done, unfinished, target, future in SCENES:
        engine = NumericV2Engine.from_mapping(numeric_v2_story())
        node = engine.nodes['start']
        # 长方向将完成范围留在末尾，重现原180 Token字段截断丢失收束说明。
        direction = (f'两人在{place}协作处理眼前事务。' +
            '她把桌面可用空间留给玩家，留意他的实际操作，按需要解释自己负责的部分；环境保持平静，双方可以交流眼前物件的用途。' * 4 +
            f'本幕只要求{task}，女主对此作出回应即可收束；随后可邀请去{target}，{future}属于下一阶段，不必现在完成。')
        node['story_beat'] = {'opening_scene': f'两人在{place}，正要{task}。', 'summary': direction}
        node['route_gates'] = [node['route_gates'][1]]
        node['route_gates'][0]['transition_contract']['reason'] = f'眼前协作结束后邀请去{target}，下一阶段再{future}。'
        engine.nodes['ending_leave'].update(type='scene', terminal=False, chapter=target,
            story_beat={'opening_scene': f'两人到达{target}，尚未{future}。'})
        for complete in (False, True):
            result = done if complete else unfinished
            record = {'revision': 1, 'from_node_id': 'start', 'to_node_id': 'start',
                'input_text': result + '，你看呢？', 'performance': '我确认了，' + result + '。谢谢你和我配合。'}
            session = replace(_session(engine), revision=1, node_turn_count=1, performance_history=(record,))
            rows.append({'name': place + ('_complete' if complete else '_incomplete'), 'engine': engine,
                'session': session, 'message': '刚才这些进展，你怎么看？', 'expected_complete': complete})
    return rows


@pytest.mark.parametrize('case', closure_cases(), ids=lambda c: c['name'])
def test_evaluator_keeps_full_closure_scope_and_route_reason(case):
    """Keep the full causal direction within the 5200-token budget without truncating its tail or removing complete current evidence."""
    engine, session = case['engine'], case['session']
    messages = ev._build_messages(engine, session, case['message'])
    data = json.loads(messages[1].content.split('：', 1)[1])
    assert data['current_story_beat']['scene_direction'] == engine.nodes['start']['story_beat']['summary']
    assert ev.count_tokens(data['current_story_beat']['scene_direction']) > ev.NUMERIC_V2_EVALUATOR_FIELD_MAX_TOKENS
    assert data['transition_preview']['transition_direction'] == engine.nodes['start']['route_gates'][0]['transition_contract']['reason']
    assert data['scene_context'][-1] == ev._current_scene_context(session)[-1]
    assert sum(ev.count_tokens(m.content) for m in messages) <= ev.numeric_v2_actor_budget(case["session"].actor_budget_profile)["evaluator_input_max_tokens"]


@pytest.mark.parametrize('phase', ['opening', 'turn', 'transition_compact'])
def test_actor_identity_instruction_does_not_use_framework_name(phase):
    """Use the script's performer identity without exposing implementation identifiers in the first identity instruction."""
    from services.theater.numeric_v2_actor import _system_prompt
    prompt = _system_prompt(catgirl_name='测试猫娘', player_address='你', phase=phase)
    if phase == 'transition_compact':
        # 正式转场还需生成 NPC 旁白；猫娘身份仍由动态角色决定，不能把实现名称当作身份。
        assert '猫娘写入 performance，在场 NPC 的必要答复写入来源旁白' in prompt
        assert '当前猫娘由“测试猫娘”扮演。' in prompt
    else:
        assert prompt.startswith('你负责扮演当前猫娘')
    assert 'N.E.K.O Numeric v2 演绎 Actor' not in prompt
