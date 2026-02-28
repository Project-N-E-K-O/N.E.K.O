# -*- coding: utf-8 -*-
"""config 包对外暴露的配置常量。"""

from copy import deepcopy
import logging
import os
import uuid
from types import MappingProxyType

from config.prompts_chara import lanlan_prompt, get_lanlan_prompt, is_default_prompt

# 应用程序名称配置
APP_NAME = "N.E.K.O"
logger = logging.getLogger(f"{APP_NAME}.{__name__}")

# GPT-SoVITS voice_id 前缀(角色管理中使用 "gsv:<voice_id>" 格式标识 GPT-SoVITS 声音)
GSV_VOICE_PREFIX = "gsv:"

# 角色档案保留字段（统一管理）
# - system: 由系统指定功能维护，不允许通用角色编辑接口直接修改
# - workshop: 创意工坊导入/发布流程专用，不应从外部角色卡直接透传
CHARACTER_SYSTEM_RESERVED_FIELDS = (
    "_reserved",
    "live2d",
    "voice_id",
    "system_prompt",
    "model_type",
    "vrm",
    "vrm_animation",
    "lighting",
    "vrm_rotation",
    "live2d_item_id",
    "item_id",
    "idleAnimation",
)

CHARACTER_WORKSHOP_RESERVED_FIELDS = (
    "原始数据",
    "文件路径",
    "创意工坊物品ID",
    "description",
    "tags",
    "name",
    "描述",
    "标签",
    "关键词",
)

CHARACTER_RESERVED_FIELDS = tuple(
    dict.fromkeys((*CHARACTER_SYSTEM_RESERVED_FIELDS, *CHARACTER_WORKSHOP_RESERVED_FIELDS))
)


def get_character_reserved_fields() -> tuple[str, ...]:
    """返回角色档案保留字段（去重后、有序）。"""
    return CHARACTER_RESERVED_FIELDS


# 角色保留字段 schema（v2）
# 所有系统保留字段统一收口到 `_reserved`，并按 avatar/live2d/vrm 分层。
RESERVED_FIELD_SCHEMA = {
    "voice_id": str,
    "system_prompt": str,
    "avatar": {
        "model_type": str,
        "asset_source": str,
        "asset_source_id": str,
        "live2d": {
            "model_path": str,
        },
        "vrm": {
            "model_path": str,
            "animation": (str, dict, list, type(None)),
            "idle_animation": str,
            "lighting": (dict, type(None)),
        },
    },
}

# 兼容迁移映射：旧平铺字段 -> _reserved 路径
# 注意：rotation / camera_position / position / scale / viewport / display 保持本地偏好存储，
# 不迁移到 characters.json。
LEGACY_FLAT_TO_RESERVED = {
    "voice_id": ("voice_id",),
    "system_prompt": ("system_prompt",),
    "model_type": ("avatar", "model_type"),
    "live2d_item_id": ("avatar", "asset_source_id"),
    "item_id": ("avatar", "asset_source_id"),
    "live2d": ("avatar", "live2d", "model_path"),
    "vrm": ("avatar", "vrm", "model_path"),
    "vrm_animation": ("avatar", "vrm", "animation"),
    "idleAnimation": ("avatar", "vrm", "idle_animation"),
    "lighting": ("avatar", "vrm", "lighting"),
}

# 运行时端口覆盖支持：
# - 首选键：NEKO_<PORT_NAME>
# - 兼容键：<PORT_NAME>
def _read_port_env(port_name: str, default: int) -> int:
    for key in (f"NEKO_{port_name}", port_name):
        raw = os.getenv(key)
        if not raw:
            continue
        try:
            value = int(raw)
            if 1 <= value <= 65535:
                return value
        except Exception:
            continue
    return default

