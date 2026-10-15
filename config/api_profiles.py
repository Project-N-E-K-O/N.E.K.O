# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Default core configuration, provider profiles, and persisted config payloads."""

from .character_defaults import DEFAULT_CHARACTERS_CONFIG

DEFAULT_CORE_CONFIG = {
    "coreApiKey": "",
    "coreApi": "qwen",
    "assistApi": "qwen",
    "assistApiKeyQwen": "",
    "assistApiKeyOpenai": "",
    "assistApiKeyGlm": "",
    "assistApiKeyStep": "",
    "assistApiKeySilicon": "",
    "assistApiKeyGemini": "",
    "assistApiKeyKimi": "",
    "assistApiKeyKimiCode": "",
    "assistApiKeyQwenIntl": "",
    "assistApiKeyMinimax": "",
    "assistApiKeyMimo": "",
    "useMimoTokenPlan": False,
    "assistApiKeyMimoTokenPlan": "",
    "assistApiKeyElevenlabs": "",
    "assistApiKeyClaude": "",
    "assistApiKeyOrcarouter": "",
    "assistApiKeyRequesty": "",
    "assistApiKeyAtlascloud": "",
    "assistApiKeyGrok": "",
    "assistApiKeyDoubao": "",
    "assistApiKeyDoubaoTts": "",
    "doubaoVoiceManagementAccessKey": "",
    "doubaoVoiceManagementSecretKey": "",
    "doubaoVoiceManagementAppId": "",
    "doubaoVoiceManagementProjectName": "",
    "mcpToken": "",
    "agentModelUrl": "",
    "agentModelId": "",
    "agentModelApiKey": "",
    "openclawUrl": "http://127.0.0.1:8088",
    "openclawTimeout": 300.0,
    "openclawDefaultSenderId": "neko_user",
    "textGuardMaxLength": 300,
}

DEFAULT_USER_PREFERENCES = []

DEFAULT_VOICE_STORAGE = {}

# 默认API配置（供 utils.api_config_loader 作为回退选项使用）
DEFAULT_CORE_API_PROFILES = {
    'free': {
        'CORE_URL': "wss://www.lanlan.tech/core",
        'CORE_MODEL': "free-model",
        'CORE_API_KEY': "free-access",
    },
    'qwen': {
        'CORE_URL': "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
        'CORE_MODEL': "qwen3.8-omni-flash-realtime",
    },
    'qwen_intl': {
        'CORE_URL': "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime",
        'CORE_MODEL': "qwen3.8-omni-flash-realtime",
    },
    'glm': {
        'CORE_URL': "wss://open.bigmodel.cn/api/paas/v4/realtime",
        'CORE_MODEL': "glm-realtime-plus",
    },
    'openai': {
        'CORE_URL': "wss://api.openai.com/v1/realtime",
        'CORE_MODEL': "gpt-realtime-2.1",
    },
    'step': {
        'CORE_URL': "wss://api.stepfun.com/v1/realtime",
        'CORE_MODEL': "stepaudio-3-realtime-preview",
    },
    'gemini': {
        # Gemini 使用 google-genai SDK，而非原生 WebSocket
        'CORE_MODEL': "gemini-3.8-live",
    },
    'grok': {
        'CORE_URL': "wss://api.x.ai/v1/realtime",
        'CORE_MODEL': "grok-voice-latest",
    },
}

