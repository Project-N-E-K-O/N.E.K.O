"""主动转场保持数值选路、提交复核和历史重放；语义夹具另用于真实模型正反例。"""

from dataclasses import replace
import json
import pytest
from services.theater.numeric_v2_actor import _turn_messages
from services.theater.numeric_v2_evaluator import _build_transition_judge_messages, _parse_output
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2, MetricChangeV2
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


def initiation_case(place='档案接待台', target='阅览室', *, public=True, message='带路吧。'):
    """目标只在公开对白有/无这一处变化；作者预览一直有目标，检验是否偷用未来信息。"""
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    source = engine.nodes['start']
    source['story_beat'] = {'opening_scene': f'两人在{place}。', 'summary': f'办妥眼前事务后可以去{target}，后续操作属于下一幕。'}
    source['route_gates'][1]['transition_contract'] = {
        'reason': f'眼前事务已办妥，可以去{target}。',
        'must_deliver': [f'两人到达{target}'], 'must_preserve': ['后续操作尚未开始。'], 'tone': '自然',
        'bridge_scene_narration': f'两人沿左侧走廊来到{target}。',
    }
    engine.nodes['ending_leave'].update(type='scene', terminal=False, chapter=target,
        story_beat={'opening_scene': f'两人在{target}入口，后续操作尚未开始。', 'summary': '先商量如何进行后续操作。'})
    opening = {'performance': f'（收好凭据）手续办妥了。左侧走廊通往{target}，通道已经开放。' if public else '（收好凭据）手续办妥了。', 'suggested_inputs': []}
    session = engine.create_session(session_id='initiation', catgirl_binding=_binding(), opening_performance=opening)
    return dict(engine=engine, session=session, message=message, target=target)


def initiation_cases():
    """三个题材的直接请求与询问、准备、含糊同意、隐藏目的地对照，不给模型期望标签。"""
    for place, target in [('档案接待台', '阅览室'), ('排练后台', '演奏厅'), ('花园值班室', '温室')]:
        for name, message, public, intent in [
            ('direct', '带路吧。', True, 'initiate'),
            ('question', f'我们能去{target}吗？', True, 'unclear'),
            ('prepare', f'我考虑去{target}，先准备一下，还没决定走。', True, 'unclear'),
            ('vague', '好。', True, 'unclear'),
            ('hidden', f'带我去{target}吧。', False, 'unclear'),
        ]:
            yield dict(name=place+'_'+name, expected_intent=intent, **initiation_case(place,target,public=public,message=message))


@pytest.mark.asyncio
async def test_initiation_commits_without_offer_and_replays(tmp_path):
    """从真实初始 Session 提交新意图，冷恢复与测试分叉都由同一 Runtime 重放。"""
    case = initiation_case(); engine = case['engine']; runtime = NumericV2Runtime(engine, tmp_path)
    stored = await runtime.start_session(session_id='initiation', catgirl_binding=_binding(), opening_performance=case['session'].opening_performance)
    assert not stored.session.transition_offered
    outcome = runtime.prepare_turn(stored, TurnRequestV2('go',0,case['message']), (), transition_intent='initiate')
    assert outcome.session.current_node_id == 'ending_leave'
    assert not outcome.session.transition_offered
    assert outcome.ledger_event['transition_intent'] == 'initiate'
    candidate = _candidate()
    candidate.update(source_performance='（点头）跟我来。', bridge_scene_narration='两人来到阅览室。',
                     target_scene_narration='两人停在阅览室入口。', target_performance='（看向书架）先看看目录吗？')
    performance = engine.finalize_transition_performance(outcome,candidate,target_opening='阅览室入口。')
    committed = await runtime.commit_turn(outcome,performance)
    assert await NumericV2Runtime(engine,tmp_path).restore_session('initiation') == committed
    forked = await runtime.fork_session_for_test('initiation',session_id='initiation_fork',through_revision=1)
    assert forked.session.current_node_id == 'ending_leave'
    assert forked.ledger_events[-1]['transition_intent'] == 'initiate'


