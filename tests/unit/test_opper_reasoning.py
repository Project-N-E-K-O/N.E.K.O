"""Opper's top-level reasoning_effort dialect at the OpenAI SDK wire boundary."""

import json

import httpx
import openai
import pytest

from config import providers as P
from config.providers import focus_extra_body
from utils.llm_client import create_chat_llm

OPPER = 'https://api.opper.ai/v3/compat'


def _wire_bodies(model, endpoint, override_sets):
    """Serialize ``_params`` through the real SDK and return the JSON bodies."""
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={
            'id': 'test-completion', 'object': 'chat.completion', 'created': 0,
            'model': model,
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'OK'},
                         'finish_reason': 'stop'}],
        })

    client = create_chat_llm(model, endpoint, 'test-key')
    try:
        with openai.OpenAI(
            api_key='test-key', base_url=endpoint,
            http_client=httpx.Client(transport=httpx.MockTransport(respond)),
        ) as sdk:
            messages = [{'role': 'user', 'content': 'Synthetic reasoning test'}]
            for overrides in override_sets:
                sdk.chat.completions.create(**client._params(messages, **overrides))
        return captured, client.extra_body
    finally:
        client.close()


@pytest.mark.unit
@pytest.mark.parametrize('model', sorted(P.OPPER_REASONING_EFFORT))
def test_opper_bare_gemini_regular_and_focus_wire_payloads(model):
    """Bare Gemini ids drop the ignored Google knob for the measured top-level effort."""
    regular, focus = P.OPPER_REASONING_EFFORT[model]
    registered = P.MODELS_EXTRA_BODY_MAP[model]
    bodies, instance_extra = _wire_bodies(model, OPPER, (
        {}, {'extra_body': focus_extra_body(model)},
        {'extra_body': None}, {'reasoning_effort': 'high'},
    ))
    assert bodies[0]['reasoning_effort'] == regular
    assert bodies[1]['reasoning_effort'] == focus
    assert 'reasoning_effort' not in bodies[2]
    assert bodies[3]['reasoning_effort'] == 'high'
    assert all('extra_body' not in body and 'google' not in body for body in bodies)
    assert instance_extra == registered


@pytest.mark.unit
def test_opper_effort_table_matches_measurements():
    """Pin the measured floors: bare 2.5-flash ignores none, 3.8-flash rejects minimal."""
    assert P.OPPER_REASONING_EFFORT['gemini-2.5-flash'] == ('low', 'low')
    for model, (regular, focus) in P.OPPER_REASONING_EFFORT.items():
        assert model in P.MODELS_EXTRA_BODY_MAP
        assert regular != 'minimal' and focus != 'minimal'
        if model != 'gemini-2.5-flash':
            assert (regular, focus) == ('none', 'low')
    assert P.HOST_REASONING_EFFORT['api.opper.ai'] is P.OPPER_REASONING_EFFORT


@pytest.mark.unit
@pytest.mark.parametrize('model', [
    'gemini/gemini-2.5-flash',
    'gemini/gemini-2.5-flash-lite',
    'gemini/gemini-3-flash-preview',
    'gemini/gemini-3.1-flash-lite',
    'gemini/gemini-3.5-flash',
    'gemini/gemini-3.6-flash',
    'gemini/gemini-3.8-flash',
])
def test_opper_prefixed_gemini_sends_native_effort(model):
    """Catalog-form ids register the top-level effort directly; Focus flips it to low."""
    assert P.MODELS_EXTRA_BODY_MAP[model] is P.EXTRA_BODY_OPENAI_NATIVE
    bodies, _ = _wire_bodies(model, OPPER, ({}, {'extra_body': focus_extra_body(model)}))
    assert bodies[0]['reasoning_effort'] == 'none'
    assert bodies[1]['reasoning_effort'] == 'low'


@pytest.mark.unit
@pytest.mark.parametrize('model', ['claude-sonnet-5-5', 'claude-haiku-4-5-20251001'])
def test_opper_leaves_claude_without_reasoning_effort(model):
    """Claude gets no effort on Opper (none/minimal 400 on Haiku 4.5, ineffective on 5.x)."""
    bodies, _ = _wire_bodies(model, OPPER, ({}, {'extra_body': focus_extra_body(model)}))
    for body in bodies:
        assert 'reasoning_effort' not in body
        assert body['thinking'] == P.MODELS_EXTRA_BODY_MAP[model]['thinking']


@pytest.mark.unit
@pytest.mark.parametrize('endpoint', [
    'https://generativelanguage.googleapis.com/v1beta/openai',
    'https://api.opper.ai.example.test/v3/compat',
    'https://example.test/api.opper.ai/v3/compat',
])
def test_other_endpoints_keep_google_dialect(endpoint):
    """Scope the rewrite to the exact Opper host."""
    client = create_chat_llm('gemini-3.8-flash', endpoint, 'test-key')
    try:
        params = client._params([{'role': 'user', 'content': 'Synthetic test'}])
        assert params['extra_body'] == P.EXTRA_BODY_GEMINI_3
        assert 'reasoning_effort' not in params
    finally:
        client.close()


@pytest.mark.unit
def test_opper_preserves_explicit_effort_and_unrelated_extras():
    """A caller's own effort wins and unrelated keys survive without mutating inputs."""
    extras = {**P.EXTRA_BODY_GEMINI_3, 'reasoning_effort': 'medium', 'metadata': {'tag': 'synthetic'}}
    snapshot = json.dumps(extras, sort_keys=True)
    client = create_chat_llm('gemini-3.8-flash', OPPER, 'test-key')
    try:
        params = client._params([], extra_body=extras)
        assert params['extra_body'] == {'reasoning_effort': 'medium', 'metadata': {'tag': 'synthetic'}}
        assert 'reasoning_effort' not in params
        params = client._params([], extra_body={**P.EXTRA_BODY_GEMINI_3, 'metadata': {'tag': 'x'}})
        assert params['extra_body'] == {'metadata': {'tag': 'x'}}
        assert params['reasoning_effort'] == 'none'
        assert json.dumps(extras, sort_keys=True) == snapshot
        assert P.EXTRA_BODY_GEMINI_3 == {
            'extra_body': {'google': {'thinking_config': {'thinking_level': 'low', 'include_thoughts': False}}},
        }
    finally:
        client.close()


@pytest.mark.unit
def test_opper_unrecognized_thinking_body_passes_through():
    """A custom thinking body that is neither registered form is not guessed at."""
    custom = {'extra_body': {'google': {'thinking_config': {'thinking_level': 'high'}}}}
    assert P.rewrite_host_reasoning_effort(OPPER, 'gemini-3.8-flash', custom) == (custom, None)
    assert P.rewrite_host_reasoning_effort(OPPER, 'unknown-model', custom) == (custom, None)
    assert P.rewrite_host_reasoning_effort(None, 'gemini-3.8-flash', custom) == (custom, None)
