"""PR review regressions with simulated OAuth/Responses; never use live credentials."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel

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
    finally:
        await client.aclose()


def test_focus_and_parallel_tool_parameters_preserve_budget(credential_store):
    client = ChatChatGPT(model='gpt-5-nano', credential_store=credential_store,
                        max_completion_tokens=128, timeout=5, extra_body={'reasoning_effort': 'minimal'})
    try:
        assert client._response_params('hi')['reasoning'] == {'effort': 'minimal'}
        body = client._response_params('hi', extra_body={'reasoning_effort': 'low', 'store': True, 'model': 'evil'},
                                       tools=[{'type': 'function', 'function': {'name': 'test', 'parameters': {}}}],
                                       parallel_tool_calls=False, max_completion_tokens=1024)
        assert body['reasoning'] == {'effort': 'low'}
        assert body['parallel_tool_calls'] is False
        assert body['max_output_tokens'] == 1024
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
    from brain import computer_use as cu
    client = MagicMock()
    client.invoke_raw.side_effect = error
    monkeypatch.setattr(cu, 'create_chat_llm', lambda **kw: client)
    adapter = object.__new__(cu.ComputerUseAdapter)
    adapter._config_manager = MagicMock()
    adapter._config_manager.get_model_api_config.return_value = {
        'model': 'selected', 'base_url': 'https://api.openai.com/v1', 'api_key': '', 'provider_type': 'chatgpt'}
    adapter._llm_client = adapter._llm_client_sig = None
    assert adapter.check_connectivity(_retries=2) == (not bool(expected), expected)
    assert client.invoke_raw.call_count == 1


@pytest.mark.parametrize('error', [ChatGPTAuthError('sign in'), ChatGPTResponseError('limit')])
def test_cua_call_terminal_failure_escapes_retry_and_action_loop(error):
    from brain.computer_use import ComputerUseAdapter
    adapter = object.__new__(ComputerUseAdapter)
    adapter._cancelled = False
    adapter._agent_model_cfg = {'model': 'test', 'base_url': 'https://api.openai.com/v1'}
    adapter.max_completion_tokens = 6000
    adapter._config_manager = MagicMock()
    adapter._config_manager.consume_agent_daily_quota.return_value = (True, {})
    adapter._llm_client = MagicMock()
    adapter._llm_client.invoke_raw.side_effect = error
    with pytest.raises(type(error)):
        adapter._call_llm([])
    assert adapter._llm_client.invoke_raw.call_count == 1


class BrowserAction(BaseModel):
    url: str


@pytest.mark.parametrize('mode', ['schema', 'text'])
@pytest.mark.asyncio
async def test_browser_adapter_oauth_responses_schema_and_images(mode, credential_store, monkeypatch):
    from browser_use.llm.messages import SystemMessage, UserMessage
    from brain.browser_use_adapter import BrowserUseAdapter
    from brain import browser_use_chatgpt as bu
    requests = []
    def handler(request):
        requests.append(request)
        event = terminal(text='{"url":"https://example.test"}')
        event['type'] = 'response.completed'
        event['response']['status'] = 'completed'
        event['response']['output'] = event['response']['output'][:1]
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, text='data: ' + json.dumps(event) + '\n\n')
    real_client = bu.ChatChatGPT
    def factory(**kw):
        client = real_client(**kw)
        client.close()
        client._aclient = AsyncOpenAI(api_key='unused', max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        return client
    monkeypatch.setattr(bu, 'ChatChatGPT', factory)
    adapter = object.__new__(BrowserUseAdapter)
    adapter._config_manager = SimpleNamespace(get_model_api_config=lambda _: {
        'model': 'selected', 'base_url': 'https://api.openai.com/v1', 'provider_type': 'chatgpt', 'api_key': ''})
    llm = adapter._build_llm(mode)
    llm.credential_store = credential_store
    messages = [SystemMessage(content='Return JSON'), UserMessage(content=[
        {'type': 'text', 'text': 'Inspect this page'},
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AAAA'}},
    ])]
    result = await llm.ainvoke(messages, BrowserAction)
    assert result.completion.url == 'https://example.test'
    assert result.usage.total_tokens == 19
    body = json.loads(requests[0].content)
    assert body['model'] == 'selected' and body['store'] is False and body['stream'] is True
    assert body['max_output_tokens'] == 6000
    assert body['text']['format']['type'] == ('json_schema' if mode == 'schema' else 'json_object')
    assert body['input'][-1]['content'][-1]['type'] == 'input_image'
    assert requests[0].headers['authorization'] == 'Bearer fixture-token'
    assert requests[0].url.path == '/v1/responses'


@pytest.mark.asyncio
async def test_browser_terminal_error_prevents_later_requests(credential_store, monkeypatch):
    from brain import browser_use_chatgpt as bu
    from browser_use.llm.exceptions import ModelProviderError
    client = SimpleNamespace(ainvoke_raw=AsyncMock(side_effect=ChatGPTResponseError('quota', status_code=429)), aclose=AsyncMock())
    monkeypatch.setattr(bu, 'ChatChatGPT', lambda **kw: client)
    llm = bu.NekoChatChatGPT(model='selected', credential_store=credential_store)
    with pytest.raises(ModelProviderError):
        await llm.ainvoke([])
    with pytest.raises(ChatGPTResponseError):
        await llm.ainvoke([])
    with pytest.raises(ChatGPTResponseError):
        llm.raise_terminal_error()
    assert client.ainvoke_raw.await_count == client.aclose.await_count == 1


@pytest.mark.parametrize('callback', [True, False])
@pytest.mark.asyncio
async def test_browser_task_does_not_fallback_after_terminal_error(monkeypatch, callback):
    import browser_use
    from brain import browser_use_adapter as bu
    adapter = object.__new__(bu.BrowserUseAdapter)
    adapter._config_manager = SimpleNamespace(aconsume_agent_daily_quota=AsyncMock(return_value=(True, {})))
    adapter.is_available = lambda: {'ready': True}
    adapter._get_browser_session = AsyncMock(return_value=object())
    adapter._current_api_signature = lambda: 'test'
    adapter._get_mode_order = lambda _: ['schema', 'text']
    adapter._mark_mode_result = MagicMock()
    adapter._start_overlay = MagicMock()
    adapter._remove_overlay = AsyncMock()
    adapter._close_browser = AsyncMock()
    adapter._get_ip_info = AsyncMock(return_value=(None, None))
    monkeypatch.setattr(bu, '_find_bundled_chromium', lambda: '/fixture/chrome')
    monkeypatch.setattr(bu, '_find_system_chrome_path', lambda: None)
    error = ChatGPTResponseError('plan limit', status_code=429)
    def fail():
        raise error
    llm = SimpleNamespace(provider='chatgpt', raise_terminal_error=fail)
    adapter._build_llm = MagicMock(return_value=llm)
    class FakeAgent:
        def __init__(self, **kw):
            pass
        async def run(self, **kw):
            if callback:
                await kw['on_step_end'](self)
            # browser-use may return an unsuccessful history instead of raising.
            return SimpleNamespace(is_successful=lambda: False)
    monkeypatch.setattr(browser_use, 'Agent', FakeAgent)
    result = await adapter.run_instruction('fixture task')
    assert not result['success']
    assert result['error'] == 'plan limit'
    assert adapter._build_llm.call_count == 1
    adapter._remove_overlay.assert_awaited_once()


@pytest.mark.asyncio
async def test_browser_rejects_truncated_but_parseable_action(credential_store, monkeypatch):
    from brain import browser_use_chatgpt as bu
    from browser_use.llm.exceptions import ModelProviderError
    state = ResponseState()
    state.feed(terminal(text='{"url":"https://example.test"}'))
    client = SimpleNamespace(ainvoke_raw=AsyncMock(return_value=state.raw('test')), aclose=AsyncMock())
    monkeypatch.setattr(bu, 'ChatChatGPT', lambda **kw: client)
    llm = bu.NekoChatChatGPT(model='selected', credential_store=credential_store)
    with pytest.raises(ModelProviderError, match='truncated'):
        await llm.ainvoke([], BrowserAction)
    with pytest.raises(ChatGPTResponseError):
        llm.raise_terminal_error()


def test_cua_rejects_truncated_parseable_action():
    from brain.computer_use import ComputerUseAdapter
    adapter = object.__new__(ComputerUseAdapter)
    adapter._cancelled = False
    adapter._agent_model_cfg = {'model': 'test', 'provider_type': 'chatgpt', 'base_url': 'https://api.openai.com/v1'}
    adapter.max_completion_tokens = 6000
    adapter._config_manager = MagicMock()
    adapter._config_manager.consume_agent_daily_quota.return_value = (True, {})
    state = ResponseState()
    state.feed(terminal(text='```python\ncomputer.click(1, 2)\n```'))
    adapter._llm_client = MagicMock()
    adapter._llm_client.invoke_raw.return_value = state.raw('test')
    with pytest.raises(ChatGPTResponseError, match='truncated'):
        adapter._call_llm([])
    assert adapter._llm_client.invoke_raw.call_count == 1
