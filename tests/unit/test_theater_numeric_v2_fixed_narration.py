"""Exercise literal delivery, trigger evidence and persistence as one transaction."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from services.theater.numeric_v2 import NumericV2CompileError
from services.theater.numeric_v2_actor import _opening_messages, _turn_messages
from services.theater.numeric_v2_actor_output import _parse_output, NumericV2ActorOutputError
from services.theater.numeric_v2_archive import _performance_memory_parts
from services.theater.numeric_v2_context import history_evidence
from services.theater.numeric_v2_evaluator import (
    NumericV2EvaluationResult, NumericV2TransitionOfferReview,
    NumericV2EvaluatorOutputError,
    NUMERIC_V2_DISPUTE_JUDGE_TIMEOUT_SECONDS,
    NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS,
    _build_transition_judge_messages, _parse_transition_judge_output,
)
from services.theater.numeric_v2_fixed_narration import add_entry, apply_triggers, displayed_ids, review_candidates, validate_delivery
from services.theater.numeric_v2_performance import performance_content_blocks
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from services.theater import numeric_v2_workflow as workflow
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


REPORT = '[REPORT]\n损坏率：87.4%\n序列号00-Aoi（不可改写）'
LOG = '0042\n“永远优先应答你的呼叫。”\n[EOF]'
ACTION = '（将铭牌放入读取器）已经接好了。'
LONG_ENTRY = ('远处的钟声穿过走廊。柜台上的公开记录逐项显示失物的来源、当前保管位置和此前移交的时间，'
              '没有任何一栏记载物主已经回来，也没有将仍然未知的部分写成得到确认的结果。'
              '负责保管的店员收起最后一页登记表，将封存的资料重新放回抽屉并锁好。'
              '她向两人说明今天的交接已经结束，后续若有新的消息会按留下的联系地址通知。')
OPENING = {'scene_narration': '修理台亮起。', 'performance': '（看向你）你是谁？', 'suggested_inputs': []}


def _piece(piece_id, text, *, entry=False, after=(), required=True):
    return {'id': piece_id, 'text': text,
            'trigger': {'type': 'entry'} if entry else {'type': 'condition', 'condition': '铭牌已经放入读取器。'},
            'after': list(after), 'required_before_exit': required}


def _engine():
    story = numeric_v2_story()
    story['nodes'][0]['story_beat']['fixed_narrations'] = [
        _piece('report', REPORT, entry=True), _piece('log', LOG, after=['report'])]
    return NumericV2Engine.from_mapping(story)


def _claims():
    return ({'id': 'log', 'evidence': '将铭牌放入读取器'},)


@pytest.mark.asyncio
@pytest.mark.parametrize('narration,expected', [
    ('工作台亮起。' + REPORT, '工作台亮起。'),
    (REPORT + '绿灯保持稳定。', '绿灯保持稳定。'),
    (REPORT, REPORT),
    ('屏幕上写着' + REPORT[:10] + '……', '屏幕上写着' + REPORT[:10] + '……'),
])
async def test_new_actor_entry_keeps_verbatim_original_once_without_rewriting_partial_echo(monkeypatch, narration, expected):
    from services.theater.numeric_v2_actor import NumericV2Actor
    from tests.unit.test_theater_numeric_v2_natural_ending import _engine as ending_engine

    story = deepcopy(ending_engine().story)
    for ending in story['nodes']:
        if ending['type'] == 'ending':
            ending['story_beat']['fixed_narrations'] = [_piece('final-report', REPORT, entry=True)]
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id='entry-echo', catgirl_binding=_binding(),
        opening_performance={'performance': '开始吧。'})
    outcome = engine.resolve_turn(session, TurnRequestV2('end', 0, '谢谢。'), (),
        scene_complete=True, natural_ending_ready=True)
    candidate = {'source_performance': '', 'bridge_scene_narration': '雨停了。',
                 'target_scene_narration': narration, 'target_performance': '事情到此结束了。',
                 'suggested_inputs': []}
    original = deepcopy(candidate)

    async def invoke(*args, **kwargs):
        return deepcopy(candidate)

    monkeypatch.setattr(NumericV2Actor, '_invoke', invoke)
    result = await NumericV2Actor(object()).generate_turn(engine=engine, session=session,
        outcome=outcome, player_input='谢谢。', character_profile='温和。')
    target = result['segments'][2]
    assert target['scene_narration'] == expected
    assert target['fixed_narrations'][0]['text'] == REPORT
    assert '\n'.join(block['text'] for block in performance_content_blocks(result)).count(REPORT) == 1
    assert candidate == original


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['scene_narration', 'performance'])
async def test_actor_rejects_long_entry_replay_hidden_behind_a_changed_sentence(monkeypatch, field):
    from services.theater.numeric_v2_actor import NumericV2Actor
    from tests.unit.test_theater_numeric_v2_natural_ending import _engine as ending_engine

    story = deepcopy(ending_engine().story)
    for node in story['nodes']:
        if node['type'] == 'ending':
            node['story_beat']['fixed_narrations'] = [_piece('entry-result', LONG_ENTRY, entry=True)]
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id='entry-replay', catgirl_binding=_binding(),
        opening_performance={'performance': '开始吧。'})
    outcome = engine.resolve_turn(session, TurnRequestV2('end', 0, '谢谢。'), (),
        scene_complete=True, natural_ending_ready=True)
    candidate = dict(source_performance='', bridge_scene_narration='两人走入前厅。',
                     target_scene_narration='前厅的木门开着。', target_performance='事情到此结束。',
                     suggested_inputs=[])
    if field == 'scene_narration':
        candidate['target_scene_narration'] = LONG_ENTRY.replace('远处的钟声穿过走廊。', '窗外的灯光映着走廊。')
    else:
        candidate['target_performance'] = '（看向你）' + LONG_ENTRY

    async def invoke(*args, **kwargs):
        return deepcopy(candidate)

    monkeypatch.setattr(NumericV2Actor, '_invoke', invoke)
    with pytest.raises(NumericV2ActorOutputError, match='repeated_output') as caught:
        await NumericV2Actor(object()).generate_turn(engine=engine, session=session,
            outcome=outcome, player_input='谢谢。', character_profile='温和。')
    assert caught.value.repetition_guard == 'fixed_narration'


@pytest.mark.parametrize('case', ['short', 'after', 'exact_scene'])
def test_entry_replay_guard_preserves_short_quotes_conditions_and_legacy_scene_dedup(case):
    from services.theater.numeric_v2_actor import _replays_entry_original

    text = '今天交接已经结束。' if case == 'short' else LONG_ENTRY
    value = dict(scene_narration=text, performance='交接结束了。',
                 fixed_narrations=[dict(text=text, position='after' if case == 'after' else 'before')])
    if case == 'short':
        value['performance'] = text
    before = deepcopy(value)
    assert not _replays_entry_original(value)
    assert value == before


@pytest.mark.parametrize('field', ['performance', 'scene_narration'])
def test_entry_guard_catches_a_complete_authored_invitation_below_long_passage_threshold(field):
    from services.theater.numeric_v2_actor import _replays_entry_original

    quote = '传说末班车离站后，在旧站台能看到这盏灯的绿光指向哪里。今晚一起去旧站台看看，等末班车开走再出发，好吗？'
    value = {'performance': '我听见末班车了。', 'scene_narration': '记录已经摊开。',
             'fixed_narrations': [{'text': '她举起信号灯：“' + quote + '”', 'position': 'before'}]}
    value[field] = '（举起信号灯）' + quote if field == 'performance' else '她再次说：“' + quote + '”'
    assert _replays_entry_original(value)
    value['fixed_narrations'][0]['position'] = 'after'
    assert not _replays_entry_original(value)


@pytest.mark.parametrize('field', ['performance', 'scene_narration'])
def test_entry_guard_rejects_a_complete_original_inside_a_prefixed_action_or_narration(field):
    from services.theater.numeric_v2_actor import _replays_entry_original

    original = '日记最后一条写着：列车长当晚没有随首批人员撤离，而是返回故障车厢，疏散滞留乘客。记录到此中断，后面没有续页。旧值班记录只留下撤离名单，没有记下这次返程救援；这些文字没有交代列车长此后的下落。'
    value = {'performance': '他确实返回救人了。', 'scene_narration': '桌上有一本日记。',
             'fixed_narrations': [{'text': original, 'position': 'before'}]}
    value[field] = '（盯着' + original + '）' if field == 'performance' else '她盯着' + original
    assert _replays_entry_original(value)


def test_entry_guard_allows_a_short_dated_record_with_ellipsis_as_a_partial_quote():
    from services.theater.numeric_v2_actor import _replays_entry_original

    quote = '10月14日，K404次列车……异常……'
    original = '记录只能读清“' + quote + '”，其余被墨迹遮盖，原因未知。她提出稍后再到旧站台观察。'
    assert not _replays_entry_original({'performance': '（指向纸页）记录上是“' + quote + '”，涂黑处的意思仍未知。',
        'fixed_narrations': [{'text': original, 'position': 'before'}]})


@pytest.mark.parametrize('mutation', [
    lambda rows: rows.append(deepcopy(rows[0])),
    lambda rows: rows[0].update(after=['log']),
    lambda rows: rows[1].update(after=['missing']),
    lambda rows: rows[1].update(required_before_exit='true'),
    lambda rows: rows[1].update(text='{{unknown_name}}'),
    lambda rows: rows[1].update(text='太长的日志。' * 3000),
    lambda rows: rows[1].update(trigger={'type': 'turn_number', 'number': 3}),
])
def test_compiler_rejects_invalid_fixed_narration_contract(mutation):
    story = deepcopy(_engine().story)
    mutation(story['nodes'][0]['story_beat']['fixed_narrations'])
    with pytest.raises(NumericV2CompileError):
        NumericV2Engine.from_mapping(story)


def test_actor_cannot_supply_runtime_owned_text():
    with pytest.raises(NumericV2ActorOutputError):
        _parse_output(json.dumps({'performance': ACTION, 'suggested_inputs': [], 'transition_offered': False,
                                 'fixed_narrations': [{'id': 'log', 'text': 'forged'}]}), opening_required=False)


@pytest.mark.parametrize('filename,node_id,piece_id,terms', [
    ('story_ccc0763441e3.json', 'mainline_03', 'lamp_legend', ('10月14日', 'K404', '原因未知', '末班车离站后', '今晚一起去旧站台')),
    ('story_ccc0763441e3.json', 'mainline_05', 'last_diary_entry', ('返回故障车厢', '没有续页', '下落')),
    ('story_neon_rebuild_ea2e45dab7.json', 'mainline_07', 'station_encircled', ('实际包围已经形成', '通信', '物理搜索')),
    ('story_neon_rebuild_ea2e45dab7.json', 'mainline_03', 'instinctive_isolation', ('自主接入', '连接建立', '隔离完成', '安全锁定', '围观者')),
])
def test_builtin_key_evidence_and_current_crisis_are_visible_before_character_reply(filename, node_id, piece_id, terms):
    story = json.loads((Path(__file__).resolve().parents[2] / 'assets/theater' / filename).read_text(encoding='utf-8'))
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id='authored-entry', catgirl_binding=_binding(), opening_performance=OPENING)
    session = replace(session, current_node_id=node_id,
        opening_performance=add_entry(engine.nodes[node_id], OPENING, _binding(), True))
    pieces = session.opening_performance['fixed_narrations']
    assert [(piece['id'], piece['position']) for piece in pieces] == [(piece_id, 'before')]
    assert all(term in pieces[0]['text'] for term in terms)
    assert (node_id, piece_id) in displayed_ids(session)
    assert session.story_state['facts'].get('scene:mainline_07:overload_executed') is None


def test_neon_ambush_entry_state_matches_delivered_result():
    story = json.loads((Path(__file__).resolve().parents[2] / 'assets/theater/story_neon_rebuild_ea2e45dab7.json').read_text(encoding='utf-8'))
    beat = next(node['story_beat'] for node in story['nodes'] if node['id'] == 'mainline_06')
    assert '已徒手砸毁' in beat['catgirl_situation']
    assert beat['character_state']['catgirl_state'] == beat['catgirl_situation']
    assert '核心升温' in beat['catgirl_situation']


def test_public_payload_keeps_literal_text_but_hides_internal_metadata():
    from main_routers.numeric_theater_router import _public_performance

    engine = _engine()
    session = engine.create_session(session_id='public-fixed', catgirl_binding=_binding(), opening_performance=OPENING)
    public = _public_performance({'segments': [session.opening_performance]})
    assert public['segments'][0]['fixed_narrations'] == [{'text': REPORT, 'position': 'before'}]
    assert session.opening_performance['fixed_narrations'][0]['node_id'] == 'start'


@pytest.mark.parametrize('transitions,node_id,piece_id,terms', [
    (1, 'mainline_02', 'morning_dispatch', ('义肢维修委托', '储能足够', '规格尚未确认')),
    (3, 'mainline_04', 'care_prepared', ('你因高热', '水杯', '不想看你这么难受', '可以先休息', '留在这里')),
    (4, 'mainline_05', 'tracking_log', ('天穹集团', '原型机', '持续追踪', '远程熔断')),
    (5, 'mainline_06', 'blockade_ambush', ('无人机', '右小腿', '擦伤', '追踪已经解除', '旧地铁站', '要一起撤过去吗')),
])
def test_builtin_neon_log_is_delivered_on_entry_without_model_review(transitions, node_id, piece_id, terms):
    story = json.loads((Path(__file__).resolve().parents[2] / 'assets/theater/story_neon_rebuild_ea2e45dab7.json').read_text(encoding='utf-8'))
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id='neon-log', catgirl_binding=_binding(), opening_performance=OPENING)
    for index in range(transitions):
        outcome = engine.resolve_turn(session, TurnRequestV2(f'enter-{index}', session.revision, '继续。'), (),
                                      scene_complete=True, transition_intent='initiate', condition_narrations_enabled=False)
        session = outcome.session
        if index < transitions - 1:
            prior = engine.finalize_transition_performance(outcome, _candidate(), target_opening='场景继续。')
            session = replace(session, performance_history=(*session.performance_history, prior))
    assert session.current_node_id == node_id
    assert engine.completion_contract_satisfied(session) is False
    result = engine.finalize_transition_performance(outcome, _candidate(), target_opening='监控镜头转向窗户。')
    target = result['segments'][2]
    pieces = target['fixed_narrations']
    expected_ids = ['tracking_scene', piece_id] if node_id == 'mainline_05' else [piece_id]
    assert [piece['id'] for piece in pieces] == expected_ids
    piece = next(piece for piece in pieces if piece['id'] == piece_id)
    assert piece['position'] == 'before'
    if node_id == 'mainline_05':
        assert _binding()['catgirl_name'] in piece['text']
    assert '{{catgirl_name}}' not in piece['text']
    assert all(term in piece['text'] for term in terms)
    assert '\n'.join(block['text'] for block in performance_content_blocks(result)).count(piece['text']) == 1
    shown = replace(session, performance_history=(*session.performance_history, result))
    assert engine.completion_contract_satisfied(shown) is True


@pytest.mark.asyncio
async def test_target_entry_is_mandatory_and_survives_committed_transition(tmp_path):
    story = numeric_v2_story()
    for node in story['nodes'][1:]:
        node['story_beat']['fixed_narrations'] = [_piece('entry', REPORT, entry=True)]
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='target-entry', catgirl_binding=_binding(), opening_performance=OPENING)
    outcome = runtime.prepare_turn(current, TurnRequestV2('one', 0, '就到这里吧。'), (),
                                   scene_complete=True, natural_ending_ready=True)
    assert outcome.session.current_node_id != current.session.current_node_id
    parsed = _parse_output(json.dumps(_candidate()), transition_required=True)
    performance = engine.finalize_transition_performance(outcome, parsed, target_opening='修理台亮起。')
    missing = deepcopy(performance)
    missing['segments'][2].pop('fixed_narrations')
    with pytest.raises(ValueError, match='entry_missing'):
        await runtime.commit_turn(outcome, missing)
    assert (await runtime.restore_session(current.session.session_id)).session.revision == 0
    saved = await runtime.commit_turn(outcome, performance)
    assert await runtime.restore_session(current.session.session_id) == saved
    assert performance['segments'][2]['fixed_narrations'][0]['text'] == REPORT


@pytest.mark.parametrize('policy,entry,accepted', [
    ('optional', True, True), ('forbidden', True, True),
    ('required', True, False), ('optional', False, False),
])
def test_empty_target_requires_program_entry_and_silence_permission(policy, entry, accepted):
    candidate = {'source_performance': '走吧。', 'bridge_scene_narration': '两人来到柜台旁。',
                 'target_scene_narration': '', 'target_performance': '', 'suggested_inputs': []}
    if accepted:
        parsed = _parse_output(json.dumps(candidate), transition_required=True,
                              target_dialogue_policy=policy, entry_narration=entry)
        assert parsed['target_performance'] == parsed['target_scene_narration'] == ''
    else:
        with pytest.raises(NumericV2ActorOutputError):
            _parse_output(json.dumps(candidate), transition_required=True,
                          target_dialogue_policy=policy, entry_narration=entry)


@pytest.mark.asyncio
async def test_program_entry_can_deliver_an_ordinary_target_without_duplicate_model_acting(tmp_path):
    from tests.unit.test_theater_numeric_v2_natural_ending import _engine as ending_engine

    story = deepcopy(ending_engine(ordinary=True).story)
    target = next(n for n in story['nodes'] if n['id'] == 'middle')
    target['story_beat']['acting_contract'] = {
        'dialogue_policy': 'optional', 'cognition_state': 'normal', 'memory_state': 'available',
        'self_reference_mode': 'persona_allowed', 'persona_scope': 'full',
        'allowed_behaviors': [], 'forbidden_behaviors': [],
    }
    target['story_beat']['fixed_narrations'] = [_piece('entry', REPORT, entry=True)]
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='silent-entry', catgirl_binding=_binding(), opening_performance=OPENING)
    outcome = runtime.prepare_turn(current, TurnRequestV2('enter', 0, '走吧。'), (),
                                   scene_complete=True, transition_intent='initiate')
    assert outcome.session.status == 'active' and outcome.session.current_node_id == 'middle'
    candidate = {'source_performance': '走吧。', 'bridge_scene_narration': '两人来到柜台旁。',
                 'target_scene_narration': '', 'target_performance': '', 'suggested_inputs': ['这份报告是什么意思？']}
    performance = engine.finalize_transition_performance(outcome, candidate, target_opening='', target_dialogue_policy='optional')
    missing = deepcopy(performance)
    missing['segments'][2].pop('fixed_narrations')
    with pytest.raises(ValueError, match='entry_missing'):
        await runtime.commit_turn(outcome, missing)
    saved = await runtime.commit_turn(outcome, performance)
    restored = await NumericV2Runtime(engine, tmp_path).restore_session('silent-entry')
    assert restored == saved
    assert [b['text'] for b in performance_content_blocks(performance)] == ['走吧。', '两人来到柜台旁。', REPORT]


@pytest.mark.asyncio
async def test_workflow_assembles_target_entry_once_in_runtime(tmp_path, monkeypatch):
    from services.theater import numeric_v2_runtime as runtime_module

    story = numeric_v2_story()
    for node in story['nodes'][1:]:
        node['story_beat']['fixed_narrations'] = [_piece('entry', REPORT, entry=True)]
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(session_id='entry-once', catgirl_binding=_binding(),
                                          opening_performance=OPENING)
    entries = []
    add_entry = runtime_module.add_entry

    def track_entry(node, *args, **kwargs):
        entries.append(node['id'])
        return add_entry(node, *args, **kwargs)

    async def evaluate(self, **kwargs):
        return NumericV2EvaluationResult((), True, natural_ending_ready=True)

    async def invoke(self, messages, **kwargs):
        assert kwargs['transition_required']
        return _parse_output(json.dumps(_candidate()), transition_required=True)

    async def review(self, **kwargs):
        target = kwargs['actor_performance']['segments'][2]
        assert target['fixed_narrations'][0]['text'] == REPORT
        return NumericV2TransitionOfferReview(False, False, (), ())

    monkeypatch.setattr(runtime_module, 'add_entry', track_entry)
    # If the orchestration layer retains an old alias, record its assembly calls to catch duplicate producers.
    if hasattr(workflow, 'add_entry'):
        monkeypatch.setattr(workflow, 'add_entry', track_entry)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(workflow.NumericV2Actor, '_invoke', invoke)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('one', 0, '就到这里吧。'), ensure_current_binding=lambda _: _binding())
    # Commit validation rebuilds the source opening; entry into the destination act is assembled only once in this Runtime finalizer.
    assert entries.count(result.stored.session.current_node_id) == 1
    assert result.stored.session.revision == len(result.stored.ledger_events) == 1
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session('entry-once') == result.stored


def test_name_placeholders_do_not_rewrite_serials_or_nested_values():
    engine = _engine()
    engine.story['initial_state']['player_address_known'] = False
    engine.nodes['start']['story_beat']['fixed_narrations'][0]['text'] = '{{catgirl_name}}／{{player_name}}／00-Aoi'
    binding = {**_binding(), 'catgirl_name': '新猫娘{{player_name}}'}
    session = engine.create_session(session_id='names', catgirl_binding=binding, opening_performance=OPENING)
    assert session.opening_performance['fixed_narrations'][0]['text'] == '新猫娘{{player_name}}／你／00-Aoi'
    renamed = replace(session, catgirl_binding={**binding, 'catgirl_name': '又一个名字'})
    # Recovery uses the names captured with the delivered text, never today's name.
    validate_delivery(engine.story, renamed.opening_performance)


def test_prompt_hides_untriggered_text_and_preserves_delivered_text():
    engine = _engine()
    session = engine.create_session(session_id='prompts', catgirl_binding=_binding(), opening_performance=OPENING)
    opening = '\n'.join(m.content for m in _opening_messages(engine, '温和。', 'Lan', '你', False, max_tokens=10000))
    entry_data = opening.split('======以下为程序自动播放的入幕原文（不输出）======\n', 1)[1].split('\n======以上为', 1)[0]
    assert json.loads(entry_data)['text'] == REPORT and LOG not in opening
    review_messages = _build_transition_judge_messages(engine, session, actor_performance={'performance': ACTION}, player_input='帮我接上。')[0]
    packed = '\n'.join(m.content for m in review_messages)
    assert 'fixed_narration_candidates' in packed and '铭牌已经放入读取器' in packed
    assert '固定五字段' not in packed
    assert '"fixed_narration_triggers":[]' in packed
    assert LOG not in packed
    engine.story['intro']['catgirl_name'] = '原来的姓名'
    engine.nodes['start']['story_beat']['fixed_narrations'][1]['trigger']['condition'] = '原来的姓名实际拿起铭牌。'
    renamed = replace(session, catgirl_binding={**session.catgirl_binding, 'catgirl_name': '新角色'})
    messages = _build_transition_judge_messages(engine, renamed, actor_performance={'performance': ACTION}, player_input='帮我接上。')[0]
    payload = json.loads(messages[1].content.split('：', 1)[1])
    assert payload['fixed_narration_candidates'][0]['condition'] == '新角色实际拿起铭牌。'
    opening = '\n'.join(m.content for m in _opening_messages(engine, '温和。', '新角色', '你', False, max_tokens=10000))
    assert '新角色实际拿起铭牌。' in opening


@pytest.mark.parametrize('phase', ['opening', 'turn', 'transition'])
def test_actor_prompt_is_independent_of_fixed_piece_ids(phase):
    story = deepcopy(_engine().story)
    if phase == 'transition':
        pieces = story['nodes'][0]['story_beat'].pop('fixed_narrations')
        middle = deepcopy(story['nodes'][0])
        middle['id'] = 'middle'
        middle['story_beat']['fixed_narrations'] = pieces
        for gate in middle['route_gates']:
            gate['id'] = 'middle_' + gate['id']
        story['nodes'].append(middle)
        for gate in story['nodes'][0]['route_gates']:
            gate['target_node_id'] = 'middle'
    renamed = deepcopy(story)
    ids = {'report': 'internal_report_7a9', 'log': 'internal_log_2b8'}
    for node in renamed['nodes']:
        for piece in node['story_beat'].get('fixed_narrations', []):
            piece['id'] = ids[piece['id']]
            piece['after'] = [ids[key] for key in piece['after']]
    prompts = []
    for raw in (story, renamed):
        engine = NumericV2Engine.from_mapping(raw)
        if phase == 'opening':
            messages = _opening_messages(engine, '温和。', 'Lan', '你', True, max_tokens=10000)
        else:
            session = engine.create_session(session_id='actor-ids', catgirl_binding=_binding(), opening_performance=OPENING)
            session = replace(session, transition_offered=phase == 'transition')
            outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '帮我接上。'), (),
                                          transition_intent='accept' if phase == 'transition' else 'unclear')
            assert (outcome.session.current_node_id != session.current_node_id) == (phase == 'transition')
            messages = _turn_messages(engine, session, outcome, '帮我接上。', '温和。', 'Lan', '你')
        prompts.append([message.content for message in messages])
    assert prompts[0] == prompts[1]
    text = '\n'.join(prompts[1])
    assert all(key not in text for key in ids.values())
    assert '铭牌已经放入读取器。' in text
    assert REPORT in text.replace('\\n', '\n') and LOG not in text.replace('\\n', '\n')


def test_fixed_dependencies_still_belong_to_review_and_delivery():
    story = deepcopy(_engine().story)
    # Both conditional pieces remain pending; triggering the child alone must not bypass its parent.
    story['nodes'][0]['story_beat']['fixed_narrations'][0] = _piece('report', REPORT)
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id='fixed-dependencies', catgirl_binding=_binding(), opening_performance=OPENING)
    assert [row['after'] for row in review_candidates(engine.nodes['start'], session)] == [[], ['report']]
    messages, _ = _build_transition_judge_messages(engine, session, actor_performance={'performance': ACTION}, player_input='帮我接上。')
    payload = json.loads(messages[1].content.split('：', 1)[1])
    assert [row['after'] for row in payload['fixed_narration_candidates']] == [[], ['0']]
    raw = {'performance': ACTION}
    assert apply_triggers(engine.nodes['start'], session, raw, _claims(), '帮我接上。', known=True) == raw
    delivered = apply_triggers(engine.nodes['start'], session, raw,
        ({'id': 'report', 'evidence': '将铭牌放入读取器'}, *_claims()), '帮我接上。', known=True)
    assert [piece['id'] for piece in delivered['fixed_narrations']] == ['report', 'log']
    validate_delivery(engine.story, delivered, session=session)


def test_actor_receive_trigger_requires_player_handoff_direction():
    """Conditions requiring the catgirl to receive something cannot be established by candidate prose alone; explicit player handover can still trigger them."""  # noqa: DOCSTRING_CJK

    story = deepcopy(_engine().story)
    story['nodes'][0]['story_beat']['fixed_narrations'][1]['trigger']['condition'] = '小葵接过旧照片。'
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id='handoff-direction', catgirl_binding=_binding(), opening_performance=OPENING)
    raw = {'performance': '（接过旧照片）谢谢。'}
    claim = ({'id': 'log', 'evidence': '接过旧照片'},)
    assert apply_triggers(engine.nodes['start'], session, raw, claim, '我伸手拿起旧照片。', known=True) == raw
    delivered = apply_triggers(engine.nodes['start'], session, raw, claim, '我把旧照片递给你。', known=True)
    assert [piece['id'] for piece in delivered['fixed_narrations']] == ['log']


@pytest.mark.asyncio
async def test_literal_order_cold_recovery_fork_and_exit_gate(tmp_path):
    engine = _engine()
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='fixed', catgirl_binding=_binding(), opening_performance=OPENING)
    opening_blocks = performance_content_blocks(current.session.opening_performance)
    assert [b['text'] for b in opening_blocks][:2] == ['修理台亮起。', REPORT]
    request = TurnRequestV2('one', 0, '帮我接上。')
    outcome = runtime.prepare_turn(current, request, (), scene_complete=True, natural_ending_ready=True)
    assert outcome.session.status == 'active'
    raw = {'performance': ACTION, 'suggested_inputs': [], 'transition_offered': False}
    performance = apply_triggers(engine.nodes['start'], current.session, raw, _claims(), request.message, known=False)
    assert performance_content_blocks(performance)[-1] == {'type': 'narration', 'text': LOG}
    assert all(LOG not in line['text'] for line in performance_content_blocks(performance) if line['type'] == 'dialogue')
    committed = await runtime.commit_turn(outcome, performance)
    restored = await NumericV2Runtime(engine, tmp_path).restore_session('fixed')
    assert restored == committed
    assert displayed_ids(restored.session) == {('start', 'report'), ('start', 'log')}
    forked = await runtime.fork_session_for_test('fixed', session_id='fork', through_revision=1)
    assert forked.session.performance_history == restored.session.performance_history
    assert apply_triggers(engine.nodes['start'], restored.session, raw, _claims(), request.message, known=False) == raw
    parts, text = _performance_memory_parts(performance, phase='ordinary')
    assert parts[-1]['kind'] == 'scene_narration' and parts[-1]['text'] == LOG
    assert text.index(ACTION) < text.index(LOG)
    assert LOG in json.dumps(history_evidence(restored.session, '日志0042写了什么？'), ensure_ascii=False).replace('\\n', '\n')
    end = runtime.prepare_turn(restored, TurnRequestV2('two', 1, '从现在开始。'), (), scene_complete=True, natural_ending_ready=True)
    assert end.session.status == 'ended'
    actor_messages = _turn_messages(engine, restored.session, end, '从现在开始。', '温和。', 'Lan', '你', False)
    assert LOG in '\n'.join(m.content for m in actor_messages).replace('\\n', '\n')
    closing = engine.finalize_transition_performance(end, _candidate(), target_opening='记录继续。')
    await runtime.commit_turn(end, closing)
    assert (await NumericV2Runtime(engine, tmp_path).restore_session('fixed')).session.status == 'ended'


@pytest.mark.parametrize('claims', [({'id': 'log', 'evidence': '没有发生的原话'},),
                                  ({'id': 'log', 'evidence': '。'},),
                                  ({'id': 'unknown', 'evidence': '将铭牌放入读取器'},)])
def test_uncited_or_unknown_triggers_do_not_display(claims):
    engine = _engine()
    session = engine.create_session(session_id='evidence', catgirl_binding=_binding(), opening_performance=OPENING)
    raw = {'performance': ACTION}
    assert apply_triggers(engine.nodes['start'], session, raw, claims, '接上。', known=False) == raw


@pytest.mark.asyncio
async def test_forged_literal_is_rejected_before_persistence(tmp_path):
    runtime = NumericV2Runtime(_engine(), tmp_path)
    current = await runtime.start_session(session_id='tamper', catgirl_binding=_binding(), opening_performance=OPENING)
    outcome = runtime.prepare_turn(current, TurnRequestV2('one', 0, '接上。'), ())
    performance = apply_triggers(runtime.engine.nodes['start'], current.session, {'performance': ACTION}, _claims(), '接上。', known=False)
    performance['fixed_narrations'][0]['text'] = '被改写了'
    with pytest.raises(ValueError, match='numeric_fixed_narration_invalid'):
        await runtime.commit_turn(outcome, performance)
    assert await runtime.restore_session('tamper') == current


@pytest.mark.asyncio
@pytest.mark.parametrize('rewrite', [False, True])
async def test_workflow_uses_final_review_and_rollback_keeps_piece_pending(tmp_path, monkeypatch, rewrite):
    runtime = NumericV2Runtime(_engine(), tmp_path)
    current = await runtime.start_session(session_id='workflow', catgirl_binding=_binding(), opening_performance=OPENING)
    calls = {'actor': 0, 'review': 0}

    async def evaluate(self, **kwargs):
        return NumericV2EvaluationResult((), True, natural_ending_ready=True)

    async def actor(self, **kwargs):
        calls['actor'] += 1
        return {'performance': ACTION if calls['actor'] == 1 else '（看着铭牌）还没有接上。', 'suggested_inputs': [], 'transition_offered': False}

    async def review(self, **kwargs):
        calls['review'] += 1
        bad = rewrite and calls['actor'] == 1
        return NumericV2TransitionOfferReview(False, False, ('author_boundary',) if bad else (), (),
            fixed_narration_triggers=_claims() if calls['actor'] == 1 else ())

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', actor)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    kwargs = dict(config_manager=object(), runtime=runtime, current=current,
                  turn=TurnRequestV2('one', 0, '接上。'), ensure_current_binding=lambda _: _binding())
    committed = await workflow.execute_numeric_v2_turn(**kwargs)
    assert (('start', 'log') in displayed_ids(committed.stored.session)) is not rewrite
    assert committed.stored.session.revision == len(committed.stored.ledger_events) == 1
    assert calls['actor'] == (2 if rewrite else 1)
    assert calls['review'] == (2 if rewrite else 1)

    # A different uncommitted session exercises the same storage-failure boundary.
    second = await runtime.start_session(session_id='failure', catgirl_binding=_binding(), opening_performance=OPENING)
    outcome = runtime.prepare_turn(second, TurnRequestV2('retry', 0, '接上。'), ())
    performance = apply_triggers(runtime.engine.nodes['start'], second.session, {'performance': ACTION}, _claims(), '接上。', known=False)
    original_commit = runtime.store.commit

    async def fail(*args, **kwargs):
        raise OSError('injected storage failure')

    monkeypatch.setattr(runtime.store, 'commit', fail)
    with pytest.raises(OSError):
        await runtime.commit_turn(outcome, performance)
    monkeypatch.setattr(runtime.store, 'commit', original_commit)
    assert await runtime.restore_session('failure') == second
    saved = await runtime.commit_turn(outcome, performance)
    assert ('start', 'log') in displayed_ids(saved.session)


def test_review_extension_is_optional_and_does_not_change_other_stories():
    raw = {'offer_present': False, 'valid': False, 'body_violations': [], 'unsafe_suggestion_indexes': []}
    assert _parse_transition_judge_output(json.dumps(raw)).fixed_narration_triggers == ()
    with pytest.raises(NumericV2EvaluatorOutputError):
        _parse_transition_judge_output(json.dumps(raw), fixed_narration_review=True)
    raw['fixed_narration_triggers'] = list(_claims())
    result = _parse_transition_judge_output(json.dumps(raw), fixed_narration_review=True)
    assert result.fixed_narration_triggers == _claims()
    with pytest.raises(NumericV2EvaluatorOutputError):
        _parse_transition_judge_output(json.dumps(raw))


@pytest.mark.asyncio
@pytest.mark.parametrize('piece_count', [0, 1, 8])
@pytest.mark.parametrize('disputed', [False, True])
@pytest.mark.parametrize('evidence', ['铭牌' * 40, '\x01' * 80], ids=['text', 'json-escaping'])
async def test_review_output_fits_all_refs_and_restores_literal_ids(monkeypatch, piece_count, disputed, evidence):
    """Exercise the actual request cap and parser with maximum-length legal IDs."""
    import hashlib
    from types import SimpleNamespace
    import tiktoken
    from services.theater import numeric_v2_evaluator as evaluator

    encoding = tiktoken.get_encoding('o200k_base')
    ids = [hashlib.sha512(str(i).encode()).hexdigest() for i in range(piece_count)]
    story = numeric_v2_story()
    if ids:
        story['nodes'][0]['story_beat']['fixed_narrations'] = [
            _piece(key, '读取完成。') for key in ids]
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id='quote-capacity', catgirl_binding=_binding(), opening_performance=OPENING)
    # Eight 80-token quotations plus their request references must fit the cap.
    assert len(encoding.encode(evidence)) == 80
    payload = {'offer_present': False, 'offer_quote': '', 'valid': False, 'body_violations': [],
               'unsafe_suggestion_indexes': [], 'failure_reason': ''}
    if ids:
        payload['fixed_narration_triggers'] = [{'id': str(index), 'evidence': evidence} for index in range(len(ids))]
    output = encoding.encode(json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
    calls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages):
            calls.append(messages)
            return SimpleNamespace(content=encoding.decode(output[:budget]))

    async def config(_):
        return {'model': 'test', 'base_url': 'http://test.invalid'}

    async def factory(*args, **kwargs):
        nonlocal budget
        budget = kwargs['max_completion_tokens']
        assert kwargs['max_retries'] == 0
        assert kwargs['timeout'] == (
            NUMERIC_V2_DISPUTE_JUDGE_TIMEOUT_SECONDS if disputed
            else NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS
        )
        return Client()

    budget = 0
    monkeypatch.setattr(evaluator, '_model_config', config)
    monkeypatch.setattr(evaluator, 'create_chat_llm_async', factory)
    monkeypatch.setattr(evaluator, 'focus_extra_body', lambda _: {'enable_thinking': True})
    result = await evaluator.NumericV2MetricEvaluator(object()).validate_transition_offer(
        engine=engine, session=session, message='（将铭牌放入读取器）' + evidence,
        actor_performance={'performance': ACTION, 'suggested_inputs': []}, dispute_review=disputed)
    assert len(calls) == 1
    assert [item['id'] for item in result.fixed_narration_triggers] == ids
    assert budget >= len(output)
    if not ids or disputed:
        assert budget == (4096 if disputed else 320)
    else:
        assert 512 <= budget < 4096


@pytest.mark.asyncio
@pytest.mark.parametrize('forgery', ['text', 'bindings', 'condition'])
async def test_opening_rejects_injected_fixed_text_before_storage(tmp_path, forgery):
    engine = _engine()
    opening = engine.create_session(session_id='expected', catgirl_binding=_binding(), opening_performance=OPENING).opening_performance
    if forgery == 'condition':
        opening['fixed_narrations'].append({'node_id': 'start', 'id': 'log', 'text': LOG,
            'bindings': opening['fixed_narrations'][0]['bindings'], 'position': 'after'})
    else:
        opening['fixed_narrations'][0][forgery] = 'forged' if forgery == 'text' else {'catgirl_name': 'Other', 'player_name': '你'}
    with pytest.raises(ValueError, match='numeric_fixed_narration_invalid'):
        await NumericV2Runtime(engine, tmp_path).start_session(session_id='injected', catgirl_binding=_binding(), opening_performance=opening)
    assert not list(tmp_path.rglob('*.json'))


def test_context_preserves_fixed_narration_display_order():
    from services.theater.numeric_v2_context import performance_history_records

    engine = _engine()
    session = engine.create_session(session_id='order', catgirl_binding=_binding(), opening_performance=OPENING)
    record = {'scene_narration': '场景', 'performance': '角色正文', 'fixed_narrations': [
        {'text': '入幕原文\n完整', 'position': 'before'}, {'text': '条件原文\n完整', 'position': 'after'}]}
    session = replace(session, opening_performance=record, performance_history=({'revision': 1, 'segments': [record]},))
    texts = [row['text'] for row in performance_history_records(session)]
    assert texts == ['场景', '入幕原文\n完整', '角色正文', '条件原文\n完整'] * 2


@pytest.mark.asyncio
async def test_generated_opening_replays_with_original_names_after_rename(tmp_path):
    from services.theater.numeric_v2_store import update_numeric_v2_character_bindings

    story = deepcopy(_engine().story)
    story['nodes'][0]['story_beat']['fixed_narrations'][0]['text'] = '{{catgirl_name}}：{{player_name}}'
    engine = NumericV2Engine.from_mapping(story)
    opening = engine.create_session(session_id='original', catgirl_binding=_binding(), opening_performance=OPENING).opening_performance
    runtime = NumericV2Runtime(engine, tmp_path)
    stored = await runtime.start_session(session_id='original', catgirl_binding=_binding(), opening_performance=opening)
    renamed = {**_binding(), 'catgirl_name': '新名字'}
    await update_numeric_v2_character_bindings(tmp_path, character_id=renamed['character_id'], legacy_catgirl_name='Lan', catgirl_binding=renamed)
    restored = await NumericV2Runtime(engine, tmp_path).restore_session('original')
    assert restored.session.opening_performance == stored.session.opening_performance
    assert restored.session.catgirl_binding['catgirl_name'] == '新名字'


@pytest.mark.asyncio
async def test_disabled_review_does_not_let_condition_piece_lock_the_exit(tmp_path):
    """Condition pieces need the review module; with it off they cannot gate exit, and replay keeps that choice."""
    engine = _engine()
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='review-off', catgirl_binding=_binding(),
                                          opening_performance=OPENING)
    gated = runtime.prepare_turn(current, TurnRequestV2('one', 0, '就到这里吧。'), (),
                                 scene_complete=True, natural_ending_ready=True)
    assert gated.session.current_node_id == 'start' and 'condition_narrations_enabled' not in gated.ledger_event
    outcome = runtime.prepare_turn(current, TurnRequestV2('one', 0, '就到这里吧。'), (),
                                   scene_complete=True, natural_ending_ready=True,
                                   condition_narrations_enabled=False)
    assert outcome.session.status == 'ended' and outcome.ledger_event['condition_narrations_enabled'] is False
    performance = engine.finalize_transition_performance(outcome, _candidate(), target_opening='记录继续。')
    committed = await runtime.commit_turn(outcome, performance)
    # Cold replay and test forks must reproduce the recorded gate, not today's default.
    assert await NumericV2Runtime(engine, tmp_path).restore_session('review-off') == committed
    forked = await runtime.fork_session_for_test('review-off', session_id='review-off-fork', through_revision=1)
    assert forked.session.status == 'ended'
    assert forked.ledger_events[0]['condition_narrations_enabled'] is False


@pytest.mark.asyncio
async def test_without_review_leaves_conditional_required_scene(tmp_path, monkeypatch):
    """The no-review path must not soft-lock a scene on conditional required pieces."""
    from services.theater.numeric_v2_options import default_options

    runtime = NumericV2Runtime(_engine(), tmp_path)
    current = await runtime.start_session(session_id='defaults', catgirl_binding=_binding(),
                                          opening_performance=OPENING)

    async def options():
        return {key: key == 'evaluator' for key in default_options()}

    async def evaluate(self, **kwargs):
        return NumericV2EvaluationResult((), True, natural_ending_ready=True)

    async def invoke(self, messages, **kwargs):
        prompt = '\n'.join(str(message.content) for message in messages)
        assert '离幕前必显片段' not in prompt
        return _parse_output(json.dumps(_candidate()), transition_required=kwargs['transition_required'])

    monkeypatch.setattr(workflow, 'aload_theater_module_options', options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_invoke', invoke)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('one', 0, '就到这里吧。'), ensure_current_binding=lambda _: _binding())
    assert result.stored.session.current_node_id != 'start'
    assert result.stored.session.status == 'ended'
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session('defaults') == result.stored
