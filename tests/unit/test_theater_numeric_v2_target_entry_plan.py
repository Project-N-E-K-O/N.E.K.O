"""Formal target staging stays an intact author plan beside actual delivered evidence."""

from copy import deepcopy
import json

import pytest

from services.theater import numeric_v2_actor as actor
from services.theater.numeric_v2_cast import NumericV2CastProjection
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding


ACTUAL = '密封匣已经交还给你，封条完好；我的双手空着，尚未打开它。'
PLAN = {
    'catgirl_state': '女主坐在工作桌旁，双手捧着茶杯。',
    'player_state': '男主坐在猫娘身旁，已经打开密封匣。',
    'environment_state': '环境中的工作桌上已经倒好了茶，密封匣里的物品已经取出。',
    'continuity_from_previous': ['仅当玩家实际打开密封匣后，才承接里面的内容。',
                                 '作者预期两人在桌旁休息；不得据此补写玩家的决定。'],
    'scene_boundaries': ['不得在未经玩家许可时打开密封匣，也不得取出其中物品。'],
}
PROFILE = '温和谨慎，有自己的主见，常先听清对方的意思再回应。'


async def _case(tmp_path, *, mode='initiate', restricted=False, absent=False):
    story = deepcopy(_engine(ordinary=mode != 'natural').story)
    target_id = 'ending_leave' if mode == 'natural' else 'middle'
    source = next(n for n in story['nodes'] if n['id'] == 'start')
    target = next(n for n in story['nodes'] if n['id'] == target_id)
    contract = {'cognition_state': 'normal', 'memory_state': 'available',
                'self_reference_mode': 'persona_allowed', 'persona_scope': 'full',
                'dialogue_policy': 'optional',
                'assertable_self_facts': ['我负责自己的工具，不能据此取得玩家物品。'],
                'allowed_behaviors': ['可查看外部封条，但未经玩家许可不得开匣。'],
                'forbidden_behaviors': ['不得替玩家决定是否打开密封匣。']}
    source['story_beat'].update(
        opening_scene='两人站在门内，密封匣仍然封着。',
        catgirl_situation='猫娘刚刚接触过密封匣。',
        character_state={'catgirl_state': '女主抱着密封匣。', 'player_state': '男主尚未接回匣子。',
                         'environment_state': '环境仍是门内的等候区。',
                         'continuity_from_previous': [], 'scene_boundaries': []},
        acting_contract=deepcopy(contract))
    target['story_beat'].update(opening_scene='猫娘站在工作桌旁，桌边有空椅子。',
        catgirl_situation='猫娘站在桌旁，等待玩家自己的选择。', acting_contract=deepcopy(contract))
    if not absent:
        target['story_beat']['character_state'] = deepcopy(PLAN)
    else:
        target['story_beat'].pop('character_state', None)
    if restricted:
        target['story_beat']['acting_contract'].update(cognition_state='limited', memory_state='partial',
            self_reference_mode='system_neutral', persona_scope='style_only')
    for route in source['route_gates']:
        route['transition_contract'].update(
            reason='当前互动已收住，进入工作桌旁的目标处境。',
            bridge_scene_narration='两人来到工作桌旁。', must_deliver=[],
            must_preserve=['密封匣的实际归属与封条状态承接已演出内容。'])
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='target-entry-plan', catgirl_binding=_binding(),
        opening_performance={'performance': '先在门内商量。', 'suggested_inputs': []})
    previous = runtime.prepare_turn(current, TurnRequestV2('previous', 0, '先把匣子交还给我。'), ())
    previous, performance = engine.finalize_transition_offer_state(previous,
        {'performance': ACTUAL + ('要一起去工作桌旁吗？' if mode == 'accept' else
                                 '门内通道通往工作桌旁，可以过去。'), 'suggested_inputs': []},
        new_offer=mode == 'accept')
    current = await runtime.commit_turn(previous, performance)
    turn = TurnRequestV2('move', current.session.revision,
        '谢谢，这次就到这里。' if mode == 'natural' else '好，我们现在去工作桌旁。')
    outcome = runtime.prepare_turn(current, turn, (),
        transition_intent='unclear' if mode == 'natural' else mode,
        scene_complete=mode == 'natural', natural_ending_ready=mode == 'natural')
    assert outcome.route_status == 'advanced' and outcome.session.current_node_id == target_id
    cast = NumericV2CastProjection.from_story(engine.story, player_name='哥哥', catgirl_name='Lan')
    expected = cast.value(engine.nodes[target_id]['story_beat'].get('character_state'))
    return runtime, current, outcome, turn, expected


def _messages(runtime, current, outcome, turn):
    return actor._turn_messages(runtime.engine, current.session, outcome, turn.message,
        PROFILE, 'Lan', '哥哥', recent_ledger_events=current.ledger_events)


def _data(messages):
    content = messages[1].content
    return prompt_payload(content)


