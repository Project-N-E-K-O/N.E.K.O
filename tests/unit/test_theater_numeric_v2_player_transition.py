"""Player initiation retains metric routing, commit review and history replay; semantic fixtures support separate real-model comparisons."""

from dataclasses import replace
import json
import pytest
from services.theater.numeric_v2_actor import _turn_messages
from services.theater.numeric_v2_evaluator import _build_messages, _build_transition_judge_messages, _parse_output
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2, MetricChangeV2
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


def initiation_case(place='档案接待台', target='阅览室', *, public=True, message='带路吧。'):
    """Vary only whether public dialogue names the destination; the author preview always names it, exposing misuse of future information."""
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
    """Compare direct requests with questions, preparation, vague agreement and hidden destinations across three genres without expected labels in model input."""
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
async def test_evaluator_distinguishes_current_choice_from_earlier_player_input(tmp_path):
    case = initiation_case()
    runtime = NumericV2Runtime(case['engine'], tmp_path)
    current = await runtime.start_session(session_id='new_choice', catgirl_binding=_binding(),
        opening_performance=case['session'].opening_performance)
    old = '你先去，我留在这里。'
    outcome = runtime.prepare_turn(current, TurnRequestV2('stay', 0, old), ())
    current = await runtime.commit_turn(outcome, {'performance': '好，我先去，你留在这里。', 'suggested_inputs': []})
    latest = '我改主意了，我们一起去阅览室。'
    messages = _build_messages(case['engine'], current.session, latest)
    data = prompt_payload(messages[1].content)
    assert old in json.dumps(data['scene_context'], ensure_ascii=False)
    assert data['player_input'] == latest and list(data)[-1] == 'player_input'
    assert data['player_input_revision'] == current.session.revision + 1


@pytest.mark.parametrize('place,target,offer,entry,aftermath', [
    ('灯会渡口', '船到岸边', '现在点亮引路灯，等摆渡人靠岸吗？',
     '引路灯亮起，摆渡人看见灯光后划向岸边。', '摆渡人靠岸后等待玩家决定是否登船。'),
    ('剧团值班台', '演员候场', '敲铃通知演员候场吧，可以吗？',
     '铃声传进后台，演员陆续来到候场区。', '演员提出临时换角，需要玩家另行决定。'),
])
def test_evaluator_compares_public_entry_action_without_target_plot(place, target, offer, entry, aftermath):
    """The real packed request retains entry authorization while future decisions stay out of its preview."""
    case = initiation_case(place, target, message='就按你说的做。')
    engine, session = case['engine'], case['session']
    engine.nodes['start']['route_gates'][1]['transition_contract'].update(
        reason=offer, bridge_scene_narration=entry)
    engine.nodes['ending_leave']['story_beat']['opening_scene'] = aftermath
    session = replace(session, opening_performance={'performance': offer, 'suggested_inputs': []})
    messages = _build_messages(engine, session, case['message'])
    payload = prompt_payload(messages[1].content)
    preview = payload['transition_preview']
    assert preview['transition_direction'] == offer
    assert preview['entry_movement'] == entry
    assert 'target_opening_situation' not in preview
    assert aftermath not in messages[1].content
    assert offer in json.dumps(payload['scene_context'], ensure_ascii=False)


@pytest.mark.asyncio
async def test_initiation_commits_without_offer_and_replays(tmp_path):
    """Submit new intent from a real initial Session and replay cold recovery and test forks through the same Runtime."""
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
    """Player initiation must not weaken the existing rejection of unsupported accept intent."""
    c = initiation_case(); result = c['engine'].resolve_turn(c['session'],TurnRequestV2('wait',0,'好。'),(),transition_intent=intent)
    assert result.session.current_node_id == 'start'
    assert result.ledger_event['transition_intent'] == 'unclear'


def test_initiation_uses_updated_metrics_and_does_not_offer_unavailable_route():
    """Initiation still selects routes using updated metrics; unreachable exits cannot masquerade as a public invitation."""
    c = initiation_case(); engine = c['engine']; session = replace(c['session'],metrics={'trust':69})
    # Use the same numeric boundaries as normal acceptance to avoid another set of route-lock states.
    change = MetricChangeV2('trust',2,'玩家兑现承诺','已按约完成。')
    result = engine.resolve_turn(session,TurnRequestV2('go',0,c['message']),(change,),transition_intent='initiate')
    assert result.session.current_node_id == 'ending_stay'
    engine.nodes['start']['route_gates'] = engine.nodes['start']['route_gates'][:1]
    result = engine.resolve_turn(c['session'],TurnRequestV2('blocked',0,c['message']),(),transition_intent='initiate')
    assert result.session.current_node_id == 'start'
    assert result.route_status == 'conditions_blocked'
    assert not result.session.transition_offered


def test_initiation_reaches_actor_and_formal_guard_without_becoming_natural_ending():
    """An erroneous ending flag must not make the Actor mark an ordinary transition as a natural ending."""
    c = initiation_case(); engine,session = c['engine'],c['session']
    parsed = _parse_output(json.dumps(dict(scene_complete=False,metric_changes={},transition_intent='initiate',public_destination_quote='左侧走廊通往阅览室，通道已经开放。',exit_matches=True,exit_comparison='玩家要求与出口都是前往阅览室。')),engine,c['message'],session)
    outcome = engine.resolve_turn(session,TurnRequestV2('go',0,c['message']),(),transition_intent=parsed.transition_intent,natural_ending_ready=True)
    actor = _turn_messages(engine,session,outcome,c['message'],'安静克制。','测试猫娘','你')
    data = prompt_payload(actor[1].content)
    assert data['transition']['player_initiated'] is True
    assert not data['transition'].get('natural_ending')
    messages = _build_transition_judge_messages(engine,session,actor_performance={'segments':[],'suggested_inputs':[]},player_input=c['message'],transition_outcome=outcome)[0]
    data = prompt_payload(messages[1].content)
    assert data['transition_authorization']['transition_intent'] == 'initiate'
    assert '左侧走廊' in json.dumps(data['scene_context'],ensure_ascii=False)
    assert '作者未来材料不能充当公开证据' in messages[0].content


@pytest.mark.parametrize('quote', ['', None, '作者计划下一幕去阅览室。', '带我去阅览室吧。'])
def test_fabricated_or_missing_public_quote_cannot_authorize(quote, caplog):
    """Source verification constrains initiation only; existing accept intent follows Runtime invitation rules, and dispute review must still reject fabricated evidence."""
    from services.theater.numeric_v2_evaluator import _parse_transition_judge_output
    c = initiation_case()
    payload = dict(scene_complete=False, metric_changes={}, transition_intent='initiate', public_destination_quote=quote,
                   exit_matches=True, exit_comparison='玩家要求与出口都是前往阅览室。')
    result = _parse_output(json.dumps(payload), c['engine'], c['message'], c['session'])
    assert result.transition_intent == 'unclear'
    assert 'public destination quote rejected' in caplog.text
    assert f"session_id={c['session'].session_id} revision={c['session'].revision}" in caplog.text
    assert c['message'] not in caplog.text
    if quote:
        assert quote not in caplog.text
    review = dict(offer_present=False,valid=False,body_violations=[],unsafe_suggestion_indexes=[],failure_reason='',public_destination_quote=quote)
    result = _parse_transition_judge_output(json.dumps(review),initiation_session=c['session'])
    assert result.body_violations == ('player_action',)
