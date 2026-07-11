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
        'assistApiKeyKimiCode': 'ASSIST_API_KEY_KIMI_CODE',
        'assistApiKeyDeepseek': 'ASSIST_API_KEY_DEEPSEEK',
        'assistApiKeyDoubao': 'ASSIST_API_KEY_DOUBAO',
        'assistApiKeyDoubaoTts': 'ASSIST_API_KEY_DOUBAO_TTS',
        'assistApiKeyMinimax': 'ASSIST_API_KEY_MINIMAX',
        'assistApiKeyMinimaxIntl': 'ASSIST_API_KEY_MINIMAX_INTL',
        'assistApiKeyMimo': 'ASSIST_API_KEY_MIMO',
        'assistApiKeyMimoTokenPlan': 'ASSIST_API_KEY_MIMO_TOKEN_PLAN',
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
    def test_missing_keys_gated_fallback_to_core_key(self, config_manager):
        """仅用户选中的 coreApi/assistApi 对应的槽位会回退到 CORE_API_KEY，
        其余槽位保持空字符串，避免主 Key 被广播到 Key Book 所有栏位。"""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core-master',
            'coreApi': 'qwen',
            'assistApi': 'openai',
        })
        cfg = config_manager.get_core_config()

        # 选中的两个 provider 应该 fallback
        assert cfg['ASSIST_API_KEY_QWEN'] == 'sk-core-master'
        assert cfg['ASSIST_API_KEY_OPENAI'] == 'sk-core-master'

        # 其余所有槽位保持空，不应被 CORE_API_KEY 污染
        for upper in ['ASSIST_API_KEY_GLM', 'ASSIST_API_KEY_STEP',
                       'ASSIST_API_KEY_SILICON', 'ASSIST_API_KEY_GEMINI',
                       'ASSIST_API_KEY_KIMI', 'ASSIST_API_KEY_KIMI_CODE',
                       'ASSIST_API_KEY_DEEPSEEK',
                       'ASSIST_API_KEY_DOUBAO', 'ASSIST_API_KEY_DOUBAO_TTS', 'ASSIST_API_KEY_GROK',
                       'ASSIST_API_KEY_CLAUDE', 'ASSIST_API_KEY_OPENROUTER',
                       'ASSIST_API_KEY_QWEN_INTL',
                       'ASSIST_API_KEY_MINIMAX', 'ASSIST_API_KEY_MINIMAX_INTL',
                       'ASSIST_API_KEY_MIMO']:
            assert cfg[upper] == '', (
                f'{upper} 未被选中，不应 fallback 到 CORE_API_KEY'
            )

    @pytest.mark.unit
    def test_qwen_intl_fallback_when_selected(self, config_manager):
        """qwen_intl 是合法的 coreApi，被选中时应 fallback，对偶其他 provider。"""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core-master',
            'coreApi': 'qwen_intl',
            'assistApi': 'qwen_intl',
        })
        cfg = config_manager.get_core_config()
        assert cfg['ASSIST_API_KEY_QWEN_INTL'] == 'sk-core-master'

    @pytest.mark.unit
    def test_free_core_does_not_fill_paid_assist_when_key_empty(self, config_manager):
        """core=free 时，空的非免费 assist Key 不应回退成 free-access。"""
        _write_core_config(config_manager, {
            'coreApiKey': 'free-access',
            'coreApi': 'free',
            'assistApi': 'qwen',
            'assistApiKeyQwen': '',
        })
        cfg = config_manager.get_core_config()

        assert cfg['CORE_API_KEY'] == 'free-access'
        assert cfg['CORE_API_TYPE'] == 'free'
        assert cfg['assistApi'] == 'qwen'
        assert cfg['ASSIST_API_KEY_QWEN'] == ''
        assert cfg['AUDIO_API_KEY'] == ''
        assert cfg['OPENROUTER_API_KEY'] == ''
        assert cfg['AGENT_MODEL_API_KEY'] == ''
        conversation_cfg = config_manager.get_model_api_config('conversation')
        assert conversation_cfg['api_key'] == ''

    @pytest.mark.unit
    def test_free_core_preserves_paid_assist_explicit_key(self, config_manager):
        """core=free 时，显式填写的非免费 assist Key 仍应生效。"""
        _write_core_config(config_manager, {
            'coreApiKey': 'free-access',
            'coreApi': 'free',
            'assistApi': 'qwen',
            'assistApiKeyQwen': 'sk-assist-qwen',
        })
        cfg = config_manager.get_core_config()

        assert cfg['ASSIST_API_KEY_QWEN'] == 'sk-assist-qwen'
        assert cfg['AUDIO_API_KEY'] == 'sk-assist-qwen'
        assert cfg['OPENROUTER_API_KEY'] == 'sk-assist-qwen'
        assert cfg['AGENT_MODEL_API_KEY'] == 'sk-assist-qwen'

    @pytest.mark.unit
    def test_qwen_intl_uses_saved_successful_us_url(self, config_manager):
        """qwen_intl 连通性测试命中美国 URL 后，运行配置应使用该 URL。"""
        us_url = 'https://dashscope-us.aliyuncs.com/compatible-mode/v1'
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core-master',
            'coreApi': 'qwen_intl',
            'assistApi': 'qwen_intl',
            'resolvedProviderUrls': {
                'assist:qwen_intl': us_url,
            },
        })
        cfg = config_manager.get_core_config()
        assert cfg['OPENROUTER_URL'] == us_url

    @pytest.mark.unit
    def test_qwen_intl_ignores_resolved_url_outside_candidates(self, config_manager):
        """保存的 resolved URL 不属于 provider 候选集时不能污染运行配置。"""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core-master',
            'coreApi': 'qwen_intl',
            'assistApi': 'qwen_intl',
            'resolvedProviderUrls': {
                'assist:qwen_intl': 'https://evil.example.com/v1',
            },
        })
        cfg = config_manager.get_core_config()
        assert cfg['OPENROUTER_URL'] == 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1'

    @pytest.mark.unit
    def test_mimo_token_plan_overrides_only_when_mimo_assist_selected(self, config_manager):
        """MiMo Token Plan is scoped to assistApi=mimo and uses its own tp key."""
        token_plan_url = 'https://token-plan-sgp.xiaomimimo.com/v1'
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core-master',
            'coreApi': 'qwen',
            'assistApi': 'mimo',
            'assistApiKeyMimo': 'sk-regular-mimo',
            'useMimoTokenPlan': True,
            'assistApiKeyMimoTokenPlan': 'tp-mimo-token-plan',
            'resolvedProviderUrls': {
                'assist:mimo_token_plan': token_plan_url,
            },
        })
        cfg = config_manager.get_core_config()
        assert cfg['OPENROUTER_URL'] == token_plan_url
        assert cfg['OPENROUTER_API_KEY'] == 'tp-mimo-token-plan'
        assert cfg['AUDIO_API_KEY'] == 'tp-mimo-token-plan'
        assert cfg['ASSIST_API_KEY_MIMO'] == 'sk-regular-mimo'
        assert cfg['ASSIST_API_KEY_MIMO_TOKEN_PLAN'] == 'tp-mimo-token-plan'

    @pytest.mark.unit
    def test_mimo_token_plan_toggle_does_not_affect_other_assist_api(self, config_manager):
        """Leaving MiMo disables Token Plan routing even if the toggle/key remain saved."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core-master',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'assistApiKeyQwen': 'sk-assist-qwen',
            'useMimoTokenPlan': True,
            'assistApiKeyMimo': 'sk-regular-mimo',
            'assistApiKeyMimoTokenPlan': 'tp-mimo-token-plan',
            'resolvedProviderUrls': {
                'assist:mimo_token_plan': 'https://token-plan-sgp.xiaomimimo.com/v1',
            },
        })
        cfg = config_manager.get_core_config()
        assert cfg['assistApi'] == 'qwen'
        assert cfg['OPENROUTER_URL'] == 'https://dashscope.aliyuncs.com/compatible-mode/v1'
        assert cfg['OPENROUTER_API_KEY'] == 'sk-assist-qwen'

    @pytest.mark.unit
    @pytest.mark.parametrize('assist_api', ['minimax', 'minimax_intl'])
    def test_minimax_never_fallbacks(self, config_manager, assist_api):
        """MiniMax 是 assist-only（TTS 专用），不在 coreApi 候选集里，
        coreApiKey 永远不是 minimax 兼容的 key。即使 assistApi=minimax* 也不应 fallback，
        以免把无效 key 塞进 TTS 凭证槽位导致 401。
        parametrize 两个变体防止"仅国际版误回退"的偏置回归。"""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core-master',
            'coreApi': 'qwen',
            'assistApi': assist_api,
        })
        cfg = config_manager.get_core_config()
        assert cfg['ASSIST_API_KEY_MINIMAX'] == ''
        assert cfg['ASSIST_API_KEY_MINIMAX_INTL'] == ''


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
        """enableCustomApi=true → all custom model types can be overridden."""
        model_types = [
            ('conversation', 'CONVERSATION_MODEL'),
            ('summary', 'SUMMARY_MODEL'),
            ('gameMain', 'GAME_MAIN_MODEL'),
            ('gameSummary', 'GAME_SUMMARY_MODEL'),
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
            'gameMainModelProvider': 'custom',
            'gameSummaryModelProvider': 'custom',
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
    def test_game_models_follow_conversation_and_summary_by_default(self, config_manager):
        """Mini-game model slots default to the main text and summary model configs."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'assistApiKeyQwen': 'sk-qwen-test',
            'enableCustomApi': True,
            'conversationModelProvider': 'custom',
            'conversationModelUrl': 'https://conversation.custom.test/v1',
            'conversationModelId': 'conversation-custom-model',
            'conversationModelApiKey': 'sk-conversation-custom',
            'summaryModelProvider': 'custom',
            'summaryModelUrl': 'https://summary.custom.test/v1',
            'summaryModelId': 'summary-custom-model',
            'summaryModelApiKey': 'sk-summary-custom',
            'gameMainModelProvider': 'follow_conversation',
            'gameSummaryModelProvider': 'follow_summary',
        })

        game_main = config_manager.get_model_api_config('game_main')
        game_summary = config_manager.get_model_api_config('game_summary')

        assert game_main['model'] == 'conversation-custom-model'
        assert game_main['base_url'] == 'https://conversation.custom.test/v1'
        assert game_main['api_key'] == 'sk-conversation-custom'
        assert game_summary['model'] == 'summary-custom-model'
        assert game_summary['base_url'] == 'https://summary.custom.test/v1'
        assert game_summary['api_key'] == 'sk-summary-custom'

    @pytest.mark.unit
    def test_game_main_explicit_custom_override(self, config_manager):
        """Mini-game main model can be overridden independently when custom API is enabled."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'enableCustomApi': True,
            'gameMainModelProvider': 'custom',
            'gameMainModelUrl': 'https://game-main.custom.test/v1',
            'gameMainModelId': 'game-main-custom-model',
            'gameMainModelApiKey': 'sk-game-main-custom',
        })

        result = config_manager.get_model_api_config('game_main')

        assert result['is_custom'] is True
        assert result['model'] == 'game-main-custom-model'
        assert result['base_url'] == 'https://game-main.custom.test/v1'
        assert result['api_key'] == 'sk-game-main-custom'

    @pytest.mark.unit
    def test_game_summary_explicit_custom_override(self, config_manager):
        """Mini-game summary model can be overridden independently when custom API is enabled."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'enableCustomApi': True,
            'gameSummaryModelProvider': 'custom',
            'gameSummaryModelUrl': 'https://game-summary.custom.test/v1',
            'gameSummaryModelId': 'game-summary-custom-model',
            'gameSummaryModelApiKey': 'sk-game-summary-custom',
        })

        result = config_manager.get_model_api_config('game_summary')

        assert result['is_custom'] is True
        assert result['model'] == 'game-summary-custom-model'
        assert result['base_url'] == 'https://game-summary.custom.test/v1'
        assert result['api_key'] == 'sk-game-summary-custom'

    @pytest.mark.unit
    def test_game_follow_conversation_and_summary_preserve_empty_api_keys(self, config_manager):
        """Mini-game followers preserve legitimate no-auth keys from followed custom slots."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'assistApiKeyQwen': 'sk-qwen-test',
            'enableCustomApi': True,
            'conversationModelProvider': 'custom',
            'conversationModelUrl': 'http://localhost:8080/v1',
            'conversationModelId': 'local-conversation-model',
            'conversationModelApiKey': '',
            'summaryModelProvider': 'custom',
            'summaryModelUrl': 'http://localhost:8081/v1',
            'summaryModelId': 'local-summary-model',
            'summaryModelApiKey': '',
            'gameMainModelProvider': 'follow_conversation',
            'gameSummaryModelProvider': 'follow_summary',
        })

        cfg = config_manager.get_core_config()
        game_main = config_manager.get_model_api_config('game_main')
        game_summary = config_manager.get_model_api_config('game_summary')

        assert cfg['GAME_MAIN_MODEL_API_KEY'] == ''
        assert cfg['GAME_SUMMARY_MODEL_API_KEY'] == ''
        assert game_main['api_key'] == ''
        assert game_summary['api_key'] == ''

    @pytest.mark.unit
    def test_game_follow_assist_derives_model_id_from_assist_profile(self, config_manager):
        """Mini-game follow-assist slots keep model/url/key from the same assist provider."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'gemini',
            'assistApiKeyGemini': 'sk-gemini',
            'enableCustomApi': True,
            'gameMainModelProvider': 'follow_assist',
            'gameMainModelId': 'stale-game-main-model',
            'gameSummaryModelProvider': 'follow_assist',
            'gameSummaryModelId': 'stale-game-summary-model',
        })

        game_main = config_manager.get_model_api_config('game_main')
        game_summary = config_manager.get_model_api_config('game_summary')
        core_config = config_manager.get_core_config()

        assert game_main['model'] == core_config['CONVERSATION_MODEL']
        assert game_main['base_url'] == core_config['OPENROUTER_URL']
        assert game_main['api_key'] == 'sk-gemini'
        assert game_summary['model'] == core_config['SUMMARY_MODEL']
        assert game_summary['base_url'] == core_config['OPENROUTER_URL']
        assert game_summary['api_key'] == 'sk-gemini'

    @pytest.mark.unit
    def test_game_follow_core_derives_model_id_from_core_profile(self, config_manager):
        """Mini-game follow-core slots keep model/url/key from the same core provider."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core-openai',
            'coreApi': 'openai',
            'assistApi': 'qwen',
            'enableCustomApi': True,
            'gameMainModelProvider': 'follow_core',
            'gameMainModelId': 'stale-game-main-model',
            'gameSummaryModelProvider': 'follow_core',
            'gameSummaryModelId': 'stale-game-summary-model',
        })

        game_main = config_manager.get_model_api_config('game_main')
        game_summary = config_manager.get_model_api_config('game_summary')
        from utils.api_config_loader import get_assist_api_profiles
        openai_profile = get_assist_api_profiles()['openai']

        assert game_main['model'] == openai_profile['CONVERSATION_MODEL']
        assert game_main['base_url'] == openai_profile['OPENROUTER_URL']
        assert game_main['api_key'] == 'sk-core-openai'
        assert game_summary['model'] == openai_profile['SUMMARY_MODEL']
        assert game_summary['base_url'] == openai_profile['OPENROUTER_URL']
        assert game_summary['api_key'] == 'sk-core-openai'

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
# 3. Assist / Core 独立选择
# ---------------------------------------------------------------------------
class TestAssistFollowsCore:

    @pytest.mark.unit
    def test_free_core_defaults_assist_to_free_when_empty(self, config_manager):
        """coreApi=free + assistApi='' → 空值兜底为 free（保持免费版一键到位体验）。"""
        _write_core_config(config_manager, {
            'coreApiKey': 'free-access',
            'coreApi': 'free',
            'assistApi': '',
        })
        cfg = config_manager.get_core_config()

        assert cfg['assistApi'] == 'free'
        assert cfg.get('CORE_API_TYPE') == 'free'

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_core_config_api_defaults_empty_assist_to_free_for_free_core(self, monkeypatch):
        """API 管理页读取旧配置时，core=free + assistApi='' 应回填 assist=free。"""
        from main_routers.config_router import core_config as config_router

        async def fake_read_json_async(_path):
            return {
                'coreApiKey': 'free-access',
                'coreApi': 'free',
                'assistApi': '',
            }

        class FakeConfigManager:
            def get_runtime_config_path(self, _filename):
                return 'core_config.json'

        monkeypatch.setattr(config_router, 'read_json_async', fake_read_json_async)
        # NOTE: get_core_config_api 内部是函数内 ``from utils.config_manager
        # import get_config_manager``，模块级 patch 从来打不进去（历史上就是
        # no-op，靠上面的 read_json_async patch 拦截真实路径的读取）。拆包后
        # core_config 模块不再有模块级 get_config_manager，故删除该死 patch。

        response = await config_router.get_core_config_api()

        assert response['success'] is True
        assert response['coreApi'] == 'free'
        assert response['assistApi'] == 'free'
        assert response['assistApiKeyQwen'] == ''

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_core_config_api_returns_kimi_code_key(self, monkeypatch):
        """GET must echo back assistApiKeyKimiCode; otherwise the frontend reads
        an empty value and a re-save overwrites the stored secret."""
        from main_routers.config_router import core_config as config_router

        async def fake_read_json_async(_path):
            return {
                'coreApiKey': 'sk-core',
                'coreApi': 'qwen',
                'assistApi': 'kimi_code',
                'assistApiKeyKimiCode': 'sk-kimi-code-stored',
            }

        class FakeConfigManager:
            def get_runtime_config_path(self, _filename):
                return 'core_config.json'

        monkeypatch.setattr(config_router, 'read_json_async', fake_read_json_async)
        # NOTE: get_core_config_api 内部是函数内 ``from utils.config_manager
        # import get_config_manager``，模块级 patch 从来打不进去（历史上就是
        # no-op，靠上面的 read_json_async patch 拦截真实路径的读取）。拆包后
        # core_config 模块不再有模块级 get_config_manager，故删除该死 patch。

        response = await config_router.get_core_config_api()

        assert response['success'] is True
        assert response['assistApi'] == 'kimi_code'
        assert response['assistApiKeyKimiCode'] == 'sk-kimi-code-stored'

    @pytest.mark.unit
    def test_free_core_defaults_assist_to_free_when_key_missing(self, config_manager):
        """Legacy file with only coreApi=free and no saved assistApi: assist follows free.

        The template default assistApi='qwen' swallows the "key missing" signal
        during merge; without the special case, assist lands on qwen with no
        API key (voice works, but text/memory etc. all fail auth).
        """
        _write_core_config(config_manager, {
            'coreApi': 'free',
        })
        cfg = config_manager.get_core_config()

        assert cfg['assistApi'] == 'free'
        assert cfg.get('CORE_API_TYPE') == 'free'

    @pytest.mark.unit
    def test_non_free_core_keeps_template_assist_when_key_missing(self, config_manager):
        """coreApi=qwen with assistApi key missing keeps template default qwen."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
        })
        cfg = config_manager.get_core_config()

        assert cfg['assistApi'] == 'qwen'

    @pytest.mark.unit
    def test_free_core_honors_explicit_assist(self, config_manager):
        """coreApi=free + assistApi=silicon → 显式选择被保留，agent/text 走 silicon。"""
        _write_core_config(config_manager, {
            'coreApiKey': 'free-access',
            'coreApi': 'free',
            'assistApi': 'silicon',
            'assistApiKeySilicon': 'sk-silicon-test',
        })
        cfg = config_manager.get_core_config()

        assert cfg['assistApi'] == 'silicon', \
            'core=free 不应强制覆盖用户显式选择的 assist'
        assert cfg['OPENROUTER_URL'] == 'https://api.siliconflow.cn/v1'
        # core=free 即语音免费（is_free_voice 维度，CORE_API_TYPE=='free'），与 assist 选择无关
        assert cfg.get('CORE_API_TYPE') == 'free'

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
# 3b. 默认兜底：coreApi 为空/缺失时保持历史默认 qwen
# ---------------------------------------------------------------------------
class TestEmptyCoreApiFallsBackToDefaultQwen:

    @pytest.mark.unit
    def test_empty_core_api_falls_back_to_qwen(self, config_manager):
        """coreApi/assistApi='' → 兜底到默认 qwen。"""
        _write_core_config(config_manager, {
            'coreApiKey': 'free-access',
            'coreApi': '',
            'assistApi': '',
        })
        cfg = config_manager.get_core_config()

        assert cfg['CORE_API_TYPE'] == 'qwen'
        assert cfg['assistApi'] == 'qwen'
        assert 'dashscope.aliyuncs.com' in (cfg.get('CORE_URL') or '')

    @pytest.mark.unit
    def test_missing_core_api_keys_fall_back_to_qwen(self, config_manager):
        """core_config.json 缺少 coreApi/assistApi 字段 → 兜底 qwen。"""
        _write_core_config(config_manager, {'coreApiKey': 'free-access'})
        cfg = config_manager.get_core_config()

        assert cfg['CORE_API_TYPE'] == 'qwen'
        assert cfg['assistApi'] == 'qwen'
        assert 'dashscope.aliyuncs.com' in (cfg.get('CORE_URL') or '')

    @pytest.mark.unit
    def test_explicit_paid_provider_still_honored(self, config_manager):
        """用户显式选了 qwen 必须被尊重。"""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-real-qwen',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
        })
        cfg = config_manager.get_core_config()

        assert cfg['CORE_API_TYPE'] == 'qwen'
        assert 'dashscope.aliyuncs.com' in (cfg.get('CORE_URL') or '')


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

        # grok joined core as a realtime voice provider (Grok Voice, wss
        # endpoint) in PR #1306 — it has a core_url, so it belongs here.
        expected_core = {'free', 'qwen', 'qwen_intl', 'openai', 'step', 'gemini', 'glm', 'grok'}
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

        # grok has a realtime voice endpoint (Grok Voice, PR #1306) so it is
        # intentionally also a core provider — only truly text-only providers
        # are listed here.
        must_not_be_core = [
            'deepseek', 'doubao', 'minimax', 'minimax_intl',
            'kimi', 'silicon',
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
        """受地区限制的 provider 应标记 restricted；默认显示的 provider 不应标记。"""
        from utils.api_config_loader import get_config
        data = get_config()
        registry = data.get('api_key_registry', {})

        expected_restricted = {
            'openai',
            'gemini',
            'grok',
            'claude',
            'openrouter',
            'elevenlabs',
            'qwen_intl',
            'minimax_intl',
        }
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
    def test_tts_custom_prefers_active_qwen_intl_for_cosyvoice(self, config_manager):
        """当前辅助 API 是 qwen_intl 时，CosyVoice 应使用国际版 key 与 URL。"""
        us_url = 'https://dashscope-us.aliyuncs.com/compatible-mode/v1'
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen_intl',
            'assistApi': 'qwen_intl',
            'assistApiKeyQwen': 'sk-qwen-cn',
            'assistApiKeyQwenIntl': 'sk-qwen-intl',
            'resolvedProviderUrls': {
                'assist:qwen_intl': us_url,
            },
            'enableCustomApi': False,
        })
        result = config_manager.get_model_api_config('tts_custom')

        assert result['api_key'] == 'sk-qwen-intl'
        assert result['base_url'] == us_url

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
        """Agent always uses AGENT_MODEL_URL even when enableCustomApi=false,
        but is_custom must be False."""
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
        assert result['base_url'] == 'https://dashscope.aliyuncs.com/compatible-mode/v1'