# 服务器端口配置
MAIN_SERVER_PORT = _read_port_env("MAIN_SERVER_PORT", 48911)
MEMORY_SERVER_PORT = _read_port_env("MEMORY_SERVER_PORT", 48912)
MONITOR_SERVER_PORT = _read_port_env("MONITOR_SERVER_PORT", 48913)
COMMENTER_SERVER_PORT = _read_port_env("COMMENTER_SERVER_PORT", 48914)
TOOL_SERVER_PORT = _read_port_env("TOOL_SERVER_PORT", 48915)
USER_PLUGIN_SERVER_PORT = _read_port_env("USER_PLUGIN_SERVER_PORT", 48916)
AGENT_MQ_PORT = _read_port_env("AGENT_MQ_PORT", 48917)
MAIN_AGENT_EVENT_PORT = _read_port_env("MAIN_AGENT_EVENT_PORT", 48918)

# 实例 ID：同一次启动的所有服务共享。
# launcher 会在拉起子进程前写入 NEKO_INSTANCE_ID 环境变量。
# 若源码直跑绕过 launcher，则每次导入使用随机回退值，确保 /health
# 始终返回有效 id。
INSTANCE_ID = os.getenv("NEKO_INSTANCE_ID") or uuid.uuid4().hex

# tfLink 文件上传服务配置
TFLINK_UPLOAD_URL = 'http://47.101.214.205:8000/api/upload'
# tfLink 允许的主机名白名单（用于 SSRF 防护）
TFLINK_ALLOWED_HOSTS = [
    '47.101.214.205',  # tfLink 官方 IP
]

# API 和模型配置的默认值
DEFAULT_CORE_API_KEY = ''
DEFAULT_AUDIO_API_KEY = ''
DEFAULT_OPENROUTER_API_KEY = ''
DEFAULT_MCP_ROUTER_API_KEY = 'Copy from MCP Router if needed'
DEFAULT_CORE_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
DEFAULT_CORE_MODEL = "qwen3-omni-flash-realtime"
DEFAULT_OPENROUTER_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# 屏幕分享模式的原生图片输入限流配置（秒）
NATIVE_IMAGE_MIN_INTERVAL = 1.5
# 无语音活动时图片发送间隔倍数（实际间隔 = NATIVE_IMAGE_MIN_INTERVAL × 此值）
IMAGE_IDLE_RATE_MULTIPLIER = 5

# 用户自定义模型配置的默认 Provider/URL/API_KEY（空字符串表示使用全局配置）
DEFAULT_CONVERSATION_MODEL_URL = ""
DEFAULT_CONVERSATION_MODEL_API_KEY = ""
DEFAULT_SUMMARY_MODEL_URL = ""
DEFAULT_SUMMARY_MODEL_API_KEY = ""
DEFAULT_CORRECTION_MODEL_URL = ""
DEFAULT_CORRECTION_MODEL_API_KEY = ""
DEFAULT_EMOTION_MODEL_URL = ""
DEFAULT_EMOTION_MODEL_API_KEY = ""
DEFAULT_VISION_MODEL_URL = ""
DEFAULT_VISION_MODEL_API_KEY = ""
DEFAULT_REALTIME_MODEL_URL = "" # 仅用于本地实时模型(语音+文字+图片)
DEFAULT_REALTIME_MODEL_API_KEY = "" # 仅用于本地实时模型(语音+文字+图片)
DEFAULT_TTS_MODEL_URL = "" # 与Realtime对应的TTS模型(Native TTS)
DEFAULT_TTS_MODEL_API_KEY = "" # 与Realtime对应的TTS模型(Native TTS)
DEFAULT_AGENT_MODEL_URL = ""
DEFAULT_AGENT_MODEL_API_KEY = ""

# 模型配置常量（默认值）
# 注：以下5个直接被导入使用的变量保留原名以保持向后兼容性
DEFAULT_ROUTER_MODEL = ROUTER_MODEL = 'qwen-plus'
DEFAULT_SETTING_PROPOSER_MODEL = SETTING_PROPOSER_MODEL = "qwen-max"
DEFAULT_SETTING_VERIFIER_MODEL = SETTING_VERIFIER_MODEL = "qwen-max"
DEFAULT_SEMANTIC_MODEL = SEMANTIC_MODEL = 'text-embedding-v4'
DEFAULT_RERANKER_MODEL = RERANKER_MODEL = 'qwen-plus'

