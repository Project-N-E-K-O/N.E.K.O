"""Regression tests for secret redaction on ``/api/config/core_api``."""

import asyncio
import json
from pathlib import Path

import pytest


_ASSIST_API_KEY_FIELDS = (
    'assistApiKeyQwen', 'assistApiKeyQwenIntl', 'assistApiKeyOpenai',
    'assistApiKeyDeepseek', 'assistApiKeyGlm', 'assistApiKeyStep',
    'assistApiKeySilicon', 'assistApiKeyGemini', 'assistApiKeyKimi',
    'assistApiKeyDoubao', 'assistApiKeyDoubaoTts', 'assistApiKeyQwenTokenPlan',
    'assistApiKeyMinimax',
    'assistApiKeyMinimaxIntl', 'assistApiKeyMimo',
    'assistApiKeyMimoTokenPlan', 'assistApiKeyElevenlabs', 'assistApiKeyGrok',
    'assistApiKeyClaude', 'assistApiKeyKimiCode', 'assistApiKeyOpenrouter',
    'assistApiKeyOrcarouter',
    'assistApiKeyRequesty',
    'assistApiKeyOpper',
    'assistApiKeyAtlascloud',
)

_MODEL_TYPES = (
    'conversation', 'memory', 'gameMain', 'gameSummary', 'recognition',
    'decision', 'vision', 'agent', 'agentAdvanced', 'agentSmall', 'gameRecognition', 'omni', 'tts', 'image',
    'asr',
)

_MODEL_API_KEY_FIELDS = tuple(
    f'{model_type}ModelApiKey' for model_type in _MODEL_TYPES
)

_CONFIG_SECRET_FIELDS = (
    'coreApiKey',
    *_ASSIST_API_KEY_FIELDS,
    'mcpToken',
    *_MODEL_API_KEY_FIELDS,
    'doubaoVoiceManagementAccessKey',
    'doubaoVoiceManagementSecretKey',
)


@pytest.fixture()
def config_manager(clean_user_data_dir):
    from utils.config_manager import get_config_manager

    manager = get_config_manager('N.E.K.O')
    manager.config_dir.mkdir(parents=True, exist_ok=True)
    return manager


@pytest.fixture()
def core_config_router(monkeypatch):
    from main_routers.config_router import core_config

    async def _noop(*args, **kwargs):
        return None

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, *args, **kwargs):
            return None

    monkeypatch.setattr(core_config, 'get_session_manager', lambda: {})
    monkeypatch.setattr(core_config, 'get_initialize_character_data', lambda: _noop)
    monkeypatch.setattr(core_config, 'ensure_default_yui_voice_for_free_api', _noop)
    monkeypatch.setattr(core_config, '_auto_resolve_provider_urls_for_save', _noop)

    import httpx

    monkeypatch.setattr(httpx, 'AsyncClient', _FakeAsyncClient)
    return core_config


class _FakeRequest:
    def __init__(self, payload):
        from types import SimpleNamespace
        from starlette.datastructures import Headers
        from main_routers.system_router._shared import AUTOSTART_CSRF_TOKEN

        self._payload = payload
        self.method = 'POST'
        self.base_url = 'http://testserver/'
        self.url = SimpleNamespace(path='/api/config/core_api')
        self.headers = Headers({
            'content-type': 'application/json',
            'origin': 'http://testserver',
            'x-csrf-token': AUTOSTART_CSRF_TOKEN,
        })

    async def json(self):
        return self._payload


def _write_core_config(manager, config):
    manager.save_json_config('core_config.json', config)
    manager._core_config_cache = None


def _stored_config_with_all_secrets(core_config_router):
    # Keep this explicit test-side inventory independent from the production
    # constants so accidentally omitting a currently exposed field is caught.
    assert core_config_router.CORE_CONFIG_ASSIST_API_KEY_FIELDS == _ASSIST_API_KEY_FIELDS
    assert core_config_router.CORE_CONFIG_MODEL_TYPES == _MODEL_TYPES
    assert core_config_router.CORE_CONFIG_MODEL_API_KEY_FIELDS == _MODEL_API_KEY_FIELDS
    assert core_config_router.CORE_CONFIG_SECRET_FIELDS == _CONFIG_SECRET_FIELDS

    config = {
        field: f'stored-secret-for-{field}'
        for field in _CONFIG_SECRET_FIELDS
    }
    config.update({
        'coreApi': 'qwen',
        'assistApi': 'openai',
        'enableCustomApi': False,
    })
    for model_type in _MODEL_TYPES:
        config[f'{model_type}ModelProvider'] = 'custom'
        config[f'{model_type}ModelUrl'] = f'https://{model_type}.example.test/v1'
        config[f'{model_type}ModelId'] = f'{model_type}-model'
    return config