# ---------------------------------------------------------------------------
# 7b. Agent URL normalization: temporary no-op
# ---------------------------------------------------------------------------
class TestAgentUrlRegionRouting:

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ('non_mainland', 'url_in', 'expected'),
        [
            # 临时保持原样：free-agent-model 走配置中的国内 lanlan.tech 文本入口。
            (True, 'https://www.lanlan.tech/text/v1', 'https://www.lanlan.tech/text/v1'),
            (False, 'https://www.lanlan.tech/text/v1', 'https://www.lanlan.tech/text/v1'),
            (None, 'https://www.lanlan.tech/text/v1', 'https://www.lanlan.tech/text/v1'),
            (False, 'https://www.lanlan.app/text/v1', 'https://www.lanlan.app/text/v1'),
            (True, 'https://www.lanlan.app/text/v1', 'https://www.lanlan.app/text/v1'),
            (True, 'https://lanlan.tech/text/v1', 'https://lanlan.tech/text/v1'),
            (False, 'https://lanlan.tech/text/v1', 'https://lanlan.tech/text/v1'),
        ],
    )
    def test_normalize_agent_url_by_region(self, config_manager, non_mainland, url_in, expected):
        config_manager._check_non_mainland = lambda: non_mainland
        assert config_manager._normalize_agent_url(url_in) == expected

    @pytest.mark.unit
    @pytest.mark.parametrize('non_mainland', [True, False, None])
    def test_normalize_agent_url_custom_url_untouched(self, config_manager, non_mainland):
        """不含 lanlan 域的自定义 URL 原样返回，不受线路影响。"""
        config_manager._check_non_mainland = lambda: non_mainland
        custom = 'https://api.openai.com/v1'
        assert config_manager._normalize_agent_url(custom) == custom

    @pytest.mark.unit
    def test_normalize_agent_url_non_string_passthrough(self, config_manager):
        config_manager._check_non_mainland = lambda: True
        assert config_manager._normalize_agent_url(None) is None


