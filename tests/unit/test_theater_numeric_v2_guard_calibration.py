"""Regress review evidence formatting and formal-output capacity; compare semantic accuracy separately with real models."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as ev
from services.theater.numeric_v2_runtime import TurnRequestV2
from tests.unit.test_theater_numeric_v2_player_transition import initiation_case


@pytest.mark.parametrize('text', [
    '（收起凭据）左侧走廊通往阅览室，通道已经开放。',
    '（拿起琴盒）演奏厅在走廊尽头。（指向左侧）我们可以从这里过去。',
    '（关上抽屉）温室入口在花园东侧，但今天尚未开放。',
])
def test_mixed_quote_matches_the_exact_history_given_to_model(text):
    # 完整混合字段和拆分内容块必须认作同一出处；出处有效不代表语义已经授权。
    case = initiation_case()
    session = replace(case['session'], opening_performance={'performance': text, 'suggested_inputs': []})
    assert ev._has_public_transition_quote(text, session)
    result = ev._parse_transition_judge_output(json.dumps(dict(
        offer_present=False, valid=False, body_violations=[], unsafe_suggestion_indexes=[],
        public_destination_quote=text)), initiation_session=session)
    assert not result.body_violations


def test_quote_cannot_splice_records_or_change_negation():
    case = initiation_case()
    session = replace(case['session'], opening_performance={'performance': '（收起凭据）通道今天没有开放。'},
                      revision=1, performance_history=({'revision': 1, 'from_node_id': 'start', 'to_node_id': 'start',
                          'input_text': '秘密门通往阅览室。', 'performance': '（打开抽屉）请先等候。',
                          'suggested_inputs': ['后门已经开放。']},))
    for quote in ['（收起凭据）通道今天已经开放。', '（收起凭据）请先等候。',
                  '秘密门通往阅览室。', '后门已经开放。']:
        assert not ev._has_public_transition_quote(quote, session)


def test_old_scene_quote_and_source_response_cannot_authorize_current_visit():
    case = initiation_case()
    old = '（收起凭据）旧码头通往档案馆。'
    public = '（指向花园）温室入口通道开放。'
    session = replace(case['session'], opening_performance={'performance': old}, revision=2,
        performance_history=({'revision': 2, 'from_node_id': 'old', 'to_node_id': 'start',
          'segments': [{'phase': 'source_response', 'performance': old},
                       {'phase': 'target_opening', 'performance': public}]},))
    assert ev._has_public_transition_quote(public, session)
    assert not ev._has_public_transition_quote(old, session)


@pytest.mark.asyncio
async def test_formal_output_has_room_without_changing_ordinary_or_dispute(monkeypatch):
    # 正式检查要输出三段冲突依据和公开引文，输出容量独立；不扩大普通快检或超时。
    options = []
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def ainvoke(self, messages):
            return SimpleNamespace(content=json.dumps(dict(offer_present=False, valid=False,
                body_violations=[], unsafe_suggestion_indexes=[], failure_reason='')))
    async def config(_): return {'model': 'test', 'base_url': 'http://test.invalid'}
    async def factory(*args, **kwargs): options.append(kwargs); return Client()
    monkeypatch.setattr(ev, '_model_config', config)
    monkeypatch.setattr(ev, 'create_chat_llm_async', factory)
    monkeypatch.setattr(ev, 'focus_extra_body', lambda _: {'enable_thinking': True})
    case = initiation_case(); engine = case['engine']; session = case['session']
    outcome = engine.resolve_turn(session, TurnRequestV2('go', 0, '好。'), (), transition_intent='accept')
    evaluator = ev.NumericV2MetricEvaluator(object())
    for formal, dispute in [(False, False), (True, False), (True, True)]:
        await evaluator.validate_transition_offer(engine=engine, session=session, message='好。',
            actor_performance={'performance': '知道了。'}, transition_outcome=outcome if formal else None,
            dispute_review=dispute)
    assert [x['max_completion_tokens'] for x in options] == [190, 512, 4096]
    assert [x['timeout'] for x in options] == [8, 8, 30]
