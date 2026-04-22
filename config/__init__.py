# -*- coding: utf-8 -*-
"""config 包对外暴露的配置常量。"""

from copy import deepcopy
import json
import logging
import os
import platform
import uuid
from types import MappingProxyType

from config.prompts_chara import lanlan_prompt, get_lanlan_prompt, is_default_prompt

# 应用程序名称与版本配置
APP_NAME = "N.E.K.O"
APP_VERSION = "0.7.4"
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
    "live3d_sub_type",
    "vrm",
    "vrm_animation",
    "lighting",
    "vrm_rotation",
    "live2d_item_id",
    "item_id",
    "idleAnimation",
    "idleAnimations",
    "mmd",
    "mmd_animation",
    "mmd_idle_animation",
    "mmd_idle_animations",
    "touch_set",
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
    "character_origin": {
        "source": str,
        "source_id": str,
        "display_name": str,
        "model_ref": str,
    },
    "avatar": {
        "model_type": str,
        "live3d_sub_type": str,
        "asset_source": str,
        "asset_source_id": str,
        "live2d": {
            "model_path": str,
        },
        "vrm": {
            "model_path": str,
            "animation": (str, dict, list, type(None)),
            "idle_animation": (str, list, type(None)),
            "lighting": (dict, type(None)),
            "cursor_follow": (dict, type(None)),
        },
        "mmd": {
            "model_path": str,
            "animation": (str, dict, list, type(None)),
            "idle_animation": (str, list, type(None)),
            "lighting": (dict, type(None)),
            "rendering": (dict, type(None)),
            "physics": (dict, type(None)),
            "cursor_follow": (dict, type(None)),
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
    "live3d_sub_type": ("avatar", "live3d_sub_type"),
    "live2d_item_id": ("avatar", "asset_source_id"),
    "item_id": ("avatar", "asset_source_id"),
    "live2d": ("avatar", "live2d", "model_path"),
    "vrm": ("avatar", "vrm", "model_path"),
    "vrm_animation": ("avatar", "vrm", "animation"),
    "idleAnimation": ("avatar", "vrm", "idle_animation"),
    "idleAnimations": ("avatar", "vrm", "idle_animation"),
    "lighting": ("avatar", "vrm", "lighting"),
    "mmd": ("avatar", "mmd", "model_path"),
    "mmd_animation": ("avatar", "mmd", "animation"),
    "mmd_idle_animation": ("avatar", "mmd", "idle_animation"),
    "mmd_idle_animations": ("avatar", "mmd", "idle_animation"),
}

# 从 Electron userData 目录读取端口覆盖配置（由前端端口设置窗口写入）
def _read_port_overrides() -> dict:
    try:
        system = platform.system()
        if system == "Windows":
            appdata = os.environ.get("APPDATA") or os.path.join(
                os.path.expanduser("~"), "AppData", "Roaming"
            )
            base = os.path.join(appdata, "N.E.K.O")
        elif system == "Darwin":
            base = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "N.E.K.O")
        else:
            base = os.path.join(
                os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
                "N.E.K.O",
            )
        port_file = os.path.join(base, "port_config.json")
        if os.path.exists(port_file):
            with open(port_file, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.debug("Failed to read port_config.json: %s", e, exc_info=True)
    return {}


_PORT_FILE_OVERRIDES = _read_port_overrides()


# 运行时端口覆盖支持：
# - 首选键：NEKO_<PORT_NAME>
# - 兼容键：<PORT_NAME>
# - 回退：Electron 前端写入的 port_config.json
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
    # 回退：从 Electron 前端写入的 port_config.json 读取
    override = _PORT_FILE_OVERRIDES.get(port_name)
    if override is not None:
        try:
            value = int(override)
            if 1 <= value <= 65535:
                return value
        except (TypeError, ValueError) as e:
            logger.warning(
                "Invalid port_config.json override for %s=%r: %s",
                port_name, override, e,
            )
    return default


def _read_list_env(var_name: str) -> tuple[str, ...]:
    for key in (f"NEKO_{var_name}", var_name):
        raw = os.getenv(key)
        if raw is None:
            continue

        values: list[str] = []
        for item in raw.split(","):
            value = item.strip().rstrip("/")
            if value:
                values.append(value)
        return tuple(dict.fromkeys(values))

    return ()


def _build_local_allowed_origins(port: int, *, extra_origins: tuple[str, ...] = ()) -> tuple[str, ...]:
    origins = [
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
        f"http://[::1]:{port}",
    ]
    origins.extend(extra_origins)
    return tuple(dict.fromkeys(origins))

# 服务器端口配置
MAIN_SERVER_PORT = _read_port_env("MAIN_SERVER_PORT", 48911)
MEMORY_SERVER_PORT = _read_port_env("MEMORY_SERVER_PORT", 48912)
MONITOR_SERVER_PORT = _read_port_env("MONITOR_SERVER_PORT", 48913)
COMMENTER_SERVER_PORT = _read_port_env("COMMENTER_SERVER_PORT", 48914)
TOOL_SERVER_PORT = _read_port_env("TOOL_SERVER_PORT", 48915)
USER_PLUGIN_SERVER_PORT = _read_port_env("USER_PLUGIN_SERVER_PORT", 48916)
AGENT_MQ_PORT = _read_port_env("AGENT_MQ_PORT", 48917)
MAIN_AGENT_EVENT_PORT = _read_port_env("MAIN_AGENT_EVENT_PORT", 48918)

# OpenFang Agent 执行后端端口 (由 Electron 并行启动，端口写入 port_config.json)
OPENFANG_PORT = _read_port_env("OPENFANG_PORT", 50051)
OPENFANG_BASE_URL = f"http://127.0.0.1:{OPENFANG_PORT}"

# 实例 ID：同一次启动的所有服务共享。
# launcher 会在拉起子进程前写入 NEKO_INSTANCE_ID 环境变量。
# 若源码直跑绕过 launcher，则每次导入使用随机回退值，确保 /health
# 始终返回有效 id。
INSTANCE_ID = os.getenv("NEKO_INSTANCE_ID") or uuid.uuid4().hex
AUTOSTART_CSRF_TOKEN = os.getenv("NEKO_AUTOSTART_CSRF_TOKEN") or INSTANCE_ID
AUTOSTART_ALLOWED_ORIGINS = _build_local_allowed_origins(
    MAIN_SERVER_PORT,
    extra_origins=_read_list_env("AUTOSTART_ALLOWED_ORIGINS"),
)

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
DEFAULT_REALTIME_MODEL = "qwen3-omni-flash-realtime"  # 全模态模型(语音+文字+图片)，与 api_providers.json 对齐
DEFAULT_TTS_MODEL = "qwen3-omni-flash-realtime"   # 与Realtime对应的TTS模型(Native TTS)，与 api_providers.json 对齐


CONFIG_FILES = [
    'characters.json',
    'core_config.json',
    'tutorial_prompt_config.json',
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
                    "idle_animation": [],
                    "lighting": None,
                },
                "mmd": {
                    "model_path": "",
                    "animation": None,
                    "idle_animation": [],
                },
            },
        },
    }
}

