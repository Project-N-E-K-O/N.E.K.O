"""follow_* 自定义模型 URL 派生的回归测试。"""

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
    """follow_assist 必须吃保存过的可用地域 URL，而不是历史联动填入的旧 URL。"""
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
            "emotionModelProvider": "follow_assist",
            "emotionModelUrl": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            "emotionModelId": "qwen3.6-flash",
            "emotionModelApiKey": "sk-intl",
        })

        assert cm.get_model_api_config("conversation")["base_url"] == us_url
        assert cm.get_model_api_config("emotion")["base_url"] == us_url
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
            "correctionModelProvider": "follow_assist",
            "correctionModelUrl": "https://api.deepseek.com/v1",
            "correctionModelId": "deepseek-v4-flash",
            "correctionModelApiKey": "sk-deepseek",
            "agentModelProvider": "follow_assist",
            "agentModelUrl": "https://api.deepseek.com/v1",
            "agentModelId": "deepseek-v4-flash",
            "agentModelApiKey": "sk-deepseek",
        })

        assert cm.get_model_api_config("conversation")["model"] == "deepseek-v4-flash"
        assert cm.get_model_api_config("correction")["model"] == "deepseek-v4-flash"
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
            "emotionModelProvider": "follow_assist",
            "emotionModelUrl": "https://www.lanlan.tech/text/v1",
            "emotionModelId": "deepseek-v4-pro",
            "emotionModelApiKey": "free-access",
            "agentModelProvider": "follow_assist",
            "agentModelUrl": "https://www.lanlan.tech/text/v1",
            "agentModelId": "deepseek-v4-pro",
            "agentModelApiKey": "free-access",
        })

        assert cm.get_model_api_config("conversation")["model"] == "free-model"
        assert cm.get_model_api_config("emotion")["model"] == "free-mini-model"
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
            "correctionModelProvider": "follow_core",
            "correctionModelUrl": "https://api.deepseek.com/v1",
            "correctionModelId": "deepseek-v4-flash",
            "correctionModelApiKey": "sk-deepseek",
            "agentModelProvider": "follow_core",
            "agentModelUrl": "https://api.deepseek.com/v1",
            "agentModelId": "deepseek-v4-flash",
            "agentModelApiKey": "sk-deepseek",
        })

        assert cm.get_model_api_config("conversation")["model"] == "deepseek-v4-flash"
        assert cm.get_model_api_config("correction")["model"] == "deepseek-v4-flash"
        assert cm.get_model_api_config("agent")["model"] == "deepseek-v4-flash"
    finally:
        shutil.rmtree(config_dir, ignore_errors=True)


def test_follow_core_non_omni_still_uses_core_provider_http_url():
    """非 omni 的 follow_core 仍应解析为核心 provider 对应的 HTTP 兼容地址。"""
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


def test_tts_custom_non_active_qwen_intl_uses_saved_resolved_url():
    """非当前 assist 的阿里国际 TTS 也要使用保存过的可用地域 URL。"""
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
