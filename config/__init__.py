# -*- coding: utf-8 -*-
"""Configuration constants exposed by the config package."""

from copy import deepcopy
import logging

from config.prompts_chara import lanlan_prompt

logger = logging.getLogger(__name__)

# 应用程序名称配置
APP_NAME = "Xiao8"

# 服务器端口配置
MAIN_SERVER_PORT = 48911
MEMORY_SERVER_PORT = 48912
MONITOR_SERVER_PORT = 48913
COMMENTER_SERVER_PORT = 48914
TOOL_SERVER_PORT = 48915

# MCP Router配置
MCP_ROUTER_URL = 'http://localhost:3283'

# API 和模型配置的默认值
DEFAULT_CORE_API_KEY = ''
DEFAULT_AUDIO_API_KEY = ''
DEFAULT_OPENROUTER_API_KEY = ''
DEFAULT_MCP_ROUTER_API_KEY = 'Copy from MCP Router if needed'
DEFAULT_CORE_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
DEFAULT_CORE_MODEL = "qwen3-omni-flash-realtime"
DEFAULT_OPENROUTER_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# 用户自定义模型配置的默认 Provider/URL/API_KEY（空字符串表示使用全局配置）
DEFAULT_SUMMARY_MODEL_PROVIDER = ""
DEFAULT_SUMMARY_MODEL_URL = ""
DEFAULT_SUMMARY_MODEL_API_KEY = ""
DEFAULT_CORRECTION_MODEL_PROVIDER = ""
DEFAULT_CORRECTION_MODEL_URL = ""
DEFAULT_CORRECTION_MODEL_API_KEY = ""
DEFAULT_EMOTION_MODEL_PROVIDER = ""
DEFAULT_EMOTION_MODEL_URL = ""
DEFAULT_EMOTION_MODEL_API_KEY = ""
DEFAULT_VISION_MODEL_PROVIDER = ""
DEFAULT_VISION_MODEL_URL = ""
DEFAULT_VISION_MODEL_API_KEY = ""
DEFAULT_OMNI_MODEL_PROVIDER = ""
DEFAULT_OMNI_MODEL_URL = ""
DEFAULT_OMNI_MODEL_API_KEY = ""
DEFAULT_TTS_MODEL_PROVIDER = ""
DEFAULT_TTS_MODEL_URL = ""
DEFAULT_TTS_MODEL_API_KEY = ""

# 模型配置常量（默认值）
# 注：以下5个直接被导入使用的变量保留原名以保持向后兼容性
DEFAULT_ROUTER_MODEL = ROUTER_MODEL = 'openai/gpt-4.1'
DEFAULT_SETTING_PROPOSER_MODEL = SETTING_PROPOSER_MODEL = "qwen-max"
DEFAULT_SETTING_VERIFIER_MODEL = SETTING_VERIFIER_MODEL = "qwen-max"
DEFAULT_SEMANTIC_MODEL = SEMANTIC_MODEL = 'text-embedding-v4'
DEFAULT_RERANKER_MODEL = RERANKER_MODEL = 'qwen-plus'

# 其他模型配置（仅通过 config_manager 动态获取）
DEFAULT_SUMMARY_MODEL = "qwen-plus"
DEFAULT_CORRECTION_MODEL = 'qwen-max'
DEFAULT_EMOTION_MODEL = 'qwen-turbo'
DEFAULT_VISION_MODEL = "qwen3-vl-plus-2025-09-23"

# 用户自定义模型配置（可选，暂未使用）
DEFAULT_OMNI_MODEL = ""  # 全模态模型(语音+文字+图片)
DEFAULT_TTS_MODEL = ""   # TTS模型(Native TTS)


CONFIG_FILES = [
    'characters.json',
    'core_config.json',
    'user_preferences.json',
    'voice_storage.json',
]

DEFAULT_MASTER_TEMPLATE = {
    "档案名": "哥哥",
    "性别": "男",
    "昵称": "哥哥",
}

DEFAULT_LANLAN_TEMPLATE = {
    "test": {
        "性别": "女",
        "年龄": 15,
        "昵称": "T酱, 小T",
        "live2d": "mao_pro",
        "voice_id": "",
        "system_prompt": lanlan_prompt,
    }
}

DEFAULT_CHARACTERS_CONFIG = {
    "主人": deepcopy(DEFAULT_MASTER_TEMPLATE),
    "猫娘": deepcopy(DEFAULT_LANLAN_TEMPLATE),
    "当前猫娘": next(iter(DEFAULT_LANLAN_TEMPLATE.keys()), "")
}

