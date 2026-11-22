"""The small familiar tier (agent_small) follows the familiar tier (agent) unless configured."""

import json

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture()
def config_manager(clean_user_data_dir):
    from utils.config_manager import get_config_manager

    cm = get_config_manager('N.E.K.O')
    cm.config_dir.mkdir(parents=True, exist_ok=True)
    yield cm


def _write_core_config(cm, data: dict):
    path = cm.get_config_path('core_config.json')
    with open(str(path), 'w', encoding='utf-8') as f:
        json.dump(data, f)
    cm._core_config_cache = None


BASE = {'coreApiKey': 'sk-core', 'coreApi': 'qwen', 'assistApi': 'qwen', 'assistApiKeyQwen': 'sk-qwen-test'}

AGENT_CUSTOM = {
    'agentModelProvider': 'custom',
    'agentModelUrl': 'https://agent.custom.test/v1',
    'agentModelId': 'agent-custom-model',
    'agentModelApiKey': 'sk-agent-custom',
}

SMALL_CUSTOM = {
    'agentSmallModelProvider': 'custom',
    'agentSmallModelUrl': 'https://small.custom.test/v1',
    'agentSmallModelId': 'small-custom-model',
    'agentSmallModelApiKey': 'sk-small-custom',
}


def _endpoint(result):
    return result['model'], result['base_url'], result['api_key'], result['provider_type']


def test_without_custom_api_it_is_the_familiar_model(config_manager):
    # Even a saved small-familiar override is ignored while custom API is off,
    # exactly like the other per-feature slots.
    _write_core_config(config_manager, {**BASE, **SMALL_CUSTOM})
    small = config_manager.get_model_api_config('agent_small')
    agent = config_manager.get_model_api_config('agent')
    assert _endpoint(small) == _endpoint(agent)
    assert small['model']  # the provider preset's familiar model


@pytest.mark.parametrize('provider', [None, 'follow_agent'])
def test_follow_agent_is_the_default_and_mirrors_a_custom_familiar(config_manager, provider):
    data = {**BASE, 'enableCustomApi': True, **AGENT_CUSTOM}
    if provider:
        data['agentSmallModelProvider'] = provider
    _write_core_config(config_manager, data)
    small = config_manager.get_model_api_config('agent_small')
    assert _endpoint(small) == (
        'agent-custom-model', 'https://agent.custom.test/v1', 'sk-agent-custom', 'openai_compatible',
    )


def test_follow_agent_snapshot_copies_the_familiar_values(config_manager):
    _write_core_config(config_manager, {
        **BASE, 'enableCustomApi': True, **AGENT_CUSTOM, 'agentSmallModelProvider': 'follow_agent',
    })
    core = config_manager.get_core_config()
    assert core['AGENT_SMALL_MODEL'] == 'agent-custom-model'
    assert core['AGENT_SMALL_MODEL_URL'] == 'https://agent.custom.test/v1'
    assert core['AGENT_SMALL_MODEL_API_KEY'] == 'sk-agent-custom'


def test_explicit_custom_small_familiar_is_used(config_manager):
    _write_core_config(config_manager, {**BASE, 'enableCustomApi': True, **AGENT_CUSTOM, **SMALL_CUSTOM})
    small = config_manager.get_model_api_config('agent_small')
    assert small['is_custom'] is True
    assert _endpoint(small) == (
        'small-custom-model', 'https://small.custom.test/v1', 'sk-small-custom', 'openai_compatible',
    )
    # The familiar model itself is untouched.
    assert config_manager.get_model_api_config('agent')['model'] == 'agent-custom-model'


def test_incomplete_custom_small_familiar_falls_back_to_the_familiar_model(config_manager):
    _write_core_config(config_manager, {
        **BASE, 'enableCustomApi': True, **AGENT_CUSTOM,
        'agentSmallModelProvider': 'custom', 'agentSmallModelUrl': '', 'agentSmallModelId': '',
    })
    small = config_manager.get_model_api_config('agent_small')
    assert _endpoint(small) == _endpoint(config_manager.get_model_api_config('agent'))


def test_follow_agent_protocol_follows_the_familiar_provider(config_manager):
    _write_core_config(config_manager, {
        'coreApiKey': 'sk-core', 'coreApi': 'qwen', 'assistApi': 'claude', 'assistApiKeyClaude': 'sk-claude-test',
        'enableCustomApi': True, 'agentSmallModelProvider': 'follow_agent',
    })
    agent = config_manager.get_model_api_config('agent')
    small = config_manager.get_model_api_config('agent_small')
    assert agent['provider_type'] == 'anthropic'
    assert _endpoint(small) == _endpoint(agent)


def test_save_whitelist_and_connectivity_know_the_tier():
    from main_routers.config_router.core_config import CORE_CONFIG_MODEL_TYPES

    assert 'agentSmall' in CORE_CONFIG_MODEL_TYPES