# 其他模型配置（仅通过 config_manager 动态获取）
DEFAULT_CONVERSATION_MODEL = 'qwen-max'
DEFAULT_SUMMARY_MODEL = "qwen-plus"
DEFAULT_CORRECTION_MODEL = 'qwen-max'
DEFAULT_EMOTION_MODEL = 'qwen-flash'
DEFAULT_VISION_MODEL = "qwen3-vl-plus-2025-09-23"
DEFAULT_AGENT_MODEL = "qwen3.5-plus"

# 用户自定义模型配置（可选，暂未使用）
DEFAULT_REALTIME_MODEL = "Qwen3-Omni-30B-A3B-Instruct"  # 全模态模型(语音+文字+图片)
DEFAULT_TTS_MODEL = "Qwen3-Omni-30B-A3B-Instruct"   # 与Realtime对应的TTS模型(Native TTS)


CONFIG_FILES = [
    'characters.json',
    'core_config.json',
    'user_preferences.json',
    'voice_storage.json',
    'workshop_config.json',
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
        "_reserved": {
            "voice_id": "",
            "system_prompt": lanlan_prompt,
            "avatar": {
                "model_type": "live2d",
                "asset_source": "local",
                "asset_source_id": "",
                "live2d": {
                    "model_path": "mao_pro/mao_pro.model3.json",
                },
                "vrm": {
                    "model_path": "",
                    "animation": None,
                    "idle_animation": "",
                    "lighting": None,
                },
            },
        },
    }
}

_DEFAULT_VRM_LIGHTING_MUTABLE = {
    "ambient": 0.4,  # HemisphereLight 强度
    "main": 1.2,     # 主光源强度
    "fill": 0.5,     # 补光强度
    "rim": 0.8,      # 轮廓光强度
    "top": 0.3,      # 顶光强度
    "bottom": 0.15,  # 底光强度
    "exposure": 0.0, # 曝光值
    "toneMapping": 0 # 色调映射类型
}

DEFAULT_VRM_LIGHTING = MappingProxyType(_DEFAULT_VRM_LIGHTING_MUTABLE)

VRM_LIGHTING_RANGES = {
    'ambient': (0, 1.0),
    'main': (0, 2.5),
    'fill': (0, 1.0),
    'rim': (0, 1.5),
    'top': (0, 1.0),
    'bottom': (0, 0.5),
    'exposure': (-10.0, 10.0),
    'toneMapping': (0, 5),
}


def get_default_vrm_lighting() -> dict[str, float]:
    """获取默认VRM打光配置的副本"""
    return dict(DEFAULT_VRM_LIGHTING)

DEFAULT_CHARACTERS_CONFIG = {
    "主人": deepcopy(DEFAULT_MASTER_TEMPLATE),
    "猫娘": deepcopy(DEFAULT_LANLAN_TEMPLATE),
    "当前猫娘": next(iter(DEFAULT_LANLAN_TEMPLATE.keys()), "")
}


# 内容值翻译映射（仅翻译值，键名保持中文不变，因为系统内部依赖这些键名）
_VALUE_TRANSLATIONS = {
    'en': {
        '哥哥': 'Brother',
        '男': 'Male',
        '女': 'Female',
        'T酱, 小T': 'T-chan, Little T',
    },
    'ja': {
        '哥哥': 'お兄ちゃん',
        '男': '男性',
        '女': '女性',
        'T酱, 小T': 'Tちゃん, 小T',
    },
    'zh-TW': {
        '哥哥': '哥哥',
        '男': '男',
        '女': '女',
        'T酱, 小T': 'T醬, 小T',
    },
    # zh 和 zh-CN 使用原始中文值（不需要翻译）
}


