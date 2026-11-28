"""Regression tests for follow_* custom-model URL derivation."""

import json
import shutil
import uuid
from pathlib import Path

from utils.config_manager import ConfigManager


def _make_workspace_temp_dir() -> Path:
    root = Path(__file__).resolve().parents[2] / ".test-tmp"
    root.mkdir(exist_ok=True)
    path = root / f"config-manager-{uuid.uuid4().hex}"
    path.mkdir()
    return path


def _manager_with_core_config(config_dir: Path, data: dict) -> ConfigManager:
    cm = ConfigManager.__new__(ConfigManager)
    cm.config_dir = config_dir
    cm.project_config_dir = config_dir
    cm.app_name = "N.E.K.O"
    cm._verbose = False
    cm._core_config_cache = None
    (config_dir / "core_config.json").write_text(json.dumps(data), encoding="utf-8")
    return cm


def test_follow_assist_uses_resolved_provider_url_instead_of_stale_saved_url():
    """follow_assist must use the saved, available regional URL instead of the old URL populated by historical synchronization."""
    us_url = "https://dashscope-us.aliyuncs.com/compatible-mode/v1"
    config_dir = _make_workspace_temp_dir()
    try:
        cm = _manager_with_core_config(config_dir, {
            "coreApiKey": "sk-core",
            "coreApi": "qwen_intl",
            "assistApi": "qwen_intl",
            "assistApiKeyQwenIntl": "sk-intl",
            "enableCustomApi": True,
            "resolvedProviderUrls": {
                "assist:qwen_intl": us_url,
            },
            "conversationModelProvider": "follow_assist",
            "conversationModelUrl": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            "conversationModelId": "qwen3.6-plus",
            "conversationModelApiKey": "sk-intl",
            "recognitionModelProvider": "follow_assist",
            "recognitionModelUrl": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            "recognitionModelId": "qwen3.6-flash",
            "recognitionModelApiKey": "sk-intl",
        })

        assert cm.get_model_api_config("conversation")["base_url"] == us_url
        assert cm.get_model_api_config("recognition")["base_url"] == us_url
    finally:
        shutil.rmtree(config_dir, ignore_errors=True)


def test_follow_assist_uses_saved_model_id_for_text_tiers():
    """follow_assist keeps saved text model IDs while following URL and key."""
    config_dir = _make_workspace_temp_dir()
    try:
        cm = _manager_with_core_config(config_dir, {
            "coreApiKey": "free-access",
            "coreApi": "free",
            "assistApi": "deepseek",
            "assistApiKeyDeepseek": "sk-deepseek",
            "enableCustomApi": True,
            "conversationModelProvider": "follow_assist",
            "conversationModelUrl": "https://api.deepseek.com/v1",
            "conversationModelId": "deepseek-v4-flash",
            "conversationModelApiKey": "sk-deepseek",
            "memoryModelProvider": "follow_assist",
            "memoryModelUrl": "https://api.deepseek.com/v1",
            "memoryModelId": "summary-override-model",
            "memoryModelApiKey": "sk-deepseek",
            "agentModelProvider": "follow_assist",
            "agentModelUrl": "https://api.deepseek.com/v1",
            "agentModelId": "deepseek-v4-flash",
            "agentModelApiKey": "sk-deepseek",
        })

        assert cm.get_model_api_config("conversation")["model"] == "deepseek-v4-flash"
        assert cm.get_model_api_config("memory")["model"] == "summary-override-model"
        assert cm.get_model_api_config("agent")["model"] == "deepseek-v4-flash"
    finally:
        shutil.rmtree(config_dir, ignore_errors=True)


def test_free_follow_assist_ignores_stale_saved_model_id_for_text_tiers():
    """Free assist has fixed model names and must not reuse stale paid provider IDs."""
    config_dir = _make_workspace_temp_dir()
    try:
        cm = _manager_with_core_config(config_dir, {
            "coreApiKey": "free-access",
            "coreApi": "free",
            "assistApi": "free",
            "assistApiKey": "free-access",
            "enableCustomApi": True,
            "conversationModelProvider": "follow_assist",
            "conversationModelUrl": "https://www.lanlan.tech/text/v1",
            "conversationModelId": "deepseek-v4-pro",
            "conversationModelApiKey": "free-access",
            "recognitionModelProvider": "follow_assist",
            "recognitionModelUrl": "https://www.lanlan.tech/text/v1",
            "recognitionModelId": "deepseek-v4-pro",
            "recognitionModelApiKey": "free-access",
            "agentModelProvider": "follow_assist",
            "agentModelUrl": "https://www.lanlan.tech/text/v1",
            "agentModelId": "deepseek-v4-pro",
            "agentModelApiKey": "free-access",
        })

        assert cm.get_model_api_config("conversation")["model"] == "free-model"
        assert cm.get_model_api_config("recognition")["model"] == "free-mini-model"
        assert cm.get_model_api_config("agent")["model"] == "free-agent-model"
    finally:
        shutil.rmtree(config_dir, ignore_errors=True)