def _assert_actor_entry_plan(system, actual, expected):
    assert actual == {key: value for key, value in expected.items() if key != 'scene_boundaries'}
    for boundary in expected.get('scene_boundaries', []):
        assert '目标幕：' + boundary in system


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['accept', 'initiate', 'natural'])
@pytest.mark.parametrize('restricted', [False, True], ids=['normal', 'limited-target'])
async def test_runtime_formal_entry_plan_keeps_every_field_and_existing_knowledge_gate(tmp_path, mode, restricted):
    runtime, current, outcome, turn, expected = await _case(tmp_path, mode=mode, restricted=restricted)
    before = deepcopy((runtime.engine.story, current, outcome, turn))
    messages = _messages(runtime, current, outcome, turn)
    data = _data(messages)
    assert 'target_character_state' not in data['acting_context']
    _assert_actor_entry_plan(messages[0].content, data['transition']['target_scene']['author_entry_plan'], expected)
    assert len(expected['continuity_from_previous']) == 2
    assert ACTUAL in json.dumps(data['recent_context'], ensure_ascii=False)
    assert PLAN['player_state'] not in json.dumps(data['recent_context'], ensure_ascii=False)
    assert data['transition']['target_scene']['opening_situation'] == '猫娘站在工作桌旁，桌边有空椅子。'
    assert expected['scene_boundaries'][0] in messages[0].content
    role = data['acting_context']
    for key in ('acting_contract', 'target_acting_contract'):
        assert role[key]['allowed_behaviors'] == ['可查看外部封条，但未经玩家许可不得开匣。']
        assert role[key]['assertable_self_facts'] == ['我负责自己的工具，不能据此取得玩家物品。']
    assert ('story_identity' in role) is not restricted
    assert ('story_role_context' in role) is not restricted
    assert role['target_acting_contract']['memory_state'] == ('partial' if restricted else 'available')
    assert role['target_dialogue_policy'] == 'optional'
    assert 'relationship_control' in role and 'target_relationship_control' in role
    assert data['player_input'] == turn.message
    assert data['transition']['transition_intent'] == ('unclear' if mode == 'natural' else mode)
    assert (runtime.engine.story, current, outcome, turn) == before


@pytest.mark.asyncio
async def test_absent_target_state_is_omitted_without_creating_an_empty_plan(tmp_path):
    runtime, current, outcome, turn, _ = await _case(tmp_path, absent=True)
    data = _data(_messages(runtime, current, outcome, turn))
    assert 'target_character_state' not in data['acting_context']
    assert 'author_entry_plan' not in data['transition']['target_scene']


@pytest.mark.asyncio
async def test_plan_transport_does_not_rewrite_canonical_history_or_cold_restore(tmp_path):
    runtime, current, outcome, turn, expected = await _case(tmp_path)
    original_story = deepcopy(runtime.engine.story)
    original_state = current.session.to_dict()
    canonical_file = runtime.store._path(current.session.session_id).read_bytes()
    messages = _messages(runtime, current, outcome, turn)
    _assert_actor_entry_plan(messages[0].content, _data(messages)['transition']['target_scene']['author_entry_plan'], expected)
    assert runtime.store._path(current.session.session_id).read_bytes() == canonical_file
    assert current.session.to_dict() == original_state
    performance = runtime.engine.finalize_transition_performance(outcome, {
        'source_performance': '', 'bridge_scene_narration': '两人来到工作桌旁。',
        'target_scene_narration': '工作桌边有空椅子，密封匣仍然封着。',
        'target_performance': '（站在桌旁，双手空着）匣子仍由你保管。', 'suggested_inputs': [],
    }, target_opening='猫娘站在工作桌旁。', source_dialogue_policy='optional', target_dialogue_policy='optional')
    committed = await runtime.commit_turn(outcome, performance)
    restored = await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id)
    fork = await runtime.fork_session_for_test(current.session.session_id,
        session_id='target-entry-plan-fork', through_revision=committed.session.revision)
    assert restored == committed
    assert fork.session.performance_history == committed.session.performance_history
    assert committed.session.performance_history[-1]['segments'][2]['performance'] == '（站在桌旁，双手空着）匣子仍由你保管。'
    assert runtime.engine.story == original_story


@pytest.mark.asyncio
async def test_fixed_context_budget_retains_complete_plan_or_rejects(tmp_path):
    runtime, current, outcome, turn, expected = await _case(tmp_path)
    messages = _messages(runtime, current, outcome, turn)
    data = _data(messages)
    _assert_actor_entry_plan(messages[0].content, data['transition']['target_scene']['author_entry_plan'], expected)
    # A single latest history row cannot be evicted to make the author plan fit;
    # the existing fixed-context check must reject rather than clip its conditions.
    data['recent_context'] = data['recent_context'][-1:]
    prefix = messages[1].content[:messages[1].content.index('{')]
    full_tokens = actor._prompt_token_counter(messages[0].content)(
        prefix + actor.render_prompt_context(data))
    before = deepcopy(data)
    fitted = actor._fit_turn_prompt_data(system_prompt=messages[0].content,
        human_prefix=prefix, data=data, max_tokens=full_tokens)
    _assert_actor_entry_plan(messages[0].content, fitted['transition']['target_scene']['author_entry_plan'], expected)
    with pytest.raises(actor.NumericV2ActorError, match='fixed_context_budget_exceeded'):
        actor._fit_turn_prompt_data(system_prompt=messages[0].content,
            human_prefix=prefix, data=data, max_tokens=full_tokens - 1)
    assert data == before
