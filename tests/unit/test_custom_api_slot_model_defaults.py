"""Regression tests for blank model IDs on explicitly-configured custom slots.

A slot whose URL and key already point at another provider (``follow_core`` or
a named provider) used to keep the assist provider's model name when its model
ID was left blank, so one vendor's model name was sent to another vendor's
endpoint. Fixed-model providers (free tier, Kimi Code) must also ignore stale
saved model IDs, whichever follow mode reaches them.
"""

import json
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))


@pytest.fixture()
def config_manager(clean_user_data_dir):
    """Return the patched ConfigManager singleton pointing at a temp dir."""
    from utils.config_manager import get_config_manager
    cm = get_config_manager('N.E.K.O')
    cm.config_dir.mkdir(parents=True, exist_ok=True)
    yield cm


@pytest.fixture()
def no_region_probe(monkeypatch):
    """Keep free-route configs from starting the background GeoIP lookup."""
    from utils.config_manager import ConfigManager
    monkeypatch.setattr(ConfigManager, '_check_non_mainland', lambda self: False)


def _write_core_config(cm, data: dict):
    path = cm.get_config_path('core_config.json')
    with open(str(path), 'w', encoding='utf-8') as f:
        json.dump(data, f)
    cm._core_config_cache = None


def _profiles():
    from utils.api_config_loader import get_assist_api_profiles
    return get_assist_api_profiles()


_TEXT_TIERS = (
    ('conversation', 'conversation', 'CONVERSATION_MODEL'),
    ('summary', 'summary', 'SUMMARY_MODEL'),
    ('correction', 'correction', 'CORRECTION_MODEL'),
    ('emotion', 'emotion', 'EMOTION_MODEL'),
    ('vision', 'vision', 'VISION_MODEL'),
    ('agent', 'agent', 'AGENT_MODEL'),
)


@pytest.mark.unit
@pytest.mark.parametrize('prefix,model_type,profile_key,text_prefix', [
    ('gameMain', 'game_main', 'CONVERSATION_MODEL', 'conversation'),
    ('gameSummary', 'game_summary', 'SUMMARY_MODEL', 'summary'),
])
@pytest.mark.parametrize('text_model', ['', 'user-core-model'])
def test_game_follow_assist_keeps_assist_default_after_text_override(
    config_manager, prefix, model_type, profile_key, text_prefix, text_model,
):
    profile = _profiles()['openrouter']
    _write_core_config(config_manager, {
        'coreApi': 'qwen', 'assistApi': 'openrouter', 'coreApiKey': 'core-key',
        'assistApiKeyOpenRouter': 'assist-key', 'enableCustomApi': True,
        f'{text_prefix}ModelProvider': 'follow_core', f'{text_prefix}ModelId': text_model,
        f'{prefix}ModelProvider': 'follow_assist',
    })
    resolved = config_manager.get_model_api_config(model_type)
    assert resolved['model'] == profile[profile_key]
    assert resolved['base_url'] == profile['OPENROUTER_URL']


@pytest.mark.unit
@pytest.mark.parametrize('provider', ['openrouter', 'extra_provider'])
@pytest.mark.parametrize('missing_summary', [False, True])
def test_game_summary_empty_assist_tier_uses_same_provider_conversation(
    config_manager, monkeypatch, provider, missing_summary,
):
    profiles = _profiles()
    profile = dict(profiles['openrouter'], CONVERSATION_MODEL='provider-chat-model', SUMMARY_MODEL='')
    if missing_summary:
        profile.pop('SUMMARY_MODEL')
    profiles[provider] = profile
    monkeypatch.setattr('utils.config_manager.get_assist_api_profiles', lambda: profiles)
    _write_core_config(config_manager, {
        'coreApi': 'qwen', 'assistApi': provider, 'coreApiKey': 'core-key',
        'enableCustomApi': True, 'summaryModelProvider': 'follow_core',
        'gameSummaryModelProvider': 'follow_assist',
    })
    resolved = config_manager.get_model_api_config('game_summary')
    assert resolved['model'] == 'provider-chat-model'
    assert resolved['base_url'] == profile['OPENROUTER_URL']


@pytest.mark.unit
@pytest.mark.parametrize('provider_mode', ['follow_core', 'openai'])
def test_missing_tier_never_uses_other_provider_model(config_manager, monkeypatch, provider_mode):
    profiles = _profiles()
    profiles['openai']['VISION_MODEL'] = ''
    monkeypatch.setattr('utils.config_manager.get_assist_api_profiles', lambda: profiles)
    _write_core_config(config_manager, {
        'coreApi': 'openai', 'assistApi': 'qwen', 'coreApiKey': 'sk-test',
        'enableCustomApi': True,
        'visionModelProvider': provider_mode, 'visionModelId': '',
        'visionModelUrl': profiles['openai']['OPENROUTER_URL'],
    })
    assert config_manager.get_model_api_config('vision')['model'] == profiles['openai']['CONVERSATION_MODEL']


