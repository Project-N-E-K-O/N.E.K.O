"""Display completion comes from committed author text, not model assertions."""

from dataclasses import replace
import json

import pytest

from services.theater.numeric_v2 import NumericV2CompileError
from services.theater.numeric_v2_actor import _completion_fact_prompt_context, _turn_messages
from services.theater.numeric_v2_cast import NumericV2CastProjection
from services.theater.numeric_v2_evaluator import _build_transition_judge_messages, _pending_completion_facts
from services.theater.numeric_v2_fixed_narration import apply_triggers
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding


OPENING = {'scene_narration': '桌边。', 'performance': '（看向你）我们先看看。', 'suggested_inputs': []}
BODY = {'performance': '（拆开信封）已经打开了。', 'suggested_inputs': [], 'transition_offered': False}
TEXT = '家书0042：\n愿你一路平安。'


def _story(*, entry=False, mixed=False):
    story = numeric_v2_story()
    node = story['nodes'][0]
    node['story_beat']['fixed_narrations'] = [{
        'id': 'document', 'text': TEXT,
        'trigger': {'type': 'entry'} if entry else {'type': 'condition', 'condition': '信封已经打开。'},
        'after': [], 'required_before_exit': True,
    }]
    node['completion_contract'] = {'all': [{'fixed_narration_id': 'document'}]}
    if mixed:
        key = 'scene:start:agreed'
        story['fact_contract'] = {'facts': {key: {'value_type': 'bool', 'visibility': 'public',
                                                 'description': '女主已同意合作。'}}}
        node['completion_contract']['all'].append({'key': key, 'equals': True})
    return story


@pytest.mark.parametrize('requirement', [
    {'fixed_narration_id': 'missing'}, {'fixed_narration_id': []},
    {'fixed_narration_id': 'document', 'equals': True},
    {'fixed_narration_id': 'document', 'key': 'anything'},
])
def test_invalid_display_requirement_rejected(requirement):
    story = _story()
    story['nodes'][0]['completion_contract']['all'] = [requirement]
    with pytest.raises(NumericV2CompileError):
        NumericV2Engine.from_mapping(story)


def test_display_requirement_must_be_unique_and_local():
    story = _story()
    story['nodes'][0]['completion_contract']['all'] *= 2
    with pytest.raises(NumericV2CompileError):
        NumericV2Engine.from_mapping(story)
    story = _story()
    story['nodes'][1]['story_beat']['fixed_narrations'] = story['nodes'][0]['story_beat'].pop('fixed_narrations')
    with pytest.raises(NumericV2CompileError):
        NumericV2Engine.from_mapping(story)


@pytest.mark.parametrize('entry', [False, True])
@pytest.mark.parametrize('mixed', [False, True])
def test_runtime_and_actor_agree_and_review_does_not_judge_display(entry, mixed):
    engine = NumericV2Engine.from_mapping(_story(entry=entry, mixed=mixed))
    session = engine.create_session(session_id='display', catgirl_binding=_binding(), opening_performance=OPENING)
    # A claim in ordinary prose does not prove the original document was displayed.
    session = replace(session, opening_performance={**session.opening_performance, 'performance': '全文已经展示过了。'})
    assert engine.completion_contract_satisfied(session) is (entry and not mixed)
    pending = _pending_completion_facts(engine, session)
    assert len(pending) == int(mixed)
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '我看到了。'), ())
    context = _completion_fact_prompt_context(engine, engine.nodes['start'], outcome,
        cast=NumericV2CastProjection('', '', '玩家', '角色'))
    assert context['status'] == ('satisfied' if entry and not mixed else 'pending')
    assert context['fixed_narrations'] == {'required': 1, 'displayed': int(entry)}
    assert len(context['all']) == int(mixed)
    messages, _ = _build_transition_judge_messages(engine, session, actor_performance=BODY, player_input='请打开。')
    payload = json.loads(messages[1].content[messages[1].content.index('{'):])
    assert bool(payload.get('pending_completion_facts')) is mixed
    assert ('fact_candidates' in messages[0].content) is mixed
    actor = '\n'.join(m.content for m in _turn_messages(engine, session, outcome, '请打开。', '温和。', 'Lan', '你'))
    assert ('结构化事实候选合同' in actor) is mixed
    if not entry:
        assert TEXT not in actor.replace('\\n', '\n')