DEFAULT_ASSIST_API_PROFILES = {
    'free': {
        'OPENROUTER_URL': "https://www.lanlan.tech/text/v1",
        'CONVERSATION_MODEL': "free-model",
        'SUMMARY_MODEL': "free-model",
        'CORRECTION_MODEL': "free-model",
        'EMOTION_MODEL': "free-mini-model",
        'VISION_MODEL': "free-vision-model",
        # 必须与 api_providers.json 的 free agent_model 及 _free_agent_model_name 一致，
        # 否则 json 缺失回退到本 defaults 时免费 agent 不计配额、is_agent_free 误判。
        'AGENT_MODEL': "free-agent-model",
        'AUDIO_API_KEY': "free-access",
        'OPENROUTER_API_KEY': "free-access",
    },
    'qwen': {
        'OPENROUTER_URL': "https://dashscope.aliyuncs.com/compatible-mode/v1",
        'CONVERSATION_MODEL': "qwen3.8-omni-flash",
        'SUMMARY_MODEL': "qwen3.8-flash",
        'CORRECTION_MODEL': "qwen3.8-flash",
        'EMOTION_MODEL': "qwen3.7-flash",
        'VISION_MODEL': "qwen3.8-omni-flash",
        'AGENT_MODEL': "qwen3.8-flash",
    },
    'qwen_intl': {
        'OPENROUTER_URL': "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        'OPENROUTER_URLS': [
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
        ],
        'CONVERSATION_MODEL': "qwen3.8-omni-flash",
        'SUMMARY_MODEL': "qwen3.8-flash",
        'CORRECTION_MODEL': "qwen3.8-flash",
        'EMOTION_MODEL': "qwen3.7-flash",
        'VISION_MODEL': "qwen3.8-omni-flash",
        'AGENT_MODEL': "qwen3.8-flash",
    },
    'openai': {
        'OPENROUTER_URL': "https://api.openai.com/v1",
        'CONVERSATION_MODEL': "gpt-5.6-luna",
        'SUMMARY_MODEL': "gpt-5.6-luna",
        'CORRECTION_MODEL': "gpt-5.6-luna",
        'EMOTION_MODEL': "gpt-5-nano",
        'VISION_MODEL': "gpt-5.6-luna",
        'AGENT_MODEL': "gpt-5.6-terra",
    },
    'glm': {
        'OPENROUTER_URL': "https://open.bigmodel.cn/api/paas/v4",
        'CONVERSATION_MODEL': "glm-4.7-flash",
        'SUMMARY_MODEL': "glm-4.7-flash",
        'CORRECTION_MODEL': "glm-4.7-flash",
        'EMOTION_MODEL': "glm-4.7-flash",
        'VISION_MODEL': "glm-4.6v-flash",
        'AGENT_MODEL': "glm-5v-turbo",
    },
    'step': {
        'OPENROUTER_URL': "https://api.stepfun.com/v1",
        'CONVERSATION_MODEL': "step-1o-turbo-vision",
        'SUMMARY_MODEL': "step-1o-turbo-vision",
        'CORRECTION_MODEL': "step-1o-turbo-vision",
        'EMOTION_MODEL': "step-1o-turbo-vision",
        'VISION_MODEL': "step-1o-turbo-vision",
        'AGENT_MODEL': "step-5-preview",
    },
    'silicon': {
        'OPENROUTER_URL': "https://api.siliconflow.cn/v1",
        'CONVERSATION_MODEL': "deepseek-ai/DeepSeek-V4-Flash",
        'SUMMARY_MODEL': "deepseek-ai/DeepSeek-V4-Flash",
        'CORRECTION_MODEL': "deepseek-ai/DeepSeek-V4-Flash",
        'EMOTION_MODEL': "inclusionAI/Ling-mini-2.0",
        'VISION_MODEL': "Qwen/Qwen3.5-122B-A10B",
        'AGENT_MODEL': "Qwen/Qwen3.5-122B-A10B",
    },
    'gemini': {
        'OPENROUTER_URL': "https://generativelanguage.googleapis.com/v1beta/openai/",
        'CONVERSATION_MODEL': "gemini-3.1-flash-lite",
        'SUMMARY_MODEL': "gemini-2.5-flash",
        'CORRECTION_MODEL': "gemini-3-flash-preview",
        'EMOTION_MODEL': "gemini-2.5-flash-lite",
        'VISION_MODEL': "gemini-3.1-flash-lite",
        'AGENT_MODEL': "gemini-3.5-flash",
    },
    'kimi': {
        'OPENROUTER_URL': "https://api.moonshot.cn/v1",
        'CONVERSATION_MODEL': "kimi-k2.6",
        'SUMMARY_MODEL': "kimi-k2.6",
        'CORRECTION_MODEL': "kimi-k2.6",
        'EMOTION_MODEL': "kimi-k2.6",
        'VISION_MODEL': "kimi-k2.6",
        'AGENT_MODEL': "kimi-k2.6",
    },
    'kimi_code': {
        'OPENROUTER_URL': "https://api.kimi.com/coding",
        'CONVERSATION_MODEL': "kimi-for-coding",
        'SUMMARY_MODEL': "kimi-for-coding",
        'CORRECTION_MODEL': "kimi-for-coding",
        'EMOTION_MODEL': "kimi-for-coding",
        'VISION_MODEL': "kimi-for-coding",
        'AGENT_MODEL': "kimi-for-coding",
        'PROVIDER_TYPE': "anthropic",
    },
    'deepseek': {
        'OPENROUTER_URL': "https://api.deepseek.com/v1",
        'CONVERSATION_MODEL': "deepseek-flash",
        'SUMMARY_MODEL': "deepseek-flash",
        'CORRECTION_MODEL': "deepseek-flash",
        'EMOTION_MODEL': "deepseek-flash",
        'VISION_MODEL': "deepseek-flash",
        'AGENT_MODEL': "deepseek-flash",
    },
    'claude': {
        'OPENROUTER_URL': "https://api.anthropic.com/v1",
        'CONVERSATION_MODEL': "claude-sonnet-5-5",
        'SUMMARY_MODEL': "claude-sonnet-5-5",
        'CORRECTION_MODEL': "claude-sonnet-5-5",
        'EMOTION_MODEL': "claude-haiku-5-5",
        'VISION_MODEL': "claude-sonnet-5-5",
        'AGENT_MODEL': "claude-sonnet-5-5",
        'PROVIDER_TYPE': "anthropic",
    },
    'openrouter': {
        'OPENROUTER_URL': "https://openrouter.ai/api/v1",
        'CONVERSATION_MODEL': "google/gemini-2.5-flash",
        'SUMMARY_MODEL': "deepseek/deepseek-v4-flash",
        'CORRECTION_MODEL': "deepseek/deepseek-v4-flash",
        'EMOTION_MODEL': "qwen/qwen3.5-9b",
        'VISION_MODEL': "google/gemini-2.5-flash",
        'AGENT_MODEL': "google/gemini-3-flash-preview",
    },
    'orcarouter': {
        'OPENROUTER_URL': "https://api.orcarouter.ai/v1",
        'CONVERSATION_MODEL': "anthropic/claude-sonnet-5.5",
        'SUMMARY_MODEL': "anthropic/claude-sonnet-5.5",
        'CORRECTION_MODEL': "anthropic/claude-sonnet-5.5",
        'EMOTION_MODEL': "anthropic/claude-haiku-4.5",
        'VISION_MODEL': "anthropic/claude-sonnet-5.5",
        'AGENT_MODEL': "anthropic/claude-sonnet-5.5",
    },
    'requesty': {
        'OPENROUTER_URL': "https://router.requesty.ai/v1",
        'CONVERSATION_MODEL': "google/gemini-2.5-flash",
        'SUMMARY_MODEL': "google/gemini-2.5-flash",
        'CORRECTION_MODEL': "google/gemini-2.5-flash",
        'EMOTION_MODEL': "google/gemini-2.5-flash-lite",
        'VISION_MODEL': "google/gemini-2.5-flash",
        'AGENT_MODEL': "google/gemini-3-flash-preview",
    },
    'atlascloud': {
        'OPENROUTER_URL': "https://api.atlascloud.ai/v1",
        'CONVERSATION_MODEL': "google/gemini-3.1-flash-lite",
        'SUMMARY_MODEL': "deepseek-ai/deepseek-v4-flash",
        'CORRECTION_MODEL': "deepseek-ai/deepseek-v4-flash",
        'EMOTION_MODEL': "google/gemini-2.5-flash-lite",
        'VISION_MODEL': "google/gemini-3.1-flash-lite",
        'AGENT_MODEL': "google/gemini-3-flash-preview",
    },
    'vllm_omni': {
        'OPENROUTER_URL': "ws://localhost:8091/v1",
        'CONVERSATION_MODEL': "",
        'SUMMARY_MODEL': "",
        'CORRECTION_MODEL': "",
        'EMOTION_MODEL': "",
        'VISION_MODEL': "",
        'AGENT_MODEL': "",
    },
    'grok': {
        'OPENROUTER_URL': "https://api.x.ai/v1",
        'CONVERSATION_MODEL': "grok-4.20-0309-non-reasoning",
        'SUMMARY_MODEL': "grok-4.20-0309-non-reasoning",
        'CORRECTION_MODEL': "grok-4.20-0309-non-reasoning",
        'EMOTION_MODEL': "grok-4.20-0309-non-reasoning",
        'VISION_MODEL': "grok-4.20-0309-non-reasoning",
        'AGENT_MODEL': "grok-4.3",
    },
    'doubao': {
        'OPENROUTER_URL': "https://ark.cn-beijing.volces.com/api/v3",
        'CONVERSATION_MODEL': "doubao-seed-character-260628",
        'SUMMARY_MODEL': "doubao-seed-2-0-lite-260428",
        'CORRECTION_MODEL': "doubao-seed-character-260628",
        'EMOTION_MODEL': "doubao-seed-2-0-mini-260428",
        'VISION_MODEL': "doubao-seed-character-260628",
        'AGENT_MODEL': "doubao-seed-2-0-lite-260428",
    },
    'minimax': {
        'OPENROUTER_URL': "https://api.minimaxi.com/v1",
        'CONVERSATION_MODEL': "MiniMax-M3",
        'SUMMARY_MODEL': "MiniMax-M3",
        'CORRECTION_MODEL': "MiniMax-M3",
        'EMOTION_MODEL': "MiniMax-M3",
        'VISION_MODEL': "MiniMax-M3",
        'AGENT_MODEL': "MiniMax-M3",
    },
    'minimax_intl': {
        'OPENROUTER_URL': "https://api.minimax.io/v1",
        'CONVERSATION_MODEL': "MiniMax-M3",
        'SUMMARY_MODEL': "MiniMax-M3",
        'CORRECTION_MODEL': "MiniMax-M3",
        'EMOTION_MODEL': "MiniMax-M3",
        'VISION_MODEL': "MiniMax-M3",
        'AGENT_MODEL': "MiniMax-M3",
    },
    'mimo': {
        'OPENROUTER_URL': "https://api.xiaomimimo.com/v1",
        'MIMO_TOKEN_PLAN_OPENROUTER_URL': "https://token-plan-cn.xiaomimimo.com/v1",
        'MIMO_TOKEN_PLAN_OPENROUTER_URLS': [
            "https://token-plan-cn.xiaomimimo.com/v1",
            "https://token-plan-sgp.xiaomimimo.com/v1",
            "https://token-plan-ams.xiaomimimo.com/v1",
        ],
        'CONVERSATION_MODEL': "mimo-v2.5",
        'SUMMARY_MODEL': "mimo-v2.5",
        'CORRECTION_MODEL': "mimo-v2.5",
        'EMOTION_MODEL': "mimo-v2.5",
        'VISION_MODEL': "mimo-v2.5",
        'AGENT_MODEL': "mimo-v2.5",
    },
}