@pytest.mark.parametrize('intent', ['accept','unclear'])
def test_plain_accept_without_offer_still_stays(intent):
    """新增主动请求不能放宽原有空口 accept 的防线。"""
    c = initiation_case(); result = c['engine'].resolve_turn(c['session'],TurnRequestV2('wait',0,'好。'),(),transition_intent=intent)
    assert result.session.current_node_id == 'start'
    assert result.ledger_event['transition_intent'] == 'unclear'


def test_initiation_uses_updated_metrics_and_does_not_offer_unavailable_route():
    """主动请求仍按更新后的数值选路；所有路线不可达时不能假装已提出邀请。"""
    c = initiation_case(); engine = c['engine']; session = replace(c['session'],metrics={'trust':69})
    # 使用与普通接受相同的数值边界，避免新增另一套锁路状态。
    change = MetricChangeV2('trust',2,'玩家兑现承诺','已按约完成。')
    result = engine.resolve_turn(session,TurnRequestV2('go',0,c['message']),(change,),transition_intent='initiate')
    assert result.session.current_node_id == 'ending_stay'
    engine.nodes['start']['route_gates'] = engine.nodes['start']['route_gates'][:1]
    result = engine.resolve_turn(c['session'],TurnRequestV2('blocked',0,c['message']),(),transition_intent='initiate')
    assert result.session.current_node_id == 'start'
    assert result.route_status == 'conditions_blocked'
    assert not result.session.transition_offered


def test_initiation_reaches_actor_and_formal_guard_without_becoming_natural_ending():
    """普通转场即使伴随错误的结束布尔值，也不能被 Actor 标成自然结局。"""
    c = initiation_case(); engine,session = c['engine'],c['session']
    parsed = _parse_output(json.dumps(dict(scene_complete=False,metric_changes={},transition_intent='initiate',public_destination_quote='左侧走廊通往阅览室，通道已经开放。')),engine,c['message'],session)
    outcome = engine.resolve_turn(session,TurnRequestV2('go',0,c['message']),(),transition_intent=parsed.transition_intent,natural_ending_ready=True)
    actor = _turn_messages(engine,session,outcome,c['message'],'安静克制。','测试猫娘','你')
    data = json.loads(actor[1].content.split('：',1)[1])
    assert data['transition']['player_initiated'] is True
    assert not data['transition'].get('natural_ending')
    messages = _build_transition_judge_messages(engine,session,actor_performance={'segments':[],'suggested_inputs':[]},player_input=c['message'],transition_outcome=outcome)
    data = json.loads(messages[1].content.split('：',1)[1])
    assert data['transition_authorization']['transition_intent'] == 'initiate'
    assert '左侧走廊' in json.dumps(data['scene_context'],ensure_ascii=False)
    assert '作者未来材料不能充当公开证据' in messages[0].content


@pytest.mark.parametrize('quote', ['', None, '作者计划下一幕去阅览室。', '带我去阅览室吧。'])
def test_fabricated_or_missing_public_quote_cannot_authorize(quote):
    """原文核对只限制主动请求，旧 accept 仍按既有邀请规则交给 Runtime；复查也不能放过伪证据。"""
    from services.theater.numeric_v2_evaluator import _parse_transition_judge_output
    c = initiation_case()
    payload = dict(scene_complete=False, metric_changes={}, transition_intent='initiate', public_destination_quote=quote)
    result = _parse_output(json.dumps(payload), c['engine'], c['message'], c['session'])
    assert result.transition_intent == 'unclear'
    review = dict(offer_present=False,valid=False,body_violations=[],unsafe_suggestion_indexes=[],failure_reason='',public_destination_quote=quote)
    result = _parse_transition_judge_output(json.dumps(review),initiation_session=c['session'])
    assert result.body_violations == ('player_action',)
