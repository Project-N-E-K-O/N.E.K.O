"""Keep API output constraints aligned with the existing theater contracts."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from services.theater import numeric_v2_actor as actor
from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_structured_output import (
    actor_output_schema, contract_output_schema, review_output_schema, response_format_for,
)
from services.theater.numeric_v2_trace import text_trace_scope
from services.theater.numeric_v2_usage import numeric_v2_usage_scope
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from utils.llm_client import HumanMessage, SystemMessage


CONFIG = {'model': 'qwen3.8-flash', 'base_url': 'https://trial.cn-beijing.maas.aliyuncs.com/compatible-mode/v1', 'provider_type': 'openai_compatible'}


@pytest.mark.parametrize('changes,enabled', [
    ({}, True),
    ({'model': 'qwen3.8-max'}, True),
    ({'model': 'qwen3.8-flash-2026-09-01'}, True),
    ({'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1/'}, True),
    ({'model': 'qwen3.5-flash'}, False),
    ({'model': 'gpt-test'}, False),
    ({'base_url': 'http://localhost:1234/v1'}, False),
    ({'base_url': 'https://trial.cn-beijing.maas.aliyuncs.com.example.org/compatible-mode/v1'}, False),
    ({'base_url': 'https://trial.cn-beijing.maas.aliyuncs.com/api/v1'}, False),
    ({'provider_type': 'anthropic'}, False),
])
def test_only_supported_provider_and_model_get_schema(changes, enabled):
    result = response_format_for({**CONFIG, **changes}, 'actor', actor_output_schema())
    assert bool(result) is enabled
    if enabled:
        assert result['type'] == 'json_schema'
        assert result['json_schema']['strict'] is True


ACTOR_CASES = [
    ({}, {'performance': '我只知道记录里的内容。', 'suggested_inputs': ['（点头）', '（保持沉默）'], 'transition_offered': False}),
    ({'opening_required': True}, {'scene_narration': '灯光亮起。', 'performance': '准备好了。', 'suggested_inputs': ['（点头）', '（保持沉默）'], 'transition_offered': False}),
    ({'transition_required': True}, {'source_scene_narration': '', 'source_performance': '好。', 'target_performance': '到了。', 'bridge_scene_narration': '门打开了。', 'target_scene_narration': '走廊亮着灯。', 'suggested_inputs': []}),
    ({'suggestions_only': True}, {'suggested_inputs': ['（点头）', '（保持沉默）']}),
    ({'transition_suggestions_only': True}, {'accept_input': '（点头）好，我们过去。', 'alternative_inputs': ['（摇头）先留在这里。']}),
    ({'fact_candidates_expected': True}, {'performance': '灯亮了。', 'suggested_inputs': [], 'transition_offered': False, 'fact_candidates': [{'key': 'ready', 'value': True, 'evidence_quote': '灯亮了'}]}),
]


@pytest.mark.parametrize('flags,payload', ACTOR_CASES)
@pytest.mark.parametrize('trace_enabled', [False, True])
@pytest.mark.asyncio
async def test_actor_schema_reaches_single_call_and_preserves_usage(monkeypatch, tmp_path, flags, payload, trace_enabled):
    seen, factories = [], []
    if trace_enabled:
        monkeypatch.setenv('NEKO_THEATER_TRACE_DIR', str(tmp_path))
    else:
        monkeypatch.delenv('NEKO_THEATER_TRACE_DIR', raising=False)

    class Client:
        model = CONFIG['model']
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def ainvoke(self, messages, **kwargs):
            seen.append((messages, kwargs))
            Draft202012Validator(kwargs['response_format']['json_schema']['schema']).validate(payload)
            return SimpleNamespace(content=json.dumps(payload), response_metadata={'token_usage': {'prompt_tokens': 12, 'completion_tokens': 7}})

    async def config(_): return CONFIG
    async def factory(*args, **kwargs):
        factories.append(kwargs)
        return Client()
    monkeypatch.setattr(actor, '_model_config', config)
    monkeypatch.setattr(actor, 'create_chat_llm_async', factory)
    messages = [SystemMessage(content='只输出JSON。'), HumanMessage(content='继续。')]
    instance = actor.NumericV2Actor(object())
    with numeric_v2_usage_scope() as usage, text_trace_scope('schema'):
        result = await instance._invoke(messages, **flags)
    assert result is not None and instance.provider_call_count == len(seen) == len(factories) == 1
    assert seen[0][0] is messages
    assert factories[0]['max_retries'] == 0
    assert usage[0]['input_tokens'] == 12 and usage[0]['output_tokens'] == 7
    if trace_enabled:
        rows = [json.loads(line) for path in tmp_path.glob('*.jsonl') for line in path.read_text(encoding='utf-8').splitlines()]
        request = next(row['data'] for row in rows if row['event'] == 'model.request')
        assert request['response_format'] == seen[0][1]['response_format']


def test_schema_rejects_wrong_fields_types_and_keeps_existing_optional_contract():
    schema = actor_output_schema()
    validator = Draft202012Validator(schema)
    valid = ACTOR_CASES[0][1]
    assert validator.is_valid(valid)
    assert validator.is_valid({**valid, 'scene_update': '灯亮了。'})
    assert not validator.is_valid({**valid, 'transition_offered': 'false'})
    assert not validator.is_valid({**valid, 'scene_update': None})
    assert not validator.is_valid({**valid, 'segments': []})
    assert not validator.is_valid({key: value for key, value in valid.items() if key != 'performance'})
    facts = actor_output_schema(fact_candidates_expected=True)
    for value in (True, 3, 'on'):
        assert Draft202012Validator(facts).is_valid({**valid, 'fact_candidates': [{'key': 'state', 'value': value, 'evidence_quote': '原文'}]})
    assert not Draft202012Validator(facts).is_valid({**valid, 'fact_candidates': [{'key': 'state', 'value': {}, 'evidence_quote': '原文'}]})


@pytest.mark.parametrize('formal,confirmed', [(False, False), (True, False), (True, True)])
def test_review_schema_keeps_authorization_separate_from_body(formal, confirmed):
    schema = review_output_schema(formal=formal, transition_intent='accept' if formal else '', confirmed_acceptance=confirmed)
    properties = schema['properties']
    assert ('delivery_matches_route' in properties) is formal
    assert ('pending_invitation_invalid' in properties) is formal
    assert ('acceptance_authorized' in properties) is (formal and not confirmed)
    assert ('offer_quote' in properties) is (not formal)
    payload = {'offer_present': False, 'valid': False, 'body_violations': [], 'unsafe_suggestion_indexes': [], 'failure_reason': ''}
    if formal:
        payload.update(delivery_matches_route=True, pending_invitation_invalid=False)
        if not confirmed:
            payload['acceptance_authorized'] = True
    else:
        payload['offer_quote'] = ''
        payload['player_action_kind'] = ''
        payload['offer_kind'] = ''
        assert not Draft202012Validator(schema).is_valid({**payload, 'offer_kind': 'unknown_kind'})
    assert ('offer_kind' in properties) is (not formal)
    Draft202012Validator(schema).validate(payload)
    evaluator._parse_transition_judge_output(json.dumps(payload), acceptance_review=formal, transition_delivery_review=formal)
    assert not Draft202012Validator(schema).is_valid({**payload, 'body_violations': ['unknown_code']})


@pytest.mark.parametrize('dispute', [False, True])
@pytest.mark.asyncio
async def test_review_attaches_schema_without_touching_evaluator_model_parameters(monkeypatch, dispute):
    seen = []
    payload = {'offer_present': False, 'offer_quote': '', 'valid': False, 'body_violations': [], 'unsafe_suggestion_indexes': [], 'failure_reason': '', 'player_action_kind': '', 'offer_kind': ''}
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def ainvoke(self, messages, **kwargs):
            seen.append(kwargs)
            Draft202012Validator(kwargs['response_format']['json_schema']['schema']).validate(payload)
            return SimpleNamespace(content=json.dumps(payload), response_metadata={})
    async def config(_): return CONFIG
    async def factory(*args, **kwargs):
        assert kwargs['max_retries'] == 0
        assert kwargs['max_completion_tokens'] == (evaluator.NUMERIC_V2_DISPUTE_JUDGE_MAX_OUTPUT_TOKENS if dispute else evaluator.NUMERIC_V2_TRANSITION_JUDGE_MAX_OUTPUT_TOKENS)
        assert kwargs.get('extra_body') == ({'enable_thinking': True, 'thinking_budget': 512} if dispute else None)
        return Client()
    monkeypatch.setattr(evaluator, '_model_config', config)
    monkeypatch.setattr(evaluator, 'create_chat_llm_async', factory)
    monkeypatch.setattr(evaluator, 'focus_extra_body', lambda _: {'enable_thinking': True, 'thinking_budget': 512})
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id='schema', catgirl_binding={'catgirl_name': '猫娘'}, opening_performance={'performance': '你好。'})
    result = await evaluator.NumericV2MetricEvaluator(object()).validate_transition_offer(engine=engine, session=session, message='继续。', actor_performance={'performance': '我等你。', 'suggested_inputs': []}, dispute_review=dispute)
    assert result.body_violations == () and len(seen) == 1


@pytest.mark.parametrize('scenario', [
    'scene_update', 'missed', 'facts', 'fixed', 'formal_initiate', 'formal_accept', 'formal_confirmed',
])
@pytest.mark.asyncio
async def test_review_schema_matches_real_prompt_branches(monkeypatch, scenario):
    from tests.unit.test_theater_numeric_v2_fact_review import _context
    from tests.unit.test_theater_numeric_v2_fixed_content_repair import _engine, _candidate
    engine, session, claims = _context()
    if scenario not in ('facts', 'formal_initiate', 'formal_accept', 'formal_confirmed'):
        engine = _engine() if scenario == 'fixed' else NumericV2Engine.from_mapping(numeric_v2_story())
        session = engine.create_session(session_id=scenario, catgirl_binding={'catgirl_name': '猫娘'}, opening_performance={'performance': '你好。'})
    candidate = _candidate() if scenario == 'fixed' else {'performance': '灯亮了。', 'suggested_inputs': []}
    if scenario == 'scene_update':
        candidate['scene_narration'] = '灯亮了。'
    options = {}
    if scenario == 'facts':
        options['evaluator_fact_claims'] = claims
    if scenario == 'missed':
        options['check_missed_initiation'] = True
    if scenario.startswith('formal_'):
        outcome = engine.resolve_turn(session, TurnRequestV2('one', 0, '带路吧。'), (), transition_intent='initiate')
        if scenario != 'formal_initiate':
            outcome = replace(outcome, ledger_event={**outcome.ledger_event, 'transition_intent': 'accept'})
        options.update(transition_outcome=outcome, route_changed=True, confirmed_acceptance=scenario == 'formal_confirmed')
    calls = []
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def ainvoke(self, messages, **kwargs):
            calls.append(kwargs)
            system = messages[0].content
            example, _ = json.JSONDecoder().raw_decode(system[system.index('{'):])
            schema = kwargs['response_format']['json_schema']['schema']
            assert set(schema['properties']) == set(example)
            assert set(schema['required']) == set(example)
            Draft202012Validator(schema).validate(example)
            return SimpleNamespace(content=json.dumps(example), response_metadata={})
    async def config(_): return CONFIG
    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(evaluator, '_model_config', config)
    monkeypatch.setattr(evaluator, 'create_chat_llm_async', factory)
    await evaluator.NumericV2MetricEvaluator(object()).validate_transition_offer(
        engine=engine, session=session, message='我按下开关。', actor_performance=candidate, **options)
    assert len(calls) == 1


def test_contract_schema_matches_existing_prompt_and_parser():
    messages = evaluator._build_contract_check_messages(required=['不得移走物品。'], candidate_text='物品仍在桌上。', player_input='继续。')
    system = messages[0].content
    example, _ = json.JSONDecoder().raw_decode(system[system.index('{'):])
    schema = contract_output_schema()
    assert set(schema['properties']) == set(example) == {'violated'}
    Draft202012Validator(schema).validate({'violated': []})
    assert evaluator._parse_contract_check_output('{"violated":[]}', ['不得移走物品。']) == ()


@pytest.mark.parametrize('supported', [False, True])
@pytest.mark.asyncio
async def test_actor_bad_json_keeps_existing_failure_without_fallback_request(monkeypatch, supported):
    seen = []
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def ainvoke(self, messages, **kwargs):
            seen.append(kwargs)
            return SimpleNamespace(content='{"performance":', response_metadata={'finish_reason': 'length'})
    async def config(_): return {**CONFIG, **({} if supported else {'model': 'other-model'})}
    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(actor, '_model_config', config)
    monkeypatch.setattr(actor, 'create_chat_llm_async', factory)
    with pytest.raises(actor.NumericV2ActorOutputError, match='invalid_json'):
        await actor.NumericV2Actor(object())._invoke([HumanMessage(content='继续。')])
    assert len(seen) == 1
    assert ('response_format' in seen[0]) is supported
