"""Natural ending affects only explicitly ready final nodes; legacy records, ordinary scenes and failed transactions retain their semantics."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.theater.numeric_v2_runtime import MetricChangeV2, NumericV2Engine, NumericV2Runtime, TurnRequestV2
from services.theater.numeric_v2_evaluator import _parse_output, _build_messages, NumericV2EvaluatorOutputError
from services.theater.numeric_v2_actor import _turn_messages
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening, _performance, _transition_performance


def _engine(*, ordinary=False, blocked=False):
    story = numeric_v2_story()
    if ordinary:
        # Let the higher-priority reachable normal act win; a reachable ending must not bypass the existing routing result.
        middle = deepcopy(story['nodes'][0])
        middle.update(id='middle', type='scene')
        for route in middle['route_gates']:
            route['id'] = 'middle_' + route['id']
        story['nodes'].append(middle)
        story['nodes'][0]['route_gates'][0]['target_node_id'] = 'middle'
        story['nodes'][0]['route_gates'][0]['conditions']['all'][0]['value'] = 0
    if blocked:
        # Keep each route reachable within valid numeric bounds, while current trust=20 meets none.
        story['nodes'][0]['route_gates'][0]['conditions']['all'][0]['value'] = 90
        story['nodes'][0]['route_gates'][1]['conditions']['all'][0]['value'] = 10
    return NumericV2Engine.from_mapping(story)


@pytest.mark.parametrize('complete,ready,ordinary,blocked,offered,intent,ended', [
    (True, True, False, False, False, 'unclear', True),
    (True, False, False, False, False, 'unclear', False),
    (False, True, False, False, False, 'unclear', False),
    (True, True, True, False, False, 'unclear', False),
    (True, True, False, True, False, 'unclear', False),
    (True, True, False, False, True, 'reject', False),
    (False, False, False, False, True, 'accept', True),
])
def test_natural_ending_respects_route_kind_conditions_and_player_refusal(complete, ready, ordinary, blocked, offered, intent, ended):
    engine = _engine(ordinary=ordinary, blocked=blocked)
    session = engine.create_session(session_id='natural', catgirl_binding=_binding(), opening_performance=_opening())
    session = replace(session, transition_offered=offered)
    result = engine.resolve_turn(session, TurnRequestV2('one', 0, '谢谢你。'), (),
        scene_complete=complete, natural_ending_ready=ready, transition_intent=intent)
    assert (result.session.status == 'ended') is ended
    if ended:
        assert result.session.current_node_id == 'ending_leave'
        assert result.ledger_event['transition_intent'] == intent
    else:
        assert result.session.current_node_id == 'start'


@pytest.mark.parametrize('ready', [None, 1, 'true', [], {}])
def test_evaluator_rejects_non_boolean_natural_ending(ready):
    with pytest.raises(NumericV2EvaluatorOutputError, match='natural_ending_invalid'):
        _parse_output(json.dumps(dict(scene_complete=True, metric_changes={}, natural_ending_ready=ready)), _engine(), '谢谢。')


def test_legacy_evaluator_completion_is_not_ending_authorization():
    result = _parse_output('{"scene_complete":true,"metric_changes":{}}', _engine(), '谢谢。')
    assert result.natural_ending_ready is False


def test_metric_change_cannot_reuse_authorization_for_another_ending():
    # The evaluator sees the leave ending, but this turn's score switches to the stay ending; recheck the latter's actual prerequisites.
    engine = _engine()
    session = engine.create_session(session_id='route_switch', catgirl_binding=_binding(), opening_performance=_opening())
    session = replace(session, metrics={'trust': 69})
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '我兑现了承诺。'),
        (MetricChangeV2('trust', 1, '玩家兑现承诺', '我兑现了承诺。'),),
        scene_complete=True, natural_ending_ready=True)
    assert outcome.session.metrics['trust'] == 70
    assert outcome.session.current_node_id == 'start'


@pytest.mark.asyncio
@pytest.mark.parametrize('ready', [False, True])
async def test_ending_signal_replays_without_reinterpreting_old_completion(tmp_path, ready):
    runtime = NumericV2Runtime(_engine(), tmp_path)
    stored = await runtime.start_session(session_id='source', catgirl_binding=_binding(), opening_performance=_opening())
    outcome = runtime.prepare_turn(stored, TurnRequestV2('one', 0, '谢谢。'), (), scene_complete=True, natural_ending_ready=ready)
    if ready:
        # Natural endings also require atomic delivery of the whole source response/bridge/ending; ordinary body text cannot impersonate a delivered ending.
        with pytest.raises(ValueError, match='numeric_transition_performance_invalid'):
            await runtime.commit_turn(outcome, _performance('谢谢。'))
        assert await runtime.restore_session('source') == stored
    performance = _transition_performance(outcome.session.current_node_id) if ready else _performance('谢谢。')
    committed = await runtime.commit_turn(outcome, performance)
    fork = await runtime.fork_session_for_test('source', session_id='fork', through_revision=1)
    assert fork.session.status == committed.session.status
    assert fork.session.current_node_id == committed.session.current_node_id
    assert fork.ledger_events[0].get('natural_ending_ready', False) is ready
    assert fork.ledger_events[0]['transition_intent'] == 'unclear'


def test_natural_ending_actor_receives_authorization_without_fake_acceptance():
    engine = _engine()
    session = engine.create_session(session_id='prompt', catgirl_binding=_binding(), opening_performance=_opening())
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '谢谢。'), (), scene_complete=True, natural_ending_ready=True)
    messages = _turn_messages(engine, session, outcome, '谢谢。', '温和。', '小葵', '你')
    payload = json.loads(messages[1].content.split('\n', 1)[1])
    assert payload['transition']['natural_ending'] is True
    assert '不声称玩家接受了未提出的邀请' in messages[0].content
    # Same-turn authorization includes delivery of the final action and response, preserving unknown success and player decision boundaries.
    assert '先在来源回应完成玩家已授权的最后互动' in messages[0].content
    # Natural-ending authorization cannot turn agreed future execution into facts through environmental narration.
    compact = _turn_messages(engine, session, outcome, '谢谢。', '温和。', '小葵', '你')
    assert '不能把约定的未来行动写成已经执行' in compact[0].content
    # A silent source and a speaking target can coexist; the model must generate from these two distinct fields.
    assert 'target_performance 遵守 acting_context.target_dialogue_policy' in compact[0].content
    evaluator_messages = _build_messages(engine, session, '谢谢。')
    assert '不必等玩家再说一句' in evaluator_messages[0].content
    # The sequential contract retains unknown outcomes and real risk thresholds, independent of the old repeated paragraph wording.
    assert '未知成败、真实风险' in evaluator_messages[0].content
    data = json.loads(evaluator_messages[1].content.split('\n', 1)[1])
    assert 'natural_ending_context' in data['transition_preview']


@pytest.mark.asyncio
@pytest.mark.parametrize('suggestions', [[], ['下次再一起做一个吧。']])
async def test_terminal_actor_never_fills_unusable_followup_choices(monkeypatch, suggestions):
    """Ended Sessions need no suggestion refill, and mistakenly generated new invitations must stay out of visible choices."""
    from services.theater.numeric_v2_actor import NumericV2Actor

    engine = _engine()
    session = engine.create_session(session_id='ending_buttons', catgirl_binding=_binding(), opening_performance=_opening())
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '谢谢。'), (), scene_complete=True, natural_ending_ready=True)
    actor = NumericV2Actor(object())

    async def invoke(*args, **kwargs):
        return {'segments': [
            {'phase': 'source_response', 'performance': '（轻轻点头）我也很高兴。'},
            {'phase': 'transition_bridge', 'scene_narration': '雨后的长街安静下来。'},
            {'phase': 'target_opening', 'performance': '（抬头望向街灯）愿你一路顺风。'},
        ], 'suggested_inputs': suggestions}

    async def forbidden_fill(**kwargs):
        raise AssertionError('终局不得调用补推荐')

    monkeypatch.setattr(actor, '_invoke', invoke)
    monkeypatch.setattr(actor, '_ensure_suggestions', forbidden_fill)
    performance = await actor.generate_turn(engine=engine, session=session, outcome=outcome,
        player_input='谢谢。', character_profile='温和克制。')
    assert performance['transition_delivered'] is True
    assert performance['suggested_inputs'] == []