def get_localized_default_characters(language: str | None = None) -> dict:
    """
    获取本地化的默认角色配置。
    
    根据 Steam 语言设置翻译内容值（如"哥哥"→"Brother"）。
    注意：键名保持中文不变，因为系统内部依赖这些键名。
    仅在首次创建 characters.json 时使用。
    
    Args:
        language: 语言代码 ('en', 'ja', 'zh', 'zh-CN', 'zh-TW')。
                  如果为 None，则从 Steam 获取或默认为 'zh-CN'。
    
    Returns:
        本地化后的 DEFAULT_CHARACTERS_CONFIG 副本
    """
    # 获取语言代码
    if language is None:
        try:
            from utils.language_utils import _get_steam_language, normalize_language_code
            steam_lang = _get_steam_language()
            language = normalize_language_code(steam_lang, format='full') if steam_lang else 'zh-CN'
        except Exception as e:
            logger.warning(f"获取 Steam 语言失败: {e}，使用默认中文")
            language = 'zh-CN'
    
    # 获取翻译映射
    value_trans = _VALUE_TRANSLATIONS.get(language)
    
    # 尝试根据前缀匹配
    if value_trans is None:
        lang_lower = language.lower()
        if lang_lower.startswith('zh'):
            if 'tw' in lang_lower:
                value_trans = _VALUE_TRANSLATIONS.get('zh-TW')
            # 简体中文不需要翻译
        elif lang_lower.startswith('ja'):
            value_trans = _VALUE_TRANSLATIONS.get('ja')
        elif lang_lower.startswith('en'):
            value_trans = _VALUE_TRANSLATIONS.get('en')
    
    # 如果不需要翻译（简体中文），直接返回原始配置
    if value_trans is None:
        return deepcopy(DEFAULT_CHARACTERS_CONFIG)
    
    def translate_value(val):
        """翻译值（仅翻译字符串类型）"""
        if isinstance(val, str):
            return value_trans.get(val, val)
        return val
    
    # 构建本地化配置（键名保持不变，只翻译值）
    result = {}
    
    # 本地化主人模板
    master = deepcopy(DEFAULT_MASTER_TEMPLATE)
    localized_master = {}
    for key, value in master.items():
        localized_master[key] = translate_value(value)
    result['主人'] = localized_master
    
    # 本地化猫娘模板
    catgirl_data = deepcopy(DEFAULT_LANLAN_TEMPLATE)
    localized_catgirl = {}
    for char_name, char_config in catgirl_data.items():
        localized_config = {}
        for key, value in char_config.items():
            localized_config[key] = translate_value(value)
        localized_catgirl[char_name] = localized_config
    result['猫娘'] = localized_catgirl
    
    result['当前猫娘'] = next(iter(catgirl_data.keys()), "")
    
    return result


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
    "mcpToken": "",
    "agentModelUrl": "",
    "agentModelId": "",
    "agentModelApiKey": "",
    "textGuardMaxLength": 400,
}

DEFAULT_USER_PREFERENCES = []

DEFAULT_VOICE_STORAGE = {}

# 默认API配置（供 utils.api_config_loader 作为回退选项使用）
DEFAULT_CORE_API_PROFILES = {
    'free': {
        'CORE_URL': "wss://lanlan.tech/core",
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
        'CORE_MODEL': "gpt-realtime-mini-2025-12-15",
    },
    'step': {
        'CORE_URL': "wss://api.stepfun.com/v1/realtime",
        'CORE_MODEL': "step-audio-2",
    },
    'gemini': {
        # Gemini 使用 google-genai SDK，而非原生 WebSocket
        'CORE_MODEL': "gemini-2.5-flash-native-audio-preview-12-2025",
    },
}

