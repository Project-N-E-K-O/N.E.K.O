"""Subscription transitions use isolated config and mocked accounts, never live credentials."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from utils.chatgpt_subscription import (
    enable, disable, preview, restricted_slots, revision, runtime_custom_api, runtime_model_type, supported_slots,
)
from utils.llm_client.chatgpt_client import ChatChatGPT, ChatGPTResponseError


@pytest.fixture
def manager(clean_user_data_dir):
    from utils.config_manager import get_config_manager
    result = get_config_manager('N.E.K.O')
    result.config_dir.mkdir(parents=True, exist_ok=True)
    return result


@pytest.mark.parametrize('raw_value, expected', [
    (False, False), (True, True), ('false', False), ('true', True),
    (' OFF ', False), (' On ', True), ('0', False), ('1', True),
    (0, False), (1, True), (None, False),
])
def test_legacy_boolean_flags_preserve_subscription_scope_and_exact_restore(raw_value, expected):
    config = {'enableCustomApi': raw_value}
    original = deepcopy(config)
    slots = ['conversation']
    available_slots = ['conversation', 'omni', 'tts', 'image']

    enabled, active_slots = runtime_custom_api(config)
    assert enabled is expected
    assert active_slots == []
    assert restricted_slots(config, available_slots) == []

    enable(config, slots, 'model', revision(config, slots))
    # Saved snapshots from older versions retain the original JSON value.
    config = json.loads(json.dumps(config))
    enabled, active_slots = runtime_custom_api(config)
    assert enabled is expected
    assert active_slots == ([] if expected else slots)
    assert restricted_slots(config, available_slots) == ([] if expected else ['omni', 'tts', 'image'])
    assert config['chatgptSubscription']['before'] == original

    assert disable(config)
    assert config == original
    enabled, active_slots = runtime_custom_api(config)
    assert enabled is expected
    assert active_slots == []


def test_missing_custom_api_flag_preserves_scope_and_field_absence():
    config = {}
    slots = ['conversation']
    assert runtime_custom_api(config) == (False, [])
    enable(config, slots, 'model', revision(config, slots))
    assert runtime_custom_api(config) == (False, slots)
    assert restricted_slots(config, ['conversation', 'tts']) == ['tts']
    assert disable(config)
    assert config == {}
    assert runtime_custom_api(config) == (False, [])


@pytest.mark.asyncio
@pytest.mark.parametrize('custom', [False, 'false', ' OFF ', True, 'true'])
async def test_save_connectivity_targets_follow_effective_subscription_scope(custom, monkeypatch):
    from main_routers.config_router import connectivity
    from utils.config_manager._shared import _as_bool

    # Each dormant provider has two endpoints so an accidental target would
    # trigger region probing when ordinary settings are saved.
    profiles = {
        name: {'openrouter_urls': [f'https://first.{name}.test/v1', f'https://second.{name}.test/v1'],
               'is_free_version': True}
        for name in ('text-provider', 'speech-provider')
    }
    api_config = {
        'assist_api_providers': profiles,
        'core_api_providers': {
            'realtime-provider': {
                'core_urls': ['wss://first.test/v1', 'wss://second.test/v1'],
                'is_free_version': True,
            },
        },
    }
    config = {'enableCustomApi': custom, 'coreApi': 'core', 'assistApi': 'assist',
              'conversationModelProvider': 'text-provider',
              'ttsModelProvider': 'speech-provider', 'omniModelProvider': 'realtime-provider'}
    http_probe = AsyncMock(return_value={'success': True})
    websocket_probe = AsyncMock(return_value={'success': True})
    monkeypatch.setattr('utils.api_config_loader.get_config', lambda: api_config)
    monkeypatch.setattr(connectivity, '_test_openai_compatible', http_probe)
    monkeypatch.setattr(connectivity, '_test_websocket', websocket_probe)

    async def assert_probe_targets(expected):
        http_probe.reset_mock()
        websocket_probe.reset_mock()
        targets = connectivity._build_save_connectivity_targets(config, api_config)
        assert set(targets) == expected
        result = await connectivity._auto_resolve_provider_urls_for_save(config)
        assert result['success'] == len(expected)
        assert result['failed'] == 0
        assert set(result['results']) == expected
        calls = http_probe.await_args_list + websocket_probe.await_args_list
        assert {call.args[0] for call in calls} == {
            url for target in targets.values() for url in target['urls']
        }

    expected = ({'assist:text-provider', 'assist:speech-provider', 'core:realtime-provider'}
                if _as_bool(custom) else set())
    await assert_probe_targets(expected)

    slots = ['conversation']
    enable(config, slots, 'model', revision(config, slots))
    # A subscribed slot may subsequently select another built-in provider.
    config['conversationModelProvider'] = 'text-provider'
    await assert_probe_targets(expected if _as_bool(custom) else {'assist:text-provider'})
    assert disable(config)
    await assert_probe_targets(expected)


@pytest.mark.parametrize('custom', [False, True])
def test_exact_restore_and_speech_runtime_isolation(manager, custom, monkeypatch):
    config = {'coreApi': 'qwen', 'assistApi': 'openai', 'coreApiKey': 'core-secret',
              'enableCustomApi': custom, 'assistApiKeyOpenai': 'book-secret',
              'conversationModelProvider': 'custom', 'conversationModelId': 'old-model',
              'conversationModelUrl': 'https://fixture.test/v1', 'conversationModelApiKey': 'slot-secret',
              'omniModelProvider': 'custom', 'omniModelId': 'dormant-omni',
              'omniModelUrl': 'https://voice.test/v1', 'omniModelApiKey': 'voice-secret',
              'ttsModelProvider': 'custom', 'ttsModelId': 'dormant-tts',
              'ttsModelUrl': 'https://tts.test/v1', 'ttsModelApiKey': 'tts-secret',
              'disableTts': False, 'imageModelProvider': 'disabled'}
    original = deepcopy(config)
    manager.save_json_config('core_config.json', config)
    speech = {slot: manager.get_model_api_config(slot) for slot in ('realtime', 'tts_default', 'tts_custom', 'image')}
    slots = supported_slots()
    enable(config, slots, 'available-model', revision(config, slots))
    manager.save_json_config('core_config.json', config)
    assert {slot: manager.get_model_api_config(slot) for slot in speech} == speech
    for slot in slots:
        assert config[f'{slot}ModelProvider'] == 'chatgpt'
        assert config[f'{slot}ModelApiKey'] == ''
        resolved = manager.get_model_api_config(runtime_model_type(slot))
        assert resolved['model'] == 'available-model'
        assert resolved['provider_type'] == 'chatgpt'
    public = json.dumps(preview(config, slots))
    assert 'secret' not in public and 'before' not in public
    # An unrelated change while enabled must survive restoration.
    config['disableTts'] = True
    assert disable(config)
    assert config == {**original, 'disableTts': True}
    assert not disable(config)


def test_subscription_slots_follow_current_tier_names():
    slots = set(supported_slots())
    # Renamed tiers and the whole-follow familiar/minigame tiers are enrolled.
    assert {'memory', 'recognition', 'agentSmall', 'agentAdvanced', 'gameRecognition'} <= slots
    # Old names, the retired vision slot and the dedicated decision protocol never are.
    assert not slots & {'summary', 'emotion', 'correction', 'vision', 'decision', 'omni', 'tts', 'image'}
    assert [runtime_model_type(slot) for slot in ('gameMain', 'agentSmall', 'gameRecognition', 'memory')] == [
        'game_main', 'agent_small', 'game_recognition', 'memory']


@pytest.mark.parametrize('custom', [False, True])
@pytest.mark.parametrize('gptsovits', [False, True])
def test_subscription_preserves_effective_tts_voice_fallback(manager, custom, gptsovits):
    config = {'coreApi': 'qwen', 'assistApi': 'openai', 'coreApiKey': 'fixture-key',
              'assistApiKeyOpenai': 'fixture-key', 'enableCustomApi': custom,
              'ttsProvider': 'deepseek', 'ttsModelProvider': 'gptsovits' if gptsovits else 'custom',
              'ttsModelUrl': 'http://127.0.0.1:9880', 'ttsVoiceId': 'saved-voice'}
    original = deepcopy(config)
    manager.save_json_config('core_config.json', config)
    before = manager.get_core_config().get('TTS_VOICE_ID')
    if custom or gptsovits:
        assert before == 'saved-voice'
    else:
        assert before != 'saved-voice'
    slots = supported_slots()
    enable(config, slots, 'model', revision(config, slots))
    manager.save_json_config('core_config.json', config)
    assert manager.get_core_config().get('TTS_VOICE_ID') == before
    assert disable(config)
    assert config == original
    manager.save_json_config('core_config.json', config)
    assert manager.get_core_config().get('TTS_VOICE_ID') == before


def test_stale_preview_and_repeated_enable_keep_original_snapshot():
    config = {'conversationModelApiKey': 'private'}
    slots = supported_slots()
    old_revision = revision(config, slots)
    config['conversationModelId'] = 'changed-in-another-tab'
    with pytest.raises(ValueError, match='configurationChanged'):
        enable(config, slots, 'model', old_revision)
    before = deepcopy(config)
    enable(config, slots, 'model', revision(config, slots))
    first = deepcopy(config)
    enable(config, slots, 'different-model', revision(config, slots))
    assert config == first
    disable(config)
    assert config == before


@pytest.fixture
def routes(manager, monkeypatch):
    from main_routers.config_router import chatgpt, core_config
    import asyncio
    monkeypatch.setattr(core_config, '_core_api_save_lock', asyncio.Lock())
    monkeypatch.setattr(core_config, 'reload_api_configuration', AsyncMock(return_value={'success': True}))
    store = SimpleNamespace(status=lambda: {'active': 'fixture-account', 'accounts': [
        {'id': 'fixture-account', 'connected': True}]}, access_token=lambda: 'fixture-token')
    monkeypatch.setattr(chatgpt, 'get_store', lambda: store)
    client = SimpleNamespace(alist_models=AsyncMock(return_value=[{'id': 'z-model'}, {'id': 'a-model'}]), aclose=AsyncMock())
    monkeypatch.setattr('utils.llm_client.chatgpt_client.ChatChatGPT', lambda **k: client)
    return chatgpt, core_config, store, client


@pytest.mark.asyncio
async def test_endpoint_save_then_restore_survives_page_reload(manager, routes):
    route, core, store, client = routes
    manager.save_json_config('core_config.json', {'conversationModelProvider': 'openai', 'coreApiKey': 'secret'})
    initial = await route.chatgpt_subscription_preview()
    result = await route.chatgpt_subscription_update(route.SubscriptionRequest(enabled=True, revision=initial['revision']))
    assert result['success'] and result['subscription']['model'] == 'a-model'
    assert (await route.chatgpt_subscription_preview())['enabled']
    await route.chatgpt_subscription_update(route.SubscriptionRequest(enabled=False))
    assert manager.load_json_config('core_config.json') == {'conversationModelProvider': 'openai', 'coreApiKey': 'secret'}
    assert core.reload_api_configuration.await_count == 2
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['signed-out', 'empty', 'catalog-error', 'stale'])
async def test_enable_failure_never_changes_configuration(manager, routes, failure):
    route, core, store, client = routes
    original = {'enableCustomApi': False, 'conversationModelApiKey': 'secret'}
    manager.save_json_config('core_config.json', original)
    rev = revision(original, supported_slots())
    if failure == 'signed-out': store.status = lambda: {'active': None}
    if failure == 'empty': client.alist_models.return_value = []
    if failure == 'catalog-error': client.alist_models.side_effect = RuntimeError('offline')
    if failure == 'stale': rev = 'obsolete'
    with pytest.raises((HTTPException, RuntimeError)):
        await route.chatgpt_subscription_update(route.SubscriptionRequest(enabled=True, revision=rev))
    assert manager.load_json_config('core_config.json') == original
    core.reload_api_configuration.assert_not_awaited()


@pytest.mark.asyncio
async def test_catalog_http_429_returns_structured_error_without_saving(manager, routes, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from utils.chatgpt_auth import store as auth

    route, core, store, _ = routes
    original = {'enableCustomApi': False, 'conversationModelProvider': 'custom'}
    manager.save_json_config('core_config.json', original)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(429, json={'error': {'message': 'fixture quota'}})

    client = ChatChatGPT(credential_store=store, timeout=5)
    monkeypatch.setattr(auth, 'http_client', lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr('utils.llm_client.chatgpt_client.ChatChatGPT', lambda **kw: client)
    monkeypatch.setattr(route, '_attempt', None)
    app = FastAPI()
    app.include_router(route.router)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 123), raise_app_exceptions=False),
            base_url='http://127.0.0.1:48999',
        ) as api:
            response = await api.post('/api/config/chatgpt/subscription',
                                      json={'enabled': True, 'revision': revision(original, supported_slots())})
        assert response.status_code == 429
        assert response.json() == {'detail': 'usageLimit'}
        assert len(requests) == 1 and requests[0].url.path == '/v1/models'
        assert manager.load_json_config('core_config.json') == original
        core.reload_api_configuration.assert_not_awaited()
        assert client._client.is_closed() and client._aclient.is_closed()
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('status, expected_status', [(None, 502), (502, 502)])
async def test_catalog_response_error_preserves_safe_status(manager, routes, status, expected_status):
    route, core, store, client = routes
    original = {'enableCustomApi': False}
    manager.save_json_config('core_config.json', original)
    client.alist_models.side_effect = ChatGPTResponseError('fixture response failure', status_code=status)
    with pytest.raises(HTTPException) as error:
        await route.chatgpt_subscription_update(route.SubscriptionRequest(
            enabled=True, revision=revision(original, supported_slots())))
    assert error.value.status_code == expected_status and error.value.detail == 'failed'
    assert manager.load_json_config('core_config.json') == original
    client.aclose.assert_awaited_once()
    core.reload_api_configuration.assert_not_awaited()


@pytest.mark.asyncio
async def test_last_account_logout_restores_before_revoking(manager, routes, monkeypatch):
    route, core, store, client = routes
    original = {'enableCustomApi': True, 'conversationModelProvider': 'custom', 'conversationModelApiKey': 'secret'}
    config = deepcopy(original)
    enable(config, supported_slots(), 'model', revision(config, supported_slots()))
    manager.save_json_config('core_config.json', config)
    def change(action, account_id):
        assert action == 'sign_out'
        assert manager.load_json_config('core_config.json') == original
        return False
    monkeypatch.setattr(route, '_change_account', change)
    result = await route.chatgpt_logout(route.AccountRequest(account_id='fixture-account'))
    assert result['subscription_restored'] and not result['revocation_confirmed']
    core.reload_api_configuration.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['write', 'authorization'])
async def test_logout_failure_still_reloads_restored_configuration(manager, routes, monkeypatch, failure):
    from utils.chatgpt_auth import ChatGPTAuthError

    route, core, store, _ = routes
    original = {'enableCustomApi': False, 'conversationModelProvider': 'custom'}
    config = deepcopy(original)
    slots = supported_slots()
    enable(config, slots, 'model', revision(config, slots))
    manager.save_json_config('core_config.json', config)

    def fail_sign_out(action, account_id):
        assert manager.load_json_config('core_config.json') == original
        if failure == 'write':
            raise OSError('fixture credential write failure')
        raise ChatGPTAuthError('fixture sign-out authorization failure')

    async def reload_restored(value):
        assert not core._core_api_save_lock.locked()
        assert value == original
        return {'success': True}

    monkeypatch.setattr(route, '_change_account', fail_sign_out)
    core.reload_api_configuration.side_effect = reload_restored
    with pytest.raises(HTTPException) as error:
        await route.chatgpt_logout(route.AccountRequest(account_id='fixture-account'))
    assert error.value.status_code == (503 if failure == 'write' else 400)
    assert manager.load_json_config('core_config.json') == original
    core.reload_api_configuration.assert_awaited_once()


@pytest.mark.asyncio
async def test_current_account_logout_keeps_subscription_usable_with_another_account(manager, routes, monkeypatch, tmp_path):
    import httpx
    from utils.chatgpt_auth import CredentialStore
    from utils.chatgpt_auth import store as auth
    route, core, _, client = routes
    credentials = CredentialStore(tmp_path / 'accounts.json')
    for account_id in ('remaining-account', 'current-account'):
        credentials.save(account_id, {'sub': account_id}, {
            'access_token': account_id + '-access', 'refresh_token': account_id + '-refresh',
            'expires_in': 3600, 'scope': auth.PLAN_SCOPE,
        })
    monkeypatch.setattr(route, 'get_store', lambda: credentials)
    monkeypatch.setattr(auth, 'http_client', lambda: httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={'revocation_endpoint': auth.ISSUER + '/revoke'})
    )))
    config = {'enableCustomApi': False}
    enable(config, supported_slots(), 'model', revision(config, supported_slots()))
    manager.save_json_config('core_config.json', config)
    result = await route.chatgpt_logout(route.AccountRequest(account_id='current-account'))
    assert not result['subscription_restored']
    assert manager.load_json_config('core_config.json') == config
    assert credentials.status()['active'] == 'remaining-account'
    assert credentials.access_token() == 'remaining-account-access'
    core.reload_api_configuration.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_authorization_warns_and_keeps_restore_available(manager, routes):
    from utils.chatgpt_auth import ChatGPTAuthError
    route, core, store, client = routes
    config = {}
    enable(config, supported_slots(), 'model', revision(config, supported_slots()))
    manager.save_json_config('core_config.json', config)
    def expired(): raise ChatGPTAuthError('expired')
    store.access_token = expired
    state = await route.chatgpt_subscription_preview(verify_authorization=True)
    assert state['enabled'] and state['authorization_required']
    await route.chatgpt_subscription_update(route.SubscriptionRequest(enabled=False))
    assert manager.load_json_config('core_config.json') == {}


@pytest.mark.asyncio
async def test_pending_login_does_not_partially_restore_on_logout(manager, routes, monkeypatch):
    route, core, store, client = routes
    config = {}
    enable(config, supported_slots(), 'model', revision(config, supported_slots()))
    manager.save_json_config('core_config.json', config)
    monkeypatch.setattr(route, '_attempt', SimpleNamespace(done=SimpleNamespace(is_set=lambda: False)))
    with pytest.raises(HTTPException) as error:
        await route.chatgpt_logout(route.AccountRequest(account_id='fixture-account'))
    assert error.value.status_code == 400
    assert manager.load_json_config('core_config.json') == config
    with pytest.raises(HTTPException):
        await route.chatgpt_subscription_update(route.SubscriptionRequest(enabled=True, revision=revision(config, supported_slots())))
    assert manager.load_json_config('core_config.json') == config


@pytest.mark.asyncio
async def test_stale_regular_save_cannot_undo_subscription(manager, routes, monkeypatch):
    route, core, store, client = routes
    config = {'coreApi': 'qwen', 'assistApi': 'qwen'}
    enable(config, supported_slots(), 'model', revision(config, supported_slots()))
    manager.save_json_config('core_config.json', config)
    request = SimpleNamespace(json=AsyncMock(return_value={'enableCustomApi': False}))
    result = await core.update_core_config(request)
    assert not result['success'] and result.get('error_code') == 'chatgptManagedSettings', result
    assert manager.load_json_config('core_config.json') == config


@pytest.mark.asyncio
@pytest.mark.parametrize('slot', ['image', 'omni', 'tts'])
async def test_subscription_rejects_independent_slot_changes(manager, routes, monkeypatch, slot):
    route, core, store, client = routes
    monkeypatch.setattr(core, '_auto_resolve_provider_urls_for_save', AsyncMock(return_value={}))
    monkeypatch.setattr(core, 'ensure_default_yui_voice_for_free_api', AsyncMock())
    config = {'coreApi': 'qwen', 'assistApi': 'qwen', 'enableCustomApi': False,
              'imageModelProvider': 'disabled'}
    enable(config, supported_slots(), 'model', revision(config, supported_slots()))
    manager.save_json_config('core_config.json', config)
    request = SimpleNamespace(json=AsyncMock(return_value={
        'enableCustomApi': True, f'{slot}ModelProvider': 'custom',
        f'{slot}ModelUrl': 'https://fixture.test/v1', f'{slot}ModelId': 'independent-model',
        f'{slot}ModelApiKey': 'independent-key',
    }))
    result = await core.update_core_config(request)
    assert not result['success'] and result.get('error_code') == 'chatgptIndependentSettings', result
    assert manager.load_json_config('core_config.json') == config
    core.reload_api_configuration.assert_not_awaited()


@pytest.mark.asyncio
async def test_ordinary_subscription_save_preserves_dormant_slots(manager, routes, monkeypatch):
    route, core, store, client = routes
    monkeypatch.setattr(core, '_auto_resolve_provider_urls_for_save', AsyncMock(return_value={}))
    monkeypatch.setattr(core, 'ensure_default_yui_voice_for_free_api', AsyncMock())
    config = {'coreApi': 'qwen', 'assistApi': 'qwen', 'enableCustomApi': False,
              'ttsModelProvider': 'custom', 'ttsModelUrl': 'https://tts.test/v1',
              'ttsModelId': 'dormant', 'ttsModelApiKey': 'private'}
    enable(config, supported_slots(), 'model', revision(config, supported_slots()))
    manager.save_json_config('core_config.json', config)
    submitted = {key: value for key, value in config.items() if key != 'chatgptSubscription'}
    submitted.update(conversationModelId='new-model', disableTts=True,
                     ttsModelApiKey=core.CORE_CONFIG_SECRET_SENTINEL,
                     imageModelProvider='disabled', omniModelProvider='follow_core')
    result = await core.update_core_config(SimpleNamespace(json=AsyncMock(return_value=submitted)))
    assert result['success'], result
    saved = manager.load_json_config('core_config.json')
    assert saved['conversationModelId'] == 'new-model' and saved['disableTts']
    assert saved['ttsModelApiKey'] == 'private'
    assert 'omniModelProvider' not in saved and 'imageModelProvider' not in saved


@pytest.mark.asyncio
@pytest.mark.parametrize('custom', [False, True])
async def test_settings_response_exposes_only_restricted_slot_names(manager, routes, custom):
    route, core, store, client = routes
    config = {'coreApi': 'qwen', 'assistApi': 'qwen', 'enableCustomApi': custom,
              'conversationModelApiKey': 'private-before-subscription'}
    enable(config, supported_slots(), 'model', revision(config, supported_slots()))
    manager.save_json_config('core_config.json', config)
    result = await core.get_core_config_api()
    assert result['success'], result
    assert result['chatgptIndependentSlots'] == ([] if custom else ['decision', 'vision', 'omni', 'tts', 'image'])
    assert 'private-before-subscription' not in json.dumps(result)
    assert 'chatgptSubscription' not in result


@pytest.mark.asyncio
@pytest.mark.parametrize('slot', ['image', 'omni', 'tts'])
async def test_subscription_keeps_previously_enabled_independent_slots_editable(manager, routes, monkeypatch, slot):
    route, core, store, client = routes
    monkeypatch.setattr(core, '_auto_resolve_provider_urls_for_save', AsyncMock(return_value={}))
    monkeypatch.setattr(core, 'ensure_default_yui_voice_for_free_api', AsyncMock())
    config = {'coreApi': 'qwen', 'assistApi': 'qwen', 'enableCustomApi': True}
    enable(config, supported_slots(), 'model', revision(config, supported_slots()))
    manager.save_json_config('core_config.json', config)
    changes = {'enableCustomApi': True, f'{slot}ModelProvider': 'custom',
               f'{slot}ModelUrl': 'https://fixture.test/v1', f'{slot}ModelId': 'new-model',
               f'{slot}ModelApiKey': 'new-key'}
    result = await core.update_core_config(SimpleNamespace(json=AsyncMock(return_value=changes)))
    assert result['success'], result
    saved = manager.load_json_config('core_config.json')
    assert all(saved[key] == value for key, value in changes.items())


@pytest.mark.asyncio
async def test_independent_image_configuration_works_after_subscription_restore(manager, routes, monkeypatch):
    route, core, store, client = routes
    monkeypatch.setattr(core, '_auto_resolve_provider_urls_for_save', AsyncMock(return_value={}))
    monkeypatch.setattr(core, 'ensure_default_yui_voice_for_free_api', AsyncMock())
    config = {'coreApi': 'qwen', 'assistApi': 'qwen', 'enableCustomApi': False}
    enable(config, supported_slots(), 'model', revision(config, supported_slots()))
    manager.save_json_config('core_config.json', config)
    await route.chatgpt_subscription_update(route.SubscriptionRequest(enabled=False))
    request = SimpleNamespace(json=AsyncMock(return_value={
        'enableCustomApi': True, 'imageModelProvider': 'custom',
        'imageModelUrl': 'https://fixture.test/v1', 'imageModelId': 'image-model',
        'imageModelApiKey': 'image-key',
    }))
    result = await core.update_core_config(request)
    assert result['success'], result
    assert manager.get_model_api_config('image')['enabled']


def test_locales_and_disclosure_contract():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    required = {'useSubscription', 'enabled', 'authorizationRequired', 'confirmEnable',
                'confirmDisable', 'none', 'configurationChanged', 'signInRequired',
                'noModels', 'accountChanged', 'managedSettings', 'independentSettings'}
    for locale in ('en', 'zh-CN', 'zh-TW', 'ja', 'ko', 'es', 'pt', 'ru'):
        messages = json.loads((root / 'static/locales' / f'{locale}.json').read_text(encoding='utf-8'))['api']['chatgpt']
        assert all(messages.get(key) for key in required)
        assert '{{slots}}' in messages['confirmEnable']
        assert '{{model}}' in messages['enabled']
    template = (root / 'templates/api_key_settings.html').read_text(encoding='utf-8')
    assert template.index('id="key-book-section"') < template.index('id="chatgpt-settings"') < template.index('id="custom-api-section"')
    assert 'id="chatgpt-options" hidden' in template
    assert 'id="chatgpt-toggle-btn" aria-expanded="false"' in template


@pytest.mark.asyncio
async def test_offline_authorization_check_does_not_block_restore(manager, routes):
    import httpx
    route, core, store, client = routes
    config = {}
    enable(config, supported_slots(), 'model', revision(config, supported_slots()))
    manager.save_json_config('core_config.json', config)
    def offline(): raise httpx.ConnectError('offline')
    store.access_token = offline
    # Confirmation preview is local; it does not require a token refresh.
    assert (await route.chatgpt_subscription_preview())['enabled']
    assert (await route.chatgpt_subscription_preview(verify_authorization=True))['authorization_required']
    await route.chatgpt_subscription_update(route.SubscriptionRequest(enabled=False))
    assert manager.load_json_config('core_config.json') == {}
