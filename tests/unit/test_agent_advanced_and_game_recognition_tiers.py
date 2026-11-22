"""使魔模型·高级 (agent_advanced) and 小游戏识别模型 (game_recognition).

Both work like the small familiar tier: unless set up in full on their own
(URL and model), they are the tier they follow, as a whole. agent_advanced
follows the familiar tier (``agent``), game_recognition the recognition tier.
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

SOURCES = {
    'agent': {
        'agentModelProvider': 'custom',
        'agentModelUrl': 'https://agent.custom.test/v1',
        'agentModelId': 'agent-custom-model',
        'agentModelApiKey': 'sk-agent-custom',
    },
    'recognition': {
        'recognitionModelProvider': 'custom',
        'recognitionModelUrl': 'https://recognition.custom.test/v1',
        'recognitionModelId': 'recognition-custom-model',
        'recognitionModelApiKey': 'sk-recognition-custom',
    },
}

# (model_type, field prefix, followed tier, follow value)
SLOTS = [
    ('agent_advanced', 'agentAdvanced', 'agent', 'follow_agent'),
    ('game_recognition', 'gameRecognition', 'recognition', 'follow_recognition'),
]


def _endpoint(result):
    return result['model'], result['base_url'], result['api_key'], result['provider_type']


def _own(prefix, **overrides):
    own = {
        f'{prefix}ModelProvider': 'custom',
        f'{prefix}ModelUrl': f'https://{prefix.lower()}.own.test/v1',
        f'{prefix}ModelId': f'{prefix}-own-model',
        f'{prefix}ModelApiKey': f'sk-{prefix}-own',
    }
    own.update({f'{prefix}Model{k}': v for k, v in overrides.items()})
    return own


@pytest.mark.parametrize('model_type, prefix, source, follow', SLOTS)
def test_without_custom_api_it_is_the_followed_tier(config_manager, model_type, prefix, source, follow):
    _write_core_config(config_manager, {**BASE, **_own(prefix)})
    result = config_manager.get_model_api_config(model_type)
    assert _endpoint(result) == _endpoint(config_manager.get_model_api_config(source))
    assert result['model']


@pytest.mark.parametrize('model_type, prefix, source, follow', SLOTS)
@pytest.mark.parametrize('provider', [None, 'follow'])
def test_the_followed_tier_is_the_default(config_manager, model_type, prefix, source, follow, provider):
    data = {**BASE, 'enableCustomApi': True, **SOURCES[source]}
    if provider:
        data[f'{prefix}ModelProvider'] = follow
    _write_core_config(config_manager, data)
    assert _endpoint(config_manager.get_model_api_config(model_type)) == _endpoint(
        config_manager.get_model_api_config(source)
    )
    assert config_manager.get_model_api_config(model_type)['model'] == SOURCES[source][f'{source}ModelId']


@pytest.mark.parametrize('model_type, prefix, source, follow', SLOTS)
def test_complete_own_settings_are_used_with_only_their_own_key(config_manager, model_type, prefix, source, follow):
    _write_core_config(config_manager, {**BASE, 'enableCustomApi': True, **SOURCES[source], **_own(prefix)})
    assert _endpoint(config_manager.get_model_api_config(model_type)) == (
        f'{prefix}-own-model', f'https://{prefix.lower()}.own.test/v1', f'sk-{prefix}-own', 'openai_compatible',
    )


@pytest.mark.parametrize('model_type, prefix, source, follow', SLOTS)
@pytest.mark.parametrize('missing', ['Url', 'Id'])
def test_incomplete_own_settings_are_the_whole_followed_tier(config_manager, model_type, prefix, source, follow, missing):
    _write_core_config(config_manager, {
        **BASE, 'enableCustomApi': True, **SOURCES[source], **_own(prefix, **{missing: ''}),
    })
    assert _endpoint(config_manager.get_model_api_config(model_type)) == _endpoint(
        config_manager.get_model_api_config(source)
    )


@pytest.mark.parametrize('model_type, prefix, source, follow', SLOTS)
@pytest.mark.parametrize('provider', ['follow', 'incomplete'])
def test_unused_fields_never_reach_the_region_scan(config_manager, model_type, prefix, source, follow, provider):
    # A paid configuration with a leftover free-route URL in a slot that is not in use
    # must not probe the user's IP (invariant #2).
    from utils.config_manager import ConfigManager

    own = _own(prefix, Url='https://www.lanlan.tech/text/v1')
    if provider == 'follow':
        own[f'{prefix}ModelProvider'] = follow
    else:
        own[f'{prefix}ModelId'] = ''
    _write_core_config(config_manager, {
        'coreApiKey': 'sk-core', 'coreApi': 'openai', 'assistApi': 'openai', 'assistApiKeyOpenai': 'sk-openai-test',
        'enableCustomApi': True, **own,
    })
    cfg = config_manager.get_core_config()
    key = model_type.upper() + '_MODEL'
    assert (cfg[key], cfg[f'{key}_URL'], cfg[f'{key}_API_KEY']) == ('', '', '')
    assert ConfigManager._config_needs_region(cfg) is False


def test_a_paid_default_configuration_never_needs_the_region_probe(config_manager):
    # The new snapshot defaults stay empty: a free-route default URL there would make
    # every paid configuration probe the IP.
    from utils.config_manager import ConfigManager

    _write_core_config(config_manager, {
        'coreApiKey': 'sk-core', 'coreApi': 'openai', 'assistApi': 'openai', 'assistApiKeyOpenai': 'sk-openai-test',
    })
    cfg = config_manager.get_core_config()
    for key in ('AGENT_ADVANCED_MODEL', 'GAME_RECOGNITION_MODEL'):
        assert (cfg[key], cfg[f'{key}_URL'], cfg[f'{key}_API_KEY']) == ('', '', '')
    assert ConfigManager._config_needs_region(cfg) is False


@pytest.mark.parametrize('model_type, prefix, source, follow', SLOTS)
def test_follow_assist_takes_the_assist_tier_model(config_manager, model_type, prefix, source, follow):
    _write_core_config(config_manager, {
        **BASE, 'enableCustomApi': True, **SOURCES[source], f'{prefix}ModelProvider': 'follow_assist',
    })
    result = config_manager.get_model_api_config(model_type)
    # The followed tier is customised; follow_assist must not copy it but take the assist preset.
    assert result['model'] != SOURCES[source][f'{source}ModelId']
    assert result['model']


def test_the_small_familiar_keeps_its_two_follow_choices(config_manager):
    # agentSmall moved into the shared table; follow_agent must still work.
    _write_core_config(config_manager, {
        **BASE, 'enableCustomApi': True, **SOURCES['agent'], **SOURCES['recognition'],
        'agentSmallModelProvider': 'follow_agent',
    })
    assert config_manager.get_model_api_config('agent_small')['model'] == 'agent-custom-model'


def test_save_whitelist_and_connectivity_know_the_tiers():
    from main_routers.config_router.core_config import CORE_CONFIG_MODEL_TYPES, CORE_CONFIG_SECRET_FIELDS

    for prefix in ('agentAdvanced', 'gameRecognition'):
        assert prefix in CORE_CONFIG_MODEL_TYPES
        assert f'{prefix}ModelApiKey' in CORE_CONFIG_SECRET_FIELDS


@pytest.mark.parametrize('prefix', ['agentAdvanced', 'gameRecognition'])
def test_save_connectivity_probes_a_named_provider_of_the_new_slots(prefix):
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
        'enableCustomApi': True, f'{prefix}ModelProvider': 'qwen_intl',
    }
    assert 'assist:qwen_intl' in _build_save_connectivity_targets(core_cfg, api_config)


def test_the_soccer_passive_guard_uses_the_minigame_recognition_tier(monkeypatch):
    from main_routers.game_router import char_info

    asked = []

    class _Manager:
        def get_model_api_config(self, model_type):
            asked.append(model_type)
            return {'model': 'm', 'base_url': 'http://127.0.0.1:1/v1', 'api_key': 'k', 'provider_type': 'openai'}

    monkeypatch.setattr(char_info, 'get_config_manager', lambda: _Manager())
    monkeypatch.setattr(char_info, '_get_character_info', lambda name=None: {})
    assert char_info._get_game_route_recognition_llm_info('Lan')['model'] == 'm'
    assert char_info._get_game_route_summary_llm_info('Lan')['model'] == 'm'
    assert asked == ['game_recognition', 'game_summary']
