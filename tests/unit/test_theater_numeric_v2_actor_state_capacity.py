"""Actor transition packing keeps factual values and public evidence under pressure."""
from copy import deepcopy
import json
import pytest
from services.theater import numeric_v2_actor as actor


@pytest.fixture(params=['science_fiction', 'fantasy'])
def inputs(request):
    place, item = ('气闸门口', '记忆晶体') if request.param == 'science_fiction' else ('湖岸边', '魔晶')
    state = {
        'revision': 8, 'coverage_from': 0, 'coverage': 'partial', 'current_node_id': 'source',
        'current': {'player': {'position': {'value': place, 'source_event': 'r8.player_input.1', 'quote': f'玩家在{place}等待。' * 120}}},
        'last_known': {f'item:{item}': {'holder': {'value': 'character', 'source_event': 'r7.turn.1', 'quote': f'角色拿着{item}。' * 120}}},
        'recent_events': [{'event_id': 'r8.player_input.1', 'quote': f'玩家在{place}等待。', 'subject': 'player',
                           'changes': [{'entity': 'player', 'attribute': 'position', 'value': place}]}],
        'projection': {'retained_entities': 2, 'omitted_entities': 0, 'omitted_recent_events': 0,
                       'quotes_omitted': 3, 'over_budget': False},
    }
    rows = [{'revision': 7, 'player_input': '先看看周围。', 'performance': f'（拿稳{item}）我们先等一等。'},
            {'revision': 8, 'player_input': f'（站在{place}）现在继续。', 'performance': '（抬眼看向前方）好，我们继续。'}]
    evidence = [{'revision': row['revision'], 'source': 'performance', 'text': row['performance'], 'current_visit': True}
                for row in rows]
    evidence.append({'revision': 2, 'source': 'performance', 'text': '此前已明确关闭身后的入口。', 'current_visit': False})
    data = {'story_context': {'knowledge_scope': '只使用公开事实。', 'narrative_state': state},
            'recent_context': rows, 'history_evidence': evidence, 'player_input': '（站在原地）现在继续。',
            'acting_context': {'dialogue_policy': 'required', 'relationship_control': {'effective_stage': 'guarded'}},
            'transition': {'source_scene': {'opening_boundary': '已经到达入口。'},
                           'target_scene': {'opening_boundary': '前方出现新的场所。'},
                           'contract': {'must_preserve': ['角色持有物品', '玩家尚未额外承诺']}}}
    return data


def tokens(data):
    return actor.count_tokens('system') + actor.count_tokens('context:' + json.dumps(data, ensure_ascii=False, separators=(',', ':')))


def minimum(data):
    result = deepcopy(data)
    result['recent_context'] = result['recent_context'][-1:]
    result['history_evidence'] = actor._remaining_history_evidence(result['recent_context'], data['history_evidence'])
    return result


def without_quotes(data):
    result = deepcopy(data)
    state = result['story_context']['narrative_state']
    state['current']['player']['position'].pop('quote')
    next(iter(state['last_known'].values()))['holder'].pop('quote')
    state['projection']['quotes_omitted'] += 2
    return result


def build(data, limit):
    return actor._fit_turn_prompt_data(system_prompt='system', human_prefix='context:', data=data, max_tokens=limit)


def test_final_pressure_omits_only_attribute_quotes_and_restores_evidence(inputs):
    original = deepcopy(inputs)
    expected = without_quotes(minimum(inputs))
    limit = tokens(expected)
    assert tokens(minimum(inputs)) > limit
    actual = build(inputs, limit)
    assert actual == expected
    assert tokens(actual) <= limit
    assert actual['story_context']['narrative_state']['recent_events'] == original['story_context']['narrative_state']['recent_events']
    assert actual['recent_context'][-1] == original['recent_context'][-1]
    assert actual['player_input'] == original['player_input']
    assert original['history_evidence'][0] in actual['history_evidence']
    assert inputs == original


def test_fitting_boundary_keeps_prompt_byte_exact(inputs):
    original = deepcopy(inputs)
    generous = build(inputs, 100000)
    exact = build(inputs, tokens(generous))
    assert json.dumps(exact, ensure_ascii=False) == json.dumps(generous, ensure_ascii=False)
    assert exact['story_context']['narrative_state'] == original['story_context']['narrative_state']
    assert inputs == original


def test_still_over_budget_rejects_without_mutating_any_input(inputs):
    original = deepcopy(inputs)
    limit = tokens(without_quotes(minimum(inputs))) - 1
    with pytest.raises(actor.NumericV2ActorError, match='fixed_context_budget_exceeded'):
        build(inputs, limit)
    assert inputs == original


def test_no_narrative_state_does_not_drop_other_fixed_fields(inputs):
    inputs['story_context'].pop('narrative_state')
    original = deepcopy(inputs)
    with pytest.raises(actor.NumericV2ActorError, match='fixed_context_budget_exceeded'):
        build(inputs, tokens(minimum(inputs)) - 1)
    assert inputs == original
