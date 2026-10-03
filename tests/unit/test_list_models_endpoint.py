"""Unit tests for the upstream model list endpoint (/api/config/list_models).

The settings page only ever holds masked keys, so the endpoint resolves stored
keys itself. These tests pin down where a stored key may go: a built-in
provider's key only reaches that provider's endpoints from api_providers.json,
and a custom slot's key only reaches the endpoint saved for that slot.
"""

import asyncio
import json
import os
import sys

import httpx
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))


_SENTINEL = '__NEKO_SECRET_MASKED__'


@pytest.fixture()
def config_manager(clean_user_data_dir):
    from utils.config_manager import get_config_manager
    cm = get_config_manager('N.E.K.O')
    cm.config_dir.mkdir(parents=True, exist_ok=True)
    yield cm


@pytest.fixture()
def model_catalog():
    from main_routers.config_router import model_catalog
    return model_catalog


@pytest.fixture()
def fetch_calls(monkeypatch, model_catalog):
    """Replace the network fetch and record where each request would go."""
    calls = []

    async def _fake_fetch(url, api_key, provider_type):
        calls.append({'url': url, 'api_key': api_key, 'provider_type': provider_type})
        return {'success': True, 'models': [{'id': 'model-a'}], 'resolved_url': url}

    monkeypatch.setattr(model_catalog, '_fetch_models', _fake_fetch)
    return calls


def _write_core_config(cm, data: dict):
    path = cm.get_config_path('core_config.json')
    with open(str(path), 'w', encoding='utf-8') as f:
        json.dump(data, f)
    cm._core_config_cache = None


def _raw_assist_profile(provider_key: str) -> dict:
    from utils.api_config_loader import get_config
    return get_config()['assist_api_providers'][provider_key]


def _run(model_catalog, **payload):
    request = model_catalog.ModelListRequest(**payload)
    return asyncio.run(model_catalog.list_models(request))


@pytest.mark.unit
def test_auth_failures_finish_without_waiting_for_timeout(config_manager, model_catalog, monkeypatch):
    calls = []

    async def fail(url, *_args):
        calls.append(url)
        return {'success': False, 'error_code': 'auth_failed'}

    monkeypatch.setattr(model_catalog, '_resolve_provider_target', lambda *_args: {
        'urls': ['https://a.test/v1', 'https://b.test/v1'],
        'api_key': 'bad', 'provider_type': 'openai_compatible',
    })
    monkeypatch.setattr(model_catalog, '_fetch_models', fail)
    assert _run(model_catalog, provider_key='qwen')['error_code'] == 'auth_failed'
    assert calls == ['https://a.test/v1', 'https://b.test/v1']


@pytest.mark.unit
def test_model_list_total_timeout_cancels_fetch(config_manager, model_catalog, monkeypatch):
    cancelled = []

    async def slow(*_args):
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.append(True)

    monkeypatch.setattr(model_catalog, '_MODEL_LIST_TIMEOUT_SECONDS', 0.01)
    monkeypatch.setattr(model_catalog, '_fetch_models', slow)
    assert _run(model_catalog, url='http://localhost:8000/v1')['error_code'] == 'timeout'
    assert cancelled == [True]


@pytest.mark.unit
def test_slow_first_candidate_leaves_time_for_healthy_fallback(config_manager, model_catalog, monkeypatch):
    calls = []
    cancelled = []

    async def fetch(url, *_args):
        calls.append(url)
        if url == 'https://slow.test/v1':
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(url)
        return {'success': True, 'models': [{'id': 'fallback-model'}], 'resolved_url': url}

    monkeypatch.setattr(model_catalog, '_MODEL_LIST_TIMEOUT_SECONDS', 0.1)
    monkeypatch.setattr(model_catalog, '_resolve_provider_target', lambda *_args: {
        'urls': ['https://slow.test/v1', 'https://healthy.test/v1'],
        'api_key': 'sk-test', 'provider_type': 'openai_compatible',
    })
    monkeypatch.setattr(model_catalog, '_fetch_models', fetch)
    result = _run(model_catalog, provider_key='qwen_intl')
    assert result['success'] is True
    assert result['resolved_url'] == 'https://healthy.test/v1'
    assert calls == ['https://slow.test/v1', 'https://healthy.test/v1']
    assert cancelled == ['https://slow.test/v1']