@pytest.mark.unit
def test_get_redacts_every_core_config_secret(
    config_manager,
    core_config_router,
):
    stored = _stored_config_with_all_secrets(core_config_router)
    _write_core_config(config_manager, stored)

    response = asyncio.run(core_config_router.get_core_config_api())

    assert response['success'] is True
    assert response['effectiveCoreApi'] == 'qwen'
    assert response['supportsIndependentAsr'] is True
    assert response['api_key_display'] == core_config_router.mask_core_config_secret_for_display(
        stored['coreApiKey']
    )
    assert response['assist_api_key_display'] == core_config_router.mask_core_config_secret_for_display(
        stored['assistApiKeyOpenai']
    )
    response_secret_fields = (
        'api_key',
        *_ASSIST_API_KEY_FIELDS,
        'mcpToken',
        *_MODEL_API_KEY_FIELDS,
    )
    assert all(
        response[field] == core_config_router.CORE_CONFIG_SECRET_SENTINEL
        for field in response_secret_fields
    )

    # Provider metadata remains usable while no original credential is exposed.
    for model_type in _MODEL_TYPES:
        assert response[f'{model_type}ModelProvider'] == 'custom'
        assert response[f'{model_type}ModelUrl'] == f'https://{model_type}.example.test/v1'
        assert response[f'{model_type}ModelId'] == f'{model_type}-model'

    serialized_response = json.dumps(response)
    for field in _CONFIG_SECRET_FIELDS:
        assert stored[field] not in serialized_response


@pytest.mark.unit
@pytest.mark.parametrize('core_provider', ['qwen', 'openai', 'free'])
@pytest.mark.parametrize('requesty_key', ['', 'sk-requesty-dedicated'])
def test_requesty_readback_uses_only_its_dedicated_key(
    config_manager, core_config_router, core_provider, requesty_key,
):
    """An unrelated core credential must not appear as a configured Requesty key."""
    core_key = 'free-access' if core_provider == 'free' else 'sk-other-core'
    _write_core_config(config_manager, {
        'coreApi': core_provider,
        'coreApiKey': core_key,
        'assistApi': 'requesty',
        'assistApiKeyRequesty': requesty_key,
    })
    response = asyncio.run(core_config_router.get_core_config_api())
    assert response['success'] is True
    assert response['assistApi'] == 'requesty'
    assert response['assistApiKeyRequesty'] == (
        core_config_router.CORE_CONFIG_SECRET_SENTINEL if requesty_key else ''
    )
    assert response['assist_api_key_display'] == (
        core_config_router.mask_core_config_secret_for_display(requesty_key)
    )
    assert 'sk-other-core' not in json.dumps(response)
    assert 'sk-requesty-dedicated' not in json.dumps(response)


def _assist_only_key_fields() -> list[tuple[str, str]]:
    path = Path(__file__).resolve().parents[2] / 'config' / 'api_providers.json'
    catalog = json.loads(path.read_text(encoding='utf-8'))
    providers = (
        set(catalog['assist_api_providers'])
        - set(catalog['core_api_providers'])
        - {'vllm_omni'}
        - {key for key, profile in catalog['assist_api_providers'].items()
           if profile.get('auth_type') == 'oauth'}
    )
    registry = catalog['api_key_registry']
    return [(p, registry[p]['config_field']) for p in sorted(providers)]


@pytest.mark.unit
@pytest.mark.parametrize(('provider', 'config_field'), _assist_only_key_fields())
def test_assist_only_readback_never_shows_core_key(
    config_manager, core_config_router, provider, config_field,
):
    """An assist-only provider's empty key must read back empty, not as coreApiKey."""
    _write_core_config(config_manager, {
        'coreApi': 'qwen',
        'coreApiKey': 'sk-other-core',
        'assistApi': provider,
    })
    response = asyncio.run(core_config_router.get_core_config_api())
    assert response['success'] is True
    assert response[config_field] == ''
    assert response['assist_api_key_display'] == ''


@pytest.mark.unit
@pytest.mark.parametrize('core_provider', ['qwen', 'openai', 'free'])
@pytest.mark.parametrize('opper_key', ['', 'sk-opper-dedicated'])
def test_opper_readback_uses_only_its_dedicated_key(
    config_manager, core_config_router, core_provider, opper_key,
):
    """An unrelated core credential must not appear as a configured Opper key."""
    core_key = 'free-access' if core_provider == 'free' else 'sk-other-core'
    _write_core_config(config_manager, {
        'coreApi': core_provider,
        'coreApiKey': core_key,
        'assistApi': 'opper',
        'assistApiKeyOpper': opper_key,
    })
    response = asyncio.run(core_config_router.get_core_config_api())
    assert response['success'] is True
    assert response['assistApi'] == 'opper'
    assert response['assistApiKeyOpper'] == (
        core_config_router.CORE_CONFIG_SECRET_SENTINEL if opper_key else ''
    )
    assert response['assist_api_key_display'] == (
        core_config_router.mask_core_config_secret_for_display(opper_key)
    )
    assert 'sk-other-core' not in json.dumps(response)
    assert 'sk-opper-dedicated' not in json.dumps(response)


@pytest.mark.unit
@pytest.mark.parametrize('core_provider', ['qwen', 'openai', 'free'])
@pytest.mark.parametrize('atlascloud_key', ['', 'apikey-atlascloud-dedicated'])
def test_atlascloud_readback_uses_only_its_dedicated_key(
    config_manager, core_config_router, core_provider, atlascloud_key,
):
    """An unrelated core credential must not appear as a configured Atlas Cloud key."""
    core_key = 'free-access' if core_provider == 'free' else 'sk-other-core'
    _write_core_config(config_manager, {
        'coreApi': core_provider,
        'coreApiKey': core_key,
        'assistApi': 'atlascloud',
        'assistApiKeyAtlascloud': atlascloud_key,
    })
    response = asyncio.run(core_config_router.get_core_config_api())
    assert response['success'] is True
    assert response['assistApi'] == 'atlascloud'
    assert response['assistApiKeyAtlascloud'] == (
        core_config_router.CORE_CONFIG_SECRET_SENTINEL if atlascloud_key else ''
    )
    assert response['assist_api_key_display'] == (
        core_config_router.mask_core_config_secret_for_display(atlascloud_key)
    )
    assert 'sk-other-core' not in json.dumps(response)
    assert 'apikey-atlascloud-dedicated' not in json.dumps(response)


