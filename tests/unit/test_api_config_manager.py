"""
Unit tests for API configuration management:
- Keybook save/load round-trip
- Custom API toggle (enableCustomApi) isolation
- Core/Assist provider hierarchy and fallback
- Assist follows core when free
- MiniMax key: no fallback to CORE_API_KEY
- Provider exclusion: core vs assist separation
- Hot-reload: config changes take effect after reload
- Custom API key empty string is valid (local providers)
- get_model_api_config fallback chains
- MiniMax / Qwen voice clone key resolution
"""

import json
import os
import pytest
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))


@pytest.fixture()
def config_manager(clean_user_data_dir):
    """Return the patched ConfigManager singleton pointing at a temp dir."""
    from utils.config_manager import get_config_manager
    cm = get_config_manager('N.E.K.O')
    cm.config_dir.mkdir(parents=True, exist_ok=True)
    yield cm


def _write_core_config(cm, data: dict):
    """Write core_config.json into the temp config dir and clear cache."""
    path = cm.get_config_path('core_config.json')
    with open(str(path), 'w', encoding='utf-8') as f:
        json.dump(data, f)
    cm._core_config_cache = None


# ---------------------------------------------------------------------------
# 1. Keybook: save 12 keys, reload, all come back
# ---------------------------------------------------------------------------
class TestKeybookSaveLoad:

    ALL_KEY_FIELDS = {
        'assistApiKeyQwen': 'ASSIST_API_KEY_QWEN',
        'assistApiKeyQwenIntl': 'ASSIST_API_KEY_QWEN_INTL',
        'assistApiKeyOpenai': 'ASSIST_API_KEY_OPENAI',
        'assistApiKeyGlm': 'ASSIST_API_KEY_GLM',
        'assistApiKeyStep': 'ASSIST_API_KEY_STEP',
        'assistApiKeySilicon': 'ASSIST_API_KEY_SILICON',
        'assistApiKeyGemini': 'ASSIST_API_KEY_GEMINI',
        'assistApiKeyKimi': 'ASSIST_API_KEY_KIMI',
        'assistApiKeyDeepseek': 'ASSIST_API_KEY_DEEPSEEK',
        'assistApiKeyDoubao': 'ASSIST_API_KEY_DOUBAO',
        'assistApiKeyMinimax': 'ASSIST_API_KEY_MINIMAX',
        'assistApiKeyMinimaxIntl': 'ASSIST_API_KEY_MINIMAX_INTL',
        'assistApiKeyGrok': 'ASSIST_API_KEY_GROK',
    }

    @pytest.mark.unit
    def test_round_trip_all_keys(self, config_manager):
        """Write 12 keybook keys → reload → verify all are correctly read."""
        payload = {
            'coreApiKey': 'sk-core-test-key',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
        }
        for camel, _ in self.ALL_KEY_FIELDS.items():
            payload[camel] = f'sk-test-{camel}'

        _write_core_config(config_manager, payload)
        cfg = config_manager.get_core_config()

        for camel, upper in self.ALL_KEY_FIELDS.items():
            assert cfg[upper] == f'sk-test-{camel}', (
                f'{upper} should be "sk-test-{camel}", got "{cfg[upper]}"'
            )

    @pytest.mark.unit
    def test_missing_keys_fallback_to_core_key(self, config_manager):
        """Unset assist keys (except minimax) fall back to CORE_API_KEY."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core-master',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
        })
        cfg = config_manager.get_core_config()

        # These should fall back to CORE_API_KEY
        for upper in ['ASSIST_API_KEY_QWEN', 'ASSIST_API_KEY_OPENAI',
                       'ASSIST_API_KEY_GLM', 'ASSIST_API_KEY_STEP',
                       'ASSIST_API_KEY_SILICON', 'ASSIST_API_KEY_GEMINI',
                       'ASSIST_API_KEY_KIMI', 'ASSIST_API_KEY_DEEPSEEK',
                       'ASSIST_API_KEY_DOUBAO', 'ASSIST_API_KEY_GROK']:
            assert cfg[upper] == 'sk-core-master', (
                f'{upper} should fall back to CORE_API_KEY'
            )

        # MiniMax should NOT fall back
        assert cfg['ASSIST_API_KEY_MINIMAX'] == '', (
            'MINIMAX must NOT fall back to CORE_API_KEY'
        )
        assert cfg['ASSIST_API_KEY_MINIMAX_INTL'] == '', (
            'MINIMAX_INTL must NOT fall back to CORE_API_KEY'
        )


# ---------------------------------------------------------------------------
# 2. Custom API toggle isolation
# ---------------------------------------------------------------------------
class TestCustomApiToggle:

    @pytest.mark.unit
    def test_off_ignores_custom_overrides(self, config_manager):
        """enableCustomApi=false → custom model fields are ignored."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'enableCustomApi': False,
            'conversationModelUrl': 'https://custom.example.com/v1',
            'conversationModelId': 'custom-model-123',
            'conversationModelApiKey': 'sk-custom-conv',
        })
        cfg = config_manager.get_core_config()

        # Should still use the assist profile's default, not the custom values
        assert cfg.get('CONVERSATION_MODEL_URL') is None or \
               cfg.get('CONVERSATION_MODEL_URL') != 'https://custom.example.com/v1', \
               'Custom URL should not be applied when enableCustomApi=false'

    @pytest.mark.unit
    def test_on_applies_custom_overrides(self, config_manager):
        """enableCustomApi=true → custom model fields override defaults."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'enableCustomApi': True,
            'conversationModelUrl': 'https://custom.example.com/v1',
            'conversationModelId': 'custom-model-123',
            'conversationModelApiKey': 'sk-custom-conv',
        })
        cfg = config_manager.get_core_config()

        assert cfg['CONVERSATION_MODEL_URL'] == 'https://custom.example.com/v1'
        assert cfg['CONVERSATION_MODEL'] == 'custom-model-123'
        assert cfg['CONVERSATION_MODEL_API_KEY'] == 'sk-custom-conv'

    @pytest.mark.unit
    def test_on_applies_all_model_types(self, config_manager):
        """enableCustomApi=true → all 8 model types can be overridden."""
        model_types = [
            ('conversation', 'CONVERSATION_MODEL'),
            ('summary', 'SUMMARY_MODEL'),
            ('correction', 'CORRECTION_MODEL'),
            ('emotion', 'EMOTION_MODEL'),
            ('vision', 'VISION_MODEL'),
            ('agent', 'AGENT_MODEL'),
            ('omni', 'REALTIME_MODEL'),
            ('tts', 'TTS_MODEL'),
        ]
        payload = {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'enableCustomApi': True,
        }
        for camel_prefix, _ in model_types:
            payload[f'{camel_prefix}ModelUrl'] = f'https://{camel_prefix}.test/v1'
            payload[f'{camel_prefix}ModelId'] = f'{camel_prefix}-test-model'
            payload[f'{camel_prefix}ModelApiKey'] = f'sk-{camel_prefix}'

        _write_core_config(config_manager, payload)
        cfg = config_manager.get_core_config()

        for camel_prefix, upper_model in model_types:
            upper_url = upper_model.replace('_MODEL', '_MODEL_URL')
            upper_key = upper_model.replace('_MODEL', '_MODEL_API_KEY')
            assert cfg[upper_model] == f'{camel_prefix}-test-model', \
                f'{upper_model} not applied'
            assert cfg[upper_url] == f'https://{camel_prefix}.test/v1', \
                f'{upper_url} not applied'
            assert cfg[upper_key] == f'sk-{camel_prefix}', \
                f'{upper_key} not applied'

    @pytest.mark.unit
    def test_custom_api_key_empty_string_valid(self, config_manager):
        """Empty string is a legal API key for local providers (no auth needed)."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'enableCustomApi': True,
            'conversationModelUrl': 'http://localhost:8080/v1',
            'conversationModelId': 'local-llm',
            'conversationModelApiKey': '',
        })
        cfg = config_manager.get_core_config()

        # Empty string should be preserved, NOT fall back to core/assist key
        assert cfg['CONVERSATION_MODEL_API_KEY'] == '', \
            'Empty API key should be preserved for local providers'


