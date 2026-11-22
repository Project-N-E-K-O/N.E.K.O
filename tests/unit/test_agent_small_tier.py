"""The small familiar tier (agent_small).

Unless configured separately it is the recognition (small, ``emotion``) tier;
``follow_agent`` makes it the familiar (``agent``) tier instead.
"""

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

EMOTION_CUSTOM = {
    'emotionModelProvider': 'custom',
    'emotionModelUrl': 'https://emotion.custom.test/v1',
    'emotionModelId': 'emotion-custom-model',
    'emotionModelApiKey': 'sk-emotion-custom',
}

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


def test_without_custom_api_it_is_the_recognition_model(config_manager):
    # Even a saved small-familiar override is ignored while custom API is off,
    # exactly like the other per-feature slots.
    _write_core_config(config_manager, {**BASE, **SMALL_CUSTOM, 'agentSmallModelProvider': 'follow_agent'})
    small = config_manager.get_model_api_config('agent_small')
    assert _endpoint(small) == _endpoint(config_manager.get_model_api_config('emotion'))
    assert small['model']


@pytest.mark.parametrize('provider', [None, 'follow_emotion'])
def test_recognition_model_is_the_default_and_is_mirrored_when_custom(config_manager, provider):
    data = {**BASE, 'enableCustomApi': True, **EMOTION_CUSTOM, **AGENT_CUSTOM}
    if provider:
        data['agentSmallModelProvider'] = provider
    _write_core_config(config_manager, data)
    assert _endpoint(config_manager.get_model_api_config('agent_small')) == (
        'emotion-custom-model', 'https://emotion.custom.test/v1', 'sk-emotion-custom', 'openai_compatible',
    )


def test_follow_agent_makes_it_the_familiar_model(config_manager):
    _write_core_config(config_manager, {
        **BASE, 'enableCustomApi': True, **EMOTION_CUSTOM, **AGENT_CUSTOM, 'agentSmallModelProvider': 'follow_agent',
    })
    assert _endpoint(config_manager.get_model_api_config('agent_small')) == (
        'agent-custom-model', 'https://agent.custom.test/v1', 'sk-agent-custom', 'openai_compatible',
    )


@pytest.mark.parametrize('provider', ['follow_emotion', 'follow_agent'])
def test_following_ignores_stale_values_left_in_the_small_familiar_fields(config_manager, provider):
    # Fields typed before switching to a follow option stay on disk; following must win over them.
    _write_core_config(config_manager, {
        **BASE, 'enableCustomApi': True, **EMOTION_CUSTOM, **AGENT_CUSTOM,
        'agentSmallModelProvider': provider,
        'agentSmallModelUrl': 'https://stale.test/v1', 'agentSmallModelId': 'stale-model',
        'agentSmallModelApiKey': 'sk-stale',
    })
    followed = 'emotion' if provider == 'follow_emotion' else 'agent'
    assert _endpoint(config_manager.get_model_api_config('agent_small')) == _endpoint(
        config_manager.get_model_api_config(followed))


def test_explicit_custom_small_familiar_is_used(config_manager):
    _write_core_config(config_manager, {**BASE, 'enableCustomApi': True, **EMOTION_CUSTOM, **SMALL_CUSTOM})
    small = config_manager.get_model_api_config('agent_small')
    assert small['is_custom'] is True
    assert _endpoint(small) == (
        'small-custom-model', 'https://small.custom.test/v1', 'sk-small-custom', 'openai_compatible',
    )
    assert config_manager.get_model_api_config('emotion')['model'] == 'emotion-custom-model'


def test_incomplete_custom_small_familiar_falls_back_to_the_recognition_model(config_manager):
    _write_core_config(config_manager, {
        **BASE, 'enableCustomApi': True, **EMOTION_CUSTOM,
        'agentSmallModelProvider': 'custom', 'agentSmallModelUrl': '', 'agentSmallModelId': '',
    })
    small = config_manager.get_model_api_config('agent_small')
    assert _endpoint(small) == _endpoint(config_manager.get_model_api_config('emotion'))


def test_partial_custom_small_familiar_completes_from_the_recognition_model(config_manager):
    # Only a URL typed: the model name comes from the recognition tier just resolved,
    # never from a hardcoded seed (Greptile P1 on #3930).
    _write_core_config(config_manager, {
        **BASE, 'enableCustomApi': True, **EMOTION_CUSTOM,
        'agentSmallModelProvider': 'custom', 'agentSmallModelUrl': 'https://my.test/v1', 'agentSmallModelId': '',
    })
    small = config_manager.get_model_api_config('agent_small')
    assert (small['model'], small['base_url']) == ('emotion-custom-model', 'https://my.test/v1')