@pytest.mark.unit
def test_get_preserves_empty_secrets_and_free_access(
    config_manager,
    core_config_router,
):
    stored = {
        field: ''
        for field in _CONFIG_SECRET_FIELDS
    }
    stored.update({
        'coreApiKey': 'free-access',
        'coreApi': 'free',
        'assistApi': 'free',
    })
    _write_core_config(config_manager, stored)

    response = asyncio.run(core_config_router.get_core_config_api())

    assert response['success'] is True
    assert response['effectiveCoreApi'] == 'free'
    assert response['supportsIndependentAsr'] is False
    assert response['api_key'] == 'free-access'
    for field in (
        *_ASSIST_API_KEY_FIELDS,
        'mcpToken',
        *_MODEL_API_KEY_FIELDS,
    ):
        assert response[field] == ''


@pytest.mark.unit
def test_get_uses_effective_realtime_core_for_asr_capability(
    config_manager,
    core_config_router,
):
    _write_core_config(config_manager, {
        'coreApiKey': 'free-access',
        'coreApi': 'free',
        'assistApi': 'free',
        'enableCustomApi': True,
        'omniModelProvider': 'custom',
        'omniModelId': 'local-omni',
        'omniModelUrl': 'http://127.0.0.1:8080/v1',
        'omniModelApiKey': 'local-key',
    })

    response = asyncio.run(core_config_router.get_core_config_api())

    assert response['success'] is True
    assert response['coreApi'] == 'free'
    assert response['effectiveCoreApi'] == 'local'
    assert response['supportsIndependentAsr'] is None


@pytest.mark.unit
@pytest.mark.parametrize('installed', [True, False])
def test_get_reports_local_asr_availability_off_the_event_loop(
    monkeypatch,
    config_manager,
    core_config_router,
    installed,
):
    import threading

    import main_logic.asr_client as asr_client

    probes = []

    def fake_probe(module_name):
        probes.append((module_name, threading.get_ident()))
        return installed

    monkeypatch.setattr(asr_client, '_optional_dependency_available', fake_probe)
    _write_core_config(config_manager, {'coreApi': 'qwen', 'assistApi': 'qwen'})

    loop_thread = threading.get_ident()
    response = asyncio.run(core_config_router.get_core_config_api())

    assert response['success'] is True
    assert response['localAsrAvailable'] is installed
    assert [name for name, _ in probes] == ['faster_whisper']
    # The import-system probe must not run on the request's event loop.
    assert all(thread != loop_thread for _, thread in probes)


@pytest.mark.unit
def test_post_sentinel_preserves_every_secret_and_never_persists_it(
    config_manager,
    core_config_router,
):
    stored = _stored_config_with_all_secrets(core_config_router)
    _write_core_config(config_manager, stored)
    payload = {
        field: core_config_router.CORE_CONFIG_SECRET_SENTINEL
        for field in _CONFIG_SECRET_FIELDS
    }
    payload.update({
        'coreApi': 'qwen',
        'assistApi': 'openai',
        'enableCustomApi': False,
    })

    response = asyncio.run(
        core_config_router.update_core_config(_FakeRequest(payload))
    )

    assert response['success'] is True
    saved = config_manager.load_json_config('core_config.json', {})
    for field in _CONFIG_SECRET_FIELDS:
        assert saved[field] == stored[field]
    assert core_config_router.CORE_CONFIG_SECRET_SENTINEL not in json.dumps(saved)


@pytest.mark.unit
def test_post_empty_string_explicitly_clears_secrets_when_core_is_optional(
    config_manager,
    core_config_router,
):
    stored = _stored_config_with_all_secrets(core_config_router)
    _write_core_config(config_manager, stored)
    payload = {
        field: ''
        for field in _CONFIG_SECRET_FIELDS
    }
    payload.update({
        'coreApi': 'qwen',
        'assistApi': 'openai',
        'enableCustomApi': True,
    })

    response = asyncio.run(
        core_config_router.update_core_config(_FakeRequest(payload))
    )

    assert response['success'] is True
    saved = config_manager.load_json_config('core_config.json', {})
    for field in _CONFIG_SECRET_FIELDS:
        assert saved[field] == ''


@pytest.mark.unit
def test_paid_core_still_rejects_explicit_empty_key(
    config_manager,
    core_config_router,
):
    stored = _stored_config_with_all_secrets(core_config_router)
    _write_core_config(config_manager, stored)

    response = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        'coreApiKey': '',
        'coreApi': 'qwen',
        'assistApi': 'openai',
        'enableCustomApi': False,
    })))

    assert response['success'] is False
    assert response['error'] == 'API Key不能为空'
    saved = config_manager.load_json_config('core_config.json', {})
    assert saved['coreApiKey'] == stored['coreApiKey']


