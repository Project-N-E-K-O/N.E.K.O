"""Regression tests for explicitly-configured custom API slots.

Covers four defects that all made an explicit configuration silently not take
effect (issue #2886):

1. A named-provider slot resolved its key from the slot field, which the
   settings page can only ever fill with the redaction sentinel, so the key
   landed as an empty string.
2. A realtime slot stamped ``api_type='local'`` unconditionally, an
   unimplemented branch that also leaks into the global core api type.
3. A slot that falls back to core/assist kept the protocol implied by its
   residual dropdown value, producing an address/protocol mismatch.
4. The vision endpoint and the vision key fell back independently, so a
   vision-only endpoint could be called with the conversation provider's
   credential.
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


def _write_core_config(cm, data: dict):
    """Write core_config.json into the temp config dir and clear cache."""
    path = cm.get_config_path('core_config.json')
    with open(str(path), 'w', encoding='utf-8') as f:
        json.dump(data, f)
    cm._core_config_cache = None


def _base_config(**overrides):
    """A minimal saved config with custom API enabled."""
    data = {
        'coreApi': 'qwen',
        'assistApi': 'qwen',
        'coreApiKey': 'sk-core',
        'assistApiKeyQwen': 'sk-core',
        'enableCustomApi': True,
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# 1. Named-provider slot keys come from the API key book
# ---------------------------------------------------------------------------
class TestNamedProviderSlotKey:

    @pytest.mark.unit
    def test_named_provider_slot_reads_key_book(self, config_manager):
        """A slot pinned to deepseek uses the key book entry for deepseek."""
        _write_core_config(config_manager, _base_config(
            conversationModelProvider='deepseek',
            conversationModelId='deepseek-v4-flash',
            conversationModelUrl='https://api.deepseek.com/v1',
            assistApiKeyDeepseek='sk-deepseek-book',
            # 前端只能把脱敏哨兵拷进槽位字段，落盘后是空串
            conversationModelApiKey='',
        ))
        cfg = config_manager.get_core_config()
        assert cfg['CONVERSATION_MODEL_API_KEY'] == 'sk-deepseek-book', (
            "具名 provider 槽必须从管理簿取 Key，实际="
            f"{cfg['CONVERSATION_MODEL_API_KEY']!r}"
        )

    @pytest.mark.unit
    def test_named_provider_slot_self_heals_existing_broken_config(self, config_manager):
        """An already-broken saved config resolves without needing a re-save."""
        _write_core_config(config_manager, _base_config(
            visionModelProvider='glm',
            visionModelId='glm-4v',
            visionModelUrl='https://open.bigmodel.cn/api/paas/v4',
            assistApiKeyGlm='sk-glm-book',
        ))
        cfg = config_manager.get_core_config()
        assert cfg['VISION_MODEL_API_KEY'] == 'sk-glm-book', (
            "槽位字段缺失时也应从管理簿解析，实际="
            f"{cfg['VISION_MODEL_API_KEY']!r}"
        )

    @pytest.mark.unit
    def test_legacy_slot_key_survives_when_key_book_empty(self, config_manager):
        """Key-book-less legacy configs keep using the stored slot key."""
        _write_core_config(config_manager, _base_config(
            summaryModelProvider='openai',
            summaryModelId='gpt-5-mini',
            summaryModelUrl='https://api.openai.com/v1',
            summaryModelApiKey='sk-legacy-slot',
            # 管理簿里没有 openai 这一条
        ))
        cfg = config_manager.get_core_config()
        assert cfg['SUMMARY_MODEL_API_KEY'] == 'sk-legacy-slot', (
            "管理簿为空时必须保住槽位存量 Key，实际="
            f"{cfg['SUMMARY_MODEL_API_KEY']!r}"
        )

    @pytest.mark.unit
    def test_custom_provider_keeps_slot_key(self, config_manager):
        """'custom' is user-typed and must never be redirected to the book."""
        _write_core_config(config_manager, _base_config(
            correctionModelProvider='custom',
            correctionModelId='my-model',
            correctionModelUrl='http://localhost:11434/v1',
            correctionModelApiKey='sk-typed-by-user',
        ))
        cfg = config_manager.get_core_config()
        assert cfg['CORRECTION_MODEL_API_KEY'] == 'sk-typed-by-user', (
            "custom 槽的 Key 必须用用户手填值，实际="
            f"{cfg['CORRECTION_MODEL_API_KEY']!r}"
        )

    @pytest.mark.unit
    def test_custom_provider_allows_empty_key(self, config_manager):
        """A keyless local endpoint stays keyless (no key-book borrowing)."""
        _write_core_config(config_manager, _base_config(
            correctionModelProvider='custom',
            correctionModelId='llama3',
            correctionModelUrl='http://localhost:11434/v1',
            correctionModelApiKey='',
        ))
        cfg = config_manager.get_core_config()
        assert cfg['CORRECTION_MODEL_API_KEY'] == '', (
            "本地无鉴权端点不应被塞进任何 Key，实际="
            f"{cfg['CORRECTION_MODEL_API_KEY']!r}"
        )


# ---------------------------------------------------------------------------
# 2. Realtime api_type reflects the provider actually selected
# ---------------------------------------------------------------------------
class TestRealtimeApiType:

    @pytest.mark.unit
    def test_user_typed_endpoint_is_local(self, config_manager):
        """'custom' keeps the original 'local' meaning: a self-hosted endpoint."""
        _write_core_config(config_manager, _base_config(
            omniModelProvider='custom',
            omniModelId='my-realtime',
            omniModelUrl='ws://localhost:8000/v1/realtime',
        ))
        rt = config_manager.get_model_api_config('realtime')
        assert rt['api_type'] == 'local', f"实际={rt['api_type']!r}"
        assert rt['base_url'] == 'ws://localhost:8000/v1/realtime'

    @pytest.mark.unit
    def test_named_realtime_provider_keeps_its_identity(self, config_manager):
        """An explicitly picked realtime provider is not flattened to 'local'."""
        _write_core_config(config_manager, _base_config(
            omniModelProvider='glm',
            omniModelId='glm-realtime-air',
            omniModelUrl='wss://open.bigmodel.cn/api/paas/v4/realtime',
        ))
        rt = config_manager.get_model_api_config('realtime')
        assert rt['api_type'] == 'glm', (
            "显式选中的实时服务商必须保住身份，不能变成未实现的 'local'，"
            f"实际={rt['api_type']!r}"
        )

    @pytest.mark.unit
    def test_provider_without_realtime_support_is_ignored(self, config_manager):
        """A text-only provider picked for omni falls back to the core API."""
        _write_core_config(config_manager, _base_config(
            coreApi='qwen',
            omniModelProvider='deepseek',
            omniModelId='deepseek-v4-flash',
            omniModelUrl='https://api.deepseek.com/v1',
        ))
        rt = config_manager.get_model_api_config('realtime')
        assert rt['api_type'] == 'qwen', (
            "没有 realtime 实现的服务商应被忽略并回落核心 API，"
            f"实际={rt['api_type']!r}"
        )
        assert 'deepseek' not in (rt['base_url'] or ''), (
            "被忽略的选择不得把自己的 URL 留在实时槽里，"
            f"实际 base_url={rt['base_url']!r}"
        )

    @pytest.mark.unit
    def test_follow_core_uses_core_api_type(self, config_manager):
        """The default follow_core value keeps tracking the core provider."""
        _write_core_config(config_manager, _base_config(
            coreApi='step',
            assistApi='step',
            omniModelProvider='follow_core',
        ))
        rt = config_manager.get_model_api_config('realtime')
        assert rt['api_type'] == 'step', f"实际={rt['api_type']!r}"

    @pytest.mark.unit
    def test_realtime_api_type_never_local_without_custom(self, config_manager):
        """No named provider may produce the unimplemented 'local' dialect."""
        from utils.api_config_loader import get_core_api_profiles
        for provider in get_core_api_profiles():
            _write_core_config(config_manager, _base_config(
                omniModelProvider=provider,
                omniModelId='some-model',
                omniModelUrl='wss://example.invalid/realtime',
            ))
            rt = config_manager.get_model_api_config('realtime')
            assert rt['api_type'] != 'local', (
                f"provider={provider} 不该落进未实现的 'local' 分支"
            )


# ---------------------------------------------------------------------------
# 3. Fallback slots keep protocol and address same-origin
# ---------------------------------------------------------------------------
class TestFallbackProtocolIsSameOrigin:

    @pytest.mark.unit
    def test_residual_anthropic_provider_does_not_survive_disable(self, config_manager):
        """Turning custom API off drops the residual anthropic protocol."""
        _write_core_config(config_manager, _base_config(
            enableCustomApi=False,
            summaryModelProvider='claude',
            summaryModelId='claude-sonnet-4',
            summaryModelUrl='https://api.anthropic.com/v1',
        ))
        cfg = config_manager.get_core_config()
        summary = config_manager.get_model_api_config('summary')
        assert summary['provider_type'] == 'openai_compatible', (
            "关掉自定义 API 后地址已回落 assist，协议必须同源回落，"
            f"实际={summary['provider_type']!r}"
        )
        assert summary['base_url'] == cfg['OPENROUTER_URL'], (
            "回退分支的地址应当就是 assist 的地址，"
            f"实际={summary['base_url']!r}"
        )

    @pytest.mark.unit
    def test_incomplete_custom_slot_does_not_keep_anthropic(self, config_manager):
        """An incomplete anthropic slot falls back protocol-and-address together."""
        _write_core_config(config_manager, _base_config(
            emotionModelProvider='claude',
            emotionModelId='claude-sonnet-4',
            emotionModelUrl='',
        ))
        emotion = config_manager.get_model_api_config('emotion')
        assert emotion['provider_type'] == 'openai_compatible', (
            "URL 不完整时该槽已回落 assist，协议不能停在 anthropic，"
            f"实际={emotion['provider_type']!r}"
        )

    @pytest.mark.unit
    def test_core_fallback_slot_uses_the_core_protocol(self, config_manager):
        """The core-fallback branch (tts_default / realtime) is same-origin too.

        The TTS dropdown used to offer every assist provider, so a residual
        'claude' can sit in a saved config while the slot itself falls back to
        the core API. Address comes from core, protocol must too.
        """
        _write_core_config(config_manager, _base_config(
            coreApi='qwen',
            enableCustomApi=False,
            ttsModelProvider='claude',
        ))
        cfg = config_manager.get_core_config()
        tts = config_manager.get_model_api_config('tts_default')
        assert tts['provider_type'] == 'openai_compatible', (
            "回落到核心 API 的槽必须用核心 API 的协议，"
            f"实际={tts['provider_type']!r}"
        )
        assert tts['base_url'] == cfg['CORE_URL'], (
            f"回退地址应为 CORE_URL，实际={tts['base_url']!r}"
        )

    @pytest.mark.unit
    def test_complete_anthropic_slot_still_speaks_anthropic(self, config_manager):
        """A genuinely complete anthropic slot keeps its protocol."""
        _write_core_config(config_manager, _base_config(
            emotionModelProvider='claude',
            emotionModelId='claude-sonnet-4',
            emotionModelUrl='https://api.anthropic.com/v1',
            assistApiKeyClaude='sk-ant-book',
        ))
        emotion = config_manager.get_model_api_config('emotion')
        assert emotion['provider_type'] == 'anthropic', (
            "配置完整时必须真的走 Anthropic 协议，"
            f"实际={emotion['provider_type']!r}"
        )
        assert emotion['base_url'] == 'https://api.anthropic.com/v1'
        assert emotion['api_key'] == 'sk-ant-book'


# ---------------------------------------------------------------------------
# 4. Vision endpoint and vision key fall back as a pair
# ---------------------------------------------------------------------------
def _make_offline_client(**kwargs):
    """Build an OmniOfflineClient without touching the network."""
    from main_logic.omni_offline_client import OmniOfflineClient
    params = {
        'base_url': 'https://api.deepseek.com/v1',
        'api_key': 'sk-conversation',
        'model': 'deepseek-v4-flash',
    }
    params.update(kwargs)
    return OmniOfflineClient(**params)


class TestVisionCredentialBoundary:

    @pytest.mark.unit
    def test_separate_vision_endpoint_does_not_borrow_conversation_key(self):
        """A vision-only endpoint with no key must not send the chat credential."""
        client = _make_offline_client(
            vision_base_url='https://generativelanguage.googleapis.com/v1beta',
            vision_api_key='',
            vision_model='gemini-3.1-flash',
        )
        assert client.vision_api_key is None, (
            "视觉端点与对话端点不同源时不得继承对话凭证，实际="
            f"{client.vision_api_key!r}"
        )
        assert client.vision_base_url == 'https://generativelanguage.googleapis.com/v1beta'

    @pytest.mark.unit
    def test_same_endpoint_still_inherits_key(self):
        """Same-origin vision config keeps inheriting, as before."""
        client = _make_offline_client(
            vision_base_url='https://api.deepseek.com/v1',
            vision_api_key='',
        )
        assert client.vision_api_key == 'sk-conversation', (
            "同源时应继续继承对话 Key，实际="
            f"{client.vision_api_key!r}"
        )

    @pytest.mark.unit
    def test_unset_vision_endpoint_inherits_both(self):
        """No vision override at all keeps the original inheritance."""
        client = _make_offline_client()
        assert client.vision_base_url == 'https://api.deepseek.com/v1'
        assert client.vision_api_key == 'sk-conversation'

    @pytest.mark.unit
    def test_explicit_vision_key_is_used(self):
        """An explicitly configured vision key is used on its own endpoint."""
        client = _make_offline_client(
            vision_base_url='https://generativelanguage.googleapis.com/v1beta',
            vision_api_key='sk-vision-own',
        )
        assert client.vision_api_key == 'sk-vision-own'


# ---------------------------------------------------------------------------
# 5. Realtime wire dialect is keyed on the real api_type vocabulary
# ---------------------------------------------------------------------------
class TestRealtimeDialectNormalisation:

    @pytest.mark.unit
    def test_openai_maps_to_the_gpt_wire_branch(self):
        from main_logic.omni_realtime_client._shared import canonical_realtime_dialect
        assert canonical_realtime_dialect('openai') == 'gpt', (
            "CORE_API_TYPE 的真实取值是 'openai'，必须命中 gpt 分支"
        )

    @pytest.mark.unit
    def test_qwen_intl_maps_to_the_qwen_wire_branch(self):
        from main_logic.omni_realtime_client._shared import canonical_realtime_dialect
        assert canonical_realtime_dialect('qwen_intl') == 'qwen'

    @pytest.mark.unit
    def test_unknown_and_legacy_values_pass_through(self):
        from main_logic.omni_realtime_client._shared import canonical_realtime_dialect
        assert canonical_realtime_dialect('gpt') == 'gpt'
        assert canonical_realtime_dialect('glm') == 'glm'
        assert canonical_realtime_dialect('') == ''
        assert canonical_realtime_dialect(None) == ''

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_openai_session_receives_mid_session_tool_updates(self):
        """The call site, not just the predicate: 'openai' must push tools."""
        from tests.unit.test_tool_calling import _make_rt_client

        client, sent = _make_rt_client('openai')

        async def fake_update_session(payload, _sent=sent):
            _sent.append(payload)

        client.update_session = fake_update_session
        await client.apply_tools_to_session()

        assert sent, "OpenAI 会话的中途工具更新被静默丢弃了"
        assert 'tools' in sent[0], f"实际推送内容={sent[0]!r}"
        assert sent[0].get('tool_choice') == 'auto'