DEFAULT_CORE_CONFIG = {
    "coreApiKey": "",
    "coreApi": "qwen",
    "assistApi": "qwen",
    "assistApiKeyQwen": "",
    "assistApiKeyOpenai": "",
    "assistApiKeyGlm": "",
    "assistApiKeyStep": "",
    "assistApiKeySilicon": "",
    "mcpToken": "",
}

DEFAULT_USER_PREFERENCES = []

DEFAULT_VOICE_STORAGE = {}

# 默认API配置（供 utils.api_config_loader 作为回退选项使用）
DEFAULT_CORE_API_PROFILES = {
    'free': {
        'CORE_URL': "ws://47.100.209.206:9805",
        'CORE_MODEL': "free-model",
        'CORE_API_KEY': "free-access",
        'IS_FREE_VERSION': True,
    },
    'qwen': {
        'CORE_URL': "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
        'CORE_MODEL': "qwen3-omni-flash-realtime",
    },
    'glm': {
        'CORE_URL': "wss://open.bigmodel.cn/api/paas/v4/realtime",
        'CORE_MODEL': "glm-realtime-air",
    },
    'openai': {
        'CORE_URL': "wss://api.openai.com/v1/realtime",
        'CORE_MODEL': "gpt-realtime",
    },
    'step': {
        'CORE_URL': "wss://api.stepfun.com/v1/realtime",
        'CORE_MODEL': "step-audio-2",
    },
}

DEFAULT_ASSIST_API_PROFILES = {
    'free': {
        'OPENROUTER_URL': "http://47.100.209.206:9807/v1",
        'SUMMARY_MODEL': "free-model",
        'CORRECTION_MODEL': "free-model",
        'EMOTION_MODEL': "free-model",
        'VISION_MODEL': "free-vision-model",
        'AUDIO_API_KEY': "free-access",
        'OPENROUTER_API_KEY': "free-access",
        'IS_FREE_VERSION': True,
    },
    'qwen': {
        'OPENROUTER_URL': "https://dashscope.aliyuncs.com/compatible-mode/v1",
        'SUMMARY_MODEL': "qwen3-next-80b-a3b-instruct",
        'CORRECTION_MODEL': "qwen3-235b-a22b-instruct-2507",
        'EMOTION_MODEL': "qwen-flash-2025-07-28",
        'VISION_MODEL': "qwen3-vl-plus-2025-09-23",
    },
    'openai': {
        'OPENROUTER_URL': "https://api.openai.com/v1",
        'SUMMARY_MODEL': "gpt-4.1-mini",
        'CORRECTION_MODEL': "gpt-5-chat-latest",
        'EMOTION_MODEL': "gpt-4.1-nano",
        'VISION_MODEL': "gpt-5-chat-latest",
    },
    'glm': {
        'OPENROUTER_URL': "https://open.bigmodel.cn/api/paas/v4",
        'SUMMARY_MODEL': "glm-4.5-flash",
        'CORRECTION_MODEL': "glm-4.5-air",
        'EMOTION_MODEL': "glm-4.5-flash",
        'VISION_MODEL': "glm-4v-plus-0111",
    },
    'step': {
        'OPENROUTER_URL': "https://api.stepfun.com/v1",
        'SUMMARY_MODEL': "step-2-mini",
        'CORRECTION_MODEL': "step-2-mini",
        'EMOTION_MODEL': "step-2-mini",
        'VISION_MODEL': "step-1o-turbo-vision",
    },
    'silicon': {
        'OPENROUTER_URL': "https://api.siliconflow.cn/v1",
        'SUMMARY_MODEL': "Qwen/Qwen3-Next-80B-A3B-Instruct",
        'CORRECTION_MODEL': "deepseek-ai/DeepSeek-V3.2-Exp",
        'EMOTION_MODEL': "inclusionAI/Ling-mini-2.0",
        'VISION_MODEL': "Qwen/Qwen3-VL-235B-A22B-Instruct",
    },
}

DEFAULT_ASSIST_API_KEY_FIELDS = {
    'qwen': 'ASSIST_API_KEY_QWEN',
    'openai': 'ASSIST_API_KEY_OPENAI',
    'glm': 'ASSIST_API_KEY_GLM',
    'step': 'ASSIST_API_KEY_STEP',
    'silicon': 'ASSIST_API_KEY_SILICON',
}