@pytest.mark.unit
def test_paid_core_rejects_placeholder_when_no_key_is_stored(
    config_manager,
    core_config_router,
):
    stored = _stored_config_with_all_secrets(core_config_router)
    stored['coreApiKey'] = ''
    stored['assistApiKeyQwen'] = ''
    _write_core_config(config_manager, stored)

    response = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        'coreApiKey': core_config_router.CORE_CONFIG_SECRET_SENTINEL,
        'coreApi': 'qwen',
        'assistApi': 'openai',
        'enableCustomApi': False,
    })))

    assert response['success'] is False
    assert response['error'] == 'API Key不能为空'


@pytest.mark.unit
def test_placeholder_promotes_same_provider_key_when_core_key_is_empty(
    config_manager,
    core_config_router,
):
    _write_core_config(config_manager, {
        'coreApiKey': '',
        'coreApi': 'qwen',
        'assistApi': 'qwen',
        'assistApiKeyQwen': 'sk-qwen-from-key-book',
        'enableCustomApi': False,
    })

    response = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        'coreApiKey': core_config_router.CORE_CONFIG_SECRET_SENTINEL,
        'coreApi': 'qwen',
        'assistApi': 'qwen',
        'enableCustomApi': False,
    })))

    assert response['success'] is True
    saved = config_manager.load_json_config('core_config.json', {})
    assert saved['coreApiKey'] == 'sk-qwen-from-key-book'


@pytest.mark.unit
def test_provider_key_lookup_rejects_non_assist_secret_fields(core_config_router):
    registry = {
        'qwen': {'config_field': 'assistApiKeyQwen'},
        'vllm_omni': {'config_field': 'ttsModelApiKey'},
        'unsafe': {'config_field': 'mcpToken'},
    }

    assert (
        core_config_router.get_core_config_provider_api_key_field('qwen', registry)
        == 'assistApiKeyQwen'
    )
    assert (
        core_config_router.get_core_config_provider_api_key_field('vllm_omni', registry)
        is None
    )
    assert (
        core_config_router.get_core_config_provider_api_key_field('unsafe', registry)
        is None
    )


@pytest.mark.unit
def test_provider_switch_preserves_both_provider_keys(
    config_manager,
    core_config_router,
):
    _write_core_config(config_manager, {
        'coreApiKey': 'sk-qwen-core',
        'coreApi': 'qwen',
        'assistApi': 'qwen',
        'assistApiKeyQwen': 'sk-qwen-stale',
        'assistApiKeyOpenai': 'sk-openai-key-book',
        'enableCustomApi': False,
    })

    to_openai = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        'coreApiKey': core_config_router.CORE_CONFIG_SECRET_SENTINEL,
        'coreApi': 'openai',
        'assistApi': 'openai',
        'assistApiKeyQwen': core_config_router.CORE_CONFIG_SECRET_SENTINEL,
        'assistApiKeyOpenai': core_config_router.CORE_CONFIG_SECRET_SENTINEL,
        'enableCustomApi': False,
    })))

    assert to_openai['success'] is True
    saved = config_manager.load_json_config('core_config.json', {})
    assert saved['coreApi'] == 'openai'
    assert saved['coreApiKey'] == 'sk-openai-key-book'
    assert saved['assistApiKeyQwen'] == 'sk-qwen-core'
    assert saved['assistApiKeyOpenai'] == 'sk-openai-key-book'

    back_to_qwen = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        'coreApiKey': core_config_router.CORE_CONFIG_SECRET_SENTINEL,
        'coreApi': 'qwen',
        'assistApi': 'qwen',
        'assistApiKeyQwen': core_config_router.CORE_CONFIG_SECRET_SENTINEL,
        'assistApiKeyOpenai': core_config_router.CORE_CONFIG_SECRET_SENTINEL,
        'enableCustomApi': False,
    })))

    assert back_to_qwen['success'] is True
    saved = config_manager.load_json_config('core_config.json', {})
    assert saved['coreApi'] == 'qwen'
    assert saved['coreApiKey'] == 'sk-qwen-core'
    assert saved['assistApiKeyQwen'] == 'sk-qwen-core'
    assert saved['assistApiKeyOpenai'] == 'sk-openai-key-book'


@pytest.mark.unit
def test_provider_switch_preserves_distinct_key_when_old_core_stays_assist(
    config_manager,
    core_config_router,
):
    _write_core_config(config_manager, {
        'coreApiKey': 'sk-qwen-core',
        'coreApi': 'qwen',
        'assistApi': 'qwen',
        'assistApiKeyQwen': 'sk-qwen-assist',
        'assistApiKeyOpenai': 'sk-openai-key-book',
        'enableCustomApi': False,
    })

    response = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        'coreApiKey': core_config_router.CORE_CONFIG_SECRET_SENTINEL,
        'coreApi': 'openai',
        'assistApi': 'qwen',
        'assistApiKeyQwen': core_config_router.CORE_CONFIG_SECRET_SENTINEL,
        'assistApiKeyOpenai': core_config_router.CORE_CONFIG_SECRET_SENTINEL,
        'enableCustomApi': False,
    })))

    assert response['success'] is True
    saved = config_manager.load_json_config('core_config.json', {})
    assert saved['coreApiKey'] == 'sk-openai-key-book'
    assert saved['assistApiKeyQwen'] == 'sk-qwen-assist'


