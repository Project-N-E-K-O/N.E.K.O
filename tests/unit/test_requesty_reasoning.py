"""Requesty's documented reasoning dialect at the OpenAI SDK wire boundary."""

import json

import httpx
import openai
import pytest

from config.providers import focus_extra_body
from utils.llm_client import create_chat_llm


@pytest.mark.unit
@pytest.mark.parametrize('model', [
    'google/gemini-2.5-flash',
    'google/gemini-2.5-flash-lite',
    'google/gemini-3-flash-preview',
])
@pytest.mark.parametrize('endpoint', [
    'https://router.requesty.ai/v1',
    'https://router.eu.requesty.ai/v1',
])
def test_requesty_regular_and_focus_wire_payloads(model, endpoint):
    """Normal, Focus and explicit overrides survive SDK serialization correctly."""
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
            for overrides in (
                {}, {'extra_body': focus_extra_body(model)},
                {'extra_body': None}, {'reasoning_effort': 'high'},
            ):
                sdk.chat.completions.create(**client._params(messages, **overrides))
        assert captured[0]['reasoning_effort'] == 'none'
        assert captured[1]['reasoning_effort'] == 'low'
        assert 'reasoning_effort' not in captured[2]
        assert captured[3]['reasoning_effort'] == 'high'
        assert all('reasoning' not in body for body in captured)
        assert client.extra_body == {'reasoning': {'effort': 'none'}}
    finally:
        client.close()


@pytest.mark.unit
@pytest.mark.parametrize('endpoint', [
    'https://openrouter.ai/api/v1',
    'https://router.requesty.ai.example.test/v1',
    'https://example.test/router.requesty.ai/v1',
])
def test_other_endpoints_keep_existing_reasoning_dialect(endpoint):
    """Scope conversion to exact Requesty hosts, preserving OpenRouter's dialect."""
    client = create_chat_llm('google/gemini-2.5-flash', endpoint, 'test-key')
    try:
        params = client._params([{'role': 'user', 'content': 'Synthetic test'}])
        assert params['extra_body'] == {'reasoning': {'effort': 'none'}}
        assert 'reasoning_effort' not in params
    finally:
        client.close()


@pytest.mark.unit
def test_requesty_preserves_explicit_effort_and_unrelated_extras():
    """A caller's native effort wins without mutating the shared extra-body data."""
    client = create_chat_llm('google/gemini-2.5-flash', 'https://router.requesty.ai/v1', 'test-key')
    extras = {'reasoning': {'effort': 'none'}, 'reasoning_effort': 'medium',
              'requesty': {'tags': ['synthetic-test']}}
    try:
        params = client._params([], extra_body=extras)
        assert params['reasoning_effort'] == 'medium'
        assert params['extra_body'] == {'requesty': {'tags': ['synthetic-test']}}
        assert extras['reasoning'] == {'effort': 'none'}
        assert extras['reasoning_effort'] == 'medium'
    finally:
        client.close()