_DEFAULT_VRM_LIGHTING_MUTABLE = {
    # 与前端 vrm-core.js defaultLighting 保持一致
    "ambient": 0.83,  # HemisphereLight 强度
    "main": 1.91,     # 主光源强度
    "fill": 0.0,      # 补光强度（简化模式下禁用）
    "rim": 0.0,       # 轮廓光强度（简化模式下禁用，MToon 内建处理）
    "top": 0.0,       # 顶光强度（简化模式下禁用）
    "bottom": 0.0,    # 底光强度（简化模式下禁用）
    "exposure": 1.1,  # 曝光值
    "toneMapping": 7, # 色调映射类型 (7 = NeutralToneMapping)
    "outlineWidthScale": 1.0, # 描边粗细倍率
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
    'toneMapping': (0, 7),
    'outlineWidthScale': (0, 3.0),
}


def get_default_vrm_lighting() -> dict[str, float]:
    """获取默认VRM打光配置的副本"""
    return dict(DEFAULT_VRM_LIGHTING)


# ─── MMD 默认设置 ───
_DEFAULT_MMD_LIGHTING_MUTABLE = {
    "ambientIntensity": 3.0,
    "ambientColor": "#aaaaaa",
    "directionalIntensity": 2.0,
    "directionalColor": "#ffffff",
}