DEFAULT_ASSIST_API_KEY_FIELDS = {
    'qwen': 'ASSIST_API_KEY_QWEN',
    'openai': 'ASSIST_API_KEY_OPENAI',
    'glm': 'ASSIST_API_KEY_GLM',
    'step': 'ASSIST_API_KEY_STEP',
    'silicon': 'ASSIST_API_KEY_SILICON',
    'gemini': 'ASSIST_API_KEY_GEMINI',
    'kimi': 'ASSIST_API_KEY_KIMI',
    'kimi_code': 'ASSIST_API_KEY_KIMI_CODE',
    'deepseek': 'ASSIST_API_KEY_DEEPSEEK',
    'qwen_intl': 'ASSIST_API_KEY_QWEN_INTL',
    'minimax': 'ASSIST_API_KEY_MINIMAX',
    'minimax_intl': 'ASSIST_API_KEY_MINIMAX_INTL',
    'mimo': 'ASSIST_API_KEY_MIMO',
    'elevenlabs': 'ASSIST_API_KEY_ELEVENLABS',
    'claude': 'ASSIST_API_KEY_CLAUDE',
    'openrouter': 'ASSIST_API_KEY_OPENROUTER',
    'orcarouter': 'ASSIST_API_KEY_ORCAROUTER',
    'requesty': 'ASSIST_API_KEY_REQUESTY',
    'atlascloud': 'ASSIST_API_KEY_ATLASCLOUD',
    'grok': 'ASSIST_API_KEY_GROK',
    'doubao': 'ASSIST_API_KEY_DOUBAO',
    'doubao_tts': 'ASSIST_API_KEY_DOUBAO_TTS',
}

DEFAULT_CONFIG_DATA = {
    'characters.json': DEFAULT_CHARACTERS_CONFIG,
    'core_config.json': DEFAULT_CORE_CONFIG,
    'user_preferences.json': DEFAULT_USER_PREFERENCES,
    'voice_storage.json': DEFAULT_VOICE_STORAGE,
}
