"""Remove only previously played complete sentences at a generated bridge's beginning."""
from copy import deepcopy

import pytest

from services.theater import numeric_v2_actor as actor_module
from services.theater.numeric_v2_actor import NumericV2Actor
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_fixed_narration import _piece
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_pending_offer import _commit
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


# Real mountain R3 narration and the later frozen Qwen R4 bridge; no external experiment file is needed.
PREVIOUS = ('她点头，牵着自己的马转向碎石缓坡，靴底踩实松动的石子。'
            '风雪裹着雪沫扑上肩头，两人沿着背风一侧慢慢绕行，窄口的薄冰被甩在身后。')
NEW_BRIDGE = ('按说定的办法过了窄口一带，山径在前方重新收拢。'
              '天色忽然暗下，风卷着雪扑来；再往前，猎户棚的矮顶在雪幕里若隐若现。')
TARGET_FIXED = '[NOTICE]\n山路图仍在原位，不可改写。'


def _payload(bridge):
    return {'source_performance': '', 'source_scene_narration': '风声从坡下传来。',
            'bridge_scene_narration': bridge, 'target_scene_narration': '棚外的马匹抖落鬃毛上的雪。',
            'target_performance': '风向变了，先站到背风处看看。',
            'suggested_inputs': ['（看看前方）棚子还有多远？', '（放慢脚步）先看看马匹的状态。']}


async def _setup(tmp_path):
    story=deepcopy(_engine(ordinary=True).story)
    next(node for node in story['nodes'] if node['id']=='middle')['story_beat']['fixed_narrations']=[
        _piece('notice',TARGET_FIXED,entry=True)]
    runtime=NumericV2Runtime(NumericV2Engine.from_mapping(story),tmp_path)
    current=await runtime.start_session(session_id='bridge-prefix',catgirl_binding=_binding(),opening_performance=_opening())
    current=await _commit(runtime,current,'路面已经过去了，接下来向前走，好吗？',offer=True,narration=PREVIOUS)
    request=TurnRequestV2('accept',current.session.revision,'好，我们继续前行。')
    outcome=runtime.prepare_turn(current,request,(),transition_intent='accept')
    assert outcome.ledger_event['from_node_id'] != outcome.ledger_event['to_node_id']
    return runtime,current,request,outcome


async def _generate(monkeypatch,runtime,current,request,outcome,payload):
    actor=NumericV2Actor(object())
    calls=[]
    async def invoke(*args,**kwargs):
        calls.append(kwargs)
        return deepcopy(payload)
    monkeypatch.setattr(actor,'_invoke',invoke)
    result=await actor.generate_turn(engine=runtime.engine,session=current.session,outcome=outcome,
        player_input=request.message,character_profile='沉稳克制。',allow_suggestion_fill=False)
    assert len(calls)==1
    return result


@pytest.mark.asyncio
async def test_real_repeated_bridge_prefix_is_trimmed_before_atomic_commit_and_replay(tmp_path,monkeypatch):
    runtime,current,request,outcome=await _setup(tmp_path)
    payload=_payload(PREVIOUS+NEW_BRIDGE)
    original=deepcopy(payload)
    result=await _generate(monkeypatch,runtime,current,request,outcome,payload)
    expected=runtime.engine.finalize_transition_performance(outcome,original,target_opening='作者备选',
        bridge_required=True,source_dialogue_policy='optional')
    expected['segments'][1]['scene_narration']=NEW_BRIDGE
    assert result==expected
    assert payload==original
    assert await runtime.restore_session('bridge-prefix')==current
    committed=await runtime.commit_turn(outcome,result)
    assert await runtime.restore_session('bridge-prefix')==committed
    fork=await runtime.fork_session_for_test('bridge-prefix',session_id='fork',through_revision=2)
    assert fork.session.performance_history[-1]['segments']==result['segments']
    assert fork.session.performance_history[-1]['segments'][2]['fixed_narrations'][0]['text']==TARGET_FIXED


@pytest.mark.asyncio
@pytest.mark.parametrize('bridge',[
    PREVIOUS,
    NEW_BRIDGE+PREVIOUS,
    '两人沿着背风的坡面绕了过去。'+NEW_BRIDGE,
])
async def test_full_repeat_middle_repeat_and_paraphrase_stay_unchanged(tmp_path,monkeypatch,bridge):
    runtime,current,request,outcome=await _setup(tmp_path)
    result=await _generate(monkeypatch,runtime,current,request,outcome,_payload(bridge))
    assert result['segments'][1]['scene_narration']==bridge


@pytest.mark.asyncio
async def test_ordinary_scene_update_keeps_existing_behavior(tmp_path,monkeypatch):
    runtime,current,_,_=await _setup(tmp_path)
    request=TurnRequestV2('stay',current.session.revision,'先聊聊现在的天气。')
    outcome=runtime.prepare_turn(current,request,())
    assert outcome.ledger_event['from_node_id']==outcome.ledger_event['to_node_id']
    payload={'performance':'这阵风从北面来，站在坡侧会好一些。',
             'scene_narration':PREVIOUS+NEW_BRIDGE,'suggested_inputs':[],'transition_offered':False}
    result=await _generate(monkeypatch,runtime,current,request,outcome,payload)
    expected=actor_module._deduplicate_scene_update(payload,current.session.performance_history[-1])
    assert result==expected


