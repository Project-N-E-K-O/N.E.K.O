"""Regression tests for the per-provider assist model ID override.

``assistModelIds`` maps an assist provider to the model the user picked for it.
Only the entry of the currently selected assist provider applies, it covers
every assist tier, fixed-model providers ignore it, and ``/core_api`` merges
submitted entries without touching the other providers.
"""

import asyncio
import json
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))


_ASSIST_TIERS = ('conversation', 'summary', 'correction', 'emotion', 'vision', 'agent')


@pytest.fixture()
def config_manager(clean_user_data_dir):
    from utils.config_manager import get_config_manager
    cm = get_config_manager('N.E.K.O')
    cm.config_dir.mkdir(parents=True, exist_ok=True)
    yield cm


@pytest.fixture()
def no_region_probe(monkeypatch):
    """Keep free-route configs from starting the background GeoIP lookup."""
    from utils.config_manager import ConfigManager
    monkeypatch.setattr(ConfigManager, '_check_non_mainland', lambda self: False)


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


def _write_core_config(cm, data: dict):
    path = cm.get_config_path('core_config.json')
    with open(str(path), 'w', encoding='utf-8') as f:
        json.dump(data, f)
    cm._core_config_cache = None


def _read_core_config(cm) -> dict:
    with open(str(cm.get_runtime_config_path('core_config.json')), encoding='utf-8') as f:
        return json.load(f)


def _profiles():
    from utils.api_config_loader import get_assist_api_profiles
    return get_assist_api_profiles()


def _openrouter_config(**overrides):
    data = {
        'coreApi': 'qwen',
        'coreApiKey': 'sk-qwen-core',
        'assistApi': 'openrouter',
        'assistApiKeyOpenrouter': 'sk-or-test',
    }
    data.update(overrides)
    return data


class TestAssistModelOverride:

    @pytest.mark.unit
    @pytest.mark.parametrize('model_type', _ASSIST_TIERS)
    def test_override_applies_to_every_assist_tier(self, config_manager, model_type):
        _write_core_config(config_manager, _openrouter_config(
            assistModelIds={'openrouter': 'anthropic/claude-test'},
        ))

        resolved = config_manager.get_model_api_config(model_type)

        assert resolved['model'] == 'anthropic/claude-test'
        assert resolved['base_url'] == _profiles()['openrouter']['OPENROUTER_URL']
        assert resolved['api_key'] == 'sk-or-test'

    @pytest.mark.unit
    def test_game_slots_follow_the_override(self, config_manager):
        _write_core_config(config_manager, _openrouter_config(
            assistModelIds={'openrouter': 'anthropic/claude-test'},
        ))

        assert config_manager.get_model_api_config('game_main')['model'] == 'anthropic/claude-test'
        assert config_manager.get_model_api_config('game_summary')['model'] == 'anthropic/claude-test'

    @pytest.mark.unit
    def test_entries_of_other_providers_are_ignored(self, config_manager):
        qwen_profile = _profiles()['qwen']
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'coreApiKey': 'sk-qwen',
            'assistApi': 'qwen',
            'assistModelIds': {'openrouter': 'anthropic/claude-test'},
        })

        resolved = config_manager.get_model_api_config('conversation')

        assert resolved['model'] == qwen_profile['CONVERSATION_MODEL']

    @pytest.mark.unit
    @pytest.mark.parametrize('assist_api,key_field', [
        ('free', 'coreApiKey'),
        ('kimi_code', 'assistApiKeyKimiCode'),
    ])
    def test_fixed_model_providers_ignore_the_override(self, config_manager, no_region_probe, assist_api, key_field):
        profile = _profiles()[assist_api]
        _write_core_config(config_manager, {
            'coreApi': 'free',
            'coreApiKey': 'free-access',
            'assistApi': assist_api,
            key_field: 'free-access' if assist_api == 'free' else 'sk-kimi-code',
            'assistModelIds': {assist_api: 'stale-model'},
        })

        for model_type, profile_key in (('conversation', 'CONVERSATION_MODEL'), ('vision', 'VISION_MODEL')):
            assert config_manager.get_model_api_config(model_type)['model'] == profile[profile_key]

    @pytest.mark.unit
    @pytest.mark.parametrize('assist_model_ids', [
        {'openrouter': '   '},
        {'openrouter': 123},
        'anthropic/claude-test',
        None,
    ])
    def test_blank_or_malformed_override_is_ignored(self, config_manager, assist_model_ids):
        openrouter_profile = _profiles()['openrouter']
        _write_core_config(config_manager, _openrouter_config(assistModelIds=assist_model_ids))

        resolved = config_manager.get_model_api_config('summary')

        assert resolved['model'] == openrouter_profile['SUMMARY_MODEL']

    @pytest.mark.unit
    def test_follow_assist_slot_inherits_the_override(self, config_manager):
        _write_core_config(config_manager, _openrouter_config(
            assistModelIds={'openrouter': 'anthropic/claude-test'},
            enableCustomApi=True,
            conversationModelProvider='follow_assist',
            conversationModelId='',
        ))

        assert config_manager.get_model_api_config('conversation')['model'] == 'anthropic/claude-test'

    @pytest.mark.unit
    def test_slot_model_id_still_wins_over_the_override(self, config_manager):
        _write_core_config(config_manager, _openrouter_config(
            assistModelIds={'openrouter': 'anthropic/claude-test'},
            enableCustomApi=True,
            conversationModelProvider='follow_assist',
            conversationModelId='slot-pick',
            summaryModelProvider='follow_assist',
            summaryModelId='',
        ))

        assert config_manager.get_model_api_config('conversation')['model'] == 'slot-pick'
        assert config_manager.get_model_api_config('summary')['model'] == 'anthropic/claude-test'

    @pytest.mark.unit
    def test_follow_core_slot_keeps_the_core_provider_default(self, config_manager):
        openai_profile = _profiles()['openai']
        _write_core_config(config_manager, _openrouter_config(
            coreApi='openai',
            coreApiKey='sk-openai-core',
            assistModelIds={'openrouter': 'anthropic/claude-test'},
            enableCustomApi=True,
            conversationModelProvider='follow_core',
            conversationModelId='',
        ))

        resolved = config_manager.get_model_api_config('conversation')

        assert resolved['model'] == openai_profile['CONVERSATION_MODEL']
        assert resolved['base_url'] == openai_profile['OPENROUTER_URL']