def test_follow_core_uses_saved_model_id_for_text_tiers():
    """follow_core keeps saved text model IDs while following URL and key."""
    config_dir = _make_workspace_temp_dir()
    try:
        cm = _manager_with_core_config(config_dir, {
            "coreApi": "deepseek",
            "coreApiKeyDeepseek": "sk-deepseek",
            "assistApi": "free",
            "assistApiKey": "free-access",
            "enableCustomApi": True,
            "conversationModelProvider": "follow_core",
            "conversationModelUrl": "https://api.deepseek.com/v1",
            "conversationModelId": "deepseek-v4-flash",
            "conversationModelApiKey": "sk-deepseek",
            "memoryModelProvider": "follow_core",
            "memoryModelUrl": "https://api.deepseek.com/v1",
            "memoryModelId": "summary-override-model",
            "memoryModelApiKey": "sk-deepseek",
            "agentModelProvider": "follow_core",
            "agentModelUrl": "https://api.deepseek.com/v1",
            "agentModelId": "deepseek-v4-flash",
            "agentModelApiKey": "sk-deepseek",
        })

        assert cm.get_model_api_config("conversation")["model"] == "deepseek-v4-flash"
        assert cm.get_model_api_config("memory")["model"] == "summary-override-model"
        assert cm.get_model_api_config("agent")["model"] == "deepseek-v4-flash"
    finally:
        shutil.rmtree(config_dir, ignore_errors=True)


def test_follow_core_non_omni_still_uses_core_provider_http_url():
    """Non-omni follow_core must still resolve to the core provider's HTTP-compatible address."""
    config_dir = _make_workspace_temp_dir()
    try:
        cm = _manager_with_core_config(config_dir, {
            "coreApiKey": "sk-openai-core",
            "coreApi": "openai",
            "assistApi": "qwen",
            "assistApiKeyQwen": "sk-qwen-assist",
            "enableCustomApi": True,
            "conversationModelProvider": "follow_core",
            "conversationModelUrl": "https://stale.example.test/v1",
            "conversationModelId": "gpt-5-chat-latest",
            "conversationModelApiKey": "sk-openai-core",
        })

        assert cm.get_core_config()["CONVERSATION_MODEL_URL"] == "https://api.openai.com/v1"
    finally:
        shutil.rmtree(config_dir, ignore_errors=True)


_INTL_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
_US_URL = "https://dashscope-us.aliyuncs.com/compatible-mode/v1"


def _named_qwen_intl_slots(slot_url: str, resolved: dict | None) -> dict:
    data = {
        "coreApi": "openai",
        "coreApiKey": "sk-openai-core",
        "assistApi": "openai",
        "assistApiKeyOpenai": "sk-openai-assist",
        "assistApiKeyQwenIntl": "sk-intl",
        "enableCustomApi": True,
    }
    if resolved is not None:
        data["resolvedProviderUrls"] = resolved
    for prefix in ("conversation", "memory", "recognition", "agent"):
        data.update({
            f"{prefix}ModelProvider": "qwen_intl",
            f"{prefix}ModelUrl": slot_url,
            f"{prefix}ModelId": "",
            f"{prefix}ModelApiKey": "",
        })
    return data


def test_named_qwen_intl_slots_follow_region_pinned_at_save():
    """Named slots saved with the intl URL move to the US candidate the save-time probe pinned."""
    config_dir = _make_workspace_temp_dir()
    try:
        cm = _manager_with_core_config(
            config_dir, _named_qwen_intl_slots(_INTL_URL, {"assist:qwen_intl": _US_URL})
        )

        for tier in ("conversation", "memory", "recognition", "agent"):
            api = cm.get_model_api_config(tier)
            assert api["base_url"] == _US_URL, tier
            # Still this provider's endpoint, so the Key Book key and tier default keep applying.
            assert api["api_key"] == "sk-intl", tier
            assert api["model"], tier
    finally:
        shutil.rmtree(config_dir, ignore_errors=True)


