"""Ordinary Actor pressure keeps state values, sources and complete latest history."""
from copy import deepcopy
import inspect
import json
import pytest
from services.theater import numeric_v2_actor as actor

MARKER = '\nnarrative_state（已提交动态叙事账本）：'

@pytest.fixture(params=['science_fiction', 'fantasy'])
def sample(request):
    place, item = ('气闸门口', '记忆晶体') if request.param == 'science_fiction' else ('湖岸边', '魔晶')
    state = {
        'revision': 8, 'coverage': 'partial',
        'current': {'player': {'position': {'value': place, 'source_event': 'r8.player_input.1', 'known_to': ['player', 'character'], 'quote': f'玩家在{place}等待。' * 120}}},
        'last_known': {f'item:{item}': {'holder': {'value': 'character', 'source_event': 'r7.turn.1', 'quote': f'角色拿着{item}。' * 120}}},
        'recent_events': [{'event_id': 'r8.player_input.1', 'subject': 'player', 'quote': f'玩家在{place}等待。', 'changes': [{'entity': 'player', 'attribute': 'position', 'value': place}]}],
        'projection': {'retained_entities': 2, 'omitted_entities': 0, 'omitted_recent_events': 0, 'quotes_omitted': 3, 'over_budget': False},
    }
    rows = [{'revision': 7, 'player_input': '先等等。', 'performance': '我也在这里等着。'},
            {'revision': 8, 'player_input': f'（站在{place}）现在继续。', 'performance': f'（拿稳{item}）我会保持原来的位置。'}]
    data = {'role': '角色身份和说话合同', 'current_scene': '当前幕作者合同与边界' + MARKER + json.dumps(state, ensure_ascii=False, separators=(',', ':')) + '\n完成事实的原始出处仍需保留。',
            'story_so_far': actor._story_so_far_text(rows), 'pacing': '不得改写玩家意图', 'next_scene': '目标开场完整合同', 'player_input': rows[-1]['player_input']}
    return data, rows, state

def size(data):
    return actor.count_tokens('system') + actor.count_tokens('context:' + json.dumps(data, ensure_ascii=False, separators=(',', ':')))

def minimum(sample, omit=False):
    data, rows, state = deepcopy(sample)
    data['story_so_far'] = actor._story_so_far_text(rows[-1:])
    if omit:
        compact = deepcopy(state)
        compact['current']['player']['position'].pop('quote')
        next(iter(compact['last_known'].values()))['holder'].pop('quote')
        compact['projection']['quotes_omitted'] += 2
        data['current_scene'] = data['current_scene'].replace(MARKER + json.dumps(state, ensure_ascii=False, separators=(',', ':')), MARKER + json.dumps(compact, ensure_ascii=False, separators=(',', ':')))
    return data

def build(sample, limit, state_enabled=True):
    data, rows, state = sample
    kwargs = dict(system_prompt='system', human_prefix='context:', data=data, history_rows=rows, max_tokens=limit)
    if state_enabled and 'narrative_state' in inspect.signature(actor._fit_simple_turn_prompt_data).parameters:
        kwargs['narrative_state'] = state
    return actor._fit_simple_turn_prompt_data(**kwargs)

def test_final_pressure_removes_only_attribute_quotes(sample):
    original = deepcopy(sample)
    want = minimum(sample, omit=True)
    assert size(minimum(sample)) > size(want)
    assert build(sample, size(want)) == want
    assert sample == original
    assert actor._story_so_far_text(sample[1][-1:]) == want['story_so_far']

def test_exact_fit_and_history_only_fit_keep_original_state(sample):
    original = deepcopy(sample)
    assert build(sample, size(sample[0])) == sample[0]
    want = minimum(sample)
    assert build(sample, size(want)) == want
    assert sample == original

def test_still_over_budget_rejects_without_mutation(sample):
    original = deepcopy(sample)
    with pytest.raises(actor.NumericV2ActorError, match='fixed_context_budget_exceeded'):
        build(sample, size(minimum(sample, omit=True)) - 1)
    assert sample == original

@pytest.mark.parametrize('case', ['no_state_argument', 'missing_marker', 'ambiguous_marker'])
def test_unproven_payload_is_not_rewritten(sample, case):
    sample = deepcopy(sample)
    data, rows, state = sample
    if case == 'missing_marker':
        data['current_scene'] = data['current_scene'].replace(MARKER, '\n普通作者文字：')
    elif case == 'ambiguous_marker':
        data['current_scene'] += MARKER + json.dumps(state, ensure_ascii=False, separators=(',', ':'))
    original = deepcopy(sample)
    with pytest.raises(actor.NumericV2ActorError, match='fixed_context_budget_exceeded'):
        build(sample, size(minimum(sample, omit=True)) - 1, state_enabled=case != 'no_state_argument')
    assert sample == original
