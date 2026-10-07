"""Review locations may enable a narrow repair, never erase a safety verdict."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater import numeric_v2_workflow as workflow
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding


def _issue(**changes):
    return {
        'code': 'player_return_after_departure',
        'field': 'scene_update',
        'quote': '你重新回到店里。',
        'violations': ['player_action'],
        **changes,
    }


def _review(issues):
    return evaluator._parse_transition_judge_output(
        json.dumps({
            'offer_present': False, 'valid': False,
            'body_violations': ['player_action'], 'unsafe_suggestion_indexes': [],
            'failure_reason': '已确认的离场结果与候选内容冲突。',
            'body_issues': issues,
        }, ensure_ascii=False),
        body_evidence={'actor_performance': '明天见。', 'scene_update': '你重新回到店里。'},
    )


@pytest.mark.parametrize('issues', [
    None, {}, [], [_issue(quote='并不存在的原文')],
    [_issue(field='actor_performance')], [_issue(code='allow')],
    [_issue(violations=[])], [_issue(violations=['author_boundary'])],
    [_issue(), _issue(quote='并不存在的原文')], [_issue()] * 7,
])
def test_bad_locations_preserve_rejection_and_cannot_enable_local_repair(issues):
    review = _review(issues)
    assert review.body_violations == ('player_action',)
    assert review.body_issues == ()
    assert workflow._safe_degrade_conflicting_scene_update(
        {'performance': '明天见。', 'scene_narration': '你重新回到店里。'},
        review, workflow.project_player_action_result('（推门离开）明天见。'),
    ) is None


def test_verified_locations_enable_only_departure_scene_update_repair():
    review = _review([_issue()])
    candidate = {
        'performance': '明天见。', 'scene_narration': '你重新回到店里。',
        'fact_candidates': [{'key': 'returned', 'value': True}],
    }
    projection = workflow.project_player_action_result('（推门离开）明天见。')
    repaired = workflow._safe_degrade_conflicting_scene_update(candidate, review, projection)
    assert repaired == {'performance': '明天见。', 'transition_offered': False}
    assert 'scene_narration' in candidate  # The uncommitted candidate is not mutated.
    for blocked in (
        replace(review, body_violations=('player_action', 'author_boundary')),
        replace(review, offer_present=True),
        replace(review, body_issues=(_issue(code='other'),)),
        replace(review, body_issues=(_issue(field='actor_performance'),)),
        replace(review, body_issues=(_issue(), _issue(code='other'))),
    ):
        assert workflow._safe_degrade_conflicting_scene_update(candidate, blocked, projection) is None
    assert workflow._safe_degrade_conflicting_scene_update(candidate, review, {}) is None
    assert workflow._safe_degrade_conflicting_scene_update(
        {**candidate, 'scene_narration': '已经换了一稿。'}, review, projection,
    ) is None
    assert workflow._safe_degrade_conflicting_scene_update(
        {**candidate, 'performance': '你重新回到店里。'}, review, projection,
    ) is None


def test_locations_must_cover_all_reported_violation_kinds():
    issues = [_issue()]
    evidence = {'scene_update': '你重新回到店里。'}
    assert evaluator._verified_body_issues(issues, ['player_action', 'scene_boundary'], evidence) == ()
    issues[0]['violations'].append('scene_boundary')
    assert evaluator._verified_body_issues(issues, ['player_action', 'scene_boundary'], evidence) == tuple(issues)


@pytest.mark.parametrize('player_input, expected', [
    ('（推门离开）明天见。', True), ('我考虑明天离开。', False), ('谢谢。', False),
])
def test_location_contract_is_requested_only_for_confirmed_departure(player_input, expected):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id='issues', catgirl_binding=_binding(),
                                    opening_performance={'performance': '你好。', 'suggested_inputs': []})
    messages, _ = evaluator._build_transition_judge_messages(
        engine, session, actor_performance={'performance': '再见。', 'suggested_inputs': []},
        player_input=player_input,
    )
    assert ('"body_issues"' in messages[0].content) is expected


def test_reason_text_cannot_override_a_verified_body_issue():
    review = replace(_review([_issue()]), failure_reason='正文承接玩家已经完成的离开动作。')
    assert not workflow._review_mislabels_explicit_player_movement(review)


@pytest.mark.asyncio
@pytest.mark.parametrize('departed', [False, True])
@pytest.mark.parametrize('completion_facts', [False, True])
async def test_location_output_room_does_not_add_calls_or_change_deadline(monkeypatch, departed, completion_facts):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id='budget', catgirl_binding=_binding(),
                                    opening_performance={'performance': '你好。', 'suggested_inputs': []})
    calls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages):
            return SimpleNamespace(content=json.dumps({
                'offer_present': False, 'valid': False, 'body_violations': [],
                'unsafe_suggestion_indexes': [], 'failure_reason': '',
            }))

    async def config(_):
        return {'model': 'test', 'base_url': 'http://test.invalid'}

    async def factory(*args, **kwargs):
        calls.append(kwargs)
        return Client()

    monkeypatch.setattr(evaluator, '_model_config', config)
    monkeypatch.setattr(evaluator, 'create_chat_llm_async', factory)
    if completion_facts:
        monkeypatch.setattr(evaluator, '_pending_completion_facts', lambda *_: [
            {'key': 'done', 'value': True, 'description': '交接完成。'},
        ])
    await evaluator.NumericV2MetricEvaluator(object()).validate_transition_offer(
        engine=engine, session=session, message='（推门离开）明天见。' if departed else '谢谢。',
        actor_performance={'performance': '再见。', 'suggested_inputs': []},
    )
    assert len(calls) == 1
    assert calls[0]['max_completion_tokens'] == (350 if completion_facts else 190) + (322 if departed else 0)
    assert calls[0]['timeout'] == evaluator.NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS
    assert calls[0]['max_retries'] == 0