@pytest.mark.unit
def test_slow_preferred_candidate_gets_full_budget(config_manager, model_catalog, monkeypatch):
    async def fetch(url, *_args):
        if url == 'https://preferred.test/v1':
            await asyncio.sleep(0.07)
            return {'success': True, 'models': [{'id': 'preferred-model'}], 'resolved_url': url}
        return {'success': False, 'error_code': 'auth_failed'}

    monkeypatch.setattr(model_catalog, '_MODEL_LIST_TIMEOUT_SECONDS', 0.15)
    monkeypatch.setattr(model_catalog, '_resolve_provider_target', lambda *_args: {
        'urls': ['https://preferred.test/v1', 'https://unavailable.test/v1', 'https://unavailable2.test/v1'],
        'api_key': 'sk-test', 'provider_type': 'openai_compatible',
    })
    monkeypatch.setattr(model_catalog, '_fetch_models', fetch)
    result = _run(model_catalog, provider_key='qwen_intl')
    assert result['success'] is True
    assert result['resolved_url'] == 'https://preferred.test/v1'


@pytest.mark.unit
@pytest.mark.parametrize('fallback_stalls', [True, False])
def test_preferred_auth_error_survives_fallback_failure(
    config_manager, model_catalog, monkeypatch, fallback_stalls,
):
    async def fetch(url, *_args):
        if url == 'https://preferred.test/v1':
            return {'success': False, 'error_code': 'auth_failed', 'error': 'invalid key'}
        if fallback_stalls:
            await asyncio.Event().wait()
        await asyncio.sleep(0)
        return {'success': False, 'error_code': 'network_error'}

    monkeypatch.setattr(model_catalog, '_MODEL_LIST_TIMEOUT_SECONDS', 0.03)
    monkeypatch.setattr(model_catalog, '_resolve_provider_target', lambda *_args: {
        'urls': ['https://preferred.test/v1', 'https://fallback.test/v1'],
        'api_key': 'bad', 'provider_type': 'openai_compatible',
    })
    monkeypatch.setattr(model_catalog, '_fetch_models', fetch)
    result = _run(model_catalog, provider_key='qwen_intl')
    assert result['error_code'] == 'auth_failed'
    assert result['error'] == 'invalid key'


def _status_error(error_cls, status_code: int):
    request = httpx.Request('GET', 'https://upstream.example.test/v1/models')
    response = httpx.Response(status_code, request=request)
    return error_cls(message=f'HTTP {status_code}', response=response, body=None)