DEFAULT_MMD_LIGHTING = MappingProxyType(_DEFAULT_MMD_LIGHTING_MUTABLE)

MMD_LIGHTING_RANGES = {
    "ambientIntensity": (0, 10.0),
    "directionalIntensity": (0, 10.0),
}

_DEFAULT_MMD_RENDERING_MUTABLE = {
    "toneMapping": 7,
    "exposure": 1.0,
    "outline": True,
    "pixelRatio": 0,
}

DEFAULT_MMD_RENDERING = MappingProxyType(_DEFAULT_MMD_RENDERING_MUTABLE)

MMD_RENDERING_RANGES = {
    "toneMapping": (0, 7),
    "exposure": (0, 5.0),
    "pixelRatio": (0, 2.0),
}

_DEFAULT_MMD_PHYSICS_MUTABLE = {
    "enabled": True,
    "strength": 1.0,
}

DEFAULT_MMD_PHYSICS = MappingProxyType(_DEFAULT_MMD_PHYSICS_MUTABLE)

MMD_PHYSICS_RANGES = {
    "strength": (0.1, 2.0),
}

_DEFAULT_MMD_CURSOR_FOLLOW_MUTABLE = {
    "enabled": True,
    "headYaw": 30,
    "headPitch": 20,
    "smoothSpeed": 3.0,
}

DEFAULT_MMD_CURSOR_FOLLOW = MappingProxyType(_DEFAULT_MMD_CURSOR_FOLLOW_MUTABLE)

MMD_CURSOR_FOLLOW_RANGES = {
    "headYaw": (10, 50),
    "headPitch": (5, 30),
    "smoothSpeed": (1.0, 8.0),
}


def get_default_mmd_settings() -> dict:
    """获取默认MMD设置的副本"""
    return {
        "lighting": dict(DEFAULT_MMD_LIGHTING),
        "rendering": dict(DEFAULT_MMD_RENDERING),
        "physics": dict(DEFAULT_MMD_PHYSICS),
        "cursor_follow": dict(DEFAULT_MMD_CURSOR_FOLLOW),
    }

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
    'ru': {
        '哥哥': 'Братик',
        '男': 'Мужской',
        '女': 'Женский',
        'T酱, 小T': 'Тян-тян, малышка Т',
    },
    'es': {
        '哥哥': 'Hermano',
        '男': 'Masculino',
        '女': 'Femenino',
        'T酱, 小T': 'T-chan, Pequeña T',
    },
    'pt': {
        '哥哥': 'Irmão',
        '男': 'Masculino',
        '女': 'Feminino',
        'T酱, 小T': 'T-chan, Pequena T',
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
        elif lang_lower.startswith('ru'):
            value_trans = _VALUE_TRANSLATIONS.get('ru')
        elif lang_lower.startswith('es'):
            value_trans = _VALUE_TRANSLATIONS.get('es')
        elif lang_lower.startswith('pt'):
            value_trans = _VALUE_TRANSLATIONS.get('pt')

    # 如果不需要翻译显示字段（简体中文/韩语等），仍需本地化 system_prompt
    if value_trans is None:
        result = deepcopy(DEFAULT_CHARACTERS_CONFIG)
        for char_config in result.get('猫娘', {}).values():
            reserved = char_config.get('_reserved')
            if isinstance(reserved, dict) and 'system_prompt' in reserved:
                reserved['system_prompt'] = get_lanlan_prompt(language)
        return result
    
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
        reserved = localized_config.get('_reserved')
        if isinstance(reserved, dict) and 'system_prompt' in reserved:
            reserved['system_prompt'] = get_lanlan_prompt(language)
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
    "assistApiKeyQwenIntl": "",
    "assistApiKeyMinimax": "",
    "assistApiKeyClaude": "",
    "assistApiKeyGrok": "",
    "assistApiKeyDoubao": "",
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
        'OPENROUTER_URL': "https://www.lanlan.tech/text/v1",
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
        'CONVERSATION_MODEL' : "qwen3.6-plus",
        'SUMMARY_MODEL': "qwen3.6-plus",
        'CORRECTION_MODEL': "qwen3.6-plus",
        'EMOTION_MODEL': "qwen-flash",
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
}

