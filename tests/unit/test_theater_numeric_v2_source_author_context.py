"""Source entrance labels retain author constraints and later committed history without changing either."""

from copy import deepcopy
import json

import pytest

from services.theater import numeric_v2_actor as actor
from services.theater.numeric_v2_cast import NumericV2CastProjection
from services.theater.numeric_v2_runtime import TurnRequestV2
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


@pytest.fixture(params=[
    ('向导在棚内查看马蹄上的冰。', '马蹄上的冰已经清除，木匣仍由玩家抱着。',
     '两人站在院门外，旧友尚未接过木匣。', '好，继续赶路。'),
    ('店主正在擦拭柜台，玩家抱着面包盒。', '收尾已经完成，店主双手空着，玩家仍抱着面包盒。',
     '店主站定在柜台后，等待玩家继续告别。', '好，把这次告别说完。'),
], ids=['wuxia', 'campus'])
def source_context(request):
    entry, completed, opening, player_input = request.param
    state = {'catgirl_state': entry, 'player_state': '玩家保管自己的物品。',
             'environment_state': '同一地点，任务仍待完成。',
             'continuity_from_previous': ['玩家已到场。'],
             'scene_boundaries': ['不得改变玩家持物归属。']}
    return {
        'story_context': {'background': '双方独立处理自己的事务。', 'player_identity': '执行当前任务的玩家',
                          'knowledge_scope': '只能使用已知事实。',
                          'runtime_scene_facts': {'revision': 9, 'facts': [{'key': 'source:work_done', 'value': True}]}},
        'player_address': '你',
        'recent_context': [{'revision': 9, 'player_input': '眼前这件事已经做完了。',
                            'segments': [{'phase': 'ordinary', 'performance': completed}]}],
        'acting_context': {
            'story_identity': '负责任但保持自主判断的同行者',
            'story_role_context': '彼此信任，不替对方作决定。',
            'current_scene_state': entry,
            'capability_state': {'craft': {'label': '熟练', 'description': '可以处理自己的工具。'}},
            'core_persona': '克制温和，自称人家。',
            'dialogue_policy': 'required',
            'character_state': state,
            'acting_contract': {'cognition_state': 'normal', 'memory_state': 'available',
                                'self_reference_mode': 'persona_allowed', 'persona_scope': 'full',
                                'forbidden_behaviors': ['不得虚构玩家承诺。']},
            'relationship_control': {'effective_stage': 'trusted', 'response_contract': '信任但不确认恋爱。'},
            'target_acting_contract': {'cognition_state': 'normal', 'memory_state': 'available',
                                       'assertable_self_facts': ['我保留自主判断。'],
                                       'forbidden_behaviors': ['不得提前宣告后续任务完成。']},
            'target_character_state': {'catgirl_state': opening, 'player_state': '玩家仍保管自己的物品。',
                                       'scene_boundaries': ['不得打开玩家持有的容器。']},
            'target_dialogue_policy': 'optional',
            'target_relationship_control': {'effective_stage': 'trusted', 'response_contract': '不新增永久关系。'},
        },
        'player_input': player_input,
        'transition': {
            'authorized': True,
            'source_scene': {'chapter_title': '来源幕', 'story_direction': '完成眼前任务后再回应玩家。',
                             'boundaries': ['不得重做已经完成的同一任务。']},
            'target_scene': {'chapter_title': '目标幕', 'opening_situation': opening,
                             'boundaries': ['后续任务尚未完成。']},
            'shared_boundaries': ['不得替玩家作决定。'],
            'reason': '玩家已经授权当前转场。', 'must_deliver': ['两人抵达目标入口。'],
            'bridge_required': True, 'bridge_scene_narration': '两人离开原处，抵达目标入口。',
            'must_preserve': [completed, '玩家仍持有自己的物品。'], 'tone': '自然克制',
        },
    }


def test_transition_names_source_author_state_without_changing_payload(source_context):
    expected = source_context
    transition = expected['transition']
    source, target = transition['source_scene'], transition['target_scene']
    inputs = dict(story_context=expected['story_context'], player_address=expected['player_address'],
        current_chapter_title=source['chapter_title'], source_story_direction=source['story_direction'],
        target_chapter_title=target['chapter_title'], shared_boundaries=transition['shared_boundaries'],
        source_boundaries=source['boundaries'], target_boundaries=target['boundaries'],
        runtime_target_opening=target['opening_situation'], transition_contract=transition,
        recent_context=expected['recent_context'], acting_context=expected['acting_context'],
        player_input=expected['player_input'])
    before = deepcopy(inputs)

    actual = actor._transition_prompt_data(**inputs)

    assert json.dumps(inputs, ensure_ascii=False) == json.dumps(before, ensure_ascii=False)
    assert list(actual['acting_context']) == [
        'story_identity', 'story_role_context', 'source_author_entry_situation', 'capability_state',
        'core_persona', 'dialogue_policy', 'source_author_entry_state', 'acting_contract',
        'relationship_control', 'target_acting_contract', 'target_character_state',
        'target_dialogue_policy', 'target_relationship_control',
    ]
    # After checking every output key, restore only its original label by position.
    # Comparing the whole ordered payload also catches lost or changed nested values and history.
    restored = deepcopy(actual)
    restored['acting_context'] = dict(zip(expected['acting_context'], actual['acting_context'].values()))
    assert json.dumps(restored, ensure_ascii=False) == json.dumps(expected, ensure_ascii=False)


def test_source_labels_stay_unchanged_outside_transition(source_context):
    engine = _engine()
    source = engine.nodes['start']
    original = source_context['acting_context']
    source['story_beat'].update(catgirl_situation=original['current_scene_state'],
                              character_state=deepcopy(original['character_state']),
                              acting_contract=deepcopy(original['acting_contract']))
    session = engine.create_session(session_id='source_labels', catgirl_binding=_binding(),
                                    opening_performance=_opening())
    cast = NumericV2CastProjection.from_story(engine.story, player_name='你', catgirl_name='小岚')
    generic = actor._acting_context(engine, cast, source, session.metrics, original['core_persona'])
    outcome = engine.resolve_turn(session, TurnRequestV2('ordinary', 0, '眼前这件事已经做完了。'), ())
    assert outcome.session.current_node_id == session.current_node_id
    opening = actor._opening_messages(engine, original['core_persona'], '小岚', '你')
    opening_data = json.loads(opening[1].content)
    for context in [generic, opening_data['acting_context']]:
        assert context['current_scene_state'] == original['current_scene_state']
        assert context['character_state'] == original['character_state']
        assert 'source_author_entry_situation' not in context
        assert 'source_author_entry_state' not in context
    ordinary = actor._turn_messages(engine, session, outcome, '眼前这件事已经做完了。',
                                    original['core_persona'], '小岚', '你')
    ordinary_data = json.loads(ordinary[1].content.split('\n', 1)[1])
    assert list(ordinary_data) == ['role', 'current_scene', 'story_so_far', 'pacing', 'next_scene', 'player_input']
    assert original['current_scene_state'] in ordinary_data['role']
    assert original['character_state']['player_state'] in ordinary_data['role']
    assert 'source_author_entry_situation' not in ordinary[1].content
    assert 'source_author_entry_state' not in ordinary[1].content
