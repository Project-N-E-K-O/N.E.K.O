"""An incomplete optional fact tail must not erase complete turn decisions."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.theater.numeric_v2_evaluator import _parse_output, NumericV2EvaluatorOutputError
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story


def _core(**overrides):
    return {
        "interaction_intent": "scene_action", "public_destination_quote": "",
        "scene_complete": True, "transition_intent": "unclear",
        "transition_reply_target": "unclear", "metric_changes": {},
        "natural_ending_ready": True, "ending_reason": "玩家已明确选择沉默，可由角色承接后收束。",
        **overrides,
    }


def _truncated(core=None, tail='[{"key":"scene:start:response","value":true,"evidence":['):
    return json.dumps(_core() if core is None else core, ensure_ascii=False)[:-1] + ',"fact_candidates":' + tail


def _parse(text, finish_reason="length"):
    return _parse_output(text, NumericV2Engine.from_mapping(numeric_v2_story()),
                         "（选择暂时沉默）", finish_reason=finish_reason)


def test_length_truncated_fact_tail_preserves_decision_without_any_facts():
    result = _parse(_truncated())
    assert result.scene_complete is True
    assert result.natural_ending_ready is True
    assert result.fact_operations == result.fact_audit == ()
    assert result.metric_changes == ()


def test_even_complete_items_before_the_cut_are_discarded():
    result = _parse(_truncated(tail='[{"key":"unknown","value":true},{"key":'))
    assert result.fact_operations == result.fact_audit == ()


@pytest.mark.parametrize('reason', [None, 'stop', 'content_filter'])
def test_recovery_requires_explicit_provider_length_reason(reason):
    with pytest.raises(NumericV2EvaluatorOutputError):
        _parse(_truncated(), reason)


@pytest.mark.parametrize('text', [
    '{"scene_complete":true,"metric_changes":{},"fact_candidates":[',
    '{"scene_complete":tru',
    _truncated(tail='{"key":'),
    _truncated(tail='[] , "scene_complete":'),
    _truncated(tail='[] } trailing text'),
    _truncated().replace('"interaction_intent": "scene_action",',
                         '"interaction_intent":"chat","interaction_intent":"scene_action",'),
])
def test_damaged_core_wrong_tail_or_duplicate_keys_are_not_recovered(text):
    with pytest.raises(NumericV2EvaluatorOutputError):
        _parse(text)


def test_core_validation_still_rejects_invalid_decision():
    with pytest.raises(NumericV2EvaluatorOutputError, match='scene_complete_invalid'):
        _parse(_truncated(_core(scene_complete="true")))


def test_decoding_respects_quoted_braces_and_field_names():
    reason = '原话包含 } 和 "fact_candidates":[，但本轮尚未选择。'
    result = _parse(_truncated(_core(ending_reason=reason, natural_ending_ready=False)))
    assert result.ending_reason == reason
    assert result.natural_ending_ready is False


def test_complete_response_is_unchanged_even_when_provider_says_length():
    result = _parse(json.dumps(_core(fact_candidates=[]), ensure_ascii=False))
    assert result.natural_ending_ready is True


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['length', 'stop'])
async def test_evaluator_passes_provider_finish_reason_without_another_call(monkeypatch, reason):
    from services.theater import numeric_v2_evaluator as module
    from tests.unit.test_theater_numeric_v2_runtime import _binding

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

    monkeypatch.setattr(module, '_model_config', AsyncMock(return_value={
        'model': 'test', 'base_url': 'https://example.invalid', 'api_key': 'test'}))
    factory = AsyncMock(return_value=Client())
    invoke = AsyncMock(return_value=SimpleNamespace(
        content=_truncated(), response_metadata={'finish_reason': reason}))
    monkeypatch.setattr(module, 'create_chat_llm_async', factory)
    monkeypatch.setattr(module, 'invoke_with_usage', invoke)
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id='truncated', catgirl_binding=_binding(),
                                    opening_performance={'performance': '我在这里。'})
    request = module.NumericV2MetricEvaluator(object()).evaluate(
        engine=engine, session=session, message='（选择暂时沉默）')
    if reason == 'length':
        result = await request
        assert result.natural_ending_ready is True
        assert result.fact_operations == ()
    else:
        with pytest.raises(NumericV2EvaluatorOutputError):
            await request
    assert invoke.await_count == 1
    assert factory.call_args.kwargs['max_completion_tokens'] == module.NUMERIC_V2_EVALUATOR_MAX_OUTPUT_TOKENS
