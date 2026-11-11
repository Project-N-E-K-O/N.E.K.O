"""Reference exact public offer duplicates before spending the history budget."""
from copy import deepcopy
import inspect
import pytest
from tests.utils.theater_prompt import serialized_prompt_tokens
from services.theater import numeric_v2_actor as actor
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_pending_offer import _commit
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.utils.theater_prompt import prompt_payload

MARKER = '当前待确认提议原文（只证明此前说过，不证明安排正确）：'
REFERENCE = MARKER + '见 story_so_far 中角色最新的完整原文。'
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
    return serialized_prompt_tokens('system', 'context:' + actor.render_prompt_context(data))

def build(data, rows, offer, limit, *, origin=None):
    kwargs = dict(system_prompt='system', human_prefix='context:', data=data,
                  history_rows=rows, max_tokens=limit)
    if 'pending_offer_text' in inspect.signature(actor._fit_simple_turn_prompt_data).parameters:
        kwargs['pending_offer_text'] = offer
    if origin is not None:
        kwargs['pending_offer_revision'] = origin
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

def test_deduplicates_even_when_original_fits(sample):
    data, rows, offer = sample
    original = deepcopy(sample)
    assert build(data, rows, offer, size(data)) == expected(sample)
    assert sample == original

def test_deduplicates_before_evicting_an_earlier_complete_turn(sample):
    data, rows, offer = sample
    rows.insert(0, {"revision": 40, "performance": "钥匙已经交还给值班的人。"})
    want = expected(sample)
    want["story_so_far"] = actor._story_so_far_text(rows)
    assert build(data, rows, offer, size(want)) == want


@pytest.mark.asyncio
async def test_keeps_one_copy_of_exact_offer_after_a_committed_followup(tmp_path):
    runtime = NumericV2Runtime(_engine(ordinary=True), tmp_path)
    stored = await runtime.start_session(
        session_id="older-offer", catgirl_binding=_binding(), opening_performance=_opening(),
    )
    body, offer = "柜台的钥匙已经归还。", "要一起去外面的阅览区吗？"
    prepared = runtime.prepare_turn(stored, TurnRequestV2("offer", 0, "钥匙还了么？"), ())
    prepared, draft = runtime.engine.finalize_transition_offer_state(
        prepared, {"performance": body + "\n" + offer, "transition_offer_start": len(body) + 1,
                   "suggested_inputs": []}, new_offer=True,
    )
    offered = await runtime.commit_turn(prepared, draft)
    followed = await _commit(runtime, offered, "柜台这里还有一盏灯亮着。")
    request = TurnRequestV2("clarify", followed.session.revision, "先等等。")
    prepared = runtime.prepare_turn(followed, request, ())
    messages = actor._turn_messages(
        runtime.engine, followed.session, prepared, request.message, "克制。", "小岚", "玩家",
        recent_ledger_events=followed.ledger_events,
    )
    payload = prompt_payload(messages[1].content)
    assert payload["story_so_far"].count(offer) == 1
    assert offer not in payload["pacing"]
    assert "倒数第2个完整回合" in payload["pacing"]


def test_restores_literal_offer_if_its_exact_origin_is_evicted(sample):
    data, rows, offer = sample
    rows.append({"revision": 42, "performance": "最新角色原文没有该邀请。"})
    want = {**data, "story_so_far": actor._story_so_far_text(rows[-1:])}
    got = build(data, rows, offer, size(want), origin=41)
    assert got == want

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