DEFAULT_ASSIST_API_PROFILES = {
    'free': {
        'OPENROUTER_URL': "https://lanlan.tech/text/v1",
        'CONVERSATION_MODEL' : "free-model" ,
        'SUMMARY_MODEL': "free-model",
        'CORRECTION_MODEL': "free-model",
        'EMOTION_MODEL': "free-model",
        'VISION_MODEL': "free-vision-model",
        'AGENT_MODEL': "free-model",
        'AUDIO_API_KEY': "free-access",
        'OPENROUTER_API_KEY': "free-access",
        'IS_FREE_VERSION': True,
    },
    'qwen': {
        'OPENROUTER_URL': "https://dashscope.aliyuncs.com/compatible-mode/v1",
        'CONVERSATION_MODEL' : "qwen3-235b-a22b-instruct-2507",
        'SUMMARY_MODEL': "qwen3-next-80b-a3b-instruct",
        'CORRECTION_MODEL': "qwen3-235b-a22b-instruct-2507",
        'EMOTION_MODEL': "qwen-flash",
        'VISION_MODEL': "qwen3-vl-plus-2025-09-23",
        'AGENT_MODEL': "qwen3.5-plus",
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
}

DEFAULT_ASSIST_API_KEY_FIELDS = {
    'qwen': 'ASSIST_API_KEY_QWEN',
    'openai': 'ASSIST_API_KEY_OPENAI',
    'glm': 'ASSIST_API_KEY_GLM',
    'step': 'ASSIST_API_KEY_STEP',
    'silicon': 'ASSIST_API_KEY_SILICON',
    'gemini': 'ASSIST_API_KEY_GEMINI',
    'kimi': 'ASSIST_API_KEY_KIMI',
}

DEFAULT_CONFIG_DATA = {
    'characters.json': DEFAULT_CHARACTERS_CONFIG,
    'core_config.json': DEFAULT_CORE_CONFIG,
    'user_preferences.json': DEFAULT_USER_PREFERENCES,
    'voice_storage.json': DEFAULT_VOICE_STORAGE,
}


TIME_ORIGINAL_TABLE_NAME = "time_indexed_original"
TIME_COMPRESSED_TABLE_NAME = "time_indexed_compressed"


# 不同模型供应商需要的 extra_body 格式
EXTRA_BODY_OPENAI = {"enable_thinking": False}
EXTRA_BODY_CLAUDE = {"thinking": {"type": "disabled"}}
EXTRA_BODY_GEMINI = {"extra_body": {"google": {"thinking_config": {"thinking_budget": 0}}}}
EXTRA_BODY_GEMINI_3 = {"extra_body": {"google": {"thinking_config": {"thinking_level": "low", "include_thoughts": False}}}}

# Agent 调用统一开关：是否加载 extra_body。
# 默认开启，配合 MODELS_EXTRA_BODY_MAP 实现默认关闭 thinking。
AGENT_USE_EXTRA_BODY = True

# 模型到 extra_body 的映射
MODELS_EXTRA_BODY_MAP = {
    # Qwen 系列
    "qwen-flash": EXTRA_BODY_OPENAI,
    "qwen3-vl-plus-2025-09-23": EXTRA_BODY_OPENAI,
    "qwen3-vl-plus": EXTRA_BODY_OPENAI,
    "qwen3-vl-flash": EXTRA_BODY_OPENAI,
    "qwen3.5-plus": EXTRA_BODY_OPENAI,
    "qwen-plus": EXTRA_BODY_OPENAI,
    "deepseek-ai/DeepSeek-V3.2": EXTRA_BODY_OPENAI,
    # GLM 系列
    "glm-4.5-air": EXTRA_BODY_CLAUDE,
    "glm-4.6v-flash": EXTRA_BODY_CLAUDE,
    "glm-4.7-flash": EXTRA_BODY_CLAUDE,
    "glm-4.6v": EXTRA_BODY_CLAUDE,
    # Silicon (zai-org) - 使用 Qwen 格式
    "zai-org/GLM-4.6V": EXTRA_BODY_OPENAI,
    # "free-model": {"tools":[{"type": "web_search", "function": {"description": "这个web_search用来搜索互联网的信息"}}]},
    "step-2-mini": {"tools":[{"type": "web_search", "function": {"description": "这个web_search用来搜索互联网的信息"}}]},
    # Gemini 系列
    "gemini-2.5-flash": EXTRA_BODY_GEMINI,  # 禁用 thinking
    "gemini-2.5-flash-lite": EXTRA_BODY_GEMINI,  # 禁用 thinking
    "gemini-3-flash-preview": EXTRA_BODY_GEMINI_3,  # 低级别 thinking
}


def get_extra_body(model: str) -> dict | None:
    """根据模型名称返回对应的 extra_body 配置。

    Args:
        model: 模型名称

    Returns:
        对应的 extra_body dict，如果模型不需要特殊配置则返回 None
    """
    if not model:
        return None
    if model in MODELS_EXTRA_BODY_MAP:
        return MODELS_EXTRA_BODY_MAP[model]
    return {}


def get_agent_extra_body(model: str) -> dict | None:
    """Return extra_body for Agent calls based on a single global switch."""
    if not AGENT_USE_EXTRA_BODY:
        return None
    return get_extra_body(model)


__all__ = [
    'APP_NAME',
    'GSV_VOICE_PREFIX',
    'CHARACTER_SYSTEM_RESERVED_FIELDS',
    'CHARACTER_WORKSHOP_RESERVED_FIELDS',
    'CHARACTER_RESERVED_FIELDS',
    'RESERVED_FIELD_SCHEMA',
    'LEGACY_FLAT_TO_RESERVED',
    'get_character_reserved_fields',
    'CONFIG_FILES',
    'DEFAULT_MASTER_TEMPLATE',
    'DEFAULT_LANLAN_TEMPLATE',
    'DEFAULT_VRM_LIGHTING',
    'VRM_LIGHTING_RANGES',
    'get_default_vrm_lighting',
    'DEFAULT_CHARACTERS_CONFIG',
    'get_localized_default_characters',
    'get_lanlan_prompt',
    'is_default_prompt',
    'DEFAULT_CORE_CONFIG',
    'DEFAULT_USER_PREFERENCES',
    'DEFAULT_VOICE_STORAGE',
    'DEFAULT_CONFIG_DATA',
    'DEFAULT_CORE_API_PROFILES',
    'DEFAULT_ASSIST_API_PROFILES',
    'DEFAULT_ASSIST_API_KEY_FIELDS',
    'TIME_ORIGINAL_TABLE_NAME',
    'TIME_COMPRESSED_TABLE_NAME',
    'MODELS_EXTRA_BODY_MAP',
    'get_extra_body',
    'get_agent_extra_body',
    'EXTRA_BODY_OPENAI',
    'EXTRA_BODY_CLAUDE',
    'EXTRA_BODY_GEMINI',
    'AGENT_USE_EXTRA_BODY',
    'MAIN_SERVER_PORT',
    'MEMORY_SERVER_PORT',
    'MONITOR_SERVER_PORT',
    'COMMENTER_SERVER_PORT',
    'TOOL_SERVER_PORT',
    'USER_PLUGIN_SERVER_PORT',
    'AGENT_MQ_PORT',
    'MAIN_AGENT_EVENT_PORT',
    'INSTANCE_ID',
    'TFLINK_UPLOAD_URL',
    'TFLINK_ALLOWED_HOSTS',
    'NATIVE_IMAGE_MIN_INTERVAL',
    'IMAGE_IDLE_RATE_MULTIPLIER',
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
    'DEFAULT_CONVERSATION_MODEL',
    'DEFAULT_SUMMARY_MODEL',
    'DEFAULT_CORRECTION_MODEL',
    'DEFAULT_EMOTION_MODEL',
    'DEFAULT_VISION_MODEL',
    'DEFAULT_AGENT_MODEL',
    'DEFAULT_REALTIME_MODEL',
    'DEFAULT_TTS_MODEL',
    # 用户自定义模型配置的 URL/API_KEY
    'DEFAULT_CONVERSATION_MODEL_URL',
    'DEFAULT_CONVERSATION_MODEL_API_KEY',
    'DEFAULT_SUMMARY_MODEL_URL',
    'DEFAULT_SUMMARY_MODEL_API_KEY',
    'DEFAULT_CORRECTION_MODEL_URL',
    'DEFAULT_CORRECTION_MODEL_API_KEY',
    'DEFAULT_EMOTION_MODEL_URL',
    'DEFAULT_EMOTION_MODEL_API_KEY',
    'DEFAULT_VISION_MODEL_URL',
    'DEFAULT_VISION_MODEL_API_KEY',
    'DEFAULT_REALTIME_MODEL_URL',
    'DEFAULT_REALTIME_MODEL_API_KEY',
    'DEFAULT_TTS_MODEL_URL',
    'DEFAULT_TTS_MODEL_API_KEY',
    'DEFAULT_AGENT_MODEL_URL',
    'DEFAULT_AGENT_MODEL_API_KEY',
]