# ---------------------------------------------------------------------------
# 7c. Free API URL region routing: 海外统一走 www.lanlan.app（含 /tts）
# ---------------------------------------------------------------------------
class TestFreeApiUrlRegionRouting:

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ('non_mainland', 'url_in', 'expected'),
        [
            # 海外：lanlan.tech → lanlan.app，/tts 不再降级到裸 lanlan.app，
            # 统一停在 www.lanlan.app（透传 voice 到 Gemini）。
            (True, 'wss://www.lanlan.tech/tts', 'wss://www.lanlan.app/tts'),
            (True, 'wss://www.lanlan.tech/core', 'wss://www.lanlan.app/core'),
            (True, 'https://www.lanlan.tech/text/v1', 'https://www.lanlan.app/text/v1'),
            # 国内：原样保留。
            (False, 'wss://www.lanlan.tech/tts', 'wss://www.lanlan.tech/tts'),
            # 非 lanlan.tech 自定义 URL 不受影响。
            (True, 'wss://api.stepfun.com/v1/realtime/audio', 'wss://api.stepfun.com/v1/realtime/audio'),
        ],
    )
    def test_adjust_free_api_url_keeps_tts_on_www_lanlan_app(
        self, config_manager, non_mainland, url_in, expected,
    ):
        config_manager._check_non_mainland = lambda: non_mainland
        assert config_manager._adjust_free_api_url(url_in, True) == expected


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
    def test_mimo_tts_key_from_keybook(self, config_manager):
        """get_tts_api_key('mimo') reads from ASSIST_API_KEY_MIMO."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'mimo',
            'assistApiKeyMimo': 'sk-mimo-tts-key',
        })
        key = config_manager.get_tts_api_key('mimo')
        assert key == 'sk-mimo-tts-key'

    @pytest.mark.unit
    def test_mimo_tts_key_uses_token_plan_key_when_enabled(self, config_manager):
        """MiMo Token Plan locks normal MiMo key and routes TTS key lookup to tp key."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'mimo',
            'assistApiKeyMimo': 'sk-regular-mimo',
            'useMimoTokenPlan': True,
            'assistApiKeyMimoTokenPlan': 'tp-mimo-token-plan',
        })
        key = config_manager.get_tts_api_key('mimo')
        assert key == 'tp-mimo-token-plan'

    @pytest.mark.unit
    def test_mimo_tts_key_empty_returns_none(self, config_manager):
        """No MiMo key configured → get_tts_api_key returns None."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core-should-not-leak',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
        })
        key = config_manager.get_tts_api_key('mimo')
        assert key is None

    @pytest.mark.unit
    def test_mimo_tts_key_does_not_fallback_when_selected(self, config_manager):
        """Selected MiMo assist API still requires an explicit MiMo key."""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core-master',
            'coreApi': 'qwen',
            'assistApi': 'mimo',
        })
        key = config_manager.get_tts_api_key('mimo')
        assert key is None

    @pytest.mark.unit
    def test_doubao_tts_key_falls_back_to_doubao_keybook(self, config_manager):
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'mimo',
            'ttsModelProvider': 'doubao_tts',
            'ttsModelApiKey': '',
            'assistApiKeyDoubaoTts': 'ark-doubao-speech-key',
            'assistApiKeyDoubao': 'chat-doubao-key',
        })
        key = config_manager.get_tts_api_key('doubao_tts')
        assert key == 'ark-doubao-speech-key'

    @pytest.mark.unit
    def test_doubao_tts_key_does_not_fallback_to_doubao_chat_key(self, config_manager):
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'mimo',
            'ttsModelProvider': 'doubao_tts',
            'ttsModelApiKey': '',
            'assistApiKeyDoubaoTts': '',
            'assistApiKeyDoubao': 'legacy-doubao-key',
        })
        key = config_manager.get_tts_api_key('doubao_tts')
        assert key is None

    @pytest.mark.unit
    def test_doubao_tts_key_ignores_shared_key_from_other_tts_provider(self, config_manager):
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'mimo',
            'ttsModelProvider': 'vllm_omni',
            'ttsModelApiKey': 'sk-vllm-should-not-leak',
            'assistApiKeyDoubaoTts': 'ark-doubao-speech-key',
        })
        key = config_manager.get_tts_api_key('doubao_tts')
        assert key == 'ark-doubao-speech-key'

    @pytest.mark.unit
    def test_doubao_tts_key_skips_masked_shared_key(self, config_manager):
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'mimo',
            'ttsModelProvider': 'doubao_tts',
            'ttsModelApiKey': 'sk-********************************',
            'assistApiKeyDoubaoTts': 'ark-doubao-speech-key',
        })
        key = config_manager.get_tts_api_key('doubao_tts')
        assert key == 'ark-doubao-speech-key'

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

    @pytest.mark.unit
    def test_cosyvoice_clone_runtime_stays_domestic_when_active_intl(self, config_manager):
        """声音克隆显式选国内阿里时，不跟随当前国际版辅助 API。"""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen_intl',
            'assistApi': 'qwen_intl',
            'assistApiKeyQwen': 'sk-qwen-cn',
            'assistApiKeyQwenIntl': 'sk-qwen-intl',
            'enableCustomApi': False,
        })
        runtime = config_manager.get_cosyvoice_clone_runtime('cosyvoice')

        assert runtime['api_key'] == 'sk-qwen-cn'
        assert runtime['provider'] == 'cosyvoice'
        assert 'dashscope.aliyuncs.com' in runtime['base_url']
        assert 'dashscope-intl' not in runtime['base_url']

    @pytest.mark.unit
    def test_cosyvoice_intl_clone_runtime_uses_saved_region_url(self, config_manager):
        """声音克隆显式选阿里国际版时，使用国际版 key 和已检测通过的地区 URL。"""
        us_url = 'https://dashscope-us.aliyuncs.com/compatible-mode/v1'
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'assistApiKeyQwen': 'sk-qwen-cn',
            'assistApiKeyQwenIntl': 'sk-qwen-intl',
            'resolvedProviderUrls': {
                'assist:qwen_intl': us_url,
            },
            'enableCustomApi': False,
        })
        runtime = config_manager.get_cosyvoice_clone_runtime('cosyvoice_intl')

        assert runtime['api_key'] == 'sk-qwen-intl'
        assert runtime['base_url'] == us_url
        assert runtime['storage_key'].startswith('__COSYVOICE_INTL__')

    @pytest.mark.unit
    def test_cosyvoice_intl_md5_dedupe_checks_legacy_raw_key_bucket(self, config_manager):
        """国际版 MD5 去重必须兼容旧版 raw API Key 分区。"""
        intl_key = 'sk-qwen-intl-legacy'
        audio_md5 = 'md5-legacy-audio'
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'assistApiKeyQwen': 'sk-qwen-cn',
            'assistApiKeyQwenIntl': intl_key,
            'enableCustomApi': False,
        })
        runtime = config_manager.get_cosyvoice_clone_runtime('cosyvoice_intl')
        config_manager.save_voice_for_api_key(intl_key, 'voice-old-intl', {
            'voice_id': 'voice-old-intl',
            'provider': 'cosyvoice_intl',
            'audio_md5': audio_md5,
            'ref_language': 'en',
        })

        assert runtime['storage_key'] != intl_key
        assert config_manager.find_voice_by_audio_md5(runtime['storage_key'], audio_md5, 'en') is None
        existing = config_manager.find_cosyvoice_voice_by_audio_md5('cosyvoice_intl', audio_md5, 'en')
        assert existing is not None
        assert existing[0] == 'voice-old-intl'


# ---------------------------------------------------------------------------
# 11. follow_core / follow_assist must NOT be misclassified as 'local' realtime
# ---------------------------------------------------------------------------
class TestFollowProviderNotLocal:
    """前端在 *ModelProvider=follow_core/follow_assist 时会用核心/辅助 provider 的
    URL/Key 把 readonly 输入框联动填上并保存。后端必须把这些字段当作 UI 提示值忽略，
    否则 get_model_api_config 在 enableCustomApi=True 时会误判 realtime=自定义=local，
    导致 TTS 调度落到 dummy_tts_worker（"local不支持原生TTS"），声音消失。
    """

    @pytest.mark.unit
    def test_realtime_follow_core_does_not_become_local(self, config_manager):
        """omniModelProvider=follow_core + 联动自填 omniModelUrl → realtime 仍走 core API。"""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-qwen-core',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'assistApiKeyQwen': 'sk-qwen-core',
            'enableCustomApi': True,
            # 这些是前端 follow_core 联动 readonly 自填的值
            'omniModelProvider': 'follow_core',
            'omniModelUrl': 'wss://dashscope.aliyuncs.com/api-ws/v1/realtime',
            'omniModelId': '',
            'omniModelApiKey': 'sk-qwen-core',
        })
        rt = config_manager.get_model_api_config('realtime')
        assert rt['api_type'] == 'qwen', \
            f"realtime api_type 应跟随 CORE_API_TYPE='qwen'，实际={rt['api_type']!r}"
        assert rt['is_custom'] is False, \
            "follow_core 不应被当作自定义 API（is_custom 必须为 False）"

    @pytest.mark.unit
    def test_tts_follow_assist_does_not_pollute_url(self, config_manager):
        """ttsModelProvider=follow_assist + 联动自填 ttsModelUrl → TTS_MODEL_URL 不被覆盖。"""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-qwen-core',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'assistApiKeyQwen': 'sk-qwen-assist',
            'enableCustomApi': True,
            'ttsModelProvider': 'follow_assist',
            'ttsModelUrl': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
            'ttsModelId': '',
            'ttsModelApiKey': 'sk-qwen-assist',
        })
        cfg = config_manager.get_core_config()
        # follow_assist 时 TTS_MODEL_URL 必须保持空（DEFAULT_TTS_MODEL_URL=""，且
        # core/assist profile 的 field_mapping 都不包含 tts_model_url，没有别的合法来源）。
        # 任何非空值都意味着 follow_* 跳过 URL 覆盖的逻辑被绕过 → 回归。
        assert cfg.get('TTS_MODEL_URL', '') in ('', None), \
            f"follow_assist 时 TTS_MODEL_URL 应为空，实际={cfg.get('TTS_MODEL_URL')!r}"

    @pytest.mark.unit
    def test_non_omni_follow_core_url_not_skipped(self, config_manager):
        """URL skip 的 scope 必须仅限 omni/tts —— 非 omni 模型（conversation/summary/
        correction/emotion/vision/agent）走 chat completion REST，没有 'local' 分支，
        不该被本 PR 的 guard 触动。否则会改变它们的 follow_core 路由行为
        （详见 PR #1084 review thread）。
        """
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-openai-core',
            'coreApi': 'openai',
            'assistApi': 'qwen',
            'assistApiKeyQwen': 'sk-qwen-assist',
            'enableCustomApi': True,
            'conversationModelProvider': 'follow_core',
            'conversationModelUrl': 'https://api.openai.com/v1',  # 前端联动填
            'conversationModelId': '',
            'conversationModelApiKey': 'sk-openai-core',
        })
        cfg = config_manager.get_core_config()
        # conversation 不在 (omni, tts) 白名单，URL 必须被覆盖（保持原逻辑）
        assert cfg.get('CONVERSATION_MODEL_URL') == 'https://api.openai.com/v1', \
            f"非 omni follow_core 的 URL 不应被本 PR 的 guard 跳过，" \
            f"实际={cfg.get('CONVERSATION_MODEL_URL')!r}"

    @pytest.mark.unit
    def test_explicit_custom_still_takes_effect(self, config_manager):
        """provider=custom（用户真的填了自定义 URL）时仍然走自定义路径。"""
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'assistApiKeyQwen': 'sk-assist',
            'enableCustomApi': True,
            'omniModelProvider': 'custom',
            'omniModelUrl': 'wss://my-local-deployment.example/realtime',
            'omniModelId': 'my-local-realtime-model',
            'omniModelApiKey': 'sk-local-key',
        })
        rt = config_manager.get_model_api_config('realtime')
        assert rt['base_url'] == 'wss://my-local-deployment.example/realtime'
        assert rt['model'] == 'my-local-realtime-model'
        assert rt['api_type'] == 'local', \
            "provider=custom 时应保留 'local' api_type 标记（自定义 realtime 部署）"
        assert rt['is_custom'] is True


# ---------------------------------------------------------------------------
# PR #1764 reviews #3403710558 / #3404339374: get_core_config() 必须把
# vLLM-Omni TTS raw camelCase key 透传到 normalized snapshot，否则
# main_logic/core.py 的路由检测和 runtime key 拿不到用户保存字段。
# ---------------------------------------------------------------------------
class TestVllmOmniRawKeyPassthrough:

    @pytest.mark.unit
    def test_ttsModelProvider_passes_through_to_snapshot(self, config_manager):
        """When the user writes ttsModelProvider=vllm_omni in core_config.json,
        the snapshot must carry the same raw key."""
        _write_core_config(config_manager, {
            'coreApi': 'gemini',
            'assistApi': 'gemini',
            'enableCustomApi': True,
            'ttsModelProvider': 'vllm_omni',
            'ttsModelUrl': 'http://localhost:8091',
            'ttsModelId': 'Qwen3-TTS',
            'ttsVoiceId': 'Puck',
        })
        cfg = config_manager.get_core_config()
        assert cfg.get('ttsModelProvider') == 'vllm_omni', \
            f"snapshot 应透传 ttsModelProvider=vllm_omni，实际={cfg.get('ttsModelProvider')!r}"
        assert cfg.get('ttsModelUrl') == 'http://localhost:8091', \
            f"snapshot 应透传 ttsModelUrl，实际={cfg.get('ttsModelUrl')!r}"
        assert cfg.get('ttsModelId') == 'Qwen3-TTS', \
            f"snapshot 应透传 ttsModelId，实际={cfg.get('ttsModelId')!r}"
        assert cfg.get('ttsVoiceId') == 'Puck', \
            f"snapshot 应透传 ttsVoiceId=Puck，实际={cfg.get('ttsVoiceId')!r}"

    @pytest.mark.unit
    def test_missing_raw_keys_default_to_empty_string(self, config_manager):
        """Legacy core_config.json files lack vLLM raw TTS keys; the
        snapshot must still expose them with empty-string defaults
        (backward compatibility)."""
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'assistApi': 'qwen',
        })
        cfg = config_manager.get_core_config()
        assert cfg.get('ttsModelProvider') == '', \
            f"缺失时应兜底为空串，实际={cfg.get('ttsModelProvider')!r}"
        assert cfg.get('ttsModelUrl') == '', \
            f"缺失时应兜底为空串，实际={cfg.get('ttsModelUrl')!r}"
        assert cfg.get('ttsModelId') == '', \
            f"缺失时应兜底为空串，实际={cfg.get('ttsModelId')!r}"
        assert cfg.get('ttsVoiceId') == '', \
            f"缺失时应兜底为空串，实际={cfg.get('ttsVoiceId')!r}"
        # 关键：这些 key 必须存在于 dict 中（即使值为空串），
        # 否则 _is_vllm_omni_tts_enabled 的 .get() 会返回 None 触发 .strip() 链路异常
        assert 'ttsModelProvider' in cfg
        assert 'ttsModelUrl' in cfg
        assert 'ttsModelId' in cfg
        assert 'ttsVoiceId' in cfg

    @pytest.mark.unit
    def test_none_value_in_raw_config_normalized_to_empty_string(self, config_manager):
        """When core_config.json is hand-edited to a null value, the snapshot
        must coerce None to an empty string so downstream .strip() does not
        raise AttributeError."""
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'ttsModelProvider': None,
            'ttsModelUrl': None,
            'ttsModelId': None,
            'ttsVoiceId': None,
        })
        cfg = config_manager.get_core_config()
        assert cfg.get('ttsModelProvider') == '', \
            f"None 应兜底为空串，实际={cfg.get('ttsModelProvider')!r}"
        assert cfg.get('ttsModelUrl') == '', \
            f"None 应兜底为空串，实际={cfg.get('ttsModelUrl')!r}"
        assert cfg.get('ttsModelId') == '', \
            f"None 应兜底为空串，实际={cfg.get('ttsModelId')!r}"
        assert cfg.get('ttsVoiceId') == '', \
            f"None 应兜底为空串，实际={cfg.get('ttsVoiceId')!r}"
        # 验证下游真实消费方不会抛 AttributeError
        from main_logic.core import LLMSessionManager
        assert LLMSessionManager._is_vllm_omni_tts_enabled(cfg) is False

    @pytest.mark.unit
    def test_snapshot_drives_is_vllm_omni_tts_enabled(self, config_manager):
        """End-to-end: when core_config.json has ttsModelProvider=vllm_omni and
        enableCustomApi=True, the snapshot returned by get_core_config() must
        make _is_vllm_omni_tts_enabled return True. This is the core contract
        from codex review #3403710558."""
        from main_logic.core import LLMSessionManager
        _write_core_config(config_manager, {
            'coreApi': 'gemini',
            'assistApi': 'gemini',
            'enableCustomApi': True,
            'ttsModelProvider': 'vllm_omni',
            'ttsVoiceId': 'Puck',
        })
        cfg = config_manager.get_core_config()
        assert LLMSessionManager._is_vllm_omni_tts_enabled(cfg) is True, \
            "snapshot 透传 ttsModelProvider 后，_is_vllm_omni_tts_enabled 应返回 True"

    @pytest.mark.unit
    def test_snapshot_disabled_when_custom_api_off(self, config_manager):
        """When enableCustomApi is off, ttsModelProvider=vllm_omni still stays disabled."""
        from main_logic.core import LLMSessionManager
        _write_core_config(config_manager, {
            'coreApi': 'gemini',
            'assistApi': 'gemini',
            'enableCustomApi': False,
            'ttsModelProvider': 'vllm_omni',
            'ttsVoiceId': 'Puck',
        })
        cfg = config_manager.get_core_config()
        assert LLMSessionManager._is_vllm_omni_tts_enabled(cfg) is False

    @pytest.mark.unit
    def test_vllm_omni_selection_uses_strict_boolean_parsing(self):
        from utils.config_manager import ConfigManager

        assert ConfigManager._is_vllm_omni_tts_selected({
            'ENABLE_CUSTOM_API': 'false',
            'ttsModelProvider': 'vllm_omni',
        }) is False
        assert ConfigManager._is_vllm_omni_tts_selected({
            'ENABLE_CUSTOM_API': '0',
            'ttsModelProvider': 'vllm_omni',
        }) is False
        assert ConfigManager._is_vllm_omni_tts_selected({
            'ENABLE_CUSTOM_API': 'true',
            'ttsModelProvider': 'vllm_omni',
        }) is True

    @pytest.mark.unit
    def test_vllm_omni_voice_ids_are_valid_while_provider_selected(self, config_manager):
        """vLLM voice names are provider-local strings and are not stored clone IDs."""
        _write_core_config(config_manager, {
            'coreApi': 'gemini',
            'assistApi': 'gemini',
            'enableCustomApi': True,
            'ttsModelProvider': 'vllm_omni',
            'ttsModelUrl': 'ws://localhost:8091/v1',
            'ttsModelId': 'Qwen3-TTS',
        })

        assert config_manager.validate_voice_id('speaker-from-vllm-server') is True

    @pytest.mark.unit
    def test_vllm_omni_keeps_custom_tts_adapter_rejections(self, config_manager, monkeypatch):
        """Provider-local voices are allowed only after custom TTS prefixes are rejected or accepted."""
        _write_core_config(config_manager, {
            'coreApi': 'gemini',
            'assistApi': 'gemini',
            'enableCustomApi': True,
            'ttsModelProvider': 'vllm_omni',
            'ttsModelUrl': 'ws://localhost:8091/v1',
            'ttsModelId': 'Qwen3-TTS',
        })
        monkeypatch.setattr(
            'utils.config_manager.check_custom_tts_voice_allowed',
            lambda voice_id, _getter: False if voice_id == 'gsv:missing' else None,
        )

        assert config_manager.validate_voice_id('gsv:missing') is False
        assert config_manager.validate_voice_id('speaker-from-vllm-server') is True

    @pytest.mark.unit
    def test_vllm_omni_does_not_expose_or_delete_local_tts_storage(self, config_manager, monkeypatch):
        _write_core_config(config_manager, {
            'coreApi': 'gemini',
            'assistApi': 'gemini',
            'enableCustomApi': True,
            'ttsModelProvider': 'vllm_omni',
            'ttsModelUrl': 'ws://localhost:8091/v1',
            'ttsModelId': 'Qwen3-TTS',
        })
        config_manager.save_voice_storage({
            '__LOCAL_TTS__': {
                'local-speaker': {'name': 'Local Speaker'},
            },
        })

        def _fake_model_config(model_type):
            assert model_type == 'tts_custom'
            return {'is_custom': True, 'base_url': 'ws://localhost:8091/v1', 'api_key': ''}

        monkeypatch.setattr(config_manager, 'get_model_api_config', _fake_model_config)

        assert 'local-speaker' not in config_manager.get_voices_for_current_api(for_listing=True)
        assert config_manager.delete_voice_for_current_api('local-speaker') is False
        assert 'local-speaker' in config_manager.load_voice_storage()['__LOCAL_TTS__']

    @pytest.mark.unit
    def test_doubao_tts_cloned_voice_is_available_for_character_binding(self, config_manager):
        _write_core_config(config_manager, {
            'coreApiKey': 'sk-core',
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'ttsModelProvider': 'doubao_tts',
            'ttsModelApiKey': '112997',
        })
        config_manager.save_voice_storage({
            '__DOUBAO_TTS__112997': {
                'S_xeC2CDp72': {
                    'voice_id': 'S_xeC2CDp72',
                    'provider': 'doubao_tts',
                    'source': 'clone',
                },
            },
        })

        voices = config_manager.get_voices_for_current_api(for_listing=True)

        assert voices['S_xeC2CDp72']['provider'] == 'doubao_tts'
        assert config_manager.validate_voice_id('S_xeC2CDp72') is True
        assert config_manager.delete_voice_for_current_api('S_xeC2CDp72') is True
        assert config_manager.load_voice_storage()['__DOUBAO_TTS__112997'] == {}

    @pytest.mark.unit
    def test_cleanup_keeps_vllm_omni_character_voice(self, config_manager, monkeypatch):
        """cleanup_invalid_voice_ids must not clear provider-local vLLM voices."""
        _write_core_config(config_manager, {
            'coreApi': 'gemini',
            'assistApi': 'gemini',
            'enableCustomApi': True,
            'ttsModelProvider': 'vllm_omni',
            'ttsModelUrl': 'ws://localhost:8091/v1',
            'ttsModelId': 'Qwen3-TTS',
        })
        character_data = {
            '猫娘': {
                'YUI': {
                    '昵称': 'YUI',
                    '_reserved': {'voice_id': 'speaker-from-vllm-server'},
                }
            }
        }
        saved = {}
        monkeypatch.setattr(config_manager, 'load_characters', lambda: character_data)
        monkeypatch.setattr(config_manager, 'save_characters', lambda data: saved.setdefault('data', data))

        cleaned, legacy = config_manager.cleanup_invalid_voice_ids()

        assert cleaned == 0
        assert legacy == []
        assert character_data['猫娘']['YUI']['_reserved']['voice_id'] == 'speaker-from-vllm-server'
        assert saved == {}

    @pytest.mark.unit
    def test_vllm_omni_tts_slot_does_not_feed_cosyvoice_clone_runtime(self, config_manager):
        """CosyVoice clone should require Qwen/CosyVoice credentials, not reuse vLLM TTS."""
        _write_core_config(config_manager, {
            'coreApi': 'gemini',
            'assistApi': 'gemini',
            'enableCustomApi': True,
            'ttsModelProvider': 'vllm_omni',
            'ttsModelUrl': 'ws://localhost:8091/v1',
            'ttsModelId': 'Qwen3-TTS',
            'ttsModelApiKey': 'sk-vllm-should-not-be-used',
        })

        runtime = config_manager.get_cosyvoice_clone_runtime('cosyvoice')

        assert runtime['api_key'] == ''
        assert runtime['base_url'] != 'ws://localhost:8091/v1'
        assert config_manager.get_tts_api_key('cosyvoice') is None