@pytest.mark.unit
def test_provider_switch_backfills_key_when_old_core_stays_assist_and_slot_empty(
    config_manager,
    core_config_router,
):
    _write_core_config(config_manager, {
        'coreApiKey': 'sk-qwen-core',
        'coreApi': 'qwen',
        'assistApi': 'qwen',
        'assistApiKeyQwen': '',
        'assistApiKeyOpenai': 'sk-openai-key-book',
        'enableCustomApi': False,
    })

    response = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        'coreApiKey': core_config_router.CORE_CONFIG_SECRET_SENTINEL,
        'coreApi': 'openai',
        'assistApi': 'qwen',
        'assistApiKeyQwen': core_config_router.CORE_CONFIG_SECRET_SENTINEL,
        'assistApiKeyOpenai': core_config_router.CORE_CONFIG_SECRET_SENTINEL,
        'enableCustomApi': False,
    })))

    assert response['success'] is True
    saved = config_manager.load_json_config('core_config.json', {})
    assert saved['coreApiKey'] == 'sk-openai-key-book'
    assert saved['assistApiKeyQwen'] == 'sk-qwen-core'


@pytest.mark.unit
def test_provider_switch_without_target_key_is_rejected_without_saving(
    config_manager,
    core_config_router,
):
    stored = {
        'coreApiKey': 'sk-qwen-core',
        'coreApi': 'qwen',
        'assistApi': 'qwen',
        'assistApiKeyQwen': '',
        'assistApiKeyOpenai': '',
        'enableCustomApi': False,
    }
    _write_core_config(config_manager, stored)

    response = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        'coreApiKey': core_config_router.CORE_CONFIG_SECRET_SENTINEL,
        'coreApi': 'openai',
        'assistApi': 'openai',
        'enableCustomApi': False,
    })))

    assert response['success'] is False
    assert response['error'] == 'API Key不能为空'
    assert config_manager.load_json_config('core_config.json', {}) == stored


@pytest.mark.unit
def test_custom_provider_switch_without_target_key_clears_stale_core_key(
    config_manager,
    core_config_router,
):
    _write_core_config(config_manager, {
        'coreApiKey': 'sk-qwen-core',
        'coreApi': 'qwen',
        'assistApi': 'qwen',
        'assistApiKeyQwen': '',
        'assistApiKeyOpenai': '',
        'enableCustomApi': True,
    })

    response = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        'coreApiKey': core_config_router.CORE_CONFIG_SECRET_SENTINEL,
        'coreApi': 'openai',
        'assistApi': 'openai',
        'enableCustomApi': True,
    })))

    assert response['success'] is True
    saved = config_manager.load_json_config('core_config.json', {})
    assert saved['coreApi'] == 'openai'
    assert saved['coreApiKey'] == ''
    assert saved['assistApiKeyQwen'] == 'sk-qwen-core'
    assert saved['assistApiKeyOpenai'] == ''


@pytest.mark.unit
def test_legacy_masks_are_narrowly_recognized_as_placeholders(
    config_manager,
    core_config_router,
):
    assert core_config_router.is_core_config_secret_placeholder('********') is True
    assert core_config_router.is_core_config_secret_placeholder('*') is False
    assert core_config_router.is_core_config_secret_placeholder('**') is False
    assert (
        core_config_router.is_core_config_secret_placeholder('abcdef***uvwxyz')
        is True
    )
    assert (
        core_config_router.is_core_config_secret_placeholder('real***secret')
        is False
    )

    stored = _stored_config_with_all_secrets(core_config_router)
    _write_core_config(config_manager, stored)
    response = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        'coreApiKey': 'abcdef***uvwxyz',
        'assistApiKeyQwen': '********',
        'assistApiKeyOpenai': '**',
        'mcpToken': 'real***secret',
        'coreApi': 'qwen',
        'assistApi': 'openai',
        'enableCustomApi': False,
    })))

    assert response['success'] is True
    saved = config_manager.load_json_config('core_config.json', {})
    assert saved['coreApiKey'] == stored['coreApiKey']
    assert saved['assistApiKeyQwen'] == stored['assistApiKeyQwen']
    assert saved['assistApiKeyOpenai'] == '**'
    assert saved['mcpToken'] == 'real***secret'

@pytest.mark.unit
def test_image_endpoint_change_requires_new_custom_key(config_manager, core_config_router):
    _write_core_config(config_manager, {
        "coreApi": "free", "assistApi": "free", "coreApiKey": "free-access",
        "imageModelProvider": "custom", "imageModelUrl": "https://old.example/v1",
        "imageModelId": "image-model", "imageModelApiKey": "private-image-key",
    })
    result = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        "imageModelUrl": "https://new.example/v1",
        "imageModelApiKey": core_config_router.CORE_CONFIG_SECRET_SENTINEL,
    })))
    assert result["success"] is False
    assert config_manager.load_json_config("core_config.json", {})["imageModelUrl"] == "https://old.example/v1"