DEFAULT_ASSIST_API_KEY_FIELDS = {
    'qwen': 'ASSIST_API_KEY_QWEN',
    'openai': 'ASSIST_API_KEY_OPENAI',
    'glm': 'ASSIST_API_KEY_GLM',
    'step': 'ASSIST_API_KEY_STEP',
    'silicon': 'ASSIST_API_KEY_SILICON',
    'gemini': 'ASSIST_API_KEY_GEMINI',
    'kimi': 'ASSIST_API_KEY_KIMI',
    'qwen_intl': 'ASSIST_API_KEY_QWEN_INTL',
    'minimax': 'ASSIST_API_KEY_MINIMAX',
    'claude': 'ASSIST_API_KEY_CLAUDE',
    'openrouter': 'ASSIST_API_KEY_OPENROUTER',
    'grok': 'ASSIST_API_KEY_GROK',
    'doubao': 'ASSIST_API_KEY_DOUBAO',
}

DEFAULT_TUTORIAL_PROMPT_CONFIG = {
    'min_prompt_foreground_ms': 15 * 1000,
    'later_cooldown_ms': 24 * 60 * 60 * 1000,
    'failure_cooldown_ms': 2 * 60 * 60 * 1000,
    'max_prompt_shows': 2,
}

DEFAULT_CONFIG_DATA = {
    'characters.json': DEFAULT_CHARACTERS_CONFIG,
    'core_config.json': DEFAULT_CORE_CONFIG,
    'tutorial_prompt_config.json': DEFAULT_TUTORIAL_PROMPT_CONFIG,
    'user_preferences.json': DEFAULT_USER_PREFERENCES,
    'voice_storage.json': DEFAULT_VOICE_STORAGE,
}


TIME_ORIGINAL_TABLE_NAME = "time_indexed_original"
TIME_COMPRESSED_TABLE_NAME = "time_indexed_compressed"


# Provider 相关配置已统一迁移至 config.providers, 此处仅 re-export 保持向后兼容
from config.providers import (  # noqa: E402, F401
    EXTRA_BODY_OPENAI,
    EXTRA_BODY_CLAUDE,
    EXTRA_BODY_GEMINI,
    AGENT_USE_EXTRA_BODY,
    MODELS_EXTRA_BODY_MAP,
    get_extra_body,
    get_agent_extra_body,
)


__all__ = [
    'APP_NAME',
    'APP_VERSION',
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
    'DEFAULT_MMD_LIGHTING',
    'MMD_LIGHTING_RANGES',
    'DEFAULT_MMD_RENDERING',
    'MMD_RENDERING_RANGES',
    'DEFAULT_MMD_PHYSICS',
    'MMD_PHYSICS_RANGES',
    'DEFAULT_MMD_CURSOR_FOLLOW',
    'MMD_CURSOR_FOLLOW_RANGES',
    'get_default_mmd_settings',
    'DEFAULT_CHARACTERS_CONFIG',
    'get_localized_default_characters',
    'get_lanlan_prompt',
    'is_default_prompt',
    'DEFAULT_CORE_CONFIG',
    'DEFAULT_TUTORIAL_PROMPT_CONFIG',
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
    'AUTOSTART_CSRF_TOKEN',
    'AUTOSTART_ALLOWED_ORIGINS',
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
    # OpenFang
    'OPENFANG_PORT',
    'OPENFANG_BASE_URL',
]