class TestAssistModelIdsApi:

    @pytest.mark.unit
    def test_get_returns_the_normalized_map(self, config_manager, core_config_router):
        _write_core_config(config_manager, _openrouter_config(
            assistModelIds={'openrouter': '  anthropic/claude-test  ', 'qwen': 7, '': 'x'},
        ))

        response = asyncio.run(core_config_router.get_core_config_api())

        assert response['success'] is True
        assert response['assistModelIds'] == {'openrouter': 'anthropic/claude-test'}

    @pytest.mark.unit
    def test_post_merges_without_touching_other_providers(self, config_manager, core_config_router):
        _write_core_config(config_manager, _openrouter_config(
            assistModelIds={'qwen': 'qwen-max'},
        ))

        result = asyncio.run(core_config_router.update_core_config(_FakeRequest({
            'coreApi': 'qwen',
            'coreApiKey': 'sk-qwen-core',
            'assistApi': 'openrouter',
            'assistModelIds': {'openrouter': ' anthropic/claude-test '},
        })))

        assert result['success'] is True
        assert _read_core_config(config_manager)['assistModelIds'] == {
            'qwen': 'qwen-max',
            'openrouter': 'anthropic/claude-test',
        }

    @pytest.mark.unit
    def test_post_empty_value_clears_that_provider(self, config_manager, core_config_router):
        _write_core_config(config_manager, _openrouter_config(
            assistModelIds={'qwen': 'qwen-max', 'openrouter': 'anthropic/claude-test'},
        ))

        result = asyncio.run(core_config_router.update_core_config(_FakeRequest({
            'coreApi': 'qwen',
            'coreApiKey': 'sk-qwen-core',
            'assistModelIds': {'openrouter': ''},
        })))

        assert result['success'] is True
        assert _read_core_config(config_manager)['assistModelIds'] == {'qwen': 'qwen-max'}

    @pytest.mark.unit
    def test_post_ignores_unknown_providers(self, config_manager, core_config_router):
        _write_core_config(config_manager, _openrouter_config())

        result = asyncio.run(core_config_router.update_core_config(_FakeRequest({
            'coreApi': 'qwen',
            'coreApiKey': 'sk-qwen-core',
            'assistModelIds': {'not-a-provider': 'model-x', 'openrouter': 'anthropic/claude-test'},
        })))

        assert result['success'] is True
        assert _read_core_config(config_manager)['assistModelIds'] == {'openrouter': 'anthropic/claude-test'}

    @pytest.mark.unit
    def test_post_without_the_field_leaves_the_map_untouched(self, config_manager, core_config_router):
        _write_core_config(config_manager, _openrouter_config(
            assistModelIds={'openrouter': 'anthropic/claude-test'},
        ))

        result = asyncio.run(core_config_router.update_core_config(_FakeRequest({
            'coreApi': 'qwen',
            'coreApiKey': 'sk-qwen-core',
        })))

        assert result['success'] is True
        assert _read_core_config(config_manager)['assistModelIds'] == {'openrouter': 'anthropic/claude-test'}

    @pytest.mark.unit
    def test_post_with_nothing_to_store_does_not_add_the_field(self, config_manager, core_config_router):
        _write_core_config(config_manager, _openrouter_config())

        result = asyncio.run(core_config_router.update_core_config(_FakeRequest({
            'coreApi': 'qwen',
            'coreApiKey': 'sk-qwen-core',
            'assistModelIds': {'openrouter': ''},
        })))

        assert result['success'] is True
        assert 'assistModelIds' not in _read_core_config(config_manager)

    @pytest.mark.unit
    @pytest.mark.parametrize('submitted', [
        ['anthropic/claude-test'],
        {'openrouter': 42},
        {'openrouter': 'x' * 257},
    ])
    def test_post_rejects_malformed_payloads(self, config_manager, core_config_router, submitted):
        _write_core_config(config_manager, _openrouter_config(
            assistModelIds={'openrouter': 'anthropic/claude-test'},
        ))

        result = asyncio.run(core_config_router.update_core_config(_FakeRequest({
            'coreApi': 'qwen',
            'coreApiKey': 'sk-qwen-core',
            'assistModelIds': submitted,
        })))

        assert result['success'] is False
        assert _read_core_config(config_manager)['assistModelIds'] == {'openrouter': 'anthropic/claude-test'}
