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
    def test_key_book_secret_is_withheld_from_a_foreign_url(self, config_manager):
        """A provider's key may only be paired with that provider's endpoint.

        Configs that went through the settings page always agree (picking a
        provider overwrites the slot URL with its own, read-only). Imported or
        hand-edited configs can pair a provider with another vendor's URL —
        resolving the key book there sends one vendor's credential to another.
        """
        _write_core_config(config_manager, _base_config(
            conversationModelProvider='deepseek',
            conversationModelId='deepseek-v4-flash',
            # 另一家的端点（存量 / 导入 / 手改）
            conversationModelUrl='https://api.openai.com/v1',
            assistApiKeyDeepseek='DEEPSEEK-REAL-KEY',
        ))
        conv = config_manager.get_model_api_config('conversation')
        assert conv['api_key'] != 'DEEPSEEK-REAL-KEY', (
            "端点不属于该服务商时不得交出它的管理簿 Key，"
            f"base_url={conv['base_url']!r} api_key={conv['api_key']!r}"
        )

    @pytest.mark.unit
    def test_key_book_secret_is_used_for_the_providers_own_url(self, config_manager):
        """The guard must not withhold the key on a legitimate pairing."""
        _write_core_config(config_manager, _base_config(
            conversationModelProvider='deepseek',
            conversationModelId='deepseek-v4-flash',
            conversationModelUrl='https://api.deepseek.com/v1/',  # 尾斜杠写法差异
            assistApiKeyDeepseek='DEEPSEEK-REAL-KEY',
        ))
        conv = config_manager.get_model_api_config('conversation')
        assert conv['api_key'] == 'DEEPSEEK-REAL-KEY', (
            "同家端点必须照常拿到管理簿 Key（尾斜杠不算换了一家），"
            f"实际={conv['api_key']!r}"
        )

    @pytest.mark.unit
    def test_path_case_makes_the_url_foreign(self, config_manager):
        """`/V1` is not `/v1` — HTTP paths are case-sensitive.

        The first version of this guard lowercased the whole URL, which let a
        case-swapped path pass as one of the provider's candidates.
        """
        _write_core_config(config_manager, _base_config(
            conversationModelProvider='deepseek',
            conversationModelId='deepseek-v4-flash',
            conversationModelUrl='https://api.deepseek.com/V1',
            assistApiKeyDeepseek='DEEPSEEK-REAL-KEY',
        ))
        conv = config_manager.get_model_api_config('conversation')
        assert conv['api_key'] != 'DEEPSEEK-REAL-KEY', (
            "path 大小写不同就是另一条路由，不该被当成该服务商的候选端点，"
            f"实际={conv['api_key']!r}"
        )

    @pytest.mark.unit
    def test_explicit_default_port_still_matches_the_provider(self, config_manager):
        """`:443` on https is the default port — still the provider's own URL.

        The first version compared raw strings, so writing the default port out
        rejected a legitimate endpoint and dropped the key.
        """
        _write_core_config(config_manager, _base_config(
            conversationModelProvider='deepseek',
            conversationModelId='deepseek-v4-flash',
            conversationModelUrl='https://api.deepseek.com:443/v1',
            assistApiKeyDeepseek='DEEPSEEK-REAL-KEY',
        ))
        conv = config_manager.get_model_api_config('conversation')
        assert conv['api_key'] == 'DEEPSEEK-REAL-KEY', (
            "写出默认端口仍是同一个端点，不该因此拒发管理簿 Key，"
            f"实际={conv['api_key']!r}"
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
    def test_non_string_key_book_value_does_not_crash_config_read(self, config_manager):
        """core_config.json is hand-editable; a non-string key must not kill startup.

        get_core_config() runs on every config read, so an AttributeError here
        is a crash-on-launch, not a degraded slot.
        """
        _write_core_config(config_manager, _base_config(
            conversationModelProvider='deepseek',
            conversationModelId='deepseek-v4-flash',
            conversationModelUrl='https://api.deepseek.com/v1',
            assistApiKeyDeepseek=12345,
        ))
        cfg = config_manager.get_core_config()
        assert cfg['CONVERSATION_MODEL_API_KEY'] == '12345', (
            "非字符串管理簿值应被安全转成字符串而不是抛异常，实际="
            f"{cfg['CONVERSATION_MODEL_API_KEY']!r}"
        )

    @pytest.mark.unit
    def test_core_key_fallback_does_not_override_a_stored_slot_key(self, config_manager):
        """ASSIST_API_KEY_* is derived from CORE_API_KEY for the active provider.

        That derivation is a default, not a key-book entry, so it must not
        outrank a key a legacy config stored on the slot itself.
        """
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'coreApiKey': 'sk-core-key',
            'enableCustomApi': True,
            'summaryModelProvider': 'qwen',
            'summaryModelId': 'qwen3.7-plus',
            'summaryModelUrl': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
            'summaryModelApiKey': 'sk-slot-specific',
            # 管理簿里没有 qwen 这一条 —— ASSIST_API_KEY_QWEN 只会是 _fb() 派生值
        })
        cfg = config_manager.get_core_config()
        assert cfg['SUMMARY_MODEL_API_KEY'] == 'sk-slot-specific', (
            "派生的核心 Key 不该压过槽位存量 Key，实际="
            f"{cfg['SUMMARY_MODEL_API_KEY']!r}"
        )

    @pytest.mark.unit
    def test_explicit_key_book_entry_outranks_the_slot(self, config_manager):
        """An actually-stored key-book entry is still the truth."""
        _write_core_config(config_manager, {
            'coreApi': 'qwen',
            'assistApi': 'qwen',
            'coreApiKey': 'sk-core-key',
            'enableCustomApi': True,
            'summaryModelProvider': 'qwen',
            'summaryModelId': 'qwen3.7-plus',
            'summaryModelUrl': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
            'summaryModelApiKey': 'sk-stale-slot',
            'assistApiKeyQwen': 'sk-book-current',
        })
        cfg = config_manager.get_core_config()
        assert cfg['SUMMARY_MODEL_API_KEY'] == 'sk-book-current', (
            "管理簿里真存的那条必须优先，实际="
            f"{cfg['SUMMARY_MODEL_API_KEY']!r}"
        )

    @pytest.mark.unit
    def test_non_string_slot_key_is_normalised(self, config_manager):
        """A hand-edited non-string slot key must not reach `.strip()` callers.

        brain/openfang_adapter.py strips the agent key straight from
        get_model_api_config(), so a raw non-string here is an AttributeError
        during OpenFang sync.
        """
        _write_core_config(config_manager, _base_config(
            agentModelProvider='custom',
            agentModelId='my-agent',
            agentModelUrl='https://llm.example.com/v1',
            agentModelApiKey=98765,
        ))
        cfg = config_manager.get_core_config()
        assert isinstance(cfg['AGENT_MODEL_API_KEY'], str), (
            "槽位 Key 必须归一化成字符串，实际类型="
            f"{type(cfg['AGENT_MODEL_API_KEY']).__name__}"
        )
        assert cfg['AGENT_MODEL_API_KEY'] == '98765'

    @pytest.mark.unit
    def test_doubao_tts_keeps_the_slot_as_its_credential_truth(self, config_manager):
        """Doubao speech reads its key from the slot, not from the key book.

        voice_storage.get_tts_api_key('doubao_tts') is slot-first with the book
        as fallback, and the save path deliberately preserves a legacy key that
        only exists on the slot. Redirecting this provider to the book would
        give one credential two opposite resolution orders.
        """
        _write_core_config(config_manager, _base_config(
            ttsModelProvider='doubao_tts',
            ttsModelId='doubao-tts',
            ttsModelUrl='https://openspeech.bytedance.com/api/v3/tts',
            ttsModelApiKey='ark-slot-key',
            assistApiKeyDoubaoTts='ark-book-key',
        ))
        cfg = config_manager.get_core_config()
        assert cfg['TTS_MODEL_API_KEY'] == 'ark-slot-key', (
            "豆包语音的凭证真相是槽位字段，改道管理簿会与 get_tts_api_key 反向，"
            f"实际={cfg['TTS_MODEL_API_KEY']!r}"
        )

    @pytest.mark.unit
    @pytest.mark.parametrize('provider', ['minimax', 'minimax_intl', 'elevenlabs', 'deepseek'])
    def test_tts_slot_never_resolves_a_key_from_the_key_book(self, config_manager, provider):
        """The TTS slot must not pull a vendor key out of the API key book.

        Several entries reachable from the TTS dropdown are selected by voice
        metadata, not by ttsModelProvider, so dispatch keeps using the core
        provider's worker. Resolving the picked vendor's real key into
        TTS_MODEL_API_KEY therefore handed one vendor's credential to another's
        TTS endpoint. TTS credentials live on the slot field and each
        provider's own resolution path; this pins that boundary.
        """
        _write_core_config(config_manager, {
            'coreApi': 'step',
            'assistApi': 'step',
            'coreApiKey': 'sk-step-core',
            'assistApiKeyStep': 'sk-step-core',
            'assistApiKeyMinimax': 'MINIMAX-REAL-KEY',
            'assistApiKeyMinimaxIntl': 'MINIMAX-INTL-REAL-KEY',
            'assistApiKeyElevenlabs': 'ELEVEN-REAL-KEY',
            'assistApiKeyDeepseek': 'DEEPSEEK-REAL-KEY',
            'enableCustomApi': True,
            'ttsModelProvider': provider,
            'ttsModelId': 'some-tts-model',
            'ttsModelUrl': 'https://api.minimaxi.com/v1',
        })
        cfg = config_manager.get_core_config()
        resolved = cfg['TTS_MODEL_API_KEY']
        for leaked in ('MINIMAX-REAL-KEY', 'MINIMAX-INTL-REAL-KEY',
                       'ELEVEN-REAL-KEY', 'DEEPSEEK-REAL-KEY'):
            assert resolved != leaked, (
                f"TTS 槽把 {provider} 的管理簿 Key 解析了出来，它会被送给核心厂商的 "
                f"TTS 端点，实际={resolved!r}"
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
    def test_legacy_hand_typed_endpoint_stays_local(self, config_manager):
        """Configs predating omniModelProvider keep their previous behaviour.

        Those users typed a realtime endpoint by hand and there is no provider
        key to read; narrowing 'local' must not change what they get.
        """
        _write_core_config(config_manager, _base_config(
            omniModelId='my-legacy-realtime',
            omniModelUrl='ws://192.168.1.50:8000/v1/realtime',
        ))
        rt = config_manager.get_model_api_config('realtime')
        assert rt['api_type'] == 'local', (
            "手填过实时端点的老配置行为必须与改动前一致，"
            f"实际={rt['api_type']!r}"
        )
        assert rt['base_url'] == 'ws://192.168.1.50:8000/v1/realtime'

    @pytest.mark.unit
    def test_no_saved_omni_url_is_not_treated_as_custom(self, config_manager):
        """A never-configured omni slot is not a hand-typed endpoint."""
        _write_core_config(config_manager, _base_config(coreApi='glm', assistApi='glm'))
        rt = config_manager.get_model_api_config('realtime')
        assert rt['api_type'] == 'glm', (
            "没存过 omni URL 的配置不该被当成自配端点，"
            f"实际={rt['api_type']!r}"
        )

    @pytest.mark.unit
    def test_named_realtime_provider_never_diverges_from_core(self, config_manager):
        """A per-slot realtime provider must not split the audio stack.

        api_type becomes the process-wide core api type, which also selects the
        TTS worker and the audio credential. Honouring a divergent pick gave a
        Qwen TTS worker holding the OpenAI core key: silent, and one vendor's
        credential sent to another. One identity only.
        """
        _write_core_config(config_manager, _base_config(
            coreApi='openai',
            assistApi='openai',
            assistApiKeyQwen='sk-qwen-book',
            omniModelProvider='qwen',
            omniModelId='qwen3.5-omni-flash-realtime',
            omniModelUrl='wss://dashscope.aliyuncs.com/api-ws/v1/realtime',
        ))
        rt = config_manager.get_model_api_config('realtime')
        tts = config_manager.get_model_api_config('tts_default')
        cfg = config_manager.get_core_config()
        assert rt['api_type'] == 'openai', (
            "跨厂商的实时槽覆盖必须被忽略并回落核心 API，"
            f"实际={rt['api_type']!r}"
        )
        assert rt['api_type'] != 'local', "也不能落进未实现的 'local'"
        # 方言回落还不够：端点和凭证必须**一起**回落。只改方言的话会得到
        # 「用核心的方言去说另一家的端点」——同样是分裂，只是换了个方向。
        assert rt['base_url'] == cfg['CORE_URL'], (
            "实时端点必须跟着方言一起回落核心，"
            f"实际={rt['base_url']!r}"
        )
        assert 'dashscope' not in (rt['base_url'] or ''), (
            f"被忽略的选择不得把自己的端点留下，实际={rt['base_url']!r}"
        )
        assert rt['api_key'] == cfg['CORE_API_KEY'], (
            "实时凭证同样必须是核心那家的，"
            f"实际={rt['api_key']!r}"
        )
        # 派发身份与凭证必须同源：worker 按 api_type 选、凭证走 tts_default
        assert 'dashscope' not in (tts['base_url'] or ''), (
            f"TTS 端点不该被实时槽的选择带偏，实际={tts['base_url']!r}"
        )
        assert tts['api_key'] == rt['api_key'], (
            "实时与 TTS 的凭证必须来自同一家，"
            f"realtime={rt['api_key']!r} tts={tts['api_key']!r}"
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
    def test_core_provider_pick_is_ignored_too(self, config_manager):
        """Even a real core provider is ignored in this slot.

        Being a valid core API says nothing about whether this slot can express
        it: the whole audio stack carries one provider identity.
        """
        # 带上一条残留 URL（换 provider 时留下的）。没有它，URL 本来就是空的、
        # 自定义分支根本走不到，改动与否结果都一样 —— 这条残留正是唯一的分界。
        _write_core_config(config_manager, _base_config(
            coreApi='qwen',
            omniModelProvider='gemini',
            omniModelId='gemini-live',
            omniModelUrl='wss://open.bigmodel.cn/api/paas/v4/realtime',
        ))
        rt = config_manager.get_model_api_config('realtime')
        assert rt['api_type'] == 'qwen', (
            "没有 realtime 端点的核心服务商应被忽略并回落核心 API，"
            f"实际={rt['api_type']!r}"
        )
        assert 'bigmodel' not in (rt['base_url'] or ''), (
            "被忽略的选择不得把残留 URL 留在实时槽里，"
            f"实际 base_url={rt['base_url']!r}"
        )

    @pytest.mark.unit
    def test_follow_assist_on_omni_resolves_against_core(self, config_manager):
        """The omni slot follows the core API even when set to follow_assist.

        The realtime session, its TTS worker and its credential all belong to
        the core provider. Deriving this slot's key from the assist provider
        left an assist credential paired with the core endpoint in the
        snapshot — currently unreachable (REALTIME_MODEL_URL stays empty under
        follow_*, so the custom triple never completes), but one change away
        from going live.
        """
        _write_core_config(config_manager, {
            'coreApi': 'openai',
            'assistApi': 'qwen',
            'coreApiKey': 'sk-openai-core',
            'assistApiKeyOpenai': 'sk-openai-core',
            'assistApiKeyQwen': 'sk-qwen-assist',
            'enableCustomApi': True,
            'omniModelProvider': 'follow_assist',
        })
        cfg = config_manager.get_core_config()
        assert cfg['REALTIME_MODEL_API_KEY'] != 'sk-qwen-assist', (
            "实时槽的 Key 不该来自辅助 API —— 那会与核心端点配成跨厂商组合，"
            f"实际={cfg['REALTIME_MODEL_API_KEY']!r}"
        )
        assert cfg['REALTIME_MODEL_API_KEY'] == cfg['CORE_API_KEY'], (
            f"应与核心同源，实际={cfg['REALTIME_MODEL_API_KEY']!r}"
        )
        rt = config_manager.get_model_api_config('realtime')
        assert rt['api_type'] == 'openai'
        assert rt['api_key'] == cfg['CORE_API_KEY']
        assert rt['base_url'] == cfg['CORE_URL']

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
    def test_agent_slot_drops_residual_dialect_when_custom_api_off(self, config_manager):
        """The agent slot bypasses the custom-API gate and needs the same rule.

        `if treat_as_custom or model_type == 'agent'` lets agent into the
        custom branch even with custom API disabled, so the generic fallback
        fix does not cover it: URL and key come from assist while the residual
        dropdown still claimed anthropic.
        """
        _write_core_config(config_manager, _base_config(
            enableCustomApi=False,
            agentModelProvider='claude',
            agentModelId='claude-sonnet-4',
            agentModelUrl='https://api.anthropic.com/v1',
        ))
        agent = config_manager.get_model_api_config('agent')
        assert agent['provider_type'] == 'openai_compatible', (
            "关掉自定义 API 后 agent 槽的地址已回落 assist，协议不能停在 anthropic，"
            f"实际={agent['provider_type']!r}"
        )

    @pytest.mark.unit
    def test_agent_slot_keeps_dialect_when_custom_api_on(self, config_manager):
        """With custom API on, an explicit agent dialect is still honoured."""
        _write_core_config(config_manager, _base_config(
            agentModelProvider='claude',
            agentModelId='claude-sonnet-4',
            agentModelUrl='https://api.anthropic.com/v1',
            assistApiKeyClaude='sk-ant-book',
        ))
        agent = config_manager.get_model_api_config('agent')
        assert agent['provider_type'] == 'anthropic', (
            "自定义 API 开着时显式选择必须生效，"
            f"实际={agent['provider_type']!r}"
        )

    @pytest.mark.unit
    def test_incomplete_named_slot_drops_the_dialect_even_with_custom_api_on(self, config_manager):
        """A back-filled URL must not keep the dropdown's protocol.

        Gating on ENABLE_CUSTOM_API is not enough: with it on but the slot left
        incomplete, upstream fills AGENT_MODEL_URL from the assist endpoint
        (it has a VISION → OPENROUTER fallback chain), the custom branch then
        accepts the triple as complete, and the residual dropdown value still
        claimed anthropic — an assist address wearing another vendor's protocol.
        """
        _write_core_config(config_manager, _base_config(
            agentModelProvider='claude',
            agentModelId='claude-sonnet-4',
            agentModelUrl='',  # 没填完 → 上游回填成 assist 地址
            assistApiKeyClaude='sk-ant-book',
        ))
        cfg = config_manager.get_core_config()
        agent = config_manager.get_model_api_config('agent')
        assert agent['base_url'] == cfg['OPENROUTER_URL'], (
            f"前提：URL 被回填成了 assist 地址，实际={agent['base_url']!r}"
        )
        assert agent['provider_type'] == 'openai_compatible', (
            "地址来自 assist 时协议必须跟着 assist，"
            f"实际={agent['provider_type']!r}"
        )

    @pytest.mark.unit
    def test_complete_named_slot_keeps_its_dialect(self, config_manager):
        """The guard must not strip the dialect from a genuinely complete slot."""
        _write_core_config(config_manager, _base_config(
            agentModelProvider='claude',
            agentModelId='claude-sonnet-4',
            agentModelUrl='https://api.anthropic.com/v1',
            assistApiKeyClaude='sk-ant-book',
        ))
        agent = config_manager.get_model_api_config('agent')
        assert agent['provider_type'] == 'anthropic', (
            "填了该服务商自己的地址时协议必须生效，"
            f"实际={agent['provider_type']!r}"
        )
        assert agent['api_key'] == 'sk-ant-book'

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
    def test_cosmetic_url_difference_is_still_same_origin(self):
        """A trailing slash must not read as 'a different provider'."""
        client = _make_offline_client(
            vision_base_url='https://api.deepseek.com/v1/',
            vision_api_key='',
        )
        assert client.vision_api_key == 'sk-conversation', (
            "尾斜杠差异不该被当成换了一家而掐掉继承，实际="
            f"{client.vision_api_key!r}"
        )

    @pytest.mark.unit
    def test_path_case_is_not_folded_away(self):
        """Host case is insignificant; path case is not.

        Two tenants or routes can differ by path case alone. Folding the whole
        URL would call them the same endpoint and hand one provider's
        credential to the other.
        """
        # 两个 path **只**差大小写：这才是「折叠整条 URL」与「只折叠 scheme/host」
        # 的分界。差别更大的话两种写法都会判成不同，测不出东西。
        client = _make_offline_client(
            base_url='https://api.example.com/v1/TenantA',
            vision_base_url='https://api.example.com/v1/tenanta',
            vision_api_key='',
        )
        assert client.vision_api_key is None, (
            "path 大小写不同就是不同路由，不得继承凭证，实际="
            f"{client.vision_api_key!r}"
        )

    @pytest.mark.unit
    def test_userinfo_case_is_not_folded_away(self):
        """netloc carries userinfo, and usernames/passwords are case-sensitive."""
        client = _make_offline_client(
            base_url='https://User:PASS@api.example.com/v1',
            vision_base_url='https://user:pass@api.example.com/v1',
            vision_api_key='',
        )
        assert client.vision_api_key is None, (
            "userinfo 大小写不同就是不同凭据主体，不得继承，实际="
            f"{client.vision_api_key!r}"
        )

    @pytest.mark.unit
    def test_same_endpoint_is_total_on_malformed_urls(self):
        """The predicate must never raise — it runs inside __init__.

        urlsplit defers malformed-port errors to the moment ``.port`` is read,
        so reading it here would turn a broken URL into a ValueError during
        construction. (httpx rejects such a URL a moment later anyway, so this
        is about keeping the predicate total, not about rescuing the config.)
        """
        from main_logic.omni_offline_client._client import _same_endpoint
        for a, b in (
            ('http://api.example.com:not-a-port/v1', 'http://api.example.com:not-a-port/v1'),
            ('http://api.example.com:99999999/v1', 'http://api.example.com:1/v1'),
            ('http://[::1]:abc/v1', 'http://[::1]:abc/v1'),
            ('::::', 'http://api.example.com/v1'),
            # urlsplit 自己就会对未闭合的 IPv6 抛 ValueError
            ('http://[::1', 'http://api.example.com/v1'),
            ('http://[::1/v1', 'http://[::1/v1'),
        ):
            _same_endpoint(a, b)  # 不抛就是通过

        assert _same_endpoint('http://[::1/v1', 'http://[::1/v1') is True, (
            "解析不了时按原串比，两个相同的坏 URL 仍是同源"
        )
        assert _same_endpoint('http://[::1/v1', 'http://[::2/v1') is False, (
            "解析不了时两个不同的坏 URL 仍是不同源"
        )

        assert _same_endpoint(
            'http://api.example.com:not-a-port/v1',
            'http://API.example.com:not-a-port/v1',
        ) is True, "host 大小写仍应折叠"
        assert _same_endpoint(
            'http://api.example.com:99999999/v1',
            'http://api.example.com:1/v1',
        ) is False, "端口串不同就是不同端点"

    @pytest.mark.unit
    def test_explicit_default_port_is_same_endpoint(self):
        """https://h/v1 and https://h:443/v1 are the same endpoint."""
        client = _make_offline_client(
            base_url='https://api.example.com/v1',
            vision_base_url='https://api.example.com:443/v1',
            vision_api_key='',
        )
        assert client.vision_api_key == 'sk-conversation', (
            "写出默认端口不该被当成换了一家，实际="
            f"{client.vision_api_key!r}"
        )

    @pytest.mark.unit
    def test_only_one_trailing_slash_is_cosmetic(self):
        """`/v1/` vs `/v1` is a writing difference; `/v1/` vs `/v1//` is not."""
        from main_logic.omni_offline_client._client import _same_endpoint
        assert _same_endpoint(
            'https://api.example.com/v1', 'https://api.example.com/v1/'
        ) is True, "单个尾斜杠是写法差异"
        assert _same_endpoint(
            'https://api.example.com/v1/', 'https://api.example.com/v1//'
        ) is False, "重复斜杠是两条不同的 HTTP 路径，不得折平"

    @pytest.mark.unit
    def test_zero_padded_ports_compare_by_value(self):
        """:0443 is port 443, and :08443 is port 8443 — compare numerically."""
        from main_logic.omni_offline_client._client import _same_endpoint
        assert _same_endpoint(
            'https://api.example.com/v1', 'https://api.example.com:0443/v1'
        ) is True, "补零的默认端口仍是默认端口"
        assert _same_endpoint(
            'http://api.example.com/v1', 'http://api.example.com:80/v1'
        ) is True, "http 的默认端口是 80，不是 443"
        assert _same_endpoint(
            'http://api.example.com/v1', 'http://api.example.com:00080/v1'
        ) is True, "补零的 http 默认端口同理"
        assert _same_endpoint(
            'http://api.example.com/v1', 'http://api.example.com:443/v1'
        ) is False, "443 不是 http 的默认端口，不该被抹掉"
        assert _same_endpoint(
            'https://api.example.com:8443/v1', 'https://api.example.com:08443/v1'
        ) is True, "非默认端口的前导零也不该造成分歧"
        assert _same_endpoint(
            'https://api.example.com:8443/v1', 'https://api.example.com:9443/v1'
        ) is False, "真正不同的端口仍是不同端点"

    @pytest.mark.unit
    def test_non_default_port_is_a_different_endpoint(self):
        """A real port difference still separates the credentials."""
        client = _make_offline_client(
            base_url='https://api.example.com/v1',
            vision_base_url='https://api.example.com:8443/v1',
            vision_api_key='',
        )
        assert client.vision_api_key is None, (
            "非默认端口是不同端点，实际="
            f"{client.vision_api_key!r}"
        )

    @pytest.mark.unit
    def test_missing_credentials_are_normalised_to_none(self):
        """Absent credentials use one representation, not '' on one side."""
        client = _make_offline_client(api_key='', vision_api_key='')
        assert client.api_key is None
        assert client.vision_api_key is None, (
            "无凭证应统一表示成 None，实际="
            f"{client.vision_api_key!r}"
        )

    @pytest.mark.unit
    def test_host_case_is_still_folded(self):
        """Scheme and host are case-insensitive per RFC 3986."""
        client = _make_offline_client(
            base_url='https://API.Example.com/v1',
            vision_base_url='https://api.example.com/v1',
            vision_api_key='',
        )
        assert client.vision_api_key == 'sk-conversation', (
            "host 大小写差异不该被当成换了一家，实际="
            f"{client.vision_api_key!r}"
        )

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