def test_named_qwen_intl_slots_keep_saved_url_without_pin():
    """No pin (never probed, or the last probe failed): the saved slot URL stands."""
    config_dir = _make_workspace_temp_dir()
    try:
        cm = _manager_with_core_config(config_dir, _named_qwen_intl_slots(_INTL_URL, {}))

        assert cm.get_model_api_config("conversation")["base_url"] == _INTL_URL
        assert cm.get_core_config()["RECOGNITION_MODEL_URL"] == _INTL_URL
    finally:
        shutil.rmtree(config_dir, ignore_errors=True)


def test_named_slot_ignores_pin_outside_its_candidates():
    """A stale pin that is no longer one of the provider's candidates is ignored."""
    config_dir = _make_workspace_temp_dir()
    try:
        cm = _manager_with_core_config(
            config_dir,
            _named_qwen_intl_slots(_INTL_URL, {"assist:qwen_intl": "https://old-region.example.test/v1"}),
        )

        assert cm.get_core_config()["CONVERSATION_MODEL_URL"] == _INTL_URL
    finally:
        shutil.rmtree(config_dir, ignore_errors=True)


def test_named_slot_with_foreign_url_is_not_moved_by_pin():
    """A slot URL outside the provider's candidates (legacy/hand-edited) is never rewritten."""
    foreign = "https://gateway.example.test/v1"
    config_dir = _make_workspace_temp_dir()
    try:
        cm = _manager_with_core_config(
            config_dir, _named_qwen_intl_slots(foreign, {"assist:qwen_intl": _US_URL})
        )

        assert cm.get_core_config()["CONVERSATION_MODEL_URL"] == foreign
    finally:
        shutil.rmtree(config_dir, ignore_errors=True)


def test_named_slot_of_single_url_provider_unchanged():
    """Providers with one URL have nothing to pin; their slot URL is untouched, even when a
    (legacy) pin holding that very candidate exists and the slot spells it differently."""
    config_dir = _make_workspace_temp_dir()
    try:
        data = _named_qwen_intl_slots(_INTL_URL, None)
        data.update({
            "assistApiKeyDeepseek": "sk-deepseek",
            "memoryModelProvider": "deepseek",
            # Same endpoint as the only candidate, spelled with a trailing slash.
            "memoryModelUrl": "https://api.deepseek.com/v1/",
            "resolvedProviderUrls": {"assist:deepseek": "https://api.deepseek.com/v1"},
        })
        cm = _manager_with_core_config(config_dir, data)

        assert cm.get_core_config()["MEMORY_MODEL_URL"] == "https://api.deepseek.com/v1/"
    finally:
        shutil.rmtree(config_dir, ignore_errors=True)


def test_named_qwen_intl_decision_slot_follows_us_pin_into_chat_fallback():
    """The decision slot follows the pin too, so it decides the way the page probe checked it:
    the US host has no workspace decision endpoint, hence the regional chat fallback."""
    config_dir = _make_workspace_temp_dir()
    try:
        data = _named_qwen_intl_slots(_INTL_URL, {"assist:qwen_intl": _US_URL})
        data.update({
            "assistWorkspaceIdQwenIntl": "llm-sg",
            "decisionModelProvider": "qwen_intl",
            "decisionModelUrl": _INTL_URL,
            "decisionModelId": "",
            "decisionModelApiKey": "",
        })
        cm = _manager_with_core_config(config_dir, data)

        dec = cm.get_model_api_config("decision")
        assert dec["base_url"] == _US_URL
        assert dec["decision_protocol"] == "chat_fallback"
        assert dec["native_unavailable_reason"] == "region_unsupported"
    finally:
        shutil.rmtree(config_dir, ignore_errors=True)


def test_tts_custom_non_active_qwen_intl_uses_saved_resolved_url():
    """Alibaba international TTS outside the current assist provider must also use the saved, available regional URL."""
    us_url = "https://dashscope-us.aliyuncs.com/compatible-mode/v1"
    config_dir = _make_workspace_temp_dir()
    try:
        cm = _manager_with_core_config(config_dir, {
            "coreApiKey": "sk-openai-core",
            "coreApi": "openai",
            "assistApi": "openai",
            "assistApiKeyOpenai": "sk-openai-assist",
            "assistApiKeyQwenIntl": "sk-intl",
            "resolvedProviderUrls": {
                "assist:qwen_intl": us_url,
            },
        })

        tts_config = cm.get_model_api_config("tts_custom")

        assert tts_config["api_key"] == "sk-intl"
        assert tts_config["base_url"] == us_url
    finally:
        shutil.rmtree(config_dir, ignore_errors=True)