DEFAULT_CONFIG_DATA = {
    'characters.json': DEFAULT_CHARACTERS_CONFIG,
    'core_config.json': DEFAULT_CORE_CONFIG,
    'user_preferences.json': DEFAULT_USER_PREFERENCES,
    'voice_storage.json': DEFAULT_VOICE_STORAGE,
}


TIME_ORIGINAL_TABLE_NAME = "time_indexed_original"
TIME_COMPRESSED_TABLE_NAME = "time_indexed_compressed"

MODELS_WITH_EXTRA_BODY = ["qwen-flash-2025-07-28", "qwen3-vl-plus-2025-09-23"]


__all__ = [
    'APP_NAME',
    'CONFIG_FILES',
    'DEFAULT_MASTER_TEMPLATE',
    'DEFAULT_LANLAN_TEMPLATE',
    'DEFAULT_CHARACTERS_CONFIG',
    'DEFAULT_CORE_CONFIG',
    'DEFAULT_USER_PREFERENCES',
    'DEFAULT_VOICE_STORAGE',
    'DEFAULT_CONFIG_DATA',
    'DEFAULT_CORE_API_PROFILES',
    'DEFAULT_ASSIST_API_PROFILES',
    'DEFAULT_ASSIST_API_KEY_FIELDS',
    'TIME_ORIGINAL_TABLE_NAME',
    'TIME_COMPRESSED_TABLE_NAME',
    'MODELS_WITH_EXTRA_BODY',
    'MAIN_SERVER_PORT',
    'MEMORY_SERVER_PORT',
    'MONITOR_SERVER_PORT',
    'COMMENTER_SERVER_PORT',
    'TOOL_SERVER_PORT',
    'MCP_ROUTER_URL',
    # API 和模型配置的默认值
    'DEFAULT_CORE_API_KEY',
    'DEFAULT_AUDIO_API_KEY',
    'DEFAULT_OPENROUTER_API_KEY',
    'DEFAULT_MCP_ROUTER_API_KEY',
    'DEFAULT_CORE_URL',
    'DEFAULT_CORE_MODEL',
    'DEFAULT_OPENROUTER_URL',
    # 直接被导入使用的5个模型配置（导出 DEFAULT_ 和无前缀版本）
    'DEFAULT_ROUTER_MODEL',
    'ROUTER_MODEL',
    'DEFAULT_SETTING_PROPOSER_MODEL',
    'SETTING_PROPOSER_MODEL',
    'DEFAULT_SETTING_VERIFIER_MODEL',
    'SETTING_VERIFIER_MODEL',
    'DEFAULT_SEMANTIC_MODEL',
    'SEMANTIC_MODEL',
    'DEFAULT_RERANKER_MODEL',
    'RERANKER_MODEL',
    # 其他模型配置（仅导出 DEFAULT_ 版本）
    'DEFAULT_SUMMARY_MODEL',
    'DEFAULT_CORRECTION_MODEL',
    'DEFAULT_EMOTION_MODEL',
    'DEFAULT_VISION_MODEL',
    'DEFAULT_OMNI_MODEL',
    'DEFAULT_TTS_MODEL',
    # 用户自定义模型配置的 Provider/URL/API_KEY
    'DEFAULT_SUMMARY_MODEL_PROVIDER',
    'DEFAULT_SUMMARY_MODEL_URL',
    'DEFAULT_SUMMARY_MODEL_API_KEY',
    'DEFAULT_CORRECTION_MODEL_PROVIDER',
    'DEFAULT_CORRECTION_MODEL_URL',
    'DEFAULT_CORRECTION_MODEL_API_KEY',
    'DEFAULT_EMOTION_MODEL_PROVIDER',
    'DEFAULT_EMOTION_MODEL_URL',
    'DEFAULT_EMOTION_MODEL_API_KEY',
    'DEFAULT_VISION_MODEL_PROVIDER',
    'DEFAULT_VISION_MODEL_URL',
    'DEFAULT_VISION_MODEL_API_KEY',
    'DEFAULT_OMNI_MODEL_PROVIDER',
    'DEFAULT_OMNI_MODEL_URL',
    'DEFAULT_OMNI_MODEL_API_KEY',
    'DEFAULT_TTS_MODEL_PROVIDER',
    'DEFAULT_TTS_MODEL_URL',
    'DEFAULT_TTS_MODEL_API_KEY',
]

