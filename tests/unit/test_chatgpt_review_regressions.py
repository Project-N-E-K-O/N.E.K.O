"""PR review regressions with simulated OAuth/Responses; never use live credentials."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from openai import AsyncOpenAI, OpenAI

from utils.chatgpt_auth import ChatGPTAuthError
from utils.llm_client.chatgpt_client import ChatChatGPT, ChatGPTOutputLimitError, ChatGPTResponseError, ResponseState


def terminal(text='partial', reason='max_output_tokens'):
    return {'type': 'response.incomplete', 'response': {
        'id': 'test', 'status': 'incomplete', 'incomplete_details': {'reason': reason},
        'output': [
            {'type': 'message', 'content': [{'type': 'output_text', 'text': text}]},
            {'type': 'function_call', 'name': 'click', 'call_id': 'call-test', 'arguments': '{}'},
        ],
        'usage': {'input_tokens': 7, 'output_tokens': 12, 'total_tokens': 19,
                  'input_tokens_details': {'cached_tokens': 3}},
    }}


@pytest.fixture(autouse=True)
def forbid_real_store(monkeypatch):
    def fail():
        raise AssertionError('Tests must never resolve real OAuth credentials')
    monkeypatch.setattr('utils.llm_client.chatgpt_client.get_store', fail)


@pytest.fixture
def credential_store():
    return SimpleNamespace(access_token=lambda: 'fixture-token')


@pytest.mark.parametrize('deltas', [False, True])
def test_output_limit_returns_text_and_usage_without_tools(deltas):
    state = ResponseState()
    if deltas:
        state.feed({'type': 'response.output_text.delta', 'delta': 'part'})
    chunks = state.feed(terminal())
    state.finish()
    raw = state.raw('test')
    assert raw.choices[0].finish_reason == 'length'
    assert raw.choices[0].message.content == 'partial'
    assert raw.choices[0].message.tool_calls is None
    assert chunks[-1].tool_call_deltas is None
    assert chunks[-1].content == ('ial' if deltas else 'partial')
    assert raw.usage.prompt_tokens_details.cached_tokens == 3


def test_output_limit_without_text_and_other_incomplete_reasons_stay_errors():
    with pytest.raises(ChatGPTOutputLimitError):
        ResponseState().feed(terminal(text=''))
    with pytest.raises(ChatGPTResponseError):
        ResponseState().feed(terminal(reason='content_filter'))


@pytest.mark.parametrize('entry', ['invoke', 'ainvoke', 'astream'])
@pytest.mark.asyncio
async def test_output_limit_sdk_paths_record_usage_once(entry, credential_store, monkeypatch):
    recorded = []
    monkeypatch.setattr('utils.token_tracker.TokenTracker.get_instance', lambda: SimpleNamespace(record=lambda **kw: recorded.append(kw)))
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                             text='data: ' + json.dumps(terminal()) + '\n\n')
    client = ChatChatGPT(model='test', credential_store=credential_store, max_completion_tokens=128, timeout=5)
    await client.aclose()
    client._client = OpenAI(api_key='unused', max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    client._aclient = AsyncOpenAI(api_key='unused', max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    try:
        if entry == 'astream':
            chunks = [chunk async for chunk in client.astream('hi')]
            assert chunks[-1].finish_reason == 'length'
        else:
            response = client.invoke('hi') if entry == 'invoke' else await client.ainvoke('hi')
            assert response.content == 'partial'
            assert response.response_metadata['finish_reason'] == 'length'
        assert len(recorded) == 1
        assert recorded[0]['cached_tokens'] == 3
        assert recorded[0]['total_tokens'] == 19
        assert len(requests) == 1
        assert requests[0].headers['authorization'] == 'Bearer fixture-token'
        assert requests[0].url.path == '/v1/responses'
        assert 'max_output_tokens' not in json.loads(requests[0].content)
    finally:
        await client.aclose()


def test_focus_and_parallel_tool_parameters_omit_unsupported_budget(credential_store):
    client = ChatChatGPT(model='gpt-5-nano', credential_store=credential_store,
                        max_completion_tokens=128, timeout=5, extra_body={'reasoning_effort': 'minimal'})
    try:
        assert client._response_params('hi')['reasoning'] == {'effort': 'minimal'}
        body = client._response_params('hi', extra_body={'reasoning_effort': 'low', 'store': True, 'model': 'evil'},
                                       tools=[{'type': 'function', 'function': {'name': 'test', 'parameters': {}}}],
                                       parallel_tool_calls=False, max_completion_tokens=1024)
        assert body['reasoning'] == {'effort': 'low'}
        assert body['parallel_tool_calls'] is False
        assert 'max_output_tokens' not in body
        assert body['store'] is False and body['model'] == 'gpt-5-nano'
    finally:
        client.close()


@pytest.mark.parametrize('builtin', [True, False])
@pytest.mark.asyncio
async def test_connection_probe_uses_selected_model_and_rejects_missing(builtin, monkeypatch):
    from main_routers.config_router import connectivity as router
    probe = AsyncMock(return_value={'success': True})
    monkeypatch.setattr(router, '_test_connectivity_candidates', probe)
    args = {'provider_key': 'chatgpt', 'provider_scope': 'assist'} if builtin else {
        'url': 'https://api.openai.com/v1', 'provider_type': 'chatgpt'}
    result = await router.test_connectivity(router.ConnectivityTestRequest(**args, model='account-model', api_key=''))
    assert result['success']
    assert probe.call_args.args[2] == 'account-model'
    assert probe.call_args.args[3] == 'chatgpt'
    probe.reset_mock()
    result = await router.test_connectivity(router.ConnectivityTestRequest(**args, api_key=''))
    assert result['error_code'] == 'missing_params'
    probe.assert_not_called()


@pytest.mark.asyncio
async def test_connection_probe_accepts_reasoning_only_budget_limit(monkeypatch):
    from main_routers.config_router.connectivity import _test_connectivity_candidates
    client = SimpleNamespace(ainvoke=AsyncMock(side_effect=ChatGPTOutputLimitError('limit')), aclose=AsyncMock())
    monkeypatch.setattr('utils.llm_client.chatgpt_client.ChatChatGPT', lambda **kw: client)
    result = await _test_connectivity_candidates(['https://api.openai.com/v1'], '', 'selected', 'chatgpt', False)
    assert result['success']
    assert client.ainvoke.await_count == client.aclose.await_count == 1


@pytest.mark.parametrize('error,expected', [(ChatGPTAuthError('sign in'), 'AGENT_API_KEY_INVALID'),
    (ChatGPTResponseError('limit', status_code=429), 'AGENT_QUOTA_EXCEEDED'),
    (ChatGPTOutputLimitError('budget'), '')])
def test_agent_probe_does_not_retry_terminal_or_budget_response(error, expected, monkeypatch):
    from app.agent_server.model_probe import AgentModelProbe
    client = MagicMock()
    client.invoke_raw.side_effect = error
    monkeypatch.setattr('utils.llm_client.create_chat_llm', lambda **kw: client)
    adapter = object.__new__(AgentModelProbe)
    adapter._config_manager = MagicMock()
    adapter._config_manager.get_model_api_config.return_value = {
        'model': 'selected', 'base_url': 'https://api.openai.com/v1', 'api_key': '', 'provider_type': 'chatgpt'}
    adapter._client = adapter._client_sig = None
    assert adapter.check_connectivity(_retries=2) == (not bool(expected), expected)
    assert client.invoke_raw.call_count == 1

    from config import OPENAI_CONNECTIVITY_TEST_MAX_TOKENS
    assert client.invoke_raw.call_args.kwargs["max_completion_tokens"] == OPENAI_CONNECTIVITY_TEST_MAX_TOKENS


@pytest.mark.parametrize('outcome', ['complete', 'truncated', 'auth', 'quota', 'failed'])
@pytest.mark.asyncio
async def test_nekopower_responses_tools_and_terminal_errors(outcome, credential_store, monkeypatch):
    """Exercise the current task-agent path through the real Responses parser."""
    from nekopower.runtime.loop.messages import UserMessage
    from nekopower.runtime.loop.provider_events import AssistantDoneEvent, AssistantErrorEvent
    from nekopower.runtime.model import LLMClientProvider
    from nekopower.runtime.protocol import ModelConfig

    requests = []
    def handler(request):
        requests.append(request)
        if outcome in ('auth', 'quota'):
            return httpx.Response(401 if outcome == 'auth' else 429, json={'error': {'message': outcome}})
        event = terminal(text='Inspect the screen')
        if outcome == 'complete':
            event['type'] = 'response.completed'
            event['response']['status'] = 'completed'
            event['response'].pop('incomplete_details')
        elif outcome == 'failed':
            event = {'type': 'response.failed', 'response': {
                'id': 'test', 'status': 'failed', 'error': {'code': 'server_error'},
            }}
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                              text='data: ' + json.dumps(event) + '\n\n')

    client = ChatChatGPT(model='selected', credential_store=credential_store, max_completion_tokens=1024, timeout=5)
    await client.aclose()
    client._aclient = AsyncOpenAI(api_key='unused', max_retries=0,
                                http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    factory = AsyncMock(return_value=client)
    monkeypatch.setattr('utils.llm_client.create_chat_llm_async', factory)
    provider = LLMClientProvider(ModelConfig(model='selected', base_url='https://api.openai.com/v1',
                                            provider_type='chatgpt', max_output_tokens=1024))
    try:
        events = [event async for event in provider.stream_response(
            model='selected', system='Inspect safely', messages=[UserMessage(content='look')], tools=[])]
        assert factory.call_args.kwargs['provider_type'] == 'chatgpt'
        assert len(requests) == 1
        assert requests[0].headers['authorization'] == 'Bearer fixture-token'
        assert requests[0].url.path == '/v1/responses'
        assert 'max_output_tokens' not in json.loads(requests[0].content)
        if outcome in ('auth', 'quota', 'failed'):
            assert isinstance(events[-1], AssistantErrorEvent)
        else:
            assert isinstance(events[-1], AssistantDoneEvent)
            calls = events[-1].message.tool_calls
            if outcome == 'complete':
                assert len(calls) == 1 and calls[0].name == 'click'
            else:
                assert events[-1].reason == 'length'
                assert not calls
    finally:
        await provider.aclose()