@pytest.mark.asyncio
async def test_delivery_is_atomic_and_recovery_and_fork_preserve_completion(tmp_path, monkeypatch):
    engine = NumericV2Engine.from_mapping(_story())
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='atomic', catgirl_binding=_binding(), opening_performance=OPENING)
    turn = TurnRequestV2('one', 0, '请打开。')
    outcome = runtime.prepare_turn(current, turn, (), scene_complete=True, natural_ending_ready=True)
    assert outcome.session.current_node_id == 'start'
    performance = apply_triggers(engine.nodes['start'], current.session, BODY,
        ({'id': 'document', 'evidence': '拆开信封'},), turn.message, known=False)
    assert engine.completion_contract_satisfied(outcome.session) is False
    original_commit = runtime.store.commit

    async def fail(*args, **kwargs):
        raise OSError('simulated storage failure')

    monkeypatch.setattr(runtime.store, 'commit', fail)
    with pytest.raises(OSError):
        await runtime.commit_turn(outcome, performance)
    assert await runtime.restore_session('atomic') == current
    monkeypatch.setattr(runtime.store, 'commit', original_commit)
    saved = await runtime.commit_turn(outcome, performance)
    assert engine.completion_contract_satisfied(saved.session) is True
    assert await NumericV2Runtime(engine, tmp_path).restore_session('atomic') == saved
    forked = await runtime.fork_session_for_test('atomic', session_id='after', through_revision=1)
    assert engine.completion_contract_satisfied(forked.session) is True
    before = await runtime.fork_session_for_test('atomic', session_id='before', through_revision=0)
    assert engine.completion_contract_satisfied(before.session) is False
    assert apply_triggers(engine.nodes['start'], saved.session, BODY,
        ({'id': 'document', 'evidence': '拆开信封'},), '', known=False) == BODY
    ending = runtime.prepare_turn(saved, TurnRequestV2('two', 1, '到这里吧。'), (),
        scene_complete=True, natural_ending_ready=True)
    assert ending.session.status == 'ended'


def test_sdk_round_trip_preserves_display_completion():
    from theater_workshop.host import InProcessPackageGateway
    from theater_workshop.sdk.numeric_v2 import NumericV2Compiler

    source = _story(mixed=True)
    compiled = NumericV2Compiler(InProcessPackageGateway()).compile(source)
    assert compiled.story['nodes'][0]['completion_contract'] == source['nodes'][0]['completion_contract']
    assert source == _story(mixed=True)


def test_actor_fact_cannot_satisfy_display_condition():
    from services.theater.numeric_v2_runtime import NumericV2RuntimeError

    engine = NumericV2Engine.from_mapping(_story(mixed=True))
    session = engine.create_session(session_id='forged', catgirl_binding=_binding(), opening_performance=OPENING)
    outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '请打开。'), ())
    with pytest.raises(NumericV2RuntimeError):
        engine.finalize_actor_fact_candidates(session, outcome,
            candidates=[{'key': 'document', 'value': True, 'evidence_quote': '全文展示了。'}],
            evidence_sources={'actor_performance': '全文展示了。'})
    assert engine.completion_contract_satisfied(outcome.session) is False


@pytest.mark.asyncio
@pytest.mark.parametrize('genre', ['letter', 'science'])
async def test_workflow_commits_display_without_fact_approval(tmp_path, monkeypatch, genre):
    from services.theater import numeric_v2_workflow as workflow
    from services.theater.numeric_v2_evaluator import NumericV2EvaluationResult, NumericV2TransitionOfferReview
    from services.theater.numeric_v2_options import default_options

    story = _story()
    body = dict(BODY)
    if genre == 'science':
        piece = story['nodes'][0]['story_beat']['fixed_narrations'][0]
        piece.update(text='系统记录：\n信号已恢复。', trigger={'type': 'condition', 'condition': '读取器已接通。'})
        body['performance'] = '（接通读取器）可以读取记录了。'
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(session_id='workflow', catgirl_binding=_binding(), opening_performance=OPENING)
    calls = {'actor': 0, 'review': 0}

    async def options():
        return {**default_options(), 'review': True}

    async def evaluate(self, **kwargs):
        return NumericV2EvaluationResult((), False)

    async def invoke(self, messages, **kwargs):
        calls['actor'] += 1
        assert kwargs['fact_candidates_expected'] is False
        return dict(body)

    async def review(self, **kwargs):
        calls['review'] += 1
        return NumericV2TransitionOfferReview(False, False, (), (),
            fixed_narration_triggers=({'id': 'document', 'evidence': body['performance']},))

    monkeypatch.setattr(workflow, 'aload_theater_module_options', options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(workflow.NumericV2Actor, '_invoke', invoke)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('one', 0, '请帮我打开。'), ensure_current_binding=lambda _: _binding())
    assert calls == {'actor': 1, 'review': 1}
    assert result.stored.ledger_events[-1].get('fact_operations', []) == []
    assert runtime.engine.completion_contract_satisfied(result.stored.session) is True
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session('workflow') == result.stored