@pytest.mark.unit
def test_image_config_round_trip_uses_core_snapshot(config_manager, core_config_router):
    _write_core_config(config_manager, {
        "coreApi": "free", "assistApi": "free", "coreApiKey": "free-access",
        "enableCustomApi": True,
    })
    result = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        "imageModelProvider": "qwen", "imageModelUrl": "https://dashscope.aliyuncs.com",
        "imageModelId": "wanx2.1-t2i-turbo", "assistApiKeyQwen": "image-qwen-key",
    })))
    assert result["success"] is True
    config = config_manager.get_model_api_config("image")
    assert config["api_key"] == "image-qwen-key"
    assert config["protocol"] == "dashscope"
    response = asyncio.run(core_config_router.get_core_config_api())
    assert response["assistApiKeyQwen"] == core_config_router.CORE_CONFIG_SECRET_SENTINEL
    assert response["imageModelProvider"] == "qwen"

@pytest.mark.unit
@pytest.mark.parametrize("core,assist,image,expected", [
    ("qwen", "qwen", "qwen", "legacy-key"),
    ("openai", "openai", "openai", "legacy-key"),
    ("qwen_intl", "qwen_intl", "qwen_intl", "legacy-key"),
    ("openai", "qwen", "qwen", "legacy-key"),
    ("openai", "openai", "qwen", ""),
    ("free", "free", "qwen", ""),
])
def test_image_legacy_key_fallback_matches_key_book(
    config_manager, core_config_router, core, assist, image, expected
):
    _write_core_config(config_manager, {
        "coreApi": core, "assistApi": assist,
        "coreApiKey": "free-access" if core == "free" else "legacy-key",
        "enableCustomApi": True, "imageModelProvider": image,
    })
    config = config_manager.get_model_api_config("image")
    assert config["api_key"] == expected
    response = asyncio.run(core_config_router.get_core_config_api())
    field = {"qwen": "assistApiKeyQwen", "qwen_intl": "assistApiKeyQwenIntl", "openai": "assistApiKeyOpenai"}[image]
    assert bool(response[field]) == bool(expected)


@pytest.mark.unit
@pytest.mark.parametrize("url", [
    "https://old.example/v1/", "  https://old.example/v1  ",
    "https://OLD.example:443/v1",
])
def test_image_equivalent_endpoint_preserves_custom_key(config_manager, core_config_router, url):
    _write_core_config(config_manager, {
        "coreApi": "free", "assistApi": "free", "coreApiKey": "free-access",
        "imageModelProvider": "custom", "imageModelUrl": "https://old.example/v1",
        "imageModelId": "image-model", "imageModelApiKey": "private-image-key",
    })
    result = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        "imageModelUrl": url,
        "imageModelApiKey": core_config_router.CORE_CONFIG_SECRET_SENTINEL,
    })))
    assert result["success"] is True
    assert config_manager.load_json_config("core_config.json", {})["imageModelApiKey"] == "private-image-key"

@pytest.mark.unit
@pytest.mark.parametrize("provider", ["disabled", "qwen"])
def test_retained_image_key_cannot_move_while_inactive(config_manager, core_config_router, provider):
    _write_core_config(config_manager, {
        "coreApi": "free", "assistApi": "free", "coreApiKey": "free-access",
        "imageModelProvider": "custom", "imageModelUrl": "https://old.example/v1",
        "imageModelId": "image-model", "imageModelApiKey": "private-image-key",
    })
    result = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        "imageModelProvider": provider, "imageModelUrl": "https://new.example/v1",
        "imageModelApiKey": core_config_router.CORE_CONFIG_SECRET_SENTINEL,
    })))
    assert result["success"] is False
    saved = config_manager.load_json_config("core_config.json", {})
    assert saved["imageModelUrl"] == "https://old.example/v1"
    assert saved["imageModelProvider"] == "custom"


@pytest.mark.unit
def test_multiple_trailing_slashes_do_not_preserve_custom_key(config_manager, core_config_router):
    _write_core_config(config_manager, {
        "coreApi": "free", "assistApi": "free", "coreApiKey": "free-access",
        "imageModelProvider": "custom", "imageModelUrl": "https://old.example/v1",
        "imageModelId": "image-model", "imageModelApiKey": "private-image-key",
    })
    result = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        "imageModelUrl": "https://old.example/v1//",
        "imageModelApiKey": core_config_router.CORE_CONFIG_SECRET_SENTINEL,
    })))
    assert result["success"] is False


@pytest.mark.unit
@pytest.mark.parametrize("change,success", [
    ({}, True),
    ({"imageModelId": "changed"}, False),
    ({"imageModelUrl": "https://changed.example/v1"}, False),
    ({"imageModelApiKey": "replacement"}, False),
    ({"imageModelProvider": "another-future-provider"}, False),
    ({"imageModelProvider": "disabled", "imageModelUrl": "", "imageModelId": "", "imageModelApiKey": ""}, True),
])
def test_unknown_image_provider_preserved_only_unchanged(config_manager, core_config_router, change, success):
    image = {
        "imageModelProvider": "future-provider", "imageModelUrl": " https://future.example/v1 ",
        "imageModelId": " future-model ", "imageModelApiKey": "private-image-key",
    }
    _write_core_config(config_manager, {
        "coreApi": "free", "assistApi": "free", "coreApiKey": "free-access", **image,
    })
    payload = {**image, "imageModelApiKey": core_config_router.CORE_CONFIG_SECRET_SENTINEL,
               "disableTts": True, **change}
    result = asyncio.run(core_config_router.update_core_config(_FakeRequest(payload)))
    assert result["success"] is success
    saved = config_manager.load_json_config("core_config.json", {})
    if success:
        assert saved["disableTts"] is True
    if not change or not success:
        assert {field: saved[field] for field in image} == image
    else:
        assert saved["imageModelProvider"] == "disabled"
        assert saved["imageModelApiKey"] == ""


