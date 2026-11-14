"""A fresh source must not hide an older target replay during formal transition."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from services.theater.numeric_v2_actor import NumericV2Actor
from services.theater.numeric_v2_actor_output import NumericV2ActorOutputError
from services.theater.numeric_v2_runtime import TurnRequestV2
from services.theater.numeric_v2_workflow import _generate_actor_turn_with_output_retry
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_prompt_contract import _session, _story_state_at


# The failed bread R12 retry changed its source and target action, but copied
# both complete R8 dialogue sentences. Keep that actual shape without a local trace dependency.
OLD_DIALOGUE = (
    '看你眉头锁着，是在纠结去哪个城市？\n'
    '路是你自己选的，我不懂那些学校的好坏，但要是想聊聊利弊，我这儿有热茶，也有时间。'
)
OLD_TARGET = '（停下手中的笔，目光落在桌上散开的资料上）' + OLD_DIALOGUE
REPLAY_TARGET = '（目光从张阿姨留下的报名表移开，重新落在桌上散开的资料上）' + OLD_DIALOGUE
NEW_SOURCE = (
    '（轻轻合上账本，将笔搁在一旁）资料收好就行，不用急着整理完。'
    '张阿姨马上就到，咱们边喝茶边听她说，省得你分心。'
)


async def generate(monkeypatch, *, source=NEW_SOURCE, target=REPLAY_TARGET,
                   previous=OLD_TARGET, visit='start:r0'):
    engine = _engine()
    base = _session(engine)
    session = replace(base, revision=2, story_state=_story_state_at(base, 2), node_turn_count=2,
        performance_history=(
            {'revision': 1, 'from_node_id': 'start', 'to_node_id': 'start',
             'input_text': '先看看这些资料。',
             'timeline_projection': {'scene_scope': {'visit_id': visit}},
             'segments': [
                 {'phase': 'source_response', 'performance': ''},
                 {'phase': 'transition_bridge', 'scene_narration': '雨声渐弱。'},
                 {'phase': 'target_opening', 'performance': previous},
             ]},
            {'revision': 2, 'from_node_id': 'start', 'to_node_id': 'start',
             'input_text': '先停一停。', 'performance': '（看向门口）来客已经到门边了。',
             'timeline_projection': {'scene_scope': {'visit_id': 'start:r0'}}},
        ))
    player_input = '（先把资料收好）好，我们一起听听她怎么说。'
    outcome = engine.resolve_turn(session, TurnRequestV2('transition_replay', 2, player_input), (),
                                  scene_complete=True, natural_ending_ready=True)
    actor = NumericV2Actor(object())

    async def invoke(_messages, **_kwargs):
        return {'source_performance': source,
                'bridge_scene_narration': '来客将活动报名表放在柜台上。',
                'target_scene_narration': '柜台上放着活动报名表。',
                'target_performance': target, 'suggested_inputs': []}

    monkeypatch.setattr(actor, '_invoke', invoke)
    return await actor.generate_turn(engine=engine, session=session, outcome=outcome,
                                      player_input=player_input, character_profile='温和克制。')


@pytest.mark.asyncio
@pytest.mark.parametrize('source', ['', NEW_SOURCE])
async def test_older_target_copy_is_rejected_with_target_guard_even_after_new_source(monkeypatch, source):
    with pytest.raises(NumericV2ActorOutputError) as raised:
        await generate(monkeypatch, source=source)
    assert raised.value.repetition_guard == 'transition_target'


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['new_target', 'short_phrase', 'different_visit'])
async def test_earlier_target_guard_keeps_existing_scope_and_confidence(monkeypatch, case):
    kwargs = {'source': ''}
    if case == 'new_target':
        kwargs['target'] = '（看着报名表）先把活动安排听完整，再决定是否参加。'
    elif case == 'short_phrase':
        kwargs.update(target='（抬头）好，走吧。', previous='（低头）好，走吧。')
    else:
        kwargs['visit'] = 'start:r1'
    result = await generate(monkeypatch, **kwargs)
    assert result['segments'][0]['performance'] == ''
    assert result['segments'][2]['performance'] == kwargs.get('target', REPLAY_TARGET)


@pytest.mark.asyncio
@pytest.mark.parametrize(('guard', 'field'), [
    ('transition_target', 'target_performance'),
    ('transition_source', 'source_performance'),
    ('earlier_session', 'source_performance'),
])
async def test_transition_repeat_hint_targets_guard_without_forcing_source_or_bridge(guard, field):
    class RetryActor:
        def __init__(self):
            self.hints = []

        async def generate_turn(self, **kwargs):
            self.hints.append(kwargs.get('retry_hint', ''))
            if len(self.hints) == 1:
                error = NumericV2ActorOutputError('numeric_v2_actor_repeated_output')
                error.repetition_guard = guard
                raise error
            return {'source_performance': '', 'target_performance': '新的目标回应。'}

    actor = RetryActor()
    outcome = SimpleNamespace(ledger_event={'from_node_id': 'start', 'to_node_id': 'next'})
    result = await _generate_actor_turn_with_output_retry(actor, outcome=outcome,
        session=SimpleNamespace(session_id='guard-targeted-retry', revision=11),
        allow_output_retry=False, allow_transition_repeat_retry=True)
    assert len(actor.hints) == 2
    hint = actor.hints[1]
    assert f'重复位置：{field}' in hint
    assert 'source_performance 可为空字符串' in hint
    assert '全新的简短来源回应' not in hint
    assert '引入一个当前事实支持的变化' not in hint
    assert result['source_performance'] == ''