def test_follow_agent_takes_the_familiar_protocol_not_the_assist_one(config_manager):
    from utils.config_manager import get_assist_api_profiles

    claude_url = get_assist_api_profiles()['claude']['OPENROUTER_URL']
    _write_core_config(config_manager, {
        **BASE, 'enableCustomApi': True, 'assistApiKeyClaude': 'sk-claude-test',
        'agentModelProvider': 'claude', 'agentModelUrl': claude_url,
        'agentSmallModelProvider': 'follow_agent',
    })
    assert config_manager.get_model_api_config('conversation')['provider_type'] == 'openai_compatible'
    assert config_manager.get_model_api_config('agent')['provider_type'] == 'anthropic'
    assert config_manager.get_model_api_config('agent_small')['provider_type'] == 'anthropic'


def test_named_provider_small_familiar_uses_that_providers_familiar_model(config_manager):
    from utils.config_manager import get_assist_api_profiles

    qwen = get_assist_api_profiles()['qwen']
    _write_core_config(config_manager, {
        **BASE, 'enableCustomApi': True, **AGENT_CUSTOM,
        'agentSmallModelProvider': 'qwen', 'agentSmallModelUrl': qwen['OPENROUTER_URL'], 'agentSmallModelId': '',
    })
    small = config_manager.get_model_api_config('agent_small')
    assert small['model'] == qwen['AGENT_MODEL']
    assert small['base_url'] == qwen['OPENROUTER_URL']


def test_save_connectivity_probes_a_named_small_familiar_provider():
    from main_routers.config_router.connectivity import _build_save_connectivity_targets

    api_config = {
        'core_api_providers': {},
        'assist_api_providers': {
            'qwen_intl': {
                'name': 'Qwen intl',
                'openrouter_url': 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1',
                'openrouter_urls': [
                    'https://dashscope-intl.aliyuncs.com/compatible-mode/v1',
                    'https://dashscope-us.aliyuncs.com/compatible-mode/v1',
                ],
                'conversation_model': 'qwen-model',
            },
        },
        'api_key_registry': {'qwen_intl': {'config_field': 'assistApiKeyQwenIntl'}},
    }
    core_cfg = {
        'coreApi': 'openai', 'assistApi': 'openai', 'coreApiKey': 'sk-core', 'assistApiKeyQwenIntl': 'sk-intl',
        'enableCustomApi': True, 'agentSmallModelProvider': 'qwen_intl',
    }
    assert 'assist:qwen_intl' in _build_save_connectivity_targets(core_cfg, api_config)


@pytest.mark.parametrize('small', [
    {'agentSmallModelProvider': 'follow_assist', 'agentSmallModelId': ''},
    {'agentSmallModelProvider': 'custom', 'agentSmallModelUrl': 'https://my.test/v1', 'agentSmallModelId': ''},
])
def test_blank_small_familiar_model_id_resolves_like_the_familiar_tier(config_manager, small):
    # A blank model ID must land on the assist provider's familiar model, the same
    # one the familiar tier would pick with these settings, never a hardcoded default.
    from utils.config_manager import get_assist_api_profiles

    openai_agent = get_assist_api_profiles()['openai']['AGENT_MODEL']
    _write_core_config(config_manager, {
        'coreApiKey': 'sk-core', 'coreApi': 'openai', 'assistApi': 'openai', 'assistApiKeyOpenai': 'sk-openai-test',
        'enableCustomApi': True, **small,
    })
    assert config_manager.get_core_config()['AGENT_SMALL_MODEL'] == openai_agent
    assert config_manager.get_model_api_config('agent_small')['model'] == openai_agent


def test_follow_assist_takes_the_assist_familiar_model_even_when_the_familiar_tier_is_custom(config_manager):
    from utils.config_manager import get_assist_api_profiles

    qwen = get_assist_api_profiles()['qwen']
    _write_core_config(config_manager, {
        **BASE, 'enableCustomApi': True, **AGENT_CUSTOM,
        'agentSmallModelProvider': 'follow_assist', 'agentSmallModelId': '',
    })
    small = config_manager.get_model_api_config('agent_small')
    assert small['model'] == qwen['AGENT_MODEL']
    assert small['base_url'] != 'https://agent.custom.test/v1'


def test_follow_agent_ignores_stale_values_left_in_the_small_familiar_fields(config_manager):
    # Fields typed before switching to follow_agent stay on disk; following must win over them.
    _write_core_config(config_manager, {
        **BASE, 'enableCustomApi': True, **AGENT_CUSTOM,
        'agentSmallModelProvider': 'follow_agent',
        'agentSmallModelUrl': 'https://stale.test/v1', 'agentSmallModelId': 'stale-model',
        'agentSmallModelApiKey': 'sk-stale',
    })
    small = config_manager.get_model_api_config('agent_small')
    assert _endpoint(small) == (
        'agent-custom-model', 'https://agent.custom.test/v1', 'sk-agent-custom', 'openai_compatible',
    )