# ---------------------------------------------------------------------------
# GPT-SoVITS「是否启用」收口到 ttsModelProvider 下拉单一真相：snapshot 在
# get_core_config() 这一处把下拉（与 pre-#1830 存量旧 gptsovitsEnabled 开关）派生进
# GPTSOVITS_ENABLED，13 个下游读点全部不动。派生语义：ttsModelProvider 非空即唯一
# 真相，仅缺失/空串时才回落旧开关——不用纯 OR，避免存量切走下拉后旧 true 粘住。
# ---------------------------------------------------------------------------
class TestGptsovitsEnabledDerivation:

    @pytest.mark.unit
    def test_dropdown_provider_only_enables(self, config_manager):
        """Dropdown only: ttsModelProvider=gptsovits (no legacy flag) -> GPTSOVITS_ENABLED=True."""
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'ttsModelProvider': 'gptsovits',
        })
        cfg = config_manager.get_core_config()
        assert cfg['GPTSOVITS_ENABLED'] is True

    @pytest.mark.unit
    def test_legacy_flag_only_enables(self, config_manager):
        """Legacy flag only: a pre-#1830 stored config (gptsovitsEnabled=true, no
        ttsModelProvider) still derives GPTSOVITS_ENABLED=True, so existing GSV
        users are not silently dropped."""
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'gptsovitsEnabled': True,
        })
        cfg = config_manager.get_core_config()
        assert cfg['GPTSOVITS_ENABLED'] is True

    @pytest.mark.unit
    def test_neither_signal_disabled(self, config_manager):
        """Neither signal: no dropdown and no legacy flag -> GPTSOVITS_ENABLED=False."""
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'assistApi': 'qwen',
        })
        cfg = config_manager.get_core_config()
        assert cfg['GPTSOVITS_ENABLED'] is False

    @pytest.mark.unit
    def test_dropdown_other_provider_disabled(self, config_manager):
        """Dropdown picked another provider: ttsModelProvider=vllm_omni -> GPTSOVITS_ENABLED=False."""
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'ttsModelProvider': 'vllm_omni',
        })
        cfg = config_manager.get_core_config()
        assert cfg['GPTSOVITS_ENABLED'] is False

    @pytest.mark.unit
    def test_explicit_provider_authoritative_over_stale_legacy_flag(self, config_manager):
        """An explicit (non-follow) provider wins over a stale legacy flag. After a
        user switches the dropdown to e.g. vllm_omni, the file may still carry
        gptsovitsEnabled=true (the frontend retired that field and the backend merges
        partially). An explicit provider is the single source of truth and must derive
        False — a naive OR would let the stale true stick and leave GSV stuck on."""
        # 这就是不用纯 OR 的原因：旧 flag 在增量合并下会粘住，显式选别家也切不掉。
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'gptsovitsEnabled': True,
            'ttsModelProvider': 'vllm_omni',
        })
        cfg = config_manager.get_core_config()
        assert cfg['GPTSOVITS_ENABLED'] is False

    @pytest.mark.unit
    def test_follow_default_provider_falls_back_to_legacy_flag(self, config_manager):
        """⚠️ Codex PR#1850 P1 regression: a pre-#1830 user who enabled GSV via the
        old checkbox has gptsovitsEnabled=true AND the TTS dropdown left at its default
        'follow_assist'/'follow_core' (the older save path submitted every provider
        dropdown). follow_* is a 'follow assist/core' sentinel — NOT an explicit
        provider — so it must fall back to the legacy flag and keep GSV enabled, not
        be misread as 'switched to another provider' and disabled."""
        for follow in ('follow_assist', 'follow_core'):
            _write_core_config(config_manager, {
                'coreApi': 'qwen',
                'assistApi': 'qwen',
                'gptsovitsEnabled': True,
                'ttsModelProvider': follow,
            })
            cfg = config_manager.get_core_config()
            assert cfg['GPTSOVITS_ENABLED'] is True, f"{follow} 应回落旧 flag 保住存量 GSV"

    @pytest.mark.unit
    def test_empty_provider_falls_back_to_legacy_flag(self, config_manager):
        """An empty ttsModelProvider (treated as unselected) falls back to the
        legacy flag, preserving existing configs."""
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'gptsovitsEnabled': True,
            'ttsModelProvider': '',
        })
        cfg = config_manager.get_core_config()
        assert cfg['GPTSOVITS_ENABLED'] is True

    @pytest.mark.unit
    def test_enabled_snapshot_self_heals_is_custom(self, config_manager):
        """End-to-end: dropdown=gptsovits + a GSV URL -> snapshot GPTSOVITS_ENABLED=True,
        and get_model_api_config('tts_custom') self-heals is_custom=True (no separate
        enableCustomApi needed), which is what dispatch's _gptsovits_is_selected requires."""
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'ttsModelProvider': 'gptsovits',
            'ttsModelUrl': 'http://127.0.0.1:9881',
            'ttsVoiceId': 'gsv:my_voice',
        })
        cfg = config_manager.get_core_config()
        assert cfg['GPTSOVITS_ENABLED'] is True
        tts_cfg = config_manager.get_model_api_config('tts_custom')
        assert tts_cfg['is_custom'] is True