@pytest.mark.unit
@pytest.mark.parametrize('provider_mode', ['follow_core', 'openai'])
@pytest.mark.parametrize('vision_model', ['vision-capable-model', ''])
def test_missing_agent_prefers_vision_then_conversation(config_manager, monkeypatch, provider_mode, vision_model):
    profiles = _profiles()
    profiles['openai']['AGENT_MODEL'] = ''
    profiles['openai']['VISION_MODEL'] = vision_model
    monkeypatch.setattr('utils.config_manager.get_assist_api_profiles', lambda: profiles)
    _write_core_config(config_manager, {
        'coreApi': 'openai', 'assistApi': 'qwen', 'coreApiKey': 'sk-test',
        'enableCustomApi': True,
        'agentModelProvider': provider_mode, 'agentModelId': '',
        'agentModelUrl': profiles['openai']['OPENROUTER_URL'],
    })
    assert config_manager.get_model_api_config('agent')['model'] == (
        vision_model or profiles['openai']['CONVERSATION_MODEL']
    )


class TestFollowCoreBlankModel:

    @pytest.mark.unit
    @pytest.mark.parametrize('prefix,model_type,profile_key', _TEXT_TIERS)
    def test_blank_model_uses_core_provider_default(self, config_manager, prefix, model_type, profile_key):
        openai_profile = _profiles()['openai']
        _write_core_config(config_manager, {
            'coreApi': 'openai',
            'coreApiKey': 'sk-openai-core',
            'assistApi': 'qwen',
            'assistApiKeyQwen': 'sk-qwen-assist',
            'enableCustomApi': True,
            f'{prefix}ModelProvider': 'follow_core',
            f'{prefix}ModelUrl': openai_profile['OPENROUTER_URL'],
            f'{prefix}ModelId': '',
        })

        resolved = config_manager.get_model_api_config(model_type)

        assert resolved['model'] == openai_profile[profile_key]
        assert resolved['base_url'] == openai_profile['OPENROUTER_URL']
        assert resolved['api_key'] == 'sk-openai-core'

    @pytest.mark.unit
    def test_saved_model_id_still_wins(self, config_manager):
        _write_core_config(config_manager, {
            'coreApi': 'openai',
            'coreApiKey': 'sk-openai-core',
            'assistApi': 'qwen',
            'assistApiKeyQwen': 'sk-qwen-assist',
            'enableCustomApi': True,
            'conversationModelProvider': 'follow_core',
            'conversationModelId': 'gpt-user-pick',
        })

        assert config_manager.get_model_api_config('conversation')['model'] == 'gpt-user-pick'

    @pytest.mark.unit
    def test_free_core_ignores_stale_model_id(self, config_manager, no_region_probe):
        free_profile = _profiles()['free']
        _write_core_config(config_manager, {
            'coreApi': 'free',
            'coreApiKey': 'free-access',
            'assistApi': 'qwen',
            'assistApiKeyQwen': 'sk-qwen-assist',
            'enableCustomApi': True,
            'conversationModelProvider': 'follow_core',
            'conversationModelId': 'stale-model-from-another-vendor',
        })

        resolved = config_manager.get_model_api_config('conversation')

        assert resolved['model'] == free_profile['CONVERSATION_MODEL']