class TestBuiltinProvider:
    @pytest.mark.unit
    def test_core_key_source_matches_runtime_and_binds_provider(self, model_catalog):
        config = {'assist_api_providers': {'openai': {'openrouter_url': 'https://api.openai.com/v1'}}}
        req = model_catalog.ModelListRequest(provider_key='openai', key_source='core', api_key=_SENTINEL)
        stored = {'coreApi': 'openai', 'coreApiKey': 'core-key', 'assistApiKeyOpenAI': 'book-key'}
        assert model_catalog._resolve_provider_target(req, stored, config)['api_key'] == 'core-key'
        stored['coreApi'] = 'qwen'
        assert model_catalog._resolve_provider_target(req, stored, config)['error_code'] == 'key_required'

    @pytest.mark.unit
    @pytest.mark.parametrize('flag', ['fixed_model', 'is_free_version'])
    @pytest.mark.parametrize('value,blocked', [('false', False), ('0', False), ('true', True), ('1', True)])
    def test_string_provider_flags_match_runtime(self, model_catalog, flag, value, blocked):
        config = {'assist_api_providers': {'openai': {
            'openrouter_url': 'https://api.openai.com/v1', flag: value,
        }}}
        target = model_catalog._resolve_provider_target(
            model_catalog.ModelListRequest(provider_key='openai'), {}, config,
        )
        assert ('error_code' in target) == blocked

    @pytest.mark.unit
    def test_token_plan_keeps_all_safe_regions(self, model_catalog):
        urls = _raw_assist_profile('mimo')['token_plan_openrouter_urls']
        config = {'assist_api_providers': {'mimo': {
            'token_plan_openrouter_urls': urls + ['http://token-plan-cn.xiaomimimo.com/v1', 'https://evil.test/v1'],
        }}}
        target = model_catalog._resolve_provider_target(
            model_catalog.ModelListRequest(provider_key='mimo', url=urls[0]),
            {'assistApiKeyMimoTokenPlan': 'token-key'}, config,
        )
        assert target['urls'] == urls
        assert target['api_key'] == 'token-key'


    @pytest.mark.unit
    def test_endpoint_comes_from_the_registry_not_the_page(self, config_manager, model_catalog, fetch_calls):
        _write_core_config(config_manager, {'coreApi': 'qwen', 'assistApi': 'openrouter'})

        result = _run(
            model_catalog,
            provider_key='openrouter',
            url='https://attacker.example.test/v1',
            api_key='sk-typed',
        )

        assert result['success'] is True
        assert fetch_calls == [{
            'url': _raw_assist_profile('openrouter')['openrouter_url'],
            'api_key': 'sk-typed',
            'provider_type': 'openai_compatible',
        }]

    @pytest.mark.unit
    @pytest.mark.parametrize('submitted_key', [_SENTINEL, '', '••••••••••••'])
    def test_masked_or_blank_key_resolves_from_the_key_book(
        self, config_manager, model_catalog, fetch_calls, submitted_key,
    ):
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'assistApi': 'openrouter',
            'assistApiKeyOpenrouter': 'sk-or-book',
        })

        _run(model_catalog, provider_key='openrouter', api_key=submitted_key)

        assert fetch_calls[0]['api_key'] == 'sk-or-book'

    @pytest.mark.unit
    def test_core_key_fallback_only_applies_to_the_core_provider(self, config_manager, model_catalog, fetch_calls):
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'coreApiKey': 'sk-qwen-core',
            'assistApi': 'qwen',
        })

        _run(model_catalog, provider_key='qwen', api_key=_SENTINEL)
        _run(model_catalog, provider_key='deepseek', api_key=_SENTINEL)

        assert fetch_calls[0]['api_key'] == 'sk-qwen-core'
        assert fetch_calls[1]['api_key'] == ''

    @pytest.mark.unit
    @pytest.mark.parametrize('provider_key', ['free', 'kimi_code', 'vllm_omni', 'not-a-provider'])
    def test_providers_without_a_listable_catalog_are_refused(
        self, config_manager, model_catalog, fetch_calls, provider_key,
    ):
        _write_core_config(config_manager, {'coreApi': 'qwen', 'assistApi': 'qwen'})

        result = _run(model_catalog, provider_key=provider_key, api_key='sk-typed')

        assert result['success'] is False
        assert result['error_code'] == 'unsupported'
        assert fetch_calls == []

    @pytest.mark.unit
    def test_saved_resolved_url_is_tried_first(self, config_manager, model_catalog, fetch_calls):
        candidates = _raw_assist_profile('qwen_intl')['openrouter_urls']
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'assistApi': 'qwen_intl',
            'assistApiKeyQwenIntl': 'sk-intl',
            'resolvedProviderUrls': {'assist:qwen_intl': candidates[-1]},
        })

        _run(model_catalog, provider_key='qwen_intl', api_key=_SENTINEL)

        assert fetch_calls[0]['url'] == candidates[-1]

    @pytest.mark.unit
    def test_failed_candidate_falls_through_to_the_next(self, config_manager, model_catalog, monkeypatch):
        candidates = _raw_assist_profile('qwen_intl')['openrouter_urls']
        tried = []

        async def _fake_fetch(url, api_key, provider_type):
            tried.append(url)
            if url == candidates[0]:
                return {'success': False, 'error': 'down', 'error_code': 'connection_refused'}
            return {'success': True, 'models': [{'id': 'qwen-x'}], 'resolved_url': url}

        monkeypatch.setattr(model_catalog, '_fetch_models', _fake_fetch)
        _write_core_config(config_manager, {'coreApi': 'qwen', 'assistApi': 'qwen_intl'})

        result = _run(model_catalog, provider_key='qwen_intl', api_key='sk-intl')

        assert result['success'] is True
        assert tried == candidates[:2]

    @pytest.mark.unit
    def test_mimo_token_plan_node_uses_the_token_plan_key(self, config_manager, model_catalog, fetch_calls):
        token_plan_url = _raw_assist_profile('mimo')['token_plan_openrouter_urls'][1]
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'assistApi': 'mimo',
            'assistApiKeyMimo': 'sk-mimo',
            'assistApiKeyMimoTokenPlan': 'tp-mimo',
            'useMimoTokenPlan': True,
        })

        _run(model_catalog, provider_key='mimo', url=token_plan_url, api_key=_SENTINEL)

        assert fetch_calls[0]['url'] == token_plan_url
        assert fetch_calls[0]['api_key'] == 'tp-mimo'

    @pytest.mark.unit
    def test_plain_http_token_plan_url_never_gets_the_token_plan_key(self, config_manager, model_catalog, fetch_calls):
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'assistApi': 'mimo',
            'assistApiKeyMimo': 'sk-mimo',
            'assistApiKeyMimoTokenPlan': 'tp-mimo',
            'useMimoTokenPlan': True,
        })

        _run(model_catalog, provider_key='mimo', url='http://token-plan-cn.xiaomimimo.com/v1', api_key=_SENTINEL)

        assert len(fetch_calls) == 1
        assert fetch_calls[0]['url'].startswith('https://')
        assert 'token-plan' not in fetch_calls[0]['url']
        assert fetch_calls[0]['api_key'] == 'sk-mimo'

    @pytest.mark.unit
    def test_anthropic_provider_keeps_its_protocol(self, config_manager, model_catalog, fetch_calls):
        _write_core_config(config_manager, {'coreApi': 'qwen', 'assistApi': 'claude'})

        _run(model_catalog, provider_key='claude', api_key='sk-ant-typed')

        assert fetch_calls[0]['provider_type'] == 'anthropic'