def test_follow_assist_takes_the_assist_recognition_model(config_manager):
    from utils.config_manager import get_assist_api_profiles

    openai_small = get_assist_api_profiles()['openai']['EMOTION_MODEL']
    _write_core_config(config_manager, {
        'coreApiKey': 'sk-core', 'coreApi': 'openai', 'assistApi': 'openai', 'assistApiKeyOpenai': 'sk-openai-test',
        'enableCustomApi': True, **EMOTION_CUSTOM,
        'agentSmallModelProvider': 'follow_assist', 'agentSmallModelId': '',
    })
    small = config_manager.get_model_api_config('agent_small')
    assert small['model'] == openai_small
    assert small['base_url'] != 'https://emotion.custom.test/v1'


def test_named_provider_small_familiar_uses_that_providers_small_model(config_manager):
    from utils.config_manager import get_assist_api_profiles

    qwen = get_assist_api_profiles()['qwen']
    _write_core_config(config_manager, {
        **BASE, 'enableCustomApi': True, **EMOTION_CUSTOM,
        'agentSmallModelProvider': 'qwen', 'agentSmallModelUrl': qwen['OPENROUTER_URL'], 'agentSmallModelId': '',
    })
    small = config_manager.get_model_api_config('agent_small')
    assert small['model'] == qwen['EMOTION_MODEL']
    assert small['base_url'] == qwen['OPENROUTER_URL']


def test_named_provider_protocol_falls_back_to_the_recognition_tier_when_the_url_is_foreign(config_manager):
    from utils.config_manager import get_assist_api_profiles

    claude_url = get_assist_api_profiles()['claude']['OPENROUTER_URL']
    _write_core_config(config_manager, {
        **BASE, 'enableCustomApi': True, 'assistApiKeyClaude': 'sk-claude-test',
        'emotionModelProvider': 'claude', 'emotionModelUrl': claude_url,
        # A named slot whose URL is not that provider's takes the protocol of what it falls back to.
        'agentSmallModelProvider': 'kimi_code', 'agentSmallModelUrl': 'https://my.test/v1',
        'agentSmallModelId': 'x-model',
    })
    assert config_manager.get_model_api_config('emotion')['provider_type'] == 'anthropic'
    assert config_manager.get_model_api_config('agent_small')['provider_type'] == 'anthropic'


def test_overseas_free_route_uses_the_rewritten_recognition_endpoint(config_manager, monkeypatch):
    from utils.config_manager import ConfigManager

    _write_core_config(config_manager, {'coreApi': 'free', 'enableCustomApi': True})
    monkeypatch.setattr(ConfigManager, '_region_cache', True)
    monkeypatch.setattr(ConfigManager, '_ip_check_cache', True)
    small = config_manager.get_model_api_config('agent_small')
    assert _endpoint(small) == _endpoint(config_manager.get_model_api_config('emotion'))


def test_save_whitelist_knows_the_tier():
    from main_routers.config_router.core_config import CORE_CONFIG_MODEL_TYPES

    assert 'agentSmall' in CORE_CONFIG_MODEL_TYPES


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


def test_hand_entered_endpoint_never_inherits_the_recognition_key(config_manager):
    # core_config.json edited by hand without a key field: the recognition key must not
    # be sent to the user's own endpoint.
    _write_core_config(config_manager, {
        **BASE, 'enableCustomApi': True, **EMOTION_CUSTOM,
        'agentSmallModelProvider': 'custom', 'agentSmallModelUrl': 'https://my.test/v1',
    })
    small = config_manager.get_model_api_config('agent_small')
    assert small['base_url'] == 'https://my.test/v1'
    assert small['api_key'] == ''


def test_named_provider_with_a_foreign_url_never_gets_the_recognition_key(config_manager):
    # Greptile security P1: kimi_code + a foreign URL + no saved key while the
    # recognition slot holds another provider's key.
    _write_core_config(config_manager, {
        **BASE, 'enableCustomApi': True, **EMOTION_CUSTOM,
        'agentSmallModelProvider': 'kimi_code', 'agentSmallModelUrl': 'https://my.test/v1',
        'agentSmallModelId': 'x-model',
    })
    small = config_manager.get_model_api_config('agent_small')
    assert small['base_url'] == 'https://my.test/v1'
    assert small['api_key'] != 'sk-emotion-custom'


