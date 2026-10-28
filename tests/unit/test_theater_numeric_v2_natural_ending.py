"""Natural ending affects only explicitly ready final nodes; legacy records, ordinary scenes and failed transactions retain their semantics."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from services.theater.numeric_v2_runtime import MetricChangeV2, NumericV2Engine, NumericV2Runtime, TurnRequestV2
from services.theater.numeric_v2_evaluator import _parse_output, _build_messages, NumericV2EvaluatorOutputError
from services.theater.numeric_v2_actor import _turn_messages
from tests.utils.theater_prompt import prompt_payload
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


def test_builtin_neon_overload_can_fall_silent_without_dialogue_retry():
    from services.theater.numeric_v2_actor_output import _parse_output

    story = json.loads((Path(__file__).resolve().parents[2] / 'assets/theater/story_neon_rebuild_ea2e45dab7.json').read_text(encoding='utf-8'))
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id='silent-overload', catgirl_binding=_binding(), opening_performance=_opening())
    session = replace(session, current_node_id='mainline_07')
    # Read the authored policy through Runtime, as on entry; an action-only
    # collapse must not need invented dialogue while model review is disabled.
    policy = engine._node_dialogue_policy(engine.nodes[session.current_node_id], session.dialogue_policy)
    result = _parse_output(json.dumps({'performance': '（核心停止闪烁，身体进入休眠。）',
        'suggested_inputs': [], 'transition_offered': False}), opening_required=False, dialogue_policy=policy)
    assert result['performance'] == '（核心停止闪烁，身体进入休眠。）'


@pytest.mark.parametrize('hacking,emotion,combat', [(30, 30, 40), (30, 30, 20), (80, 30, 40)])
@pytest.mark.parametrize('executed', [False, True])
def test_builtin_neon_normal_ending_requires_actual_overload(hacking, emotion, combat, executed):
    from services.theater.numeric_v2_fixed_narration import add_entry

    story = json.loads((Path(__file__).resolve().parents[2] / 'assets/theater/story_neon_rebuild_ea2e45dab7.json').read_text(encoding='utf-8'))
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id='overload-result', catgirl_binding=_binding(), opening_performance=_opening())
    session = replace(session, current_node_id='mainline_07',
                      metrics={**session.metrics, 'hacking_mastery': hacking, 'emotional_sync': emotion, 'combat_bond': combat},
                      opening_performance=add_entry(engine.nodes['mainline_07'], _opening(), _binding(), True))
    facts = [{'op': 'set', 'key': 'scene:mainline_07:plan_explained', 'value': True, 'visibility': 'public'}]
    session = engine.resolve_turn(session, TurnRequestV2('plan', 0, '我明白这份预案。'), (),
                                  fact_operations=tuple(facts)).session
    if executed:
        session = engine.resolve_turn(session, TurnRequestV2('result', 1, '看向追兵。'), (), fact_operations=(
            {'op': 'set', 'key': 'scene:mainline_07:overload_executed', 'value': True, 'visibility': 'public'},)).session
    outcome = engine.resolve_turn(session, TurnRequestV2('end', session.revision, '我试试能不能跟上，往哪边撤？'), (),
                                  scene_complete=True, natural_ending_ready=True)
    assert (outcome.session.status == 'ended') is executed
    assert outcome.session.current_node_id == ('ending_normal' if executed else 'mainline_07')


def test_builtin_neon_counterattack_route_does_not_require_overload():
    story = json.loads((Path(__file__).resolve().parents[2] / 'assets/theater/story_neon_rebuild_ea2e45dab7.json').read_text(encoding='utf-8'))
    engine = NumericV2Engine.from_mapping(story)
    route = engine.preview_route('mainline_07',
        {'hacking_mastery': 80, 'emotional_sync': 80, 'combat_bond': 40},
        facts={'scene:mainline_07:plan_explained': {'value': True}})
    assert route['target_node_id'] == 'node_branch_69169df41e'


@pytest.mark.asyncio
@pytest.mark.parametrize('ordinary,offered,focused', [(False, False, True), (True, False, False), (False, True, False)])
async def test_ending_prompt_scope_keeps_ordinary_and_pending_invitation_contracts(tmp_path, ordinary, offered, focused):
    from utils.tokenize import count_tokens
    from services.theater.numeric_v2_budget import numeric_v2_actor_budget

    engine = _engine(ordinary=ordinary)
    runtime = NumericV2Runtime(engine, tmp_path)
    stored = await runtime.start_session(session_id='ending-scope', catgirl_binding=_binding(), opening_performance=_opening())
    if offered:
        from tests.unit.test_theater_numeric_v2_pending_offer import _commit
        stored = await _commit(runtime, stored, '我们回去休息吧，好吗？', offer=True)
    session = stored.session
    messages = _build_messages(engine, session, '谢谢。', recent_ledger_events=stored.ledger_events)
    assert ('你是本轮结局判定器' in messages[0].content) is focused
    assert sum(count_tokens(m.content) for m in messages) <= numeric_v2_actor_budget(session.actor_budget_profile)['evaluator_input_max_tokens']
    # Both prompts still use the same parser fields and separate author plans
    # from actual performances; a natural ending does not fabricate acceptance.
    assert 'scene_complete' in messages[0].content
    assert 'transition_intent' in messages[0].content
    if focused:
        parsed = _parse_output(json.dumps({'scene_complete': True, 'natural_ending_ready': True,
            'transition_intent': 'unclear', 'metric_changes': {}}), engine, '谢谢。', session)
        assert parsed.natural_ending_ready is True
        assert parsed.transition_intent == 'unclear'
    elif offered:
        assert 'pending_transition' in prompt_payload(messages[1].content)


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
    payload = prompt_payload(messages[1].content)
    assert payload['transition']['natural_ending'] is True
    assert '不声称玩家接受了未提出的邀请' in messages[0].content
    # Same-turn authorization includes delivery of the final action and response, preserving unknown success and player decision boundaries.
    assert '先在来源回应完成玩家已授权的最后互动' in messages[0].content
    # Natural-ending authorization cannot turn agreed future execution into facts through environmental narration.
    compact = _turn_messages(engine, session, outcome, '谢谢。', '温和。', '小葵', '你')
    assert '不能把约定的未来行动写成已经执行' in compact[0].content
    # A silent source and a speaking target can coexist; the model must generate from these two distinct fields.
    assert 'target_performance遵守target_dialogue_policy' in compact[0].content
    evaluator_messages = _build_messages(engine, session, '谢谢。')
    assert '不必等玩家再说一句' in evaluator_messages[0].content
    # The sequential contract retains unknown outcomes and real risk thresholds, independent of the old repeated paragraph wording.
    assert '未知成败、真实风险' in evaluator_messages[0].content
    data = prompt_payload(evaluator_messages[1].content)
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