# ---------------------------------------------------------------------------
# 3. Assist follows core when free
# ---------------------------------------------------------------------------
class TestAssistFollowsCore:

    @pytest.mark.unit
    def test_free_core_forces_free_assist(self, config_manager):
        """coreApi=free → assistApi forced to free regardless of saved value."""
        _write_core_config(config_manager, {
            'coreApiKey': 'free-access',
            'coreApi': 'free',
            'assistApi': 'qwen',  # User saved qwen, but core is free
        })
        cfg = config_manager.get_core_config()

        assert cfg['assistApi'] == 'free', \
            'When core is free, assist must be forced to free'
        assert cfg.get('IS_FREE_VERSION') is True

    @pytest.mark.unit
    def test_non_free_core_allows_independent_assist(self, config_manager):
        """coreApi=qwen + assistApi=silicon → both independent."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'silicon',
            'assistApiKeySilicon': 'sk-silicon-test',
        })
        cfg = config_manager.get_core_config()

        assert cfg['assistApi'] == 'silicon'
        assert cfg['OPENROUTER_URL'] == 'https://api.siliconflow.cn/v1'


# ---------------------------------------------------------------------------
# 4. MiniMax key: no fallback to CORE_API_KEY
# ---------------------------------------------------------------------------
class TestMinimaxKeyIsolation:

    @pytest.mark.unit
    def test_minimax_empty_stays_empty(self, config_manager):
        """MiniMax keys should NOT fall back to CORE_API_KEY when empty."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core-master-key',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            # minimax keys intentionally omitted
        })
        cfg = config_manager.get_core_config()

        assert cfg['ASSIST_API_KEY_MINIMAX'] == ''
        assert cfg['ASSIST_API_KEY_MINIMAX_INTL'] == ''

    @pytest.mark.unit
    def test_minimax_explicit_key_preserved(self, config_manager):
        """Explicitly set MiniMax keys are preserved."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'assistApiKeyMinimax': 'eyJ-minimax-cn-key',
            'assistApiKeyMinimaxIntl': 'eyJ-minimax-intl-key',
        })
        cfg = config_manager.get_core_config()

        assert cfg['ASSIST_API_KEY_MINIMAX'] == 'eyJ-minimax-cn-key'
        assert cfg['ASSIST_API_KEY_MINIMAX_INTL'] == 'eyJ-minimax-intl-key'


# ---------------------------------------------------------------------------
# 5. Provider exclusion: core vs assist separation
# ---------------------------------------------------------------------------
class TestProviderExclusion:

    @pytest.mark.unit
    def test_core_only_has_realtime_providers(self):
        """core_api_providers should only contain providers with WebSocket URLs."""
        from utils.api_config_loader import get_core_api_profiles
        core_profiles = get_core_api_profiles()

        expected_core = {'free', 'qwen', 'qwen_intl', 'openai', 'step', 'gemini', 'glm'}
        actual_core = set(core_profiles.keys())

        assert actual_core == expected_core, (
            f'Core providers mismatch: expected {expected_core}, got {actual_core}'
        )

    @pytest.mark.unit
    def test_assist_includes_text_only_providers(self):
        """assist_api_providers should include text-only providers like minimax, deepseek."""
        from utils.api_config_loader import get_assist_api_profiles
        assist_profiles = get_assist_api_profiles()

        text_only = {'deepseek', 'doubao', 'minimax', 'minimax_intl', 'kimi', 'grok'}
        for provider in text_only:
            assert provider in assist_profiles, (
                f'{provider} should be in assist_api_providers'
            )

    @pytest.mark.unit
    def test_text_only_providers_not_in_core(self):
        """Providers without realtime endpoints must NOT appear in core."""
        from utils.api_config_loader import get_core_api_profiles
        core_profiles = get_core_api_profiles()

        must_not_be_core = [
            'deepseek', 'doubao', 'minimax', 'minimax_intl',
            'kimi', 'grok', 'silicon',
        ]
        for provider in must_not_be_core:
            assert provider not in core_profiles, (
                f'{provider} should NOT be in core_api_providers'
            )

    @pytest.mark.unit
    def test_api_key_registry_covers_all_assist_providers(self):
        """api_key_registry should have an entry for every non-free assist provider."""
        from utils.api_config_loader import get_config
        data = get_config()

        assist_keys = set(data.get('assist_api_providers', {}).keys()) - {'free'}
        registry_keys = set(data.get('api_key_registry', {}).keys())

        missing = assist_keys - registry_keys
        assert not missing, (
            f'Assist providers missing from api_key_registry: {missing}'
        )

    @pytest.mark.unit
    def test_restricted_providers(self):
        """openai, gemini, grok should be restricted; others should not."""
        from utils.api_config_loader import get_config
        data = get_config()
        registry = data.get('api_key_registry', {})

        expected_restricted = {'openai', 'gemini', 'grok', 'claude', 'qwen_intl'}
        for pk, entry in registry.items():
            if pk in expected_restricted:
                assert entry.get('restricted') is True, \
                    f'{pk} should be restricted'
            else:
                assert entry.get('restricted') is not True, \
                    f'{pk} should NOT be restricted'


# ---------------------------------------------------------------------------
# 6. Hot-reload: config changes take effect after reload
# ---------------------------------------------------------------------------
class TestHotReload:

    @pytest.mark.unit
    def test_config_change_reflected_after_reload(self, config_manager):
        """Write config A → read → write config B → read → values change."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-old',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
        })
        cfg_old = config_manager.get_core_config()
        assert cfg_old['CORE_API_KEY'] == 'sk-old'

        _write_core_config(config_manager, {
            'coreApiKey': 'sk-new',
            'coreApi': 'openai',
            'assistApi': 'openai',
            'assistApiKeyOpenai': 'sk-openai-new',
        })
        cfg_new = config_manager.get_core_config()

        assert cfg_new['CORE_API_KEY'] == 'sk-new'
        assert cfg_new['CORE_API_TYPE'] == 'openai'
        assert cfg_new['CORE_URL'] == 'wss://api.openai.com/v1/realtime'
        assert cfg_new['ASSIST_API_KEY_OPENAI'] == 'sk-openai-new'

    @pytest.mark.unit
    def test_switch_assist_provider_changes_models(self, config_manager):
        """Switching assistApi changes all model defaults."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'glm',
            'assistApiKeyGlm': 'sk-glm-test',
        })
        cfg = config_manager.get_core_config()

        assert 'glm' in cfg['CONVERSATION_MODEL'].lower(), \
            f'CONVERSATION_MODEL should be a GLM model, got {cfg["CONVERSATION_MODEL"]}'
        assert cfg['OPENROUTER_URL'] == 'https://open.bigmodel.cn/api/paas/v4'


# ---------------------------------------------------------------------------
# 7. get_model_api_config fallback chains
# ---------------------------------------------------------------------------
class TestGetModelApiConfig:

    @pytest.mark.unit
    def test_custom_off_returns_assist_fallback(self, config_manager):
        """enableCustomApi=false → get_model_api_config('summary') returns assist profile."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'assistApiKeyQwen': 'sk-qwen-test',
            'enableCustomApi': False,
        })
        result = config_manager.get_model_api_config('summary')

        assert result['is_custom'] is False
        assert result['api_key'] == 'sk-qwen-test'
        assert 'dashscope' in result['base_url']

    @pytest.mark.unit
    def test_custom_on_with_complete_config_returns_custom(self, config_manager):
        """enableCustomApi=true + complete custom config → is_custom=True."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'enableCustomApi': True,
            'summaryModelUrl': 'https://custom-summary.test/v1',
            'summaryModelId': 'custom-summary-v2',
            'summaryModelApiKey': 'sk-custom-summary',
        })
        result = config_manager.get_model_api_config('summary')

        assert result['is_custom'] is True
        assert result['model'] == 'custom-summary-v2'
        assert result['base_url'] == 'https://custom-summary.test/v1'
        assert result['api_key'] == 'sk-custom-summary'

    @pytest.mark.unit
    def test_realtime_fallback_to_core(self, config_manager):
        """Realtime model falls back to core API, not assist."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core-realtime',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'enableCustomApi': False,
        })
        result = config_manager.get_model_api_config('realtime')

        assert result['is_custom'] is False
        assert result['api_key'] == 'sk-core-realtime'
        assert 'wss://' in result['base_url']

    @pytest.mark.unit
    def test_tts_custom_prefers_qwen_for_cosyvoice(self, config_manager):
        """tts_custom falls back to qwen key (for CosyVoice) before generic assist."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'step',
            'assistApi': 'step',
            'assistApiKeyQwen': 'sk-qwen-for-cosyvoice',
            'assistApiKeyStep': 'sk-step-assist',
            'enableCustomApi': False,
        })
        result = config_manager.get_model_api_config('tts_custom')

        assert result['api_key'] == 'sk-qwen-for-cosyvoice', \
            'tts_custom should prefer qwen key for CosyVoice'

    @pytest.mark.unit
    def test_agent_resolves_custom_when_toggle_on(self, config_manager):
        """Agent model resolves custom config when enableCustomApi=true."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'enableCustomApi': True,
            'agentModelUrl': 'https://agent.custom.test/v1',
            'agentModelId': 'agent-custom-model',
            'agentModelApiKey': 'sk-agent-custom',
        })
        result = config_manager.get_model_api_config('agent')

        assert result['is_custom'] is True
        assert result['model'] == 'agent-custom-model'
        assert result['api_key'] == 'sk-agent-custom'

    @pytest.mark.unit
    def test_agent_uses_dedicated_fields_but_not_custom_when_toggle_off(self, config_manager):
        """Agent always uses AGENT_MODEL_URL (with lanlan.app normalization)
        even when enableCustomApi=false, but is_custom must be False."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'assistApiKeyQwen': 'sk-qwen-key',
            'enableCustomApi': False,
        })
        result = config_manager.get_model_api_config('agent')

        assert result['is_custom'] is False, \
            'Agent is_custom should be False when enableCustomApi=false'
        # Agent should still use its dedicated fields, not generic OPENROUTER_URL
        assert result['model'] != '', 'Agent model should be populated'
        assert result['base_url'] != '', 'Agent URL should be populated'
        # AGENT_MODEL_URL is normalized to lanlan.app; OPENROUTER_URL is not
        assert 'lanlan.tech' not in result['base_url'], \
            'Agent URL should have lanlan.app normalization applied'


# ---------------------------------------------------------------------------
# 8. MiniMax / Qwen voice clone key resolution
# ---------------------------------------------------------------------------
class TestVoiceCloneKeyResolution:

    @pytest.mark.unit
    def test_minimax_tts_key_from_keybook(self, config_manager):
        """get_tts_api_key('minimax') reads from ASSIST_API_KEY_MINIMAX."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'assistApiKeyMinimax': 'eyJ-minimax-tts-key',
        })
        key = config_manager.get_tts_api_key('minimax')
        assert key == 'eyJ-minimax-tts-key'

    @pytest.mark.unit
    def test_minimax_intl_tts_key_from_keybook(self, config_manager):
        """get_tts_api_key('minimax_intl') reads from ASSIST_API_KEY_MINIMAX_INTL."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'assistApiKeyMinimaxIntl': 'eyJ-minimax-intl-tts-key',
        })
        key = config_manager.get_tts_api_key('minimax_intl')
        assert key == 'eyJ-minimax-intl-tts-key'

    @pytest.mark.unit
    def test_minimax_tts_key_empty_returns_none(self, config_manager):
        """No minimax key configured → get_tts_api_key returns None."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core-should-not-leak',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            # minimax keys intentionally omitted
        })
        key = config_manager.get_tts_api_key('minimax')
        # Should be None (not CORE_API_KEY!)
        assert key is None, \
            'MiniMax TTS key should be None when not configured, not fall back to core key'

    @pytest.mark.unit
    def test_cosyvoice_tts_key_from_custom_config(self, config_manager):
        """get_tts_api_key('cosyvoice') reads from tts_custom model config."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'assistApiKeyQwen': 'sk-qwen-cosyvoice',
            'enableCustomApi': True,
            'ttsModelUrl': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
            'ttsModelId': 'cosyvoice-v2',
            'ttsModelApiKey': 'sk-tts-custom-key',
        })
        key = config_manager.get_tts_api_key('cosyvoice')
        assert key == 'sk-tts-custom-key'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
