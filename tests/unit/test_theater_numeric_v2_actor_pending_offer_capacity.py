"""Only exact, latest, public role duplicates may be referenced under final pressure."""
from copy import deepcopy
import inspect
import json
import pytest
from services.theater import numeric_v2_actor as actor

MARKER = '当前待确认提议原文（只证明此前说过，不证明安排正确）：'
REFERENCE = MARKER + '见 story_so_far 最新完整猫娘原文。'
WARNING = '若原提议与 next_scene 不符，承认错误并保留玩家选择。'

@pytest.fixture(params=['science_fiction', 'fantasy'])
def sample(request):
    place, item = ('气闸', '晶体') if request.param == 'science_fiction' else ('湖岸', '魔晶')
    offer = f'（拿稳{item}）我们先去{place}旁边等一等，等灯亮了再继续。' * 12
    row = {'revision': 41, 'player_input': '我先看着。', 'performance': offer}
    data = {'role': '角色身份和说话合同', 'current_scene': '{"state":{"value":"角色持有物品","source_event":"r41.turn.1","quote":"原文"},"recent_events":[{"quote":"完整原文"}]}',
            'story_so_far': actor._story_so_far_text([row]), 'pacing': '留在本幕，不自动换幕。' + MARKER + offer + WARNING,
            'next_scene': '下一幕合同必须保留', 'player_input': '我明确同意按你刚才提出的下一步继续，走吧。'}
    return data, [row], offer

def size(data):
    return actor.count_tokens('system') + actor.count_tokens('context:' + json.dumps(data, ensure_ascii=False, separators=(',', ':')))

def build(data, rows, offer, limit):
    kwargs = dict(system_prompt='system', human_prefix='context:', data=data,
                  history_rows=rows, max_tokens=limit)
    if 'pending_offer_text' in inspect.signature(actor._fit_simple_turn_prompt_data).parameters:
        kwargs['pending_offer_text'] = offer
    return actor._fit_simple_turn_prompt_data(**kwargs)

def expected(sample):
    data, rows, offer = sample
    result = deepcopy(data)
    result['story_so_far'] = actor._story_so_far_text(rows[-1:])
    result['pacing'] = result['pacing'].replace(MARKER + offer, REFERENCE, 1)
    return result

def test_final_pressure_references_only_latest_complete_role_duplicate(sample):
    data, rows, offer = sample
    original = deepcopy(sample)
    want = expected(sample)
    assert size(data) > size(want)
    got = build(data, rows, offer, size(want))
    assert got == want
    assert WARNING in got['pacing']
    assert got['current_scene'] == data['current_scene']
    assert got['player_input'] == data['player_input']
    assert sample == original

def test_exact_fit_keeps_original_bytes(sample):
    data, rows, offer = sample
    original = deepcopy(sample)
    assert build(data, rows, offer, size(data)) == data
    assert sample == original

@pytest.mark.parametrize('where', ['player_only', 'old_role_only', 'index_only', 'previous_scene_tail', 'not_last_role', 'withdrawn_marker', 'ambiguous_marker', 'nonexact_role', 'no_pending_api'])
def test_unproven_or_withdrawn_duplicates_still_fail_without_mutation(sample, where):
    data, rows, offer = deepcopy(sample)
    if where == 'player_only': rows[0].update(player_input=offer, performance='角色没有说出邀请。')
    elif where == 'old_role_only': rows.append({'revision': 42, 'performance': '最新角色原文没有该邀请。'})
    elif where == 'index_only':
        rows[0]['performance'] = '最新角色原文没有该邀请。'
        data['current_scene'] += offer
    elif where == 'previous_scene_tail':
        rows[0]['segments'] = [{'phase': 'previous_scene_tail', 'performance': offer}]
        rows[0].pop('performance')
    elif where == 'not_last_role':
        rows[0]['segments'] = [{'phase': 'source_response', 'performance': offer}, {'phase': 'target_opening', 'performance': '最新目标角色没有邀请。'}]
        rows[0].pop('performance')
    elif where == 'withdrawn_marker': data['pacing'] = data['pacing'].replace(MARKER, '已确认去向错误并撤下的旧邀请原文：')
    elif where == 'ambiguous_marker': data['pacing'] += MARKER + offer
    elif where == 'nonexact_role': rows[0]['performance'] = offer.replace('等灯亮了', '等天亮了')
    elif where == 'no_pending_api': offer = ''
    data['story_so_far'] = actor._story_so_far_text(rows[-1:])
    original = deepcopy((data, rows, offer))
    sized_data = deepcopy(data)
    if where == 'previous_scene_tail':
        sized_data['story_so_far'] = actor._story_so_far_text([{**rows[0], 'segments': []}])
    with pytest.raises(actor.NumericV2ActorError, match='fixed_context_budget_exceeded'):
        build(data, rows, offer, size(sized_data) - 1)
    assert (data, rows, offer) == original

def test_still_over_budget_keeps_explicit_failure(sample):
    data, rows, offer = sample
    original = deepcopy(sample)
    with pytest.raises(actor.NumericV2ActorError, match='fixed_context_budget_exceeded'):
        build(data, rows, offer, size(expected(sample)) - 1)
    assert sample == original