@pytest.mark.unit
def test_new_unknown_image_provider_still_rejected(config_manager, core_config_router):
    _write_core_config(config_manager, {"coreApi": "free", "assistApi": "free", "coreApiKey": "free-access"})
    result = asyncio.run(core_config_router.update_core_config(_FakeRequest({"imageModelProvider": "future-provider"})))
    assert result["success"] is False


@pytest.mark.unit
@pytest.mark.parametrize("field", ["imageModelProvider", "imageModelUrl", "imageModelId", "imageModelApiKey"])
@pytest.mark.parametrize("value", [None, 123, False, [], {}])
def test_disabled_image_fields_require_strings(config_manager, core_config_router, field, value):
    original = {"coreApi": "free", "assistApi": "free", "coreApiKey": "free-access", "imageModelProvider": "disabled"}
    _write_core_config(config_manager, original)
    result = asyncio.run(core_config_router.update_core_config(_FakeRequest({"imageModelProvider": "disabled", field: value})))
    assert result["success"] is False
    assert result["error"] == "Image settings must be strings"
    assert config_manager.load_json_config("core_config.json", {}) == original


@pytest.mark.unit
def test_chatgpt_save_pins_origin_and_discards_api_key(config_manager, core_config_router):
    _write_core_config(config_manager, {'coreApi': 'qwen', 'coreApiKey': 'audio-key', 'assistApi': 'qwen'})
    result = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        'enableCustomApi': True, 'conversationModelProvider': 'chatgpt',
        'conversationModelId': 'account-model', 'conversationModelUrl': 'https://attacker.test',
        'conversationModelApiKey': 'old-api-key',
    })))
    assert result['success'] is True
    saved = config_manager.load_json_config('core_config.json', {})
    assert saved['conversationModelUrl'] == 'https://api.openai.com/v1'
    assert saved['conversationModelApiKey'] == ''
    assert saved['coreApiKey'] == 'audio-key'


@pytest.mark.unit
@pytest.mark.parametrize('slot,model', [
    ('conversation', ''), ('conversation', '   '), ('conversation', {'id': 'model-a'}), ('conversation', ['model-a']),
    ('tts', 'model'), ('omni', 'model'), ('image', 'model'), ('decision', 'model'), ('vision', 'model'),
])
def test_chatgpt_save_rejects_missing_model_and_unsupported_slots(config_manager, core_config_router, slot, model):
    original = {'coreApi': 'qwen', 'coreApiKey': 'audio-key', 'assistApi': 'qwen'}
    _write_core_config(config_manager, original)
    result = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        'enableCustomApi': True, f'{slot}ModelProvider': 'chatgpt',
        f'{slot}ModelId': model, f'{slot}ModelUrl': 'https://api.openai.com/v1',
    })))
    assert result['success'] is False
    assert config_manager.load_json_config('core_config.json', {}) == original


_ASR_SAVED = {
    "coreApi": "free", "assistApi": "free", "coreApiKey": "free-access",
    "enableCustomApi": True,
    "asrModelProvider": "custom", "asrModelUrl": "http://lan-box:8081/v1",
    "asrModelId": "gigaam-v3", "asrModelApiKey": "private-asr-key",
}


@pytest.mark.unit
@pytest.mark.parametrize("key_fields", [
    {},  # key omitted
    {"asrModelApiKey": "__NEKO_SECRET_MASKED__"},
])
def test_asr_endpoint_change_requires_new_key(config_manager, core_config_router, key_fields):
    _write_core_config(config_manager, dict(_ASR_SAVED))
    result = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        "asrModelUrl": "http://elsewhere:9000/v1", **key_fields,
    })))
    assert result["success"] is False
    saved = config_manager.load_json_config("core_config.json", {})
    assert saved["asrModelUrl"] == "http://lan-box:8081/v1"
    assert saved["asrModelApiKey"] == "private-asr-key"


@pytest.mark.unit
@pytest.mark.parametrize("url", [
    "http://lan-box:8081/v1",
    "http://lan-box:8081/v1/",
    "http://lan-box:8081/v1/audio/transcriptions",
])
def test_asr_same_endpoint_round_trip_keeps_the_key(config_manager, core_config_router, url):
    _write_core_config(config_manager, dict(_ASR_SAVED))
    result = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        "asrModelProvider": "custom", "asrModelUrl": url,
        "asrModelApiKey": core_config_router.CORE_CONFIG_SECRET_SENTINEL,
    })))
    assert result["success"] is True
    assert config_manager.load_json_config("core_config.json", {})["asrModelApiKey"] == "private-asr-key"


