"""Subscription transitions use isolated config and mocked accounts, never live credentials."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from utils.chatgpt_subscription import enable, disable, preview, revision, supported_slots


@pytest.fixture
def manager(clean_user_data_dir):
    from utils.config_manager import get_config_manager
    result = get_config_manager('N.E.K.O')
    result.config_dir.mkdir(parents=True, exist_ok=True)
    return result


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
        resolved = manager.get_model_api_config({'gameMain': 'game_main', 'gameSummary': 'game_summary'}.get(slot, slot))
        assert resolved['model'] == 'available-model'
        assert resolved['provider_type'] == 'chatgpt'
    public = json.dumps(preview(config, slots))
    assert 'secret' not in public and 'before' not in public
    # An unrelated change while enabled must survive restoration.
    config['disableTts'] = True
    assert disable(config)
    assert config == {**original, 'disableTts': True}
    assert not disable(config)


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


def test_locales_and_disclosure_contract():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    required = {'useSubscription', 'enabled', 'authorizationRequired', 'confirmEnable',
                'confirmDisable', 'none', 'configurationChanged', 'signInRequired',
                'noModels', 'accountChanged', 'managedSettings'}
    for locale in ('en', 'zh-CN', 'zh-TW', 'ja', 'ko', 'es', 'pt', 'ru'):
        messages = json.loads((root / 'static/locales' / f'{locale}.json').read_text())['api']['chatgpt']
        assert all(messages.get(key) for key in required)
        assert '{{slots}}' in messages['confirmEnable']
        assert '{{model}}' in messages['enabled']
    template = (root / 'templates/api_key_settings.html').read_text()
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