# ---------------------------------------------------------------------------
# save choke point 惰性迁移：gptsovitsEnabled 退役后，用户经下拉显式切到非 gptsovits
# provider（含 follow_*）保存时，把残留旧 flag 落 False——否则 get_core_config 的
# follow_* 回落分支会把旧 true 兜回来，导致切到 follow_assist 也关不掉 GSV。对偶 #1842
# voice_id 的 access-choke-point 惰性迁移思路。
# ---------------------------------------------------------------------------
class TestGptsovitsEnabledSaveMigration:

    @staticmethod
    def _neutralize_side_effects(monkeypatch):
        """Stub out the heavy post-save side effects of update_core_config so the
        test exercises only the persisted-config logic."""
        import asyncio
        from main_routers.config_router import core_config as config_router

        async def _noop(*args, **kwargs):
            return None

        monkeypatch.setattr(config_router, 'get_session_manager', lambda: {})
        monkeypatch.setattr(config_router, 'get_initialize_character_data', lambda: _noop)
        monkeypatch.setattr(config_router, 'ensure_default_yui_voice_for_free_api', _noop)
        monkeypatch.setattr(config_router, '_auto_resolve_provider_urls_for_save', _noop)
        return config_router, asyncio

    class _FakeRequest:
        def __init__(self, payload):
            self._payload = payload

        async def json(self):
            return self._payload

    @pytest.mark.unit
    def test_save_non_gptsovits_provider_clears_stale_flag(self, config_manager, monkeypatch):
        """Stored gptsovitsEnabled=true; the user explicitly switches the dropdown to
        follow_assist and saves -> the stale flag is set to False, and the derived
        GPTSOVITS_ENABLED becomes False (GSV is actually turned off)."""
        config_router, asyncio = self._neutralize_side_effects(monkeypatch)
        _write_core_config(config_manager, {
            'coreApi': 'qwen', 'assistApi': 'qwen', 'enableCustomApi': True,
            'gptsovitsEnabled': True, 'ttsModelProvider': 'gptsovits',
            'ttsModelUrl': 'http://127.0.0.1:9881', 'ttsVoiceId': 'gsv:v',
        })

        resp = asyncio.run(config_router.update_core_config(self._FakeRequest({
            'enableCustomApi': True, 'coreApi': 'qwen', 'assistApi': 'qwen',
            'ttsModelProvider': 'follow_assist',
        })))
        assert resp.get('success') is True

        saved = config_manager.load_json_config('core_config.json', {})
        assert saved.get('gptsovitsEnabled') is False
        config_manager._core_config_cache = None
        assert config_manager.get_core_config()['GPTSOVITS_ENABLED'] is False

    @pytest.mark.unit
    def test_save_gptsovits_provider_keeps_flag_enabled(self, config_manager, monkeypatch):
        """Saving while the dropdown stays on/returns to gptsovits does not clear the
        legacy flag; GSV remains enabled."""
        config_router, asyncio = self._neutralize_side_effects(monkeypatch)
        _write_core_config(config_manager, {
            'coreApi': 'qwen', 'assistApi': 'qwen', 'enableCustomApi': True,
            'gptsovitsEnabled': True, 'ttsModelProvider': 'gptsovits',
            'ttsModelUrl': 'http://127.0.0.1:9881', 'ttsVoiceId': 'gsv:v',
        })

        resp = asyncio.run(config_router.update_core_config(self._FakeRequest({
            'enableCustomApi': True, 'coreApi': 'qwen', 'assistApi': 'qwen',
            'ttsModelProvider': 'gptsovits',
            'ttsModelUrl': 'http://127.0.0.1:9881', 'ttsVoiceId': 'gsv:v',
        })))
        assert resp.get('success') is True

        saved = config_manager.load_json_config('core_config.json', {})
        # 切到/保持 gptsovits 不触发惰性清理，存量 true 原样保留。
        assert saved.get('gptsovitsEnabled') is True
        config_manager._core_config_cache = None
        assert config_manager.get_core_config()['GPTSOVITS_ENABLED'] is True

    @pytest.mark.unit
    def test_update_core_config_persists_kimi_code_assist_key(self, config_manager, monkeypatch):
        config_router, asyncio = self._neutralize_side_effects(monkeypatch)
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'assistApi': 'kimi_code',
            'enableCustomApi': True,
        })

        resp = asyncio.run(config_router.update_core_config(self._FakeRequest({
            'enableCustomApi': True,
            'coreApi': 'qwen',
            'assistApi': 'kimi_code',
            'assistApiKeyKimiCode': 'sk-kimi-code-test',
        })))
        assert resp.get('success') is True

        saved = config_manager.load_json_config('core_config.json', {})
        assert saved.get('assistApiKeyKimiCode') == 'sk-kimi-code-test'
        config_manager._core_config_cache = None
        assert config_manager.get_core_config()['ASSIST_API_KEY_KIMI_CODE'] == 'sk-kimi-code-test'

    @pytest.mark.unit
    def test_update_core_config_doubao_tts_overwrites_stale_shared_tts_key(self, config_manager, monkeypatch):
        config_router, asyncio = self._neutralize_side_effects(monkeypatch)
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'enableCustomApi': True,
            'ttsModelProvider': 'vllm_omni',
            'ttsModelApiKey': 'sk-vllm-should-not-leak',
        })

        resp = asyncio.run(config_router.update_core_config(self._FakeRequest({
            'enableCustomApi': True,
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'ttsModelProvider': 'doubao_tts',
            'assistApiKeyDoubaoTts': 'ark-doubao-speech-key',
        })))
        assert resp.get('success') is True

        saved = config_manager.load_json_config('core_config.json', {})
        assert saved.get('ttsModelApiKey') == 'ark-doubao-speech-key'

    @pytest.mark.unit
    def test_update_core_config_doubao_tts_clears_stale_shared_tts_key_without_speech_key(
        self,
        config_manager,
        monkeypatch,
    ):
        config_router, asyncio = self._neutralize_side_effects(monkeypatch)
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'assistApiKeyDoubao': 'ark-chat-key',
            'enableCustomApi': True,
            'ttsModelProvider': 'vllm_omni',
            'ttsModelApiKey': 'sk-vllm-should-not-leak',
        })

        resp = asyncio.run(config_router.update_core_config(self._FakeRequest({
            'enableCustomApi': True,
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'ttsModelProvider': 'doubao_tts',
        })))
        assert resp.get('success') is True

        saved = config_manager.load_json_config('core_config.json', {})
        assert saved.get('ttsModelApiKey') == ''
        assert saved.get('assistApiKeyDoubaoTts', '') == ''

    @pytest.mark.unit
    def test_get_core_config_api_doubao_tts_display_ignores_foreign_shared_key(
        self,
        config_manager,
    ):
        import asyncio
        from main_routers import config_router

        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'enableCustomApi': True,
            'ttsModelProvider': 'vllm_omni',
            'ttsModelApiKey': 'sk-vllm-should-not-display',
            'assistApiKeyDoubaoTts': '',
        })

        resp = asyncio.run(config_router.get_core_config_api())

        assert resp['success'] is True
        assert resp['assistApiKeyDoubaoTts'] == ''

    @pytest.mark.unit
    def test_get_core_config_api_doubao_tts_display_uses_owned_shared_key(
        self,
        config_manager,
    ):
        import asyncio
        from main_routers import config_router

        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'enableCustomApi': True,
            'ttsModelProvider': 'doubao_tts',
            'ttsModelApiKey': 'ark-doubao-speech-key',
            'assistApiKeyDoubaoTts': '',
        })

        resp = asyncio.run(config_router.get_core_config_api())

        assert resp['success'] is True
        assert resp['assistApiKeyDoubaoTts'] == 'ark-doubao-speech-key'

    @pytest.mark.unit
    def test_get_model_api_config_returns_kimi_code_provider_type(self, config_manager):
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'assistApi': 'kimi_code',
            'assistApiKeyKimiCode': 'sk-kimi-code-test',
            'enableCustomApi': False,
        })

        config_manager._core_config_cache = None
        api_config = config_manager.get_model_api_config('conversation')

        assert api_config['model'] == 'kimi-for-coding'
        assert api_config['base_url'] == 'https://api.kimi.com/coding'
        assert api_config['api_key'] == 'sk-kimi-code-test'
        assert api_config['provider_type'] == 'anthropic'

    @pytest.mark.unit
    def test_kimi_code_agent_falls_back_to_vision_model(self, config_manager):
        # kimi_code 的 agent 槽和其它 provider 一样回退到 VISION_MODEL
        # （= kimi-for-coding）。曾经用 AGENT_MODEL_DISABLED 把它单独关掉，
        # 但 claude 同样走 Anthropic CUA 路径却未关，门控是不一致的半成品；
        # 已 revert，剩余 Anthropic 路径问题在 follow-up issue 跟踪。
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'assistApi': 'kimi_code',
            'assistApiKeyKimiCode': 'sk-kimi-code-test',
            'enableCustomApi': False,
        })

        config_manager._core_config_cache = None
        api_config = config_manager.get_model_api_config('agent')

        assert api_config['model'] == 'kimi-for-coding'
        assert api_config['provider_type'] == 'anthropic'

    @pytest.mark.unit
    def test_provider_type_follow_cycle_does_not_recurse(self, config_manager):
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'assistApi': 'kimi_code',
            'assistApiKeyKimiCode': 'sk-kimi-code-test',
            'enableCustomApi': True,
            'conversationModelProvider': 'follow_summary',
            'summaryModelProvider': 'follow_conversation',
        })

        summary_config = config_manager.get_model_api_config('summary')
        conversation_config = config_manager.get_model_api_config('conversation')

        assert summary_config['provider_type'] == 'anthropic'
        assert conversation_config['provider_type'] == 'anthropic'

    @pytest.mark.unit
    def test_empty_core_fallback_provider_type_uses_core_profile(self, config_manager):
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'coreApiKey': 'sk-core-qwen',
            'assistApi': 'kimi_code',
            'assistApiKeyKimiCode': 'sk-kimi-code-test',
            'enableCustomApi': False,
            'omniModelProvider': '',
        })

        realtime_config = config_manager.get_model_api_config('realtime')

        assert realtime_config['provider_type'] == 'openai_compatible'
        assert realtime_config['api_key'] == 'sk-core-qwen'
        assert 'dashscope' in realtime_config['base_url']


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