class TestNamedProviderBlankModel:

    @pytest.mark.unit
    @pytest.mark.parametrize('prefix,model_type,profile_key', _TEXT_TIERS)
    @pytest.mark.parametrize('slot_url', ['', 'https://dashscope.aliyuncs.com/compatible-mode/v1'])
    def test_unbound_named_slot_keeps_assist_default(
        self, config_manager, prefix, model_type, profile_key, slot_url,
    ):
        qwen_profile = _profiles()['qwen']
        _write_core_config(config_manager, {
            'coreApi': 'qwen', 'assistApi': 'qwen', 'coreApiKey': 'sk-qwen',
            'enableCustomApi': True,
            f'{prefix}ModelProvider': 'openai',
            f'{prefix}ModelUrl': slot_url,
            f'{prefix}ModelId': '',
        })
        resolved = config_manager.get_model_api_config(model_type)
        assert resolved['model'] == qwen_profile[profile_key]
        assert resolved['base_url'] == (slot_url or qwen_profile['OPENROUTER_URL'])

    @pytest.mark.unit
    @pytest.mark.parametrize('prefix,model_type,profile_key', _TEXT_TIERS)
    def test_blank_model_uses_that_providers_default(self, config_manager, prefix, model_type, profile_key):
        deepseek_profile = _profiles()['deepseek']
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'coreApiKey': 'sk-qwen-core',
            'assistApi': 'qwen',
            'assistApiKeyQwen': 'sk-qwen-core',
            'assistApiKeyDeepseek': 'sk-deepseek',
            'enableCustomApi': True,
            f'{prefix}ModelProvider': 'deepseek',
            f'{prefix}ModelUrl': deepseek_profile['OPENROUTER_URL'],
            f'{prefix}ModelId': '',
        })

        resolved = config_manager.get_model_api_config(model_type)

        assert resolved['model'] == deepseek_profile[profile_key]
        assert resolved['base_url'] == deepseek_profile['OPENROUTER_URL']
        assert resolved['api_key'] == 'sk-deepseek'

    @pytest.mark.unit
    def test_anthropic_provider_keeps_its_protocol(self, config_manager):
        claude_profile = _profiles()['claude']
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'coreApiKey': 'sk-qwen-core',
            'assistApi': 'qwen',
            'assistApiKeyQwen': 'sk-qwen-core',
            'assistApiKeyClaude': 'sk-ant-test',
            'enableCustomApi': True,
            'summaryModelProvider': 'claude',
            'summaryModelUrl': claude_profile['OPENROUTER_URL'],
            'summaryModelId': '',
        })

        resolved = config_manager.get_model_api_config('summary')

        assert resolved['model'] == claude_profile['SUMMARY_MODEL']
        assert resolved['provider_type'] == 'anthropic'

    @pytest.mark.unit
    def test_saved_model_id_still_wins(self, config_manager):
        deepseek_profile = _profiles()['deepseek']
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'coreApiKey': 'sk-qwen-core',
            'assistApi': 'qwen',
            'assistApiKeyQwen': 'sk-qwen-core',
            'assistApiKeyDeepseek': 'sk-deepseek',
            'enableCustomApi': True,
            'visionModelProvider': 'deepseek',
            'visionModelUrl': deepseek_profile['OPENROUTER_URL'],
            'visionModelId': 'deepseek-user-pick',
        })

        assert config_manager.get_model_api_config('vision')['model'] == 'deepseek-user-pick'

    @pytest.mark.unit
    def test_fixed_model_provider_ignores_stale_model_id(self, config_manager):
        kimi_code_profile = _profiles()['kimi_code']
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'coreApiKey': 'sk-qwen-core',
            'assistApi': 'qwen',
            'assistApiKeyQwen': 'sk-qwen-core',
            'assistApiKeyKimiCode': 'sk-kimi-code',
            'enableCustomApi': True,
            'agentModelProvider': 'kimi_code',
            'agentModelUrl': kimi_code_profile['OPENROUTER_URL'],
            'agentModelId': 'stale-model-from-another-vendor',
        })

        assert config_manager.get_model_api_config('agent')['model'] == kimi_code_profile['AGENT_MODEL']


class TestUnchangedFallbacks:

    @pytest.mark.unit
    def test_custom_provider_blank_model_keeps_assist_model(self, config_manager):
        qwen_profile = _profiles()['qwen']
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'coreApiKey': 'sk-qwen-core',
            'assistApi': 'qwen',
            'assistApiKeyQwen': 'sk-qwen-core',
            'enableCustomApi': True,
            'conversationModelProvider': 'custom',
            'conversationModelUrl': 'http://127.0.0.1:8000/v1',
            'conversationModelId': '',
            'conversationModelApiKey': 'sk-local',
        })

        resolved = config_manager.get_model_api_config('conversation')

        assert resolved['model'] == qwen_profile['CONVERSATION_MODEL']
        assert resolved['base_url'] == 'http://127.0.0.1:8000/v1'

    @pytest.mark.unit
    def test_follow_assist_on_fixed_model_assist_ignores_stale_model_id(self, config_manager):
        kimi_code_profile = _profiles()['kimi_code']
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'coreApiKey': 'sk-qwen-core',
            'assistApi': 'kimi_code',
            'assistApiKeyKimiCode': 'sk-kimi-code',
            'enableCustomApi': True,
            'conversationModelProvider': 'follow_assist',
            'conversationModelId': 'stale-model-from-another-vendor',
        })

        assert config_manager.get_model_api_config('conversation')['model'] == kimi_code_profile['CONVERSATION_MODEL']