class TestCustomEndpoint:

    @pytest.mark.unit
    def test_typed_key_is_used(self, config_manager, model_catalog, fetch_calls):
        _write_core_config(config_manager, {'coreApi': 'qwen'})

        _run(model_catalog, url='http://127.0.0.1:11434/v1', api_key='sk-local', model_type='conversation')

        assert fetch_calls == [{
            'url': 'http://127.0.0.1:11434/v1',
            'api_key': 'sk-local',
            'provider_type': 'openai_compatible',
        }]

    @pytest.mark.unit
    def test_masked_key_reuses_the_stored_key_for_the_saved_endpoint(
        self, config_manager, model_catalog, fetch_calls,
    ):
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'conversationModelUrl': 'https://relay.example.test/v1',
            'conversationModelApiKey': 'sk-slot',
        })

        _run(model_catalog, url='https://relay.example.test/v1/', api_key=_SENTINEL, model_type='conversation')

        assert fetch_calls[0]['api_key'] == 'sk-slot'

    @pytest.mark.unit
    @pytest.mark.parametrize('model_type', ['conversation', '', 'notASlot'])
    def test_masked_key_is_refused_for_any_other_endpoint(
        self, config_manager, model_catalog, fetch_calls, model_type,
    ):
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'conversationModelUrl': 'https://relay.example.test/v1',
            'conversationModelApiKey': 'sk-slot',
        })

        result = _run(
            model_catalog,
            url='https://attacker.example.test/v1',
            api_key=_SENTINEL,
            model_type=model_type,
        )

        assert result['success'] is False
        assert result['error_code'] == 'key_required'
        assert fetch_calls == []

    @pytest.mark.unit
    @pytest.mark.parametrize('url,error_code', [
        ('wss://realtime.example.test/v1', 'unsupported'),
        ('file:///etc/passwd', 'unsupported'),
        ('', 'missing_params'),
    ])
    def test_unusable_urls_are_refused(self, config_manager, model_catalog, fetch_calls, url, error_code):
        _write_core_config(config_manager, {'coreApi': 'qwen'})

        result = _run(model_catalog, url=url, api_key='sk-typed')

        assert result['error_code'] == error_code
        assert fetch_calls == []

    @pytest.mark.unit
    def test_anthropic_url_switches_the_protocol(self, config_manager, model_catalog, fetch_calls):
        _write_core_config(config_manager, {'coreApi': 'qwen'})

        _run(model_catalog, url='https://api.anthropic.com/v1', api_key='sk-ant-typed')

        assert fetch_calls[0]['provider_type'] == 'anthropic'


