"""Atlas Cloud's reasoning dialect at the OpenAI SDK wire boundary."""

import json

import httpx
import openai
import pytest

from config.providers import focus_extra_body
from utils.llm_client import create_chat_llm

ENDPOINT = 'https://api.atlascloud.ai/v1'


@pytest.mark.unit
@pytest.mark.parametrize('model', [
    'google/gemini-3.1-flash-lite',
    'google/gemini-2.5-flash-lite',
    'google/gemini-3-flash-preview',
])
def test_atlascloud_regular_and_focus_wire_payloads(model):
    """Normal and Focus effort reach Atlas Cloud as a top-level reasoning_effort."""
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={
            'id': 'test-completion', 'object': 'chat.completion', 'created': 0,
            'model': model,
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'OK'},
                         'finish_reason': 'stop'}],
        })

    client = create_chat_llm(model, ENDPOINT, 'test-key')
    try:
        with openai.OpenAI(
            api_key='test-key', base_url=ENDPOINT,
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
    'https://api.atlascloud.ai.example.test/v1',
    'https://example.test/api.atlascloud.ai/v1',
])
def test_lookalike_hosts_keep_the_openrouter_dialect(endpoint):
    """Only the exact Atlas Cloud host is converted."""
    client = create_chat_llm('google/gemini-3.1-flash-lite', endpoint, 'test-key')
    try:
        params = client._params([{'role': 'user', 'content': 'Synthetic test'}])
        assert params['extra_body'] == {'reasoning': {'effort': 'none'}}
        assert 'reasoning_effort' not in params
    finally:
        client.close()
