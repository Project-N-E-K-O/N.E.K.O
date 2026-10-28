"""Formal agency and scene reviews are independent, parallel, and fail closed."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_runtime import TurnRequestV2
from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


QUOTE = '左侧走廊通往阅览室，通道已经开放。'


def _case():
    case = initiation_case()
    outcome = case['engine'].resolve_turn(case['session'], TurnRequestV2('split', 0, case['message']), (),
                                          transition_intent='initiate')
    candidate = case['engine'].finalize_transition_performance(outcome, _candidate(), target_opening='阅览室入口。')
    return dict(engine=case['engine'], session=case['session'], message=case['message'],
                actor_performance=candidate, route_changed=True, transition_outcome=outcome,
                public_destination_quote=QUOTE)


def _clients(monkeypatch, *, role=(), scene=(), fail=None, together=False):
    seen, closed = [], []
    ready = asyncio.Event()

    async def config(_):
        return dict(model='qwen3.8-flash', base_url='https://dashscope.aliyuncs.com/compatible-mode/v1')

    class Client:
        def __init__(self, options):
            self.options = options
            self.task = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            closed.append(self.task)

        async def ainvoke(self, messages, **kwargs):
            is_role = messages[1].content.startswith('{')
            self.task = 'role' if is_role else 'scene'
            seen.append((self.task, messages, kwargs, self.options))
            if len(seen) == 2:
                ready.set()
            if together:
                await asyncio.wait_for(ready.wait(), 0.2)
            if fail == self.task:
                await asyncio.sleep(1)
            if is_role:
                body = dict(body_violations=list(role), failure_reason='新增玩家行动。' if role else '')
            else:
                body = dict(offer_present=False, valid=False, body_violations=list(scene),
                            unsafe_suggestion_indexes=[], failure_reason='场景事实冲突。' if scene else '')
                data = json.loads(messages[1].content[messages[1].content.index('{'):])
                if 'candidate_segments' in data:
                    body.update(delivery_matches_route='scene_boundary' not in scene,
                                public_destination_quote=QUOTE, initiation_authorized=True)
            return SimpleNamespace(content=json.dumps(body))

    async def factory(*args, **kwargs):
        return Client(kwargs)

    monkeypatch.setattr(evaluator, '_model_config', config)
    monkeypatch.setattr(evaluator, 'create_chat_llm_async', factory)
    return seen, closed


@pytest.mark.asyncio
@pytest.mark.parametrize('role,scene', [((), ()), (('player_action',), ()),
                                       ((), ('author_boundary',)), (('player_action',), ('scene_boundary',))])
async def test_neither_review_can_erase_the_other_denial(monkeypatch, role, scene):
    seen, closed = _clients(monkeypatch, role=role, scene=scene)
    result = await evaluator.NumericV2MetricEvaluator(object()).validate_transition_offer(**_case())
    assert set(result.body_violations) == set(role + scene)
    assert len(seen) == len(closed) == 2
    if role:
        assert '新增玩家行动' in result.failure_reason
    if scene:
        assert '场景事实冲突' in result.failure_reason


@pytest.mark.asyncio
async def test_short_review_excludes_future_buttons_and_author_templates_and_runs_in_parallel(monkeypatch):
    seen, closed = _clients(monkeypatch, together=True)
    await evaluator.NumericV2MetricEvaluator(object()).validate_transition_offer(**_case())
    assert len(seen) == len(closed) == 2
    role, = [row for row in seen if row[0] == 'role']
    scene, = [row for row in seen if row[0] == 'scene']
    data = json.loads(role[1][1].content)
    assert data['review_task'] == 'player_actions'
    assert data['player_input'] == _case()['message']
    assert data['candidate_segments'] == _case()['actor_performance']['segments']
    assert 'suggested_inputs' not in data and 'author_entry_plan' not in data and 'story_direction' not in data
    assert len(data['latest_public_context']) <= 1
    for row, expected in [(role, ['player_action']), (scene, ['scene_boundary', 'author_boundary'])]:
        schema = row[2]['response_format']['json_schema']['schema']
        assert schema['properties']['body_violations']['items']['enum'] == expected
        assert row[3]['timeout'] == 8.0 and row[3]['max_retries'] == 0
    assert role[3]['max_completion_tokens'] == 190
    assert role[2]['response_format']['json_schema']['schema']['properties']['failure_reason']['maxLength'] == 80
    assert '只写最终结论' in role[1][0].content


@pytest.mark.asyncio
@pytest.mark.parametrize('fail', ['role', 'scene'])
async def test_either_timeout_cancels_and_closes_both_clients(monkeypatch, fail):
    seen, closed = _clients(monkeypatch, fail=fail)
    monkeypatch.setattr(evaluator, 'NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS', 0.05)
    started = time.monotonic()
    with pytest.raises(evaluator.NumericV2EvaluatorError, match='transition_judge_timeout'):
        await evaluator.NumericV2MetricEvaluator(object()).validate_transition_offer(**_case())
    assert time.monotonic() - started < 0.5
    assert len(seen) == len(closed) == 2


@pytest.mark.asyncio
async def test_ordinary_review_remains_one_call(monkeypatch):
    seen, closed = _clients(monkeypatch)
    case = _case()
    case.update(route_changed=False, transition_outcome=None, public_destination_quote='')
    case['actor_performance'] = {'performance': '我们先留在这里。', 'suggested_inputs': []}
    await evaluator.NumericV2MetricEvaluator(object()).validate_transition_offer(**case)
    assert len(seen) == len(closed) == 1 and seen[0][0] == 'scene'


@pytest.mark.asyncio
async def test_ordinary_narration_uses_short_agency_review_and_preserves_veto(monkeypatch):
    seen, closed = _clients(monkeypatch, role=('player_action',), together=True)
    case = _case()
    case.update(route_changed=False, transition_outcome=None, public_destination_quote='')
    case['actor_performance'] = {'performance': '水在桌上。', 'scene_narration': '玩家自行取出了课本。', 'suggested_inputs': []}
    result = await evaluator.NumericV2MetricEvaluator(object()).validate_transition_offer(**case)
    assert result.body_violations == ('player_action',)
    assert len(seen) == len(closed) == 2
    role, = [row for row in seen if row[0] == 'role']
    assert json.loads(role[1][1].content)['candidate_segments'][0]['scene_narration'] == '玩家自行取出了课本。'


def test_player_action_parser_never_turns_missing_or_foreign_verdicts_into_approval():
    for payload in ({'failure_reason': ''}, {'body_violations': ['author_boundary'], 'failure_reason': ''},
                    {'body_violations': False, 'failure_reason': ''}):
        with pytest.raises(evaluator.NumericV2EvaluatorOutputError):
            evaluator._parse_player_action_review(json.dumps(payload))
