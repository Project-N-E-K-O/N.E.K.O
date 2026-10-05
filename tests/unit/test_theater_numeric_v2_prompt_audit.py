"""Prompt inputs preserve rule exceptions, speakers, phase boundaries and evidence."""

import json

import pytest

from services.theater import numeric_v2_actor as actor, numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_cast import NumericV2CastProjection
from services.theater.numeric_v2_context import project_contract_boundaries
from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_prompt_contract import _session
from tests.unit.test_theater_numeric_v2_prompt_permissions import _contract


def _data(messages):
    text = messages[1].content
    return json.loads(text[text.index('{'):])


@pytest.mark.parametrize('prop', ['家书', '晶片'])
def test_full_boundary_exception_and_last_rule_reach_actor_and_reviewer(prop):
    rule = (f'不得移动{prop}；该规则仅适用于当前区域的公开展示阶段，'
            + '旁观者仍在观看，保留完整展示环境与操作条件。' * 10
            + '当保管人明确要求归还时允许交还。')
    beat = {'must_not_happen': [rule] + [f'第{i}项独立禁令。' for i in range(18)]}
    story = numeric_v2_story()
    story['nodes'][0]['story_beat'].update(beat)
    engine = NumericV2Engine.from_mapping(story)
    cast = NumericV2CastProjection.from_story(engine.story, catgirl_name='猫娘', player_name='玩家')
    expected = list(project_contract_boundaries(beat))
    assert actor._suggestion_hard_boundaries(cast, beat) == expected
    assert evaluator._actor_fact_boundaries(beat) == expected
    relationship = '只允许已建立的关系表达；' * 40 + '玩家可以拒绝。'
    assert relationship in actor._suggestion_hard_boundaries(cast, beat, relationship_boundary=relationship)


def test_opening_only_references_fields_it_receives_and_parser_accepts():
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    messages = actor._opening_messages(engine, '温和', '猫娘', '玩家', True, max_tokens=10000)
    text = '\n'.join(m.content for m in messages)
    assert 'scene_update' not in text
    assert 'opening_deliverables' not in text
    assert 'opening_scene' in _data(messages)['current_story_beat']
    assert 'scene_narration' in messages[0].content


@pytest.mark.parametrize('background,identity', [
    ('旧宅整理契约的共同背景。', '受托核验契约的档案员。'),
    ('轨道站校准天线的共同背景。', '负责天线校准的工程师。'),
])
@pytest.mark.parametrize('restricted', [False, True])
def test_ordinary_actor_keeps_only_role_permitted_stable_premise(background, identity, restricted):
    story = numeric_v2_story()
    story['intro']['background'] = background
    story['intro']['player_identity'] = '林舟，' + identity
    if restricted:
        story['nodes'][0]['story_beat']['acting_contract'] = {
            **_contract('required'), 'memory_state': 'empty', 'self_reference_mode': 'system_neutral',
            'persona_scope': 'style_only',
        }
    engine = NumericV2Engine.from_mapping(story)
    session = _session(engine)
    outcome = engine.resolve_turn(session, TurnRequestV2('audit', 0, '继续聊。'), ())
    messages = actor._turn_messages(engine, session, outcome, '继续聊。', '温和', '猫娘', '玩家')
    data = _data(messages)
    assert list(data) == ['role', 'current_scene', 'story_so_far', 'pacing', 'next_scene', 'player_input']
    assert (background in data['role']) is not restricted
    assert (identity in data['role']) is not restricted


@pytest.mark.parametrize('prop', ['家书', '晶片'])
def test_flat_transition_suggestions_include_full_target_and_exclude_consumed_source(prop):
    target = {'scene_narration': f'{prop}已经放回玩家手中。', 'performance': '已经交还了。'}
    flat = {'target_scene_narration': target['scene_narration'], 'target_performance': target['performance'],
            'source_performance': '现在准备交接。', 'bridge_scene_narration': '中途的经过。'}
    messages = actor._suggestion_fill_messages(catgirl_name='猫娘', performance=flat,
        player_input='把东西还我。', after_scene_change=True, max_tokens=10000)
    data = _data(messages)
    assert data['player_input'] == ''
    assert data['visible_performance'] == actor._suggestion_source_text(
        {'segments': [{'phase': 'target_opening', **target}]})
    assert '现在准备交接' not in data['visible_performance']


def test_transition_cannot_silently_drop_latest_record_to_fit():
    with pytest.raises(actor.NumericV2ActorError, match='budget_exceeded'):
        actor._fit_turn_prompt_data(system_prompt='规则', human_prefix='数据', max_tokens=80,
            data={'recent_context': [{'revision': 8, 'performance': '最新必要原文。' * 100}]})


@pytest.mark.parametrize('trim', [False, True])
@pytest.mark.parametrize('text', ['家书已交还。', '晶片已交还。'])
def test_transition_evidence_dedup_tracks_final_history(trim, text):
    evidence = [
        {'revision': 1, 'source': 'performance', 'text': text, 'current_visit': True},
        {'revision': 2, 'source': 'player_input', 'text': text, 'current_visit': True},
        {'revision': 0, 'source': 'performance', 'text': text, 'current_visit': False},
    ]
    history = [{'revision': 1, 'player_input': '旧回合。' * (400 if trim else 1), 'performance': text},
               {'revision': 2, 'player_input': '继续。', 'performance': '请继续说。'}]
    data = actor._fit_turn_prompt_data(system_prompt='规则', human_prefix='数据', max_tokens=230 if trim else 3000,
        data={'recent_context': history, 'history_evidence': evidence, 'player_input': '本轮输入。'})
    remaining = data['history_evidence']
    assert (evidence[0] in remaining) is trim
    assert evidence[1] in remaining and evidence[2] in remaining
    assert data['recent_context'][-1] == history[-1]
    assert len(evidence) == 3


def test_narrow_contract_does_not_claim_unprovided_history():
    messages = evaluator._build_contract_check_messages(required=['不得擅自归还。'],
        candidate_text='继续拿着。', player_input='先不动。')
    assert '未提供历史' in messages[0].content
    assert '不能仅凭缺项' in messages[0].content


def test_evaluator_empty_fact_contract_does_not_request_fact_extraction():
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    messages = evaluator._build_messages(engine, _session(engine), '继续聊。')
    assert _data(messages)['current_story_beat']['fact_contract']['facts'] == {}
    assert 'fact_candidates 必须为空数组' in messages[0].content
    assert 'subject、action、object、result' not in messages[0].content
