"""Regression tests for secret redaction on ``/api/config/core_api``."""

import asyncio
import json

import pytest


_ASSIST_API_KEY_FIELDS = (
    'assistApiKeyQwen', 'assistApiKeyQwenIntl', 'assistApiKeyOpenai',
    'assistApiKeyDeepseek', 'assistApiKeyGlm', 'assistApiKeyStep',
    'assistApiKeySilicon', 'assistApiKeyGemini', 'assistApiKeyKimi',
    'assistApiKeyDoubao', 'assistApiKeyDoubaoTts', 'assistApiKeyMinimax',
    'assistApiKeyMinimaxIntl', 'assistApiKeyMimo',
    'assistApiKeyMimoTokenPlan', 'assistApiKeyElevenlabs', 'assistApiKeyGrok',
    'assistApiKeyClaude', 'assistApiKeyKimiCode', 'assistApiKeyOpenrouter',
    'assistApiKeyOrcarouter',
)

_MODEL_TYPES = (
    'conversation', 'summary', 'gameMain', 'gameSummary', 'correction', 'emotion',
    'vision', 'agent', 'omni', 'tts', 'image',
)

_MODEL_API_KEY_FIELDS = tuple(
    f'{model_type}ModelApiKey' for model_type in _MODEL_TYPES
)

_CONFIG_SECRET_FIELDS = (
    'coreApiKey',
    *_ASSIST_API_KEY_FIELDS,
    'mcpToken',
    *_MODEL_API_KEY_FIELDS,
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
        self._payload = payload

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
