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
    "assistApiKeyGrok": "",
    "assistApiKeyDoubao": "",
    "assistApiKeyDoubaoTts": "",
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
        'CORE_MODEL': "qwen3-omni-flash-realtime",
    },
    'qwen_intl': {
        'CORE_URL': "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime",
        'CORE_MODEL': "qwen3-omni-flash-realtime",
    },
    'glm': {
        'CORE_URL': "wss://open.bigmodel.cn/api/paas/v4/realtime",
        'CORE_MODEL': "glm-realtime-air",
    },
    'openai': {
        'CORE_URL': "wss://api.openai.com/v1/realtime",
        'CORE_MODEL': "gpt-realtime-mini-2025-12-15",
    },
    'step': {
        'CORE_URL': "wss://api.stepfun.com/v1/realtime",
        'CORE_MODEL': "stepaudio-2.5-realtime",
    },
    'gemini': {
        # Gemini 使用 google-genai SDK，而非原生 WebSocket
        'CORE_MODEL': "gemini-2.5-flash-native-audio-preview-12-2025",
    },
    'grok': {
        'CORE_URL': "wss://api.x.ai/v1/realtime",
        'CORE_MODEL': "grok-voice-fast-1.0",
    },
}

DEFAULT_ASSIST_API_PROFILES = {
    'free': {
        'OPENROUTER_URL': "https://www.lanlan.tech/text/v1",
        'CONVERSATION_MODEL' : "free-model" ,
        'SUMMARY_MODEL': "free-model",
        'CORRECTION_MODEL': "free-model",
        'EMOTION_MODEL': "free-model",
        'VISION_MODEL': "free-vision-model",
        # 必须与 api_providers.json 的 free agent_model 及 _free_agent_model_name 一致，
        # 否则 json 缺失回退到本 defaults 时免费 agent 不计配额、is_agent_free 误判。
        'AGENT_MODEL': "free-agent-model",
        'AUDIO_API_KEY': "free-access",
        'OPENROUTER_API_KEY': "free-access",
    },
    'qwen': {
        'OPENROUTER_URL': "https://dashscope.aliyuncs.com/compatible-mode/v1",
        'CONVERSATION_MODEL' : "qwen3.6-plus",
        'SUMMARY_MODEL': "qwen3.6-plus",
        'CORRECTION_MODEL': "qwen3.6-plus",
        'EMOTION_MODEL': "qwen3.6-flash-2026-04-16",
        'VISION_MODEL': "qwen3.6-plus",
        'AGENT_MODEL': "qwen3.6-plus",
    },
    'qwen_intl': {
        'OPENROUTER_URL': "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        'OPENROUTER_URLS': [
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
        ],
        'CONVERSATION_MODEL' : "qwen3.6-plus",
        'SUMMARY_MODEL': "qwen3.6-plus",
        'CORRECTION_MODEL': "qwen3.6-plus",
        'EMOTION_MODEL': "qwen3.6-flash-2026-04-16",
        'VISION_MODEL': "qwen3.6-plus",
        'AGENT_MODEL': "qwen3.6-plus",
    },
    'openai': {
        'OPENROUTER_URL': "https://api.openai.com/v1",
        'CONVERSATION_MODEL' : "gpt-5-chat-latest",
        'SUMMARY_MODEL': "gpt-4.1-mini",
        'CORRECTION_MODEL': "gpt-5-chat-latest",
        'EMOTION_MODEL': "gpt-4.1-nano",
        'VISION_MODEL': "gpt-5-chat-latest",
        'AGENT_MODEL': "gpt-5-chat-latest",
    },
    'glm': {
        'OPENROUTER_URL': "https://open.bigmodel.cn/api/paas/v4",
        'CONVERSATION_MODEL' : "glm-4.5-air" ,
        'SUMMARY_MODEL': "glm-4.5-flash",
        'CORRECTION_MODEL': "glm-4.5-air",
        'EMOTION_MODEL': "glm-4.5-flash",
        'VISION_MODEL': "glm-4.6v-flash",
        'AGENT_MODEL': "glm-4.5-air",
    },
    'step': {
        'OPENROUTER_URL': "https://api.stepfun.com/v1",
        'CONVERSATION_MODEL' : "step-2-mini",
        'SUMMARY_MODEL': "step-2-mini",
        'CORRECTION_MODEL': "step-2-mini",
        'EMOTION_MODEL': "step-2-mini",
        'VISION_MODEL': "step-1o-turbo-vision",
        'AGENT_MODEL': "step-2-mini",
    },
    'silicon': {
        'OPENROUTER_URL': "https://api.siliconflow.cn/v1",
        'CONVERSATION_MODEL' : "deepseek-ai/DeepSeek-V3.2" ,
        'SUMMARY_MODEL': "Qwen/Qwen3-Next-80B-A3B-Instruct",
        'CORRECTION_MODEL': "deepseek-ai/DeepSeek-V3.2",
        'EMOTION_MODEL': "inclusionAI/Ling-mini-2.0",
        'VISION_MODEL': "zai-org/GLM-4.6V",
        'AGENT_MODEL': "deepseek-ai/DeepSeek-V3.2",
    },
    'gemini': {
        'OPENROUTER_URL': "https://generativelanguage.googleapis.com/v1beta/openai/",
        'CONVERSATION_MODEL' : "gemini-3-flash-preview",
        'SUMMARY_MODEL': "gemini-3-flash-preview",
        'CORRECTION_MODEL': "gemini-3-flash-preview",
        'EMOTION_MODEL': "gemini-2.5-flash",
        'VISION_MODEL': "gemini-3-flash-preview",
        'AGENT_MODEL': "gemini-3-flash-preview",
    },
    'kimi': {
        'OPENROUTER_URL': "https://api.moonshot.cn/v1",
        'CONVERSATION_MODEL': "kimi-latest",
        'SUMMARY_MODEL': "moonshot-v1-8k",
        'CORRECTION_MODEL': "kimi-latest",
        'EMOTION_MODEL': "moonshot-v1-8k",
        'VISION_MODEL': "kimi-latest",
        'AGENT_MODEL': "kimi-latest",
    },
    'kimi_code': {
        'OPENROUTER_URL': "https://api.kimi.com/coding",
        'PROVIDER_TYPE': "anthropic",
        'CONVERSATION_MODEL': "kimi-for-coding",
        'SUMMARY_MODEL': "kimi-for-coding",
        'CORRECTION_MODEL': "kimi-for-coding",
        'EMOTION_MODEL': "kimi-for-coding",
        'VISION_MODEL': "kimi-for-coding",
        'AGENT_MODEL': "kimi-for-coding",
    },
    'claude': {
        'OPENROUTER_URL': "https://api.anthropic.com/v1",
        'CONVERSATION_MODEL': "claude-sonnet-4-6",
        'SUMMARY_MODEL': "claude-sonnet-4-6",
        'CORRECTION_MODEL': "claude-sonnet-4-6",
        'EMOTION_MODEL': "claude-haiku-4-5-20251001",
        'VISION_MODEL': "claude-sonnet-4-6",
        'AGENT_MODEL': "claude-opus-4-6",
    },
    'openrouter': {
        'OPENROUTER_URL': "https://openrouter.ai/api/v1",
        'CONVERSATION_MODEL': "openai/gpt-4.1",
        'SUMMARY_MODEL': "openai/gpt-4.1-mini",
        'CORRECTION_MODEL': "openai/gpt-4.1-mini",
        'EMOTION_MODEL': "openai/gpt-4.1-nano",
        'VISION_MODEL': "openai/gpt-4.1",
        'AGENT_MODEL': "openai/gpt-4.1",
    },
    'grok': {
        'OPENROUTER_URL': "https://api.x.ai/v1",
        'CONVERSATION_MODEL': "grok-4-1-fast-non-reasoning",
        'SUMMARY_MODEL': "grok-4-1-fast-non-reasoning",
        'CORRECTION_MODEL': "grok-4-1-fast-non-reasoning",
        'EMOTION_MODEL': "grok-3-mini-fast",
        'VISION_MODEL': "grok-4-1-fast-non-reasoning",
        'AGENT_MODEL': "grok-4-1-fast-non-reasoning",
    },
    'doubao': {
        'OPENROUTER_URL': "https://ark.cn-beijing.volces.com/api/v3",
        'CONVERSATION_MODEL': "doubao-seed-2-0-lite-260215",
        'SUMMARY_MODEL': "doubao-seed-2-0-lite-260215",
        'CORRECTION_MODEL': "doubao-seed-2-0-lite-260215",
        'EMOTION_MODEL': "doubao-seed-2-0-mini-260215",
        'VISION_MODEL': "doubao-seed-2-0-lite-260215",
        'AGENT_MODEL': "doubao-seed-2-0-pro-260215",
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
    'qwen_intl': 'ASSIST_API_KEY_QWEN_INTL',
    'minimax': 'ASSIST_API_KEY_MINIMAX',
    'mimo': 'ASSIST_API_KEY_MIMO',
    'elevenlabs': 'ASSIST_API_KEY_ELEVENLABS',
    'claude': 'ASSIST_API_KEY_CLAUDE',
    'openrouter': 'ASSIST_API_KEY_OPENROUTER',
    'grok': 'ASSIST_API_KEY_GROK',
    'doubao': 'ASSIST_API_KEY_DOUBAO',
}

DEFAULT_CONFIG_DATA = {
    'characters.json': DEFAULT_CHARACTERS_CONFIG,
    'core_config.json': DEFAULT_CORE_CONFIG,
    'user_preferences.json': DEFAULT_USER_PREFERENCES,
    'voice_storage.json': DEFAULT_VOICE_STORAGE,
}