class TestNormalization:

    @pytest.mark.unit
    def test_entries_are_cleaned_sorted_and_deduplicated(self, model_catalog):
        entries = model_catalog._normalize_model_entries([
            {'id': 'models/gemini-2.5-flash', 'name': 'Gemini 2.5 Flash'},
            {'id': 'gemini-2.5-flash', 'name': 'duplicate'},
            {'id': '  Zeta-Model  ', 'name': 'Zeta-Model'},
            {'id': 'alpha', 'name': ''},
            {'id': '   ', 'name': 'blank'},
        ], strip_gemini_prefix=True)

        assert entries == [
            {'id': 'alpha'},
            {'id': 'gemini-2.5-flash', 'name': 'Gemini 2.5 Flash'},
            {'id': 'Zeta-Model'},
        ]

    @pytest.mark.unit
    def test_models_prefix_is_kept_unless_asked_to_strip(self, model_catalog):
        entries = model_catalog._normalize_model_entries(
            [{'id': 'models/example'}, {'id': 'example'}], strip_gemini_prefix=False,
        )

        assert entries == [{'id': 'example'}, {'id': 'models/example'}]


class _FakeCatalogClient:
    instances = []

    def __init__(self, raw_models=None, error=None, **kwargs):
        self.kwargs = kwargs
        self.raw_models = raw_models or []
        self.error = error
        self.closed = False
        _FakeCatalogClient.instances.append(self)

    async def alist_models(self, *, limit):
        self.limit = limit
        if self.error is not None:
            raise self.error
        return self.raw_models

    async def aclose(self):
        self.closed = True


def _patch_client(monkeypatch, attr, **behaviour):
    import utils.llm_client as llm_client
    _FakeCatalogClient.instances = []
    monkeypatch.setattr(llm_client, attr, lambda **kwargs: _FakeCatalogClient(**behaviour, **kwargs))


