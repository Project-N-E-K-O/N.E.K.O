"""Prompt inputs preserve rule exceptions, speakers, phase boundaries and evidence."""


import json
from copy import deepcopy

import pytest

from services.theater import numeric_v2_actor as actor, numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_cast import NumericV2CastProjection
from services.theater.numeric_v2_context import project_contract_boundaries
from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_prompt_contract import _session
from tests.unit.test_theater_numeric_v2_prompt_permissions import _contract


def _data(messages):
    text = messages[1].content
    return prompt_payload(text)


def test_compact_turn_explains_dialogue_policy_without_repeating_it():
    system = actor._system_prompt(catgirl_name='小雨', phase='turn')
    assert system.count('dialogue_policy=required 必须有括号外对白') == 1
    assert 'forbidden 只能写动作，optional 两者均可' in system


@pytest.mark.parametrize('phase', ['opening', 'turn', 'transition_compact'])
def test_player_recommendations_cannot_be_unfinished_sentence_templates(phase):
    system = actor._system_prompt(catgirl_name='小雨', phase=phase)
    assert system.count('不输出模板标签或待续半句') == 1


def test_program_entry_and_optional_speech_have_one_target_author():
    from tests.unit.test_theater_numeric_v2_dramatization_contract import _context, FIXED

    _, _, _, messages, _ = _context()
    system = messages[0].content
    assert '原文已完整交付开场时填空' in system
    assert '纯环境原文不替代作者要求的角色发问' in system
    assert 'required仍须交付原文未承担的必要对白' in system
    assert '没有必要新反应时target_performance填空' not in system
    assert '固定旁白是程序独立播放的内容' not in system
    assert json.dumps(FIXED, ensure_ascii=False) in system


def test_actor_keeps_narrated_player_state_and_own_changes_in_playback_order():
    from config.prompts.prompts_theater import NUMERIC_V2_CONTEXT_READING_INSTRUCTION

    assert '未引述的场景旁白或作者原文中‘你’指玩家' in NUMERIC_V2_CONTEXT_READING_INSTRUCTION
    ordinary = actor._system_prompt(catgirl_name='小雨')
    assert '身体状态变化及其确定的直接可见结果，只在 performance 中按发生顺序交付' in ordinary
    assert '失去意识、休眠或失声后不再说话' in ordinary
    assert '允许行为是条件成立后的可选权限，不是已执行事实或逐项任务' in ordinary
    assert '不因性格、职业习惯或担心新增客观危险、手续或资格条件' in ordinary
    assert '邀请仅写此字段，不在performance重复' in ordinary
    assert 'NPC、玩家的动作和对白不能写入你的动作括号' in ordinary


def test_formal_entry_dedup_keeps_condition_fragment_private_and_gated():
    from services.theater.numeric_v2_fixed_narration import actor_note
    from tests.unit.test_theater_numeric_v2_fixed_narration import _piece

    node = {'id': 'mixed', 'story_beat': {'fixed_narrations': [
        _piece('entry', '公开告示。', entry=True), _piece('later', '尚未拆开的信。'),
    ]}}
    note = actor_note(node, None, {}, False, include_instructions=False)
    assert '公开告示。' in note and '尚未拆开的信。' not in note
    assert '条件片段在本轮实际触发动作及复核后才展示' in note
    assert '当前尚有离幕前必显片段' in note


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
    expected = list(project_contract_boundaries(beat, explicit_prohibitions=True))
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


@pytest.mark.parametrize('full_system', [False, True])
def test_opening_sends_complete_boundaries_once_without_dropping_unsent_rules(monkeypatch, full_system):
    story = numeric_v2_story()
    beat = story['nodes'][0]['story_beat']
    scene_rule = '不得移动窗边的封存箱；保管人当面提出归还时，允许交还。'
    role_rule = '不得拆开柜台上的封条；所有者明确授权后才允许查看里面。'
    state_rule = '不得把尚在柜台的旧信写成已经交给玩家。'
    beat['must_not_happen'] = [scene_rule]
    beat['acting_contract'] = {**_contract('optional'), 'forbidden_behaviors': [role_rule]}
    beat['character_state'] = {
        'catgirl_state': '女主站在柜台旁。', 'player_state': '男主在门边。',
        'environment_state': '环境中旧信放在柜台上。',
        'continuity_from_previous': [], 'scene_boundaries': [state_rule],
    }
    engine = NumericV2Engine.from_mapping(story)
    before = deepcopy(engine.story)
    if not full_system:
        monkeypatch.setattr(actor, '_hard_boundary_system_instruction',
                            lambda boundaries, **kw: next(text for text in boundaries if scene_rule in text))
    messages = actor._opening_messages(engine, '温和', '猫娘', '玩家', max_tokens=10000)
    system, human = (m.content for m in messages)
    assert system.count(scene_rule) == 1
    assert scene_rule not in human
    for rule in (role_rule, state_rule):
        if full_system:
            assert system.count(rule) == 1
            assert rule not in human
        else:
            assert rule in human
    assert _data(messages)['acting_context']['acting_contract']['dialogue_policy'] == 'optional'
    assert engine.story == before


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
    assert list(data) == ["role", "current_scene", "pacing", "next_scene", "story_so_far", "player_input"]
    assert (background in data['current_scene']) is not restricted
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