@pytest.mark.parametrize('previous,bridge,expected', [
    ('雪停了。风也缓了。', '雪停了。风也缓了。\n新路口有一盏灯。', '新路口有一盏灯。'),
    ('雪停了。', '雪停了。新路口有一盏灯。雪停了。', '新路口有一盏灯。雪停了。'),
    ('雪停了。', '雪停了！新路口有一盏灯。', '雪停了！新路口有一盏灯。'),
    ('雪 停了。', '雪停了。新路口有一盏灯。', '雪停了。新路口有一盏灯。'),
    ('风缓了', '风缓了\n新路口有一盏灯。', '风缓了\n新路口有一盏灯。'),
])
def test_only_complete_exact_prefix_is_removed(previous, bridge, expected):
    performance = {'segments': [
        {'phase': 'source_response', 'performance': '再看一眼。', 'scene_narration': bridge},
        {'phase': 'transition_bridge', 'scene_narration': bridge},
        {'phase': 'target_opening', 'performance': '到这里了。', 'scene_narration': bridge,
         'fixed_narrations': [{'text': bridge}]},
    ]}
    original = deepcopy(performance)
    result = actor_module._deduplicate_transition_bridge_prefix(
        performance, {'scene_narration': previous})
    assert result['segments'][1]['scene_narration'] == expected
    assert result['segments'][0] == original['segments'][0]
    assert result['segments'][2] == original['segments'][2]
    assert performance == original


@pytest.mark.parametrize('bridge,expected', [
    ('雪停了。新路口有一盏灯。', '新路口有一盏灯。'),
    ('风缓了。新路口有一盏灯。', '风缓了。新路口有一盏灯。'),
    ('招牌写着请进。新路口有一盏灯。', '招牌写着请进。新路口有一盏灯。'),
])
def test_previous_transition_uses_last_visible_scene_narration_only(bridge, expected):
    previous = {'segments': [
        {'phase': 'source_response', 'scene_narration': '风缓了。'},
        {'phase': 'transition_bridge', 'scene_narration': '已经走过山路了。'},
        {'phase': 'target_opening', 'scene_narration': '雪停了。',
         'fixed_narrations': [{'text': '招牌写着请进。'}]},
    ]}
    current = {'segments': [{}, {'scene_narration': bridge}, {}]}
    result = actor_module._deduplicate_transition_bridge_prefix(current, previous)
    assert result['segments'][1]['scene_narration'] == expected


@pytest.mark.parametrize('left,right', [
    ('“', '”'),
    ('"', '"'),
    ('（', '）'),
])
def test_quoted_or_parenthetical_previous_text_cannot_prove_an_action_repeated(left, right):
    previous = {'scene_narration': f'管事讲起昨天的事：{left}大家等了半天。院门打开了。{right}'}
    bridge = '院门打开了。两人走进院中，门边的灯照亮脚下。'
    current = {'segments': [{}, {'scene_narration': bridge}, {}]}
    assert actor_module._deduplicate_transition_bridge_prefix(current, previous) == current


@pytest.mark.parametrize('previous,bridge,expected', [
    ('院门打开了。管事离开后，院门又关上了。',
     '院门打开了。两人跨过门槛，门边的灯照亮脚下。',
     '院门打开了。两人跨过门槛，门边的灯照亮脚下。'),
    ('雪停了。风缓了。', '风缓了。雪停了。新路口有一盏灯。', '风缓了。雪停了。新路口有一盏灯。'),
    ('雪停了。风缓了。', '风缓了。新路口有一盏灯。', '新路口有一盏灯。'),
])
def test_repeated_prefix_must_match_previous_contiguous_suffix_in_order(previous, bridge, expected):
    current = {'segments': [{}, {'scene_narration': bridge}, {}]}
    result = actor_module._deduplicate_transition_bridge_prefix(current, {'scene_narration': previous})
    assert result['segments'][1]['scene_narration'] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['target', 'fixed'])
async def test_trimmed_bridge_does_not_bypass_atomic_commit_validation(tmp_path, monkeypatch, invalid):
    runtime, current, request, outcome = await _setup(tmp_path)
    result = await _generate(monkeypatch, runtime, current, request, outcome,
                             _payload(PREVIOUS + NEW_BRIDGE))
    assert result['segments'][1]['scene_narration'] == NEW_BRIDGE
    if invalid == 'target':
        result['segments'][2]['performance'] = ''
    else:
        result['segments'][2]['fixed_narrations'][0]['text'] = '被改写的固定文本。'
    with pytest.raises(ValueError):
        await runtime.commit_turn(outcome, result)
    assert await runtime.restore_session('bridge-prefix') == current
