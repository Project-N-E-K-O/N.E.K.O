"""Source-first generation gets an author task, not an unapproved result."""

from copy import deepcopy
import json

import pytest

from services.theater import numeric_v2_actor as actor, numeric_v2_evaluator as ev
from services.theater import numeric_v2_options as options, numeric_v2_workflow as wf
from services.theater.numeric_v2_actor_output import _parse_output
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_source_delivery_routing import _engine, KEY
from tests.utils.theater_prompt import prompt_payload


CASES = [
    (
        'equipment', '双方已同意把设备送回仓库。',
        '我同意送回仓库。（我搬起设备向仓库走去）',
        '我同意这个去向，我们按这个安排办。', '',
        '不得在本幕搬运设备或进入仓库。',
    ),
    (
        'library', '馆员已当场清点并签收玩家归还的图书。',
        '请馆员清点并签收这些书，拿到回执后我们再离开。',
        '签收回执给你，书已经收好了。', '馆员逐本清点归还图书，在签收单上签名并递出回执。',
        '不得在本幕离开图书馆。',
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize('topic,description,message,body,narration,boundary', CASES)
@pytest.mark.parametrize('approved', [False, True])
async def test_source_task_reaches_real_actor_before_proof_and_atomic_continuation(
    tmp_path, monkeypatch, topic, description, message, body, narration, boundary, approved,
):
    story = deepcopy(_engine().story)
    story['fact_contract']['facts'][KEY]['description'] = description
    story['nodes'][0]['story_beat']['must_not_happen'] = [boundary]
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id=topic, catgirl_binding=_binding(),
        opening_performance={'performance': '先把眼前的事情商量清楚。', 'suggested_inputs': []})
    before = runtime.store._path(topic).read_bytes()
    calls = []
    overstated = '玩家已完成搬运并到达后幕地点'

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), False,
            fact_operations=({'op': 'set', 'key': KEY, 'value': True, 'visibility': 'public'},),
            fact_audit=({'key': KEY, 'subject': '双方', 'action': overstated, 'object': '物品',
                         'result': overstated, 'evidence': [{'source': 'player_input', 'quote': message}]},))

    async def invoke(self, messages, **kwargs):
        data = prompt_payload(messages[1].content)
        assert runtime.store._path(topic).read_bytes() == before
        if not kwargs['transition_required']:
            assert calls == []
            assert data['source_delivery_task'] == {
                'pending_results': [{'key': KEY, 'value': True, 'description': description}],
            }
            assert overstated not in messages[0].content + messages[1].content
            assert data['player_input'] == message
            assert boundary in messages[0].content
            assert '本幕补充交付段' in messages[0].content
            assert '审核通过且出幕条件满足后' in messages[0].content
            assert '先自然回应当前发言' in messages[1].content
            assert '可以不同意或说明尚未确定的部分' in messages[0].content
            calls.append('source')
            return _parse_output(json.dumps({'performance': body, 'transition_offer': '',
                'suggested_inputs': [], 'fact_candidates': [],
                **({'scene_update': narration} if narration else {})}, ensure_ascii=False))
        assert calls == ['source', 'proof']
        assert 'source_delivery_task' not in data
        assert '本幕补充交付段' not in messages[0].content
        assert kwargs['transition_tail_only'] is True
        calls.append('tail')
        return _parse_output(json.dumps({'bridge_scene_narration': '',
            'target_scene_narration': '工作台的灯熄灭。', 'target_performance': '接下来我们继续。',
            'suggested_inputs': []}, ensure_ascii=False), transition_required=True,
            transition_tail_only=True, bridge_required=False)

    async def review(self, **kwargs):
        if not kwargs['claims']:
            return ev.NumericV2StoryEvidenceReview()
        assert calls == ['source']
        assert runtime.store._path(topic).read_bytes() == before
        assert body in kwargs['actor_evidence']
        if narration:
            assert narration in kwargs['actor_evidence']
        kwargs['evidence_batch'].validate()
        assert any(row['source'] == 'actor_performance' and body in row['quote']
                   for row in kwargs['claims'][0]['evidence'])
        calls.append('proof')
        return ev.NumericV2StoryEvidenceReview((0,) if approved else ())

    async def contract(*args, **kwargs):
        return ()

    monkeypatch.setattr(options, 'MODEL_REVIEWS_ENABLED', False)
    monkeypatch.setattr(wf, 'aload_theater_module_options', options.aload_theater_module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'review_story_evidence', review)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'verify_contract_boundaries', contract)
    monkeypatch.setattr(wf.NumericV2Actor, '_invoke', invoke)
    monkeypatch.setattr(wf.NumericV2Actor, '_character_profile', lambda self: '温和。')
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('deliver', 0, message), ensure_current_binding=lambda _: _binding())
    assert result.stored.session.revision == 1
    transitions = [row for row in result.stored.ledger_events if row['from_node_id'] != row['to_node_id']]
    assert len(transitions) == int(approved)
    assert calls == (['source', 'proof', 'tail'] if approved else ['source', 'proof'])
    assert (KEY in result.stored.session.story_state['facts']) is approved
    if approved:
        assert result.performance['segments'][0]['performance'] == body
    else:
        assert result.performance['performance'] == body
    assert await runtime.restore_session(topic) == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['no_claim', 'nonautomatic', 'rejected', 'departure', 'private'])
async def test_unrelated_dispatch_does_not_receive_source_task(tmp_path, monkeypatch, mode):
    story = deepcopy(_engine().story)
    if mode == 'nonautomatic':
        transition = story['nodes'][0]['route_gates'][0]['transition_contract']
        transition.pop('advance_on_completion')
        transition['accept_input'] = '好，我们过去。'
        transition['bridge_required'] = True
        transition['fallback_offer'] = '要一起去仓库吗？'
    if mode == 'private':
        story['fact_contract']['facts'][KEY]['visibility'] = 'story'
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='isolation', catgirl_binding=_binding(),
        opening_performance={'performance': '先商量。', 'suggested_inputs': []})
    message = '我建议把设备送回仓库。'

    async def evaluate(self, **kwargs):
        if mode == 'no_claim':
            return ev.NumericV2EvaluationResult((), False)
        return ev.NumericV2EvaluationResult((), False,
            transition_intent='reject' if mode == 'rejected' else 'unclear',
            departure_attempt_observed=mode == 'departure',
            fact_operations=({'op': 'set', 'key': KEY, 'value': True,
                              'visibility': 'story' if mode == 'private' else 'public'},),
            fact_audit=({'key': KEY, 'subject': '双方', 'action': '同意', 'object': '设备', 'result': '约定',
                         'evidence': [{'source': 'player_input', 'quote': message}]},))

    async def generate(self, **kwargs):
        assert 'source_delivery_task' not in kwargs
        return {'performance': '我们再考虑一下。', 'suggested_inputs': [], 'fact_candidates': []}

    async def review(*args, **kwargs):
        return ev.NumericV2StoryEvidenceReview()

    monkeypatch.setattr(options, 'MODEL_REVIEWS_ENABLED', False)
    monkeypatch.setattr(wf, 'aload_theater_module_options', options.aload_theater_module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'review_story_evidence', review)
    monkeypatch.setattr(wf.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(wf.NumericV2Actor, '_character_profile', lambda self: '温和。')
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('ordinary', 0, message), ensure_current_binding=lambda _: _binding())
    assert result.stored.session.current_node_id == 'start'
