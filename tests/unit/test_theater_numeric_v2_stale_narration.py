"""Cross-scene paragraph copies must not reset the new scene or reverse a route."""

from types import SimpleNamespace

import pytest

from services.theater.numeric_v2_actor import NumericV2Actor, NumericV2ActorOutputError, _stale_transition_narration
from services.theater.numeric_v2_runtime import TurnRequestV2
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding


TEXT = '午后的旧工作室里，刚刚通电的机械躯体睁开双眼，门外的暴雨敲打着铁皮屋顶。' * 5


def _check(candidate, *, node='care', history=(), opening=TEXT, ending=False):
    return _stale_transition_narration(candidate,
        SimpleNamespace(current_node_id='market', opening_performance={'scene_narration': opening},
                        performance_history=history), initial_node_id='awakening', target_node_id=node,
                        target_is_ending=ending)


def test_new_opening_cannot_replay_initial_awakening_even_without_turn_history():
    assert _check({'segments': [{'phase': 'target_opening', 'scene_narration': TEXT}]}) == 'transition_target_narration'


@pytest.mark.parametrize('source,target,expected', [
    ('workshop', 'market', 'transition_bridge_narration'),
    ('market', 'care', ''),
    (None, None, ''),
])
def test_bridge_compares_route_identity_and_preserves_unknown_legacy_history(source, target, expected):
    history = ({'from_node_id': source, 'to_node_id': target,
                'segments': [{'phase': 'transition_bridge', 'scene_narration': TEXT}]},)
    assert _check({'segments': [{'phase': 'transition_bridge', 'scene_narration': TEXT}]}, history=history) == expected


@pytest.mark.parametrize('node,text', [('awakening', TEXT), ('care', TEXT + '如今设备早已关闭。'), ('care', '屋里很安静。')])
def test_same_scene_partial_matches_and_short_common_scenery_remain_legal(node, text):
    assert _check({'segments': [{'phase': 'target_opening', 'scene_narration': text}]}, node=node) == ''


@pytest.mark.parametrize('ending,expected', [(False, ''), (True, 'transition_target_narration')])
def test_ending_cannot_hide_a_long_old_scene_behind_a_new_first_sentence(ending, expected):
    old = '玩家昨夜留下的半块黑面包仍在桌边，维修架上放着一小罐用途标识清晰的机油；这两样东西都不是为同步验收1023准备的补能选项，她的安全能源摄入方式仍未确认。昨夜出现异常的维修终端继续断电并保持物理断网，另一台独立联网的店务终端安静地亮着。'
    text = '数月后，窗外的地下街重新亮起灯光。' + old
    assert _check({'segments': [{'phase': 'target_opening', 'scene_narration': text}]},
                  opening='清晨，阳光照进工作室。' + old, ending=ending) == expected


def test_ending_still_allows_short_shared_scenery_and_changed_sentences():
    assert _check({'segments': [{'phase': 'target_opening', 'scene_narration':
                  '数月后，屋里很安静。她已经修复。'}]}, opening='清晨，屋里很安静。', ending=True) == ''


@pytest.mark.asyncio
@pytest.mark.parametrize('prefix', ['', '数月后，地下街重新亮起了灯光。'])
async def test_actor_routes_stale_opening_through_existing_output_retry(monkeypatch, prefix):
    engine = _engine()
    session = engine.create_session(session_id='stale-opening', catgirl_binding=_binding(),
        opening_performance={'scene_narration': TEXT, 'performance': '你来了。'})
    outcome = engine.resolve_turn(session, TurnRequestV2('end', 0, '谢谢。'), (),
                                  scene_complete=True, natural_ending_ready=True)

    async def invoke(*args, **kwargs):
        return {'source_performance': '', 'bridge_scene_narration': '雨停了。',
                'target_scene_narration': prefix + TEXT, 'target_performance': '愿你一路平安。', 'suggested_inputs': []}

    monkeypatch.setattr(NumericV2Actor, '_invoke', invoke)
    with pytest.raises(NumericV2ActorOutputError, match='repeated_output') as caught:
        await NumericV2Actor(object()).generate_turn(engine=engine, session=session, outcome=outcome,
            player_input='谢谢。', character_profile='温和。')
    assert caught.value.repetition_guard == 'transition_target_narration'