class TestFetchModels:

    @pytest.mark.unit
    def test_openai_compatible_fetch_normalizes_and_closes(self, model_catalog, monkeypatch):
        _patch_client(monkeypatch, 'ChatOpenAI', raw_models=[{'id': 'b'}, {'id': 'a'}])

        result = asyncio.run(model_catalog._fetch_models('https://x.example.test/v1', 'sk', 'openai_compatible'))

        client = _FakeCatalogClient.instances[0]
        assert result == {
            'success': True,
            'models': [{'id': 'a'}, {'id': 'b'}],
            'resolved_url': 'https://x.example.test/v1',
        }
        assert client.closed is True
        assert client.kwargs['max_retries'] == 0
        assert client.kwargs['timeout'] > 0

    @pytest.mark.unit
    def test_only_the_gemini_endpoint_drops_the_models_prefix(self, model_catalog, monkeypatch):
        _patch_client(monkeypatch, 'ChatOpenAI', raw_models=[{'id': 'models/gemini-2.5-flash'}])

        gemini = asyncio.run(model_catalog._fetch_models(
            'https://generativelanguage.googleapis.com/v1beta/openai/', 'sk', 'openai_compatible'))
        other = asyncio.run(model_catalog._fetch_models('https://x.example.test/v1', 'sk', 'openai_compatible'))

        assert gemini['models'] == [{'id': 'gemini-2.5-flash'}]
        assert other['models'] == [{'id': 'models/gemini-2.5-flash'}]

    @pytest.mark.unit
    def test_anthropic_fetch_uses_the_anthropic_client(self, model_catalog, monkeypatch):
        _patch_client(monkeypatch, 'ChatAnthropic', raw_models=[{'id': 'claude-x', 'name': 'Claude X'}])

        result = asyncio.run(model_catalog._fetch_models('https://api.anthropic.com/v1', 'sk', 'anthropic'))

        assert result['models'] == [{'id': 'claude-x', 'name': 'Claude X'}]
        assert _FakeCatalogClient.instances[0].closed is True

    @pytest.mark.unit
    def test_empty_catalog_is_a_failure(self, model_catalog, monkeypatch):
        _patch_client(monkeypatch, 'ChatOpenAI', raw_models=[])

        result = asyncio.run(model_catalog._fetch_models('https://x.example.test/v1', 'sk', 'openai_compatible'))

        assert result['success'] is False
        assert result['error_code'] == 'empty'

    @pytest.mark.unit
    @pytest.mark.parametrize('error_name,status_code,error_code', [
        ('NotFoundError', 404, 'unsupported'),
        ('AuthenticationError', 401, 'auth_failed'),
        ('RateLimitError', 429, 'rate_limited'),
    ])
    def test_openai_errors_are_classified(self, model_catalog, monkeypatch, error_name, status_code, error_code):
        import openai
        error = _status_error(getattr(openai, error_name), status_code)
        _patch_client(monkeypatch, 'ChatOpenAI', error=error)

        result = asyncio.run(model_catalog._fetch_models('https://x.example.test/v1', 'sk', 'openai_compatible'))

        assert result['success'] is False
        assert result['error_code'] == error_code
        assert _FakeCatalogClient.instances[0].closed is True

    @pytest.mark.unit
    @pytest.mark.parametrize('error_name,status_code,error_code', [
        ('NotFoundError', 404, 'unsupported'),
        ('AuthenticationError', 401, 'auth_failed'),
    ])
    def test_anthropic_errors_are_classified(self, model_catalog, monkeypatch, error_name, status_code, error_code):
        import anthropic
        error = _status_error(getattr(anthropic, error_name), status_code)
        _patch_client(monkeypatch, 'ChatAnthropic', error=error)

        result = asyncio.run(model_catalog._fetch_models('https://api.anthropic.com/v1', 'sk', 'anthropic'))

        assert result['error_code'] == error_code


class _AsyncItems:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


class TestClientListModels:

    @pytest.mark.unit
    def test_chat_openai_reports_ids_names_and_honours_the_limit(self, monkeypatch):
        from openai.types import Model
        from utils.llm_client import ChatOpenAI

        upstream = [
            Model.model_validate({'id': 'google/gemini-2.5-flash', 'created': 0, 'object': 'model',
                                  'owned_by': 'google', 'name': 'Gemini 2.5 Flash'}),
            Model.model_validate({'id': 'qwen/qwen3.5-9b', 'created': 0, 'object': 'model', 'owned_by': 'qwen'}),
            Model.model_validate({'id': 'over-the-limit', 'created': 0, 'object': 'model', 'owned_by': 'x'}),
        ]

        async def _scenario():
            client = ChatOpenAI(base_url='https://x.example.test/v1', api_key='sk', timeout=5, max_retries=0,
                                max_completion_tokens=1)
            try:
                monkeypatch.setattr(client._aclient.models, 'list', lambda **kwargs: _AsyncItems(upstream))
                return await client.alist_models(limit=2)
            finally:
                await client.aclose()

        assert asyncio.run(_scenario()) == [
            {'id': 'google/gemini-2.5-flash', 'name': 'Gemini 2.5 Flash'},
            {'id': 'qwen/qwen3.5-9b', 'name': ''},
        ]

    @pytest.mark.unit
    def test_chat_anthropic_reports_display_names(self, monkeypatch):
        from types import SimpleNamespace
        from utils.llm_client import ChatAnthropic

        seen_kwargs = {}

        def _fake_list(**kwargs):
            seen_kwargs.update(kwargs)
            return _AsyncItems([SimpleNamespace(id='claude-x', display_name='Claude X')])

        async def _scenario():
            client = ChatAnthropic(base_url='https://api.anthropic.com/v1', api_key='sk', timeout=5, max_retries=0)
            try:
                monkeypatch.setattr(client._aclient.models, 'list', _fake_list)
                return await client.alist_models(limit=5000)
            finally:
                await client.aclose()

        assert asyncio.run(_scenario()) == [{'id': 'claude-x', 'name': 'Claude X'}]
        assert seen_kwargs == {'limit': 1000}