def test_formal_author_prohibitions_are_sent_once_with_their_phase():
    rule = '不得把尚未交接的晶片写成已经交还；保管人实际要求归还时允许交还。'
    story = numeric_v2_story()
    story['nodes'][0]['story_beat']['acting_contract'] = {
        **_contract('required'), 'forbidden_behaviors': [rule],
    }
    engine = NumericV2Engine.from_mapping(story)
    session = _session(engine)
    outcome = engine.resolve_turn(session, TurnRequestV2('dedup', 0, '就到这里吧。'), (),
                                  scene_complete=True, natural_ending_ready=True)
    messages = actor._turn_messages(engine, session, outcome, '就到这里吧。', '温和', '猫娘', '玩家')
    assert '来源幕：演出角色禁止行为：' + rule in messages[0].content
    assert rule not in json.dumps(_data(messages), ensure_ascii=False)


def test_route_definition_is_retained_when_only_a_later_alternative_needs_its_description():
    from tests.unit.test_theater_numeric_v2_fact_routes import fact_route_story, choice, KEY

    engine = NumericV2Engine.from_mapping(fact_route_story())
    session = _session(engine)
    outcome = engine.resolve_turn(session, TurnRequestV2('choose', 0, '我选官道。'), (), fact_operations=choice('官道'))
    cast = NumericV2CastProjection.from_story(engine.story, catgirl_name='猫娘', player_name='玩家')
    routes = actor._route_fact_prompt_context(engine, 'start', outcome.session, cast=cast)
    first, second = (route['facts'][0] for route in routes)
    assert first['key'] == second['key'] == KEY
    assert first['satisfied'] is True and second['satisfied'] is False
    assert 'description' not in first
    assert second['description'] == engine.fact_contract[KEY]['description']
    assert 'value_type' in first and 'value_type' not in second


def test_actor_does_not_send_empty_narrative_ledger_or_scene_event_log():
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = _session(engine)
    outcome = engine.resolve_turn(session, TurnRequestV2('lean', 0, '你好。'), ())
    messages = actor._turn_messages(engine, session, outcome, '你好。', '温和', '猫娘', '玩家')
    data = _data(messages)
    assert 'narrative_state（' not in data['current_scene']
    assert 'Runtime 已提交的场景事件' not in data['story_so_far']
    assert actor._COMMITTED_NARRATIVE_STATE_RULE not in messages[0].content


@pytest.mark.parametrize('simple', [False, True])
def test_fitting_actor_request_bounds_supplemental_retrieval_instead_of_filling_the_limit(simple):
    evidence = [{'revision': 1, 'source': 'performance', 'current_visit': False, 'text': '晶片仍由保管员持有。'}]
    def bounded_expansion(retained, within_prompt):
        oversized = {'revision': 2, 'source': 'performance', 'current_visit': False,
                     'text': '保管员逐项说明档案馆里的日常安排。' * 300}
        assert not within_prompt([*retained, oversized])
        return retained
    if simple:
        result = actor._fit_simple_turn_prompt_data(system_prompt='规则', human_prefix='数据',
            data={'player_input': '看看。'}, history_rows=[], max_tokens=10000,
            evidence_records=evidence, evidence_expander=bounded_expansion)
        assert evidence[0]['text'] in result['story_so_far']
    else:
        result = actor._fit_turn_prompt_data(system_prompt='规则', human_prefix='数据',
            data={'player_input': '看看。', 'history_evidence': evidence}, max_tokens=10000,
            evidence_expander=bounded_expansion)
        assert result['history_evidence'] == evidence