def test_inherited_recognition_endpoint_keeps_its_protocol(config_manager):
    # Codex P2: custom with a blank URL inherits an Anthropic recognition endpoint; the
    # request must use the Messages protocol that endpoint speaks.
    from utils.config_manager import get_assist_api_profiles

    claude_url = get_assist_api_profiles()['claude']['OPENROUTER_URL']
    _write_core_config(config_manager, {
        **BASE, 'enableCustomApi': True, 'assistApiKeyClaude': 'sk-claude-test',
        'emotionModelProvider': 'claude', 'emotionModelUrl': claude_url,
        'agentSmallModelProvider': 'custom', 'agentSmallModelUrl': '', 'agentSmallModelId': '',
    })
    small = config_manager.get_model_api_config('agent_small')
    assert small['base_url'] == claude_url
    assert small['provider_type'] == 'anthropic'


def test_custom_small_familiar_with_its_own_endpoint_speaks_openai(config_manager):
    from utils.config_manager import get_assist_api_profiles

    claude_url = get_assist_api_profiles()['claude']['OPENROUTER_URL']
    _write_core_config(config_manager, {
        **BASE, 'enableCustomApi': True, 'assistApiKeyClaude': 'sk-claude-test',
        'emotionModelProvider': 'claude', 'emotionModelUrl': claude_url,
        **SMALL_CUSTOM,
    })
    assert config_manager.get_model_api_config('agent_small')['provider_type'] == 'openai_compatible'


@pytest.mark.parametrize('provider', ['follow_emotion', 'follow_agent', None])
def test_stale_fields_under_a_follow_choice_never_reach_the_region_scan(config_manager, provider):
    # A free-route URL left in the small-familiar fields from before switching to a follow
    # choice must not make a paid configuration probe the user's IP (invariant #2).
    from utils.config_manager import ConfigManager

    data = {
        'coreApiKey': 'sk-core', 'coreApi': 'openai', 'assistApi': 'openai', 'assistApiKeyOpenai': 'sk-openai-test',
        'enableCustomApi': True,
        'agentSmallModelUrl': 'https://www.lanlan.tech/text/v1', 'agentSmallModelId': 'stale-model',
        'agentSmallModelApiKey': 'sk-stale',
    }
    if provider:
        data['agentSmallModelProvider'] = provider
    _write_core_config(config_manager, data)
    cfg = config_manager.get_core_config()
    assert cfg['AGENT_SMALL_MODEL_URL'] == ''
    assert cfg['AGENT_SMALL_MODEL_API_KEY'] == ''
    assert ConfigManager._config_needs_region(cfg) is False


@pytest.mark.parametrize('small', [
    # The settings page saves blank fields as '' rather than omitting them.
    {'agentSmallModelUrl': '', 'agentSmallModelId': '', 'agentSmallModelApiKey': ''},
    {'agentSmallModelUrl': '', 'agentSmallModelId': 'my-model', 'agentSmallModelApiKey': ''},
    {'agentSmallModelUrl': 'https://emotion.custom.test/v1', 'agentSmallModelId': '', 'agentSmallModelApiKey': ''},
    {'agentSmallModelUrl': 'https://emotion.custom.test/v1/', 'agentSmallModelId': '', 'agentSmallModelApiKey': ''},
])
def test_blank_key_on_the_recognition_endpoint_uses_the_recognition_key(config_manager, small):
    _write_core_config(config_manager, {
        **BASE, 'enableCustomApi': True, **EMOTION_CUSTOM, 'agentSmallModelProvider': 'custom', **small,
    })
    result = config_manager.get_model_api_config('agent_small')
    assert result['base_url'].rstrip('/') == 'https://emotion.custom.test/v1'
    assert result['api_key'] == 'sk-emotion-custom'


def test_blank_key_on_a_different_endpoint_stays_blank(config_manager):
    _write_core_config(config_manager, {
        **BASE, 'enableCustomApi': True, **EMOTION_CUSTOM, 'agentSmallModelProvider': 'custom',
        'agentSmallModelUrl': 'https://my.test/v1', 'agentSmallModelId': '', 'agentSmallModelApiKey': '',
    })
    assert config_manager.get_model_api_config('agent_small')['api_key'] == ''
