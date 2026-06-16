from main_logic.core import LLMSessionManager


def test_resolve_tts_api_key_blocks_fallback_for_vllm_omni():
    tts_config = {"api_key": "default-tts-key"}

    assert LLMSessionManager.resolve_tts_api_key("vllm_omni", None, tts_config) == ""
    assert LLMSessionManager.resolve_tts_api_key("vllm_omni", "", tts_config) == ""
    assert (
        LLMSessionManager.resolve_tts_api_key("vllm_omni", "vllm-key", tts_config)
        == "vllm-key"
    )


def test_resolve_tts_api_key_uses_default_fallback_for_other_providers():
    tts_config = {"api_key": "default-tts-key"}

    assert (
        LLMSessionManager.resolve_tts_api_key("openai", None, tts_config)
        == "default-tts-key"
    )
    assert (
        LLMSessionManager.resolve_tts_api_key("openai", "override-key", tts_config)
        == "override-key"
    )
    assert LLMSessionManager.resolve_tts_api_key("openai", None, {}) == ""