@pytest.mark.unit
def test_asr_endpoint_change_with_a_new_key_is_saved(config_manager, core_config_router):
    _write_core_config(config_manager, dict(_ASR_SAVED))
    result = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        "asrModelUrl": "http://elsewhere:9000/v1", "asrModelApiKey": "",
    })))
    assert result["success"] is True
    saved = config_manager.load_json_config("core_config.json", {})
    assert saved["asrModelUrl"] == "http://elsewhere:9000/v1"
    assert saved["asrModelApiKey"] == ""


@pytest.mark.unit
@pytest.mark.parametrize("fields", [
    {"asrModelProvider": "deepseek"},
    {"asrModelProvider": "custom", "asrModelUrl": "192.168.1.5:8081"},
    {"asrModelProvider": "custom", "asrModelUrl": ""},
    {"asrModelProvider": "qwen", "asrModelTransport": "stream"},
    {"asrModelProvider": 1},
])
def test_asr_slot_that_could_not_run_is_rejected(config_manager, core_config_router, fields):
    _write_core_config(config_manager, {
        "coreApi": "free", "assistApi": "free", "coreApiKey": "free-access",
    })
    result = asyncio.run(core_config_router.update_core_config(_FakeRequest(fields)))
    assert result["success"] is False
    assert "asrModelProvider" not in config_manager.load_json_config("core_config.json", {})


@pytest.mark.unit
@pytest.mark.parametrize("fields", [
    {"asrModelProvider": "follow_core"},
    {"asrModelProvider": "qwen", "asrModelTransport": "request"},
    {"asrModelProvider": "custom", "asrModelUrl": "http://lan-box:8081"},
])
def test_valid_asr_slot_is_saved(config_manager, core_config_router, fields):
    _write_core_config(config_manager, {
        "coreApi": "free", "assistApi": "free", "coreApiKey": "free-access",
    })
    result = asyncio.run(core_config_router.update_core_config(_FakeRequest(fields)))
    assert result["success"] is True
    assert config_manager.load_json_config("core_config.json", {})["asrModelProvider"] == fields["asrModelProvider"]


@pytest.mark.unit
@pytest.mark.parametrize("url,availability", [
    ("http://lan-box:8081", "implemented"),
    ("lan-box:8081", "misconfigured"),  # saved by an older build
])
def test_get_reports_the_asr_slot_and_its_availability(
    config_manager, core_config_router, url, availability
):
    _write_core_config(config_manager, {
        "coreApi": "free", "assistApi": "free", "coreApiKey": "free-access",
        "enableCustomApi": True, "asrModelProvider": "custom", "asrModelUrl": url,
    })
    response = asyncio.run(core_config_router.get_core_config_api())
    assert response["asrSlotActive"] is True
    assert response["asrSlotProvider"] == "custom"
    assert response["asrSlotAvailability"] == availability


def _model_slot_types():
    from main_routers.config_router.core_config import CORE_CONFIG_MODEL_TYPES

    return list(CORE_CONFIG_MODEL_TYPES)


@pytest.mark.unit
@pytest.mark.parametrize("model_type", _model_slot_types())
def test_core_switch_keeps_a_key_book_entry_an_active_slot_reads(
    config_manager, core_config_router, model_type
):
    # Core qwen -> gemini while assist is openai. Without the slot, the qwen
    # entry would be treated as stale and overwritten with the Core key; any
    # active per-feature slot on qwen (ASR, image, conversation...) still
    # reads it, so it must survive. Covers slot types added later too.
    _write_core_config(config_manager, {
        "coreApi": "qwen", "assistApi": "openai", "coreApiKey": "sk-core-qwen",
        "assistApiKeyQwen": "sk-slot-qwen", "assistApiKeyGemini": "sk-gemini",
        "assistApiKeyOpenai": "sk-openai",
        "enableCustomApi": True, f"{model_type}ModelProvider": "qwen",
    })
    result = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        "coreApi": "gemini", "assistApi": "openai",
        "coreApiKey": core_config_router.CORE_CONFIG_SECRET_SENTINEL,
        "enableCustomApi": True,
    })))
    assert result["success"] is True, result
    saved = config_manager.load_json_config("core_config.json", {})
    assert saved["coreApi"] == "gemini"
    assert saved["assistApiKeyQwen"] == "sk-slot-qwen"


@pytest.mark.unit
@pytest.mark.parametrize("enable_custom_api", [False, True])
def test_core_switch_still_replaces_an_entry_nothing_else_reads(
    config_manager, core_config_router, enable_custom_api
):
    # Existing rule: with no other reader the old Core's entry is stale. A
    # slot only counts while per-feature models are on.
    _write_core_config(config_manager, {
        "coreApi": "qwen", "assistApi": "openai", "coreApiKey": "sk-core-qwen",
        "assistApiKeyQwen": "sk-stale-qwen", "assistApiKeyGemini": "sk-gemini",
        "assistApiKeyOpenai": "sk-openai",
        "enableCustomApi": enable_custom_api,
        "asrModelProvider": "qwen" if not enable_custom_api else "openai",
    })
    result = asyncio.run(core_config_router.update_core_config(_FakeRequest({
        "coreApi": "gemini", "assistApi": "openai",
        "coreApiKey": core_config_router.CORE_CONFIG_SECRET_SENTINEL,
        "enableCustomApi": enable_custom_api,
    })))
    assert result["success"] is True, result
    assert config_manager.load_json_config("core_config.json", {})["assistApiKeyQwen"] == "sk-core-qwen"
