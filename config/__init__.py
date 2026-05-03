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
APP_VERSION = "0.8.0"
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
    "persona_override": {
        "preset_id": str,
        "selected_at": str,
        "source": str,
        "prompt_guidance": str,
        "profile": dict,
    },
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
USER_PLUGIN_BASE = f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}"

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
# 注：以下退环境的常量已经从导出列表里删除（2026-04）：
#   * SETTING_PROPOSER_MODEL / SETTING_VERIFIER_MODEL —— 旧的 memory.settings
#     抽取/校验链路已被 evidence + reflection 取代，参见 memory/settings.py
#     顶部说明。
#   * ROUTER_MODEL —— 当年规划的"记忆路由模型"从未在代码里被读过；记忆路由
#     已经走 tier 化的 summary/correction，没有独立模型。
#   * SEMANTIC_MODEL —— "text-embedding-v4" 字面量没人用；嵌入服务走本地
#     ONNX（memory/embeddings.py 的 EmbeddingService），模型 id 由
#     profile_id+dim+quantization 拼出。
#   * RERANKER_MODEL —— 记忆 LLM 重排（memory/recall.py::MemoryRecallReranker）
#     按 tier="summary" 拿 api_config['model']，不再有 hardcoded 'qwen-plus'。
# 走 LLM 的 memory 子模块一律按 tier 拿 api_config['model']，不再有 hardcoded
# fallback；新增需求请加 tier，不要再加这种"全局默认模型字面量"。

# 其他模型配置（仅通过 config_manager 动态获取）
DEFAULT_CONVERSATION_MODEL = 'qwen-max'
DEFAULT_SUMMARY_MODEL = "qwen-plus"
DEFAULT_CORRECTION_MODEL = 'qwen-max'
DEFAULT_EMOTION_MODEL = 'qwen3.6-flash-2026-04-16'
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

# 默认 Live2D 模型名（不带后缀的目录/文件 stem）。
# DEFAULT_LANLAN_TEMPLATE.live2d.model_path 与 main_routers/characters_router.py
# 里"未设置 Live2D 模型时的回退"逻辑共享这个常量，避免两处漂移。新增/替换默认
# 模型只需要改这一处。
DEFAULT_LIVE2D_MODEL_NAME = "yui-origin"
DEFAULT_LIVE2D_MODEL_PATH = f"{DEFAULT_LIVE2D_MODEL_NAME}/{DEFAULT_LIVE2D_MODEL_NAME}.model3.json"

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
                    "model_path": DEFAULT_LIVE2D_MODEL_PATH,
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


# ── Memory evidence mechanism (docs/design/memory-evidence-rfc.md) ────
# 用户驱动的 evidence 计数器相关常量。所有评分计算都以 "净用户确认次数"
# 为单位（§3.1.2 偏离 task spec 原公式——去掉 importance 项）。阈值改值
# 会产生实际 behavior 变化，详见 RFC §6.5 pre-merge reviewer gates。

# §3.1.4 派生状态阈值
EVIDENCE_CONFIRMED_THRESHOLD = 1.0   # score ≥ 1 → confirmed
EVIDENCE_PROMOTED_THRESHOLD = 2.0    # score ≥ 2 → promoted
EVIDENCE_ARCHIVE_THRESHOLD = -2.0    # score ≤ -2 → archive_candidate

# 强力记忆 OFF（powerful_memory_enabled=False）时的 time-driven fallback 阈值。
# pre-RFC 行为：不靠 evidence_score，纯按 reflection 年龄推进 lifecycle，零
# LLM 成本。pre-RFC 用 3 天，但实测过激（"3 天没否认 != 用户认可"）；这里
# 拉到 7 天给用户更长窗口主动反驳。
WEAK_MEMORY_AUTO_CONFIRM_DAYS = 7   # pending → confirmed (按 created_at 计)
WEAK_MEMORY_AUTO_PROMOTE_DAYS = 7   # confirmed → promoted (按 confirmed_at 计)

# §3.5.3 归档相关（sub_zero_days 计数 + 分片大小上限）
EVIDENCE_ARCHIVE_DAYS = 14           # sub_zero 累计达此天数 → 真正归档
ARCHIVE_FILE_MAX_ENTRIES = 500       # 归档分片文件单文件最大 entry 数

# §3.1.5 ignored 扣分
IGNORED_REINFORCEMENT_DELTA = -0.2   # check_feedback ignored → reinforcement += delta

# §3.1.8 每种 signal 源的 delta 权重（v1.2.1：区分 direct vs indirect）
# 直接信号（用户显式回应 surfaced reflection 或命中负面关键词）权重 1.0；
# 间接信号（Stage-2 LLM 推断 fact 对 reflection 的关系）权重 0.5，避免
# LLM 误关联把 evidence 污染太快。
USER_FACT_REINFORCE_DELTA = 0.5      # Stage-2 reinforces（间接，银标准）
USER_FACT_NEGATE_DELTA = 1.0         # Stage-2 negates（否定即使间接也保留强权，
                                     # 因 LLM 判 negates 通常语义更明确）
USER_CONFIRM_DELTA = 1.0             # check_feedback confirmed（直接，金标准）
USER_REBUT_DELTA = 1.0               # check_feedback denied（直接）
USER_KEYWORD_REBUT_DELTA = 1.0       # 关键词 + LLM target 检查（直接 + 显式）

# user_fact reinforces 的 combo bonus：累计 count 超过阈值后，每条新信号额
# 外加 bonus，让"用户反复间接表达"的信号仍能追上"一次直接确认"的权重。
# 默认：前 2 条各 0.5；第 3 条起每条 0.5 + 0.5 bonus = 1.0。
USER_FACT_REINFORCE_COMBO_THRESHOLD = 2   # count > threshold 时激活
USER_FACT_REINFORCE_COMBO_BONUS = 0.5     # 超阈值后每条的额外加权

# §3.4.3 signal 抽取背景循环触发条件
EVIDENCE_SIGNAL_CHECK_ENABLED = True             # 独立开关
EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS = 10         # 累积 N 轮触发
EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES = 5           # 或空闲 N 分钟触发
EVIDENCE_SIGNAL_CHECK_INTERVAL_SECONDS = 40      # 轮询间隔（与 IDLE_CHECK_INTERVAL 对齐）
EVIDENCE_DETECT_SIGNALS_MAX_OBSERVATIONS = 30    # Stage-2 LLM rerank 后进 prompt 的 obs 上限（减少 NxM 配对决策点）

# §3.5 / §6.5 Gate 4：归档扫描背景循环间隔
# 1 小时一次：sub_zero_days 计数本身按"自然日"防抖（每天最多 +1），
# 所以扫描频率 ≥ 一天即可保证不漏；选 1h 是为了让"score 跌穿 0 当天"
# 也能尽快被抓住而非等到次日 00:00。低频远低于 evidence 信号循环
# (40s)，对 IO/CPU 影响可以忽略。
EVIDENCE_ARCHIVE_SWEEP_INTERVAL_SECONDS = 3600

# §3.6 render budget（PR-3 使用，此处先占位）
PERSONA_RENDER_TOKEN_BUDGET = 2000       # 非-protected persona 预算
REFLECTION_RENDER_TOKEN_BUDGET = 2000    # reflection 渲染预算（pending+confirmed 总和）
PERSONA_RENDER_ENCODING = "o200k_base"   # tiktoken encoding

# ========================================================================
# §3.7 LLM Context & Output Budget
# ------------------------------------------------------------------------
# 所有"会被拼进 LLM messages 的输入侧 component"和"LLM 输出侧 max_tokens"
# 都集中在这里。对应的设计文档：docs/design/llm-prompt-budget.md
#
# 命名约定：
#   *_MAX_TOKENS                       → tiktoken o200k_base token 数
#                                         （≈ 1.3-1.5 CJK char / 4 EN char）
#   *_TRIGGER_TOKENS                   → 触发某个动作的 token 阈值（不是硬上限）
#   *_MAX_ITEMS / *_MAX                → 条数（消息 / deque maxlen / list[-N:]）
#   *_MAX_CHARS                        → 字符数（仅遗留 char-based 流程用）
#   *_BYTES                            → 字节
#   *_MS                               → 毫秒
#
# 注释格式（每条常量）：
#   - "用途"：这个值会卡哪个 component
#   - "上游"：被 cap 的内容来自哪里（用户输入 / 外部 API / 内部计算）
#   - 设计依据 / 互动关系（如有）
#
# 已知"咎由自取"项（NOT capped by design）：
#   - 用户原话直接拼进 HumanMessage（omni_offline_client.py:413）
#   - OpenClaw magic intent user_text（用 1MB 输入做 80-token 分类，自找的）
#   - emotion 分析 user text
#   - bilibili knowledge_context（用户配置的知识库）
#   - 插件自定义 prompt / strategy 文件（由插件自行管理）
# 详见 docs/design/llm-prompt-budget.md "已知不 cap 项"。
# ========================================================================

# ---- Memory: recent history compression ----
RECENT_HISTORY_MAX_ITEMS = 10
"""压缩后保留的近期消息条数。
- 用途：CompressedRecentHistoryManager 把超过 compress_threshold 的旧消息
  压缩成 1 条 summary 后，原始消息列表保留最后 N 条。
- 上游：用户和 AI 的对话流水。
- 互动：和 RECENT_COMPRESS_THRESHOLD_ITEMS 配对——压缩后保留 N 条 +
  Stage-1 summary 1 条 = N+1 条进入下次压缩计数。"""

RECENT_COMPRESS_THRESHOLD_ITEMS = 15
"""触发 LLM 压缩的条数阈值。
- 用途：当某 lanlan 的 user_histories 累积到 > 此值时调一次
  compress_history。
- 上游：累积的对话条数。"""

RECENT_SUMMARY_MAX_TOKENS = 1000
"""Stage-1 压缩输出的 token 上限。
- 用途：Stage-1 LLM 把 N 条原始消息压缩成一段文本；如果输出
  > 此值则触发 Stage-2 进一步压缩（500 chars/words 硬截）。
- 上游：Stage-1 LLM 自由生成的摘要长度。
- 触发关系：output_tokens > 此值 → further_compress() 二次压缩。"""

RECENT_PER_MESSAGE_MAX_TOKENS = 500
"""压缩输入的单条 message token 上限。
- 用途：compress_history 把每条原始 message 拼进 prompt 前先做头尾保留
  截断（utils.tokenize.truncate_head_tail_tokens，head=tail=250）。
- 上游：用户/AI 的原始对话文本，正常一轮 30-500 token，长贴可能数 KB。
- 截断策略：保留头尾各 250 token，中段用 "…[省略中段]…" 替换。"""

# ---- Memory: reflection ----
REFLECTION_TEXT_MAX_TOKENS = 150
"""单条 reflection 文本的 soft cap。
- 用途：超过此值的 reflection 在保存时会剥离 ontology 字段
  (relation_type / temporal_scope) — 文本本身不丢。
- 上游：LLM 综合若干 fact 后输出的反思文本。"""

REFLECTION_SURFACE_TOP_K = 3
"""单次 surfacing 最多返回的反思条数。
- 用途：get_pending_reflections_for_check / followup 等查询接口的截断。
- 上游：满足 evidence_score≥0 且 cooldown 已过的候选反思集合。"""

REFLECTION_SYNTHESIS_FACTS_MAX = 20
"""单次 reflection synthesis 最多带入的 unabsorbed fact 数。
- 用途：_synthesize_reflections_locked 调用 LLM 前先按 importance/创建
  时间排序，截到此数。
- 上游：用户长期不"吸收"事实就会堆积；外循环（aget_unabsorbed_facts）
  当前没数量限制，所以这层是唯一保护。
- 设计依据：30 条 × 平均 50 token = 1500 token，留给 LLM 综合处理够用。"""

# ---- Memory: persona ----
PERSONA_MERGE_POOL_MAX_TOKENS = 4000
"""promote-merge 时同 entity persona+reflection 池总 token 上限。
- 用途：_allm_call_promotion_merge 把同 entity 的所有 confirmed/promoted
  persona 和 reflection 全拼进 prompt，本 cap 防止该池失控。
- 上游：同一 entity 长期累积的 persona/reflection。
- 注意：这条不复用 PERSONA_RENDER_TOKEN_BUDGET（render 是给主对话看的，
  merge 是给 promotion LLM 看的，需要更大的池才能做合并判断）。"""

PERSONA_CORRECTION_BATCH_LIMIT = 10
"""单次 persona corrections resolve 处理的 batch 大小。
- 用途：_resolve_corrections_locked 从 pending_corrections 队列取前 N
  条丢给 LLM 做对错判断，剩下的下一轮再处理。
- 上游：pending_corrections 队列。"""

# ---- Memory: recall ----
RECALL_COARSE_OVERSAMPLE = 3
"""vector coarse-rank 的过采样倍数。
- 用途：top_k = budget * 此值；coarse 阶段多取 3× 候选给 LLM rerank
  挑选。
- 上游：embedding 检索的 candidate pool。"""

RECALL_PER_CANDIDATE_MAX_TOKENS = 200
"""LLM rerank 输入的单条 candidate text 上限。
- 用途：_fine_rank 拼 candidates 前对每条 candidate.text 做截断。
- 上游：archived fact / observation 文本。"""

RECALL_CANDIDATES_TOTAL_MAX_TOKENS = 15000
"""LLM rerank 输入的 candidates 拼合后总 token 上限。
- 用途：候选数已 cap 但单条单独 cap 仍可能撑爆——这条是兜底。
- 上游：cap 之后的 candidates 列表序列化。
- 设计依据：理论上 budget*3 × per_candidate = 600*200 = 120k；25k 是
  实际安全值，超出时按尾部截断（保留高 score 的）。"""

# ---- Memory: evidence signal detection ----
EVIDENCE_PER_OBSERVATION_MAX_TOKENS = 200
"""Stage-2 signal detection 输入的单条 observation text 上限。
- 用途：_allm_detect_signals 拼 observations 前对每条 text 截断。
- 上游：archived fact / observation 文本。"""

EVIDENCE_OBSERVATIONS_TOTAL_MAX_TOKENS = 15000
"""Stage-2 signal detection observations 拼合后总 token 上限。
- 用途：兜底，防止单条上限 × 条数撑爆。
- 上游：cap 之后的 observations 列表序列化。"""

EVIDENCE_DETECT_SIGNALS_MAX_NEW_FACTS = 20
"""Stage-2 signal detection 单次 batch 处理的 new_facts 上限。
- 用途：_allm_detect_signals 入口对 new_facts 按 importance DESC 截到 N 条；
  超出部分留在 facts.json 中 `signal_processed=False`，下次 idle 维护循环
  再 drain 一批。
- 与 FACT_DEDUP_BATCH_LIMIT 同口径（LLM 在 N×M 配对决策时的舒适 batch
  ~20 条），避免 LLM 在 30+ 条 new_facts 上判失焦。
- 上游：Stage-1 LLM 抽取出来的 new facts 列表。"""

NEGATIVE_KEYWORD_CHECK_CONTEXT_ITEMS = 3
"""负面关键词检查带的 user message 上下文条数。
- 用途：memory_server._amaybe_trigger_negative_keyword_hook 取 user
  消息列表的最后 N 条作为 LLM 上下文。
- 上游：会话流水。"""

# ---- Agent: task results / history / plugin pipeline ----
AGENT_HISTORY_TURNS = 10
"""task_executor messages[-N:] 历史窗口。
- 用途：_extract_context_for_user_intent / _resolve_openclaw_sender_id
  等多个站点统一从最近 N 条消息里抽取 user 意图。
- 上游：core.py 维护的 conversation_history。"""

TASK_DETAIL_MAX_TOKENS = 200
"""任务详情字段（detail / desc）回流给 LLM 的 token 上限。
- 用途：agent_server._sanitize / result_parser._truncate / brain/
  task_executor 等多处 detail 字段统一档位。
- 上游：plugin 返回值 / ComputerUse 子任务结果 / OpenFang 输出。"""

TASK_SUMMARY_MAX_TOKENS = 400
"""任务摘要字段（summary）回流给 LLM 的 token 上限。
- 用途：_emit_task_result 的 summary 档位（比 detail 长）。
- 上游：result_parser 生成的自然语言摘要。"""

TASK_LARGE_DETAIL_MAX_TOKENS = 1000
"""任务大详情字段回流给前端 HUD 的 token 上限。
- 用途：_emit_task_result 的 detail 字段；前端展示用，不直接进 LLM。
- 上游：plugin 完整结构化输出。"""

TASK_ERROR_MAX_TOKENS = 350
"""任务错误消息字段的 token 上限。
- 用途：_emit_task_result 的 error 档位。
- 上游：异常 stack / API 错误响应。"""

# ---- Agent: defensive char-caps (NOT token caps) ----
# 下面这些是"防御性 char-cap"——在异常文本 / cancel reason / plugin reply
# 流入下游字段（summary / detail / error_message / tracker.detail / 前端
# notification）之前的硬截。
#
# 为什么是 char 而不是 token：
# - LLM-facing 字段（summary / detail / error_message / tracker.detail）
#   真正的 prompt budget 在 _emit_task_result 内部用 TASK_*_MAX_TOKENS
#   二次截断；外层 char-cap 只是为了避免把 MB 级原始字符串直接喂给
#   tiktoken（编码本身就很慢）。
# - 前端 agent_notification 字段是 toast / 错误面板展示，不进 LLM；
#   token 精度无业务意义。
#
# 常量值分组（按"是否进 LLM 上下文"切）：
#   进上下文（防御性 char-cap，下游再走 token-cap）：
#     - EXCEPTION_TEXT_MAX_CHARS         = 500  → summary 字段、_exc_text
#                                                / cancel_msg 等共享变量
#     - ERROR_MESSAGE_MAX_CHARS          = 300  → error_message 字段直接 cap
#     - TASK_TRACKER_DETAIL_MAX_CHARS    = 300  → tracker.record_completed
#                                                .detail 字段（inject 时进
#                                                LLM 的 system 消息）
#     - TASK_TRACKER_INJECT_DETAIL_MAX_CHARS = 300 → tracker.inject 渲染
#                                                detail 写进 LLM prompt
#                                                的最终一次 char-cap
#   不进上下文（前端展示）：
#     - USER_NOTIFICATION_REASON_MAX_CHARS = 200  → agent_notification.text
#     - USER_NOTIFICATION_ERROR_MAX_CHARS  = 500  → agent_notification
#                                                  .error_message

EXCEPTION_TEXT_MAX_CHARS = 500
"""LLM-facing summary 字段 / 共享异常变量的防御性 char-cap。
- 用途：
  1. summary=reply[:N] / summary=_exc_text 等直接对 summary 字段的 char-cap。
  2. cancel_msg = str(e)[:N] / _exc_text = str(e)[:N] 这类"一份截断给
     summary/detail/error_message 三个字段共用"的局部变量。
- 为什么是 char：tracebacks / API 错误体可能高达 MB，先 char-cap 再让
  _emit_task_result 内部用 TASK_SUMMARY_MAX_TOKENS / TASK_LARGE_DETAIL_
  MAX_TOKENS / TASK_ERROR_MAX_TOKENS 做精确 token 截，省去对整个原始
  字符串做 tiktoken 编码的开销。
- 与 ERROR_MESSAGE_MAX_CHARS 的关系：单纯 error_message 字段直接 char-cap
  统一走 300（更紧）；本常量是变量级 / summary 级，500 给 summary 留点
  余量；当 cancel_msg / _exc_text 这类已经 500 的变量再赋给 error_message
  时，沿用变量截断结果，不再做二次截。"""

ERROR_MESSAGE_MAX_CHARS = 300
"""LLM-facing error_message 字段直接 char-cap。
- 用途：error_message=str(e)[:N] / error_message=str(nk_result.get("error"))[:N]
  这类直接对 error_message 字段的 char-cap（没有走中间共享变量的那种）。
- 为什么是 char：和 EXCEPTION_TEXT_MAX_CHARS 同样是给下游 _emit_task_result
  内部 TASK_ERROR_MAX_TOKENS（350 token）做防御性预处理。
- 为什么和 EXCEPTION_TEXT_MAX_CHARS 数值不同：error_message 字段下游 token
  budget 比 summary 紧（350 vs 400），300 char 能避免给 token-cap 留无效
  空间，同时与 TASK_TRACKER_*_MAX_CHARS 对齐。"""

TASK_TRACKER_DETAIL_MAX_CHARS = 300
"""AgentTaskTracker.record_completed 的 detail 字段 char-cap。
- 用途：失败 / 取消路径上 detail=str(e)[:N] / detail=cancel_msg[:N] /
  detail=reply[:N] 等给 tracker 的 detail 字段做硬截。
- 为什么是 char：tracker.detail 看似只进内存日志，但 AgentTaskTracker.
  inject() 会把整段记录拼成 system 消息塞进 task_executor 的下次决策
  messages（agent_server.py 中的 _task_tracker.inject(messages, lanlan)），
  所以这条字段实际上会进 LLM 上下文。三层防御链路：
    1. 入站 char-cap = 本常量（300）
    2. record_completed 内部 _tt(detail, TASK_DETAIL_MAX_TOKENS)（200 token）
    3. inject 渲染时再 char-cap = TASK_TRACKER_INJECT_DETAIL_MAX_CHARS（300）
- 注意：成功路径上 OpenFang 已用 _tt(_track_detail, TASK_DETAIL_MAX_TOKENS)
  走 token-cap，那条路径不在本常量管辖范围。"""

TASK_TRACKER_INJECT_DETAIL_MAX_CHARS = 300
"""AgentTaskTracker.inject 渲染 detail 进 LLM system 消息时的最终 char-cap。
- 用途：agent_server.AgentTaskTracker.inject 内部 _sanitize(detail, N) 在把
  每条 record 的 detail 拼进 [AGENT TASK TRACKING …] system 消息前做的
  最后一次 char-cap。
- 为什么是 char：进 LLM prompt 前的硬上限——已经被入站 char-cap +
  record_completed 内 token-cap 处理过；这里再 char-cap 是渲染时为了让
  单行长度可控。"""

USER_NOTIFICATION_REASON_MAX_CHARS = 200
"""agent_notification.text 内嵌 reason 片段的 char-cap。
- 用途：DirectTaskExecutor 评估失败时把 reason 拼进面向前端 toast 的
  text 字段（"⚠️ Agent评估失败: {reason[:N]}"）。
- 为什么是 char：toast 容量小、不进 LLM。"""

USER_NOTIFICATION_ERROR_MAX_CHARS = 500
"""agent_notification.error_message 字段 char-cap（前端展示，不进 LLM）。
- 用途：main_server EventBus 在转发 agent_notification 给前端 WS 时对
  error_message 做的硬截；agent_server 评估失败 / 后台异常时也按此
  cap reason / str(e) 写进 agent_notification.error_message。
- 为什么是 char：纯前端展示字段，不进 LLM；和 USER_NOTIFICATION_REASON_
  MAX_CHARS 数值不同（错误详情比 toast 文本宽容）。
- 注意：本常量服务的是"前端 agent_notification 通道"的 error_message，
  和 LLM-facing 的 ERROR_MESSAGE_MAX_CHARS（300）不是一回事——前者直
  接灌 WS 帧给浏览器，后者是 _emit_task_result 字段经 callback 进
  LLM prompt。"""

AGENT_TASK_TRACKER_MAX_RECORDS = 50
"""AgentTaskTracker 最多保留的任务执行记录数。
- 用途：deque-like 结构 maxlen，供 analyzer 去重 / 上下文交错排序。
- 上游：分发出去的 agent 任务数。"""

AGENT_RECENT_CTX_PER_ITEM_TOKENS = 400
"""task_executor _sanitize_recent_context 单条上限。
- 用途：从 conversation 抽取最近 user/assistant 消息，每条进 prompt
  前先 truncate 到此值。
- 上游：会话流水。"""

AGENT_RECENT_CTX_TOTAL_TOKENS = 1000
"""task_executor _sanitize_recent_context 总和上限。
- 用途：累计 token 超过此值停止收集后续消息（partial last item dropped）。
- 上游：cap 后的 4 条 messages 序列化。"""

AGENT_PLUGIN_DESC_BM25_THRESHOLD = 3000
"""plugins_desc 触发 stage1 BM25 + LLM coarse-screen 并行的 token 阈值。
- 用途：≤ 此值直接 stage2；> 此值跑两阶段筛选。
- 上游：所有可用 plugin 的 description 拼合。"""

AGENT_PLUGIN_SHORTDESC_MAX_TOKENS = 150
"""插件短描述（生成阶段）的 max_completion_tokens。
- 用途：_ensure_short_descriptions LLM 生成 short_description 输出的上限。
- 上游：LLM 输出（不是输入）。"""

AGENT_PLUGIN_COARSE_MAX_TOKENS = 300
"""插件粗筛 stage1 LLM 的 max_completion_tokens。
- 用途：返回选中的 plugin id 列表。
- 上游：LLM 输出。"""

AGENT_UNIFIED_ASSESS_MAX_TOKENS = 600
"""Unified channel assessment 的 max_completion_tokens。
- 用途：判断走哪条执行通道（QwenPaw / OpenFang / BrowserUse / ComputerUse）。
- 上游：LLM 输出。"""

AGENT_PLUGIN_FULL_MAX_TOKENS = 500
"""插件完整评估 stage2 LLM 的 max_completion_tokens。
- 用途：返回 plugin_id + plugin_args + reason。
- 上游：LLM 输出。"""

PLUGIN_INPUT_DESC_MAX_TOKENS = 1000
"""_ensure_short_descriptions 输入的 plugin manifest description 上限。
- 用途：生成 short_description 时把原始 description 截断后再送入 prompt
  （防止恶意/超大 plugin 喂超长 manifest）。
- 上游：plugin 注册时的 manifest description 字段。"""

# ---- Agent: ComputerUse / OpenClaw ----
COMPUTER_USE_MAX_TOKENS = 6000
"""ComputerUse 主调用的 max_completion_tokens。
- 用途：VLM 生成 thought + action + code 的输出上限。
- 上游：LLM 输出。"""

LLM_PING_MAX_TOKENS = 5
"""LLM 健康检查的 max_completion_tokens。
- 用途：连通性 ping 仅返回 "ok" 即可。
- 上游：LLM 输出。"""

OPENCLAW_MAGIC_INTENT_MAX_TOKENS = 80
"""OpenClaw magic intent 分类的 max_completion_tokens。
- 用途：判断用户输入是 /clear /new /stop /daemon-approve 中的哪个。
- 上游：LLM 输出固定 JSON ~15 token，80 留 5x 安全垫。"""

# ---- Main: session / avatar / omni ----
SESSION_ARCHIVE_TRIGGER_TOKENS = 5000
"""会话历史归档触发的累计 token 总量。
- 用途：core.py 主循环每 turn-end 后检查；超过则置
  is_preparing_new_session=True，触发记忆压缩 + 新会话准备。
- 上游：当前会话的 conversation_history。
- 限制：仅对 OmniOfflineClient 路径生效（realtime 不维护历史，走轮次触发）。
- 设计依据：用户一轮平均 ~150 token + AI 一轮平均 ~400 token =
  ~550/轮；5000/550 ≈ 9 轮触发归档（与 SESSION_TURN_THRESHOLD 对齐）。"""

SESSION_TURN_THRESHOLD = 10
"""触发会话归档的用户轮次阈值。
- 用途：core.py:_session_turn_count >= 此值触发新会话准备（与
  SESSION_ARCHIVE_TRIGGER_TOKENS 是 OR 关系，任一满足即触发）。
- 计数语义：仅用户输入计数（AI 回复不算），见 core.py:980。
- 设计依据：~10 轮约对应 5500 token 总量，跟 token 触发对齐。"""

AVATAR_INTERACTION_DEDUPE_MAX_ITEMS = 32
"""_recent_avatar_interaction_ids deque maxlen。
- 用途：去重已处理的 avatar 交互 ID。
- 上游：UI/avatar 端的交互事件序列。"""

AVATAR_INTERACTION_DEDUPE_WINDOW_MS = 8000
"""avatar 交互去重的时间窗口。
- 用途：cross_server _should_persist_avatar_interaction_memory 在此窗口
  内同 key 的交互不重复持久化。
- 上游：UI 端的交互时间戳。"""

AVATAR_INTERACTION_CONTEXT_MAX_TOKENS = 80
"""avatar 交互文本上下文的 token 上限。
- 用途：_sanitize_avatar_interaction_text_context 截断后写进 LLM
  prompt 作为 avatar 触发的现场上下文。
- 上游：avatar 端透传的现场文本片段。"""

PENDING_USER_IMAGES_MAX = 3
"""cross_server pending_user_images 保留的最近图片数。
- 用途：del pending_user_images[:-N] 滑动窗口。
- 上游：用户上传的图片队列。"""

OMNI_RECENT_RESPONSES_MAX = 3
"""omni_offline / omni_realtime 最近 AI 回复轮数。
- 用途：_recent_responses 列表 pop(0) 维护的滑动窗口；用于重复检测
  (_check_repetition)。
- 上游：当前会话内的 AI 历史回复。"""

OMNI_WS_FRAME_LIMIT_BYTES = 250_000
"""omni_realtime WebSocket 帧大小安全阈值。
- 用途：发送前检查 payload size，超过则拒绝（低于 256KB 服务器上限）。
- 上游：序列化后的 WS 帧字节数（不是 token）。"""

# ---- Main: proactive search & emotion ----
PROACTIVE_PHASE1_FETCH_PER_SOURCE = 10
"""Phase 1 每个信息源固定抓取条数。
- 用途：fetch_news_content / fetch_video_content 等的 limit 参数统一值。
- 上游：外部 web/news/video 抓取结果。"""

PROACTIVE_PHASE1_TOTAL_TOPICS = 12
"""Phase 1 输入给筛选 LLM 的候选话题总数。
- 用途：从所有 source 合并后去重，截到此数后送 LLM 筛选。
- 上游：cap 后的 fetch 结果汇总。
- 设计依据：原值 20。早期 external 是主要信号源，候选池开得很大。
  Phase 2 引入 vision / music / meme / reminiscence 等并行通道后，
  external 的相对权重下降——筛选 LLM 多看 8 条边际候选无助于挑出更
  好的 top-1，反而让 Phase 1 prompt 一次跑过 2k tokens 上限。下调到
  12 仍给筛选 LLM 充分多样性，且单次调用 token 减半左右。"""

PROACTIVE_EXTERNAL_PER_ITEM_MAX_TOKENS = 200
"""Phase 2 外部内容（news/video/social/meme 等）单条 token 上限。
- 用途：build_phase2_external_section 拼 system prompt 前对每条 web
  content 做截断。
- 上游：外部 API 返回的 title + source + url + 摘要。
- 设计依据：单条 200 token 已足够 LLM 知道"这是什么"，详细信息靠
  Phase 2 LLM 自行总结。"""

PROACTIVE_EXTERNAL_TOTAL_MAX_TOKENS = 1500
"""Phase 1 外部候选拼合后的总 token 上限（Phase 2 实际只看 top-1）。
- 用途：所有 selected web items 序列化后，再做一次总和截断。
- 上游：cap 后的 external_section 文本。
- 设计依据：跟 PROACTIVE_PHASE1_TOTAL_TOPICS 同步下调。原值 2000 是
  20 候选 × 200 token 留的硬顶；候选数收到 12 之后，1500 已留出
  ~250 token 富余，超出仍兜底截断。Phase 2 generate prompt 实际只
  把 Phase 1 选中的单条 web_topic（~50-100 token）放进
  external_section，本字段约束的是 Phase 1 的 prompt 大小。"""

PROACTIVE_PHASE2_OUTPUT_MAX_TOKENS = 300
"""Phase 2 流式输出的 abort fence。
- 用途：流式生成超过此值则 abort（防止 LLM 跑飞写小作文）。
- 上游：LLM 输出（不是输入）。"""

PROACTIVE_PHASE2_GENERATE_MAX_TOKENS = int(PROACTIVE_PHASE2_OUTPUT_MAX_TOKENS * 1.5)
"""Phase 2 主流式生成的 SDK 端 max_completion_tokens。
- 用途：_make_llm 默认值，由 Phase 2 stream 主调用使用。
- 设计依据：应用层在 [main_routers/system_router.py] 流式中段
  `count_tokens(full_text + chunk) > PROACTIVE_PHASE2_OUTPUT_MAX_TOKENS`
  硬 abort，所以 SDK 端再大也用不上。设成 abort fence × 1.5 留 50%
  bandwidth 给 token 计数误差和 prompt-cache flush 边界。"""

PROACTIVE_PHASE1_UNIFIED_MAX_TOKENS = 1024
"""Phase 1 unified 筛选 LLM 的 max_completion_tokens。
- 用途：_llm_call_with_retry 默认值，由 Phase 1 unified prompt 使用
  （web 筛选 + music 关键词 + meme 关键词单次合并调用）。
- 上游：LLM 输出 JSON（话题 ID 列表 + 简短理由）。"""

PROACTIVE_CHAT_HISTORY_MAX = 10
"""_proactive_chat_history deque maxlen。
- 用途：每个 lanlan 维护的最近主动搭话记录，用于 1h 内去重。
- 上游：proactive 触发的搭话事件。"""

PROACTIVE_SOURCE_HARD_SKIP_SECONDS = 5 * 3600
"""主动搭话 source 衰减历史的硬窗口（p_skip=1.0）。
- 用途：5h 内同一 URL 必跳，超过后按 kind 半衰期指数衰减。
- 上游：system_router._should_skip_source。"""

PROACTIVE_SOURCE_HALF_LIFE_BY_KIND: dict[str, float] = {
    'web': 3 * 86400.0,
    'image': 3 * 86400.0,
    'music': 1 * 86400.0,
}
"""硬窗口外按 kind 各自的 p_skip 半衰期（秒）。
- web/image：3d（新闻 / 表情包重复成本相对低，慢慢复活）
- music：1d（曲库小，更频繁轮转）
- 用途：system_router._half_life_for 查表。"""

PROACTIVE_SOURCE_HALF_LIFE_DEFAULT = 3 * 86400.0
"""未在 _BY_KIND 命中时的兜底半衰期。"""

PROACTIVE_SOURCE_FORGET_P = 0.05
"""p_skip 跌破此阈值即从衰减历史中遗忘（让文件体积自然有界）。
- 当前参数下：music ≈ 4.5d 后遗忘，web/image ≈ 13d 后遗忘。"""

EMOTION_ANALYSIS_MAX_TOKENS = 40
"""情感分析 LLM 的 max_completion_tokens。
- 用途：返回情感标签 + score 等短输出。
- 上游：LLM 输出（注意：Gemini 可能返回 markdown 包裹，留 40 token 余量）。"""

# ---- Plugin platform ----
PLUGIN_USER_CONTEXT_MAX_ITEMS = 200
"""每用户上下文 deque maxlen（plugin core state）。
- 用途：plugin 跨调用维护的 per-user 上下文条数上限。
- 上游：用户与 plugin 的交互事件序列。"""

# ---- Utils: translation / vision / connectivity test / MCP ----
TRANSLATION_OUTPUT_MAX_TOKENS = 1000
"""翻译 LLM 的 max_completion_tokens。
- 用途：单 chunk 翻译输出上限。
- 上游：LLM 输出。"""

TRANSLATION_CHUNK_MAX_CHARS_SHORT = 5000
"""翻译短文本路径的分块字符数上限（chars，遗留 char-based）。
- 用途：单次翻译调用的输入字符数；长文本被切成多块串行翻译。
- 上游：用户/系统传入的待翻译原文。"""

TRANSLATION_CHUNK_MAX_CHARS_LONG = 15000
"""翻译长文本路径的分块字符数上限（chars，遗留 char-based）。
- 用途：长文本翻译路径下的更大 chunk size。
- 上游：用户/系统传入的待翻译原文。"""

VISION_ANALYSIS_MAX_TOKENS = 500
"""截图 / 图像分析 LLM 的 max_completion_tokens。
- 用途：返回画面描述。
- 上游：LLM 输出。"""

CONNECTIVITY_TEST_MAX_TOKENS = 1
"""provider 连通性测试请求的 max_completion_tokens。
- 用途：仅测试 API 可达，最小请求。
- 上游：LLM 输出。"""

MCP_TOOL_RESULT_MAX_TOKENS = 1000
"""MCP 工具结果回流给 LLM 前的 token 上限。
- 用途：mcp_adapter._truncate_llm_text 默认 limit；超过则截断 + "..."。
- 上游：MCP server 返回的工具执行结果。"""

# §3.9 merge-on-promote 节流（PR-3 使用）
EVIDENCE_PROMOTE_RETRY_BACKOFF_MINUTES = 30      # 连续失败节流窗口
EVIDENCE_PROMOTE_MAX_RETRIES = 5                 # 死信阈值

# §6.5 pre-merge reviewer gates —— 草案值，reviewer 敲定前保留
# Gate 1: 半衰期（§3.5.2）
EVIDENCE_REIN_HALF_LIFE_DAYS = 30        # reinforcement 半衰期
EVIDENCE_DISP_HALF_LIFE_DAYS = 180       # disputation 半衰期（longer than rein）

# Gate 2: reflection 合成 context 量（§3.4.3 阶段 2）
REFLECTION_SYNTHESIS_CONTEXT_ABSORBED_COUNT = 10   # 最近 N 条 absorbed fact 作参考
REFLECTION_SYNTHESIS_CONTEXT_ABSORBED_DAYS = 14    # 且在 N 天内

# Gate 3: LLM tier 选型（候选见 RFC §6.5 Gate 3 表）
# "summary" = qwen-plus 级；"correction" = qwen-max 级；"emotion" = qwen-flash 级
EVIDENCE_EXTRACT_FACTS_MODEL_TIER = "summary"       # Stage-1 抽 fact
EVIDENCE_DETECT_SIGNALS_MODEL_TIER = "summary"      # Stage-2 判 signal 映射
EVIDENCE_NEGATIVE_TARGET_MODEL_TIER = "emotion"     # 关键词二次判定（延迟敏感）
EVIDENCE_PROMOTION_MERGE_MODEL_TIER = "correction"  # Promote 合并决策


# memory-enhancements P2: vector hybrid retrieval (memory/embeddings.py).
# Master kill switch + auto-resolve hints. The service degrades to no-op
# under any of: VECTORS_ENABLED=False / RAM < min / no onnxruntime / no
# model file. See memory/embeddings.py docstring for the full fallback
# matrix. Defaults are tuned so the feature is opt-out at the install
# level (drop the model file → on; remove it → off) without a config edit.
VECTORS_ENABLED = True                       # master kill switch
VECTORS_EMBEDDING_DIM = "auto"               # "auto" | 32/64/128/256/512/768
VECTORS_QUANTIZATION = "auto"                # "auto" | "int8" | "fp32" (fp32 needs model.onnx on disk)
VECTORS_MIN_RAM_GB = 4.0                     # below this → disabled regardless
VECTORS_MODEL_PROFILE_ID = "local-text-retrieval-v1"  # anonymous profile id + local model folder
# Warmup: the ONNX session (~150 MB unpack) loads on first triggering
# event after startup. The warmup task waits up to this many seconds
# after startup OR until first /process call, whichever comes first.
VECTORS_WARMUP_DELAY_SECONDS = 30


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
    'USER_PLUGIN_BASE',
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
    # ROUTER_MODEL / SEMANTIC_MODEL / RERANKER_MODEL / SETTING_PROPOSER_MODEL /
    # SETTING_VERIFIER_MODEL 于 2026-04 全部退环境（无 Python 调用方），见
    # memory/settings.py 顶部说明 + 上方常量块的注释。新增需求走 tier 化路径。
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
    # Memory evidence mechanism (RFC: docs/design/memory-evidence-rfc.md)
    'EVIDENCE_CONFIRMED_THRESHOLD',
    'EVIDENCE_PROMOTED_THRESHOLD',
    'WEAK_MEMORY_AUTO_CONFIRM_DAYS',
    'WEAK_MEMORY_AUTO_PROMOTE_DAYS',
    'EVIDENCE_ARCHIVE_THRESHOLD',
    'EVIDENCE_ARCHIVE_DAYS',
    'ARCHIVE_FILE_MAX_ENTRIES',
    'IGNORED_REINFORCEMENT_DELTA',
    'USER_FACT_REINFORCE_DELTA',
    'USER_FACT_NEGATE_DELTA',
    'USER_CONFIRM_DELTA',
    'USER_REBUT_DELTA',
    'USER_KEYWORD_REBUT_DELTA',
    'USER_FACT_REINFORCE_COMBO_THRESHOLD',
    'USER_FACT_REINFORCE_COMBO_BONUS',
    'EVIDENCE_SIGNAL_CHECK_ENABLED',
    'EVIDENCE_SIGNAL_CHECK_EVERY_N_TURNS',
    'EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES',
    'EVIDENCE_SIGNAL_CHECK_INTERVAL_SECONDS',
    'EVIDENCE_DETECT_SIGNALS_MAX_OBSERVATIONS',
    'EVIDENCE_ARCHIVE_SWEEP_INTERVAL_SECONDS',
    'PERSONA_RENDER_TOKEN_BUDGET',
    'REFLECTION_RENDER_TOKEN_BUDGET',
    'PERSONA_RENDER_ENCODING',
    # §3.7 LLM Context & Output Budget
    'RECENT_HISTORY_MAX_ITEMS',
    'RECENT_COMPRESS_THRESHOLD_ITEMS',
    'RECENT_SUMMARY_MAX_TOKENS',
    'RECENT_PER_MESSAGE_MAX_TOKENS',
    'REFLECTION_TEXT_MAX_TOKENS',
    'REFLECTION_SURFACE_TOP_K',
    'REFLECTION_SYNTHESIS_FACTS_MAX',
    'PERSONA_MERGE_POOL_MAX_TOKENS',
    'PERSONA_CORRECTION_BATCH_LIMIT',
    'RECALL_COARSE_OVERSAMPLE',
    'RECALL_PER_CANDIDATE_MAX_TOKENS',
    'RECALL_CANDIDATES_TOTAL_MAX_TOKENS',
    'EVIDENCE_PER_OBSERVATION_MAX_TOKENS',
    'EVIDENCE_OBSERVATIONS_TOTAL_MAX_TOKENS',
    'EVIDENCE_DETECT_SIGNALS_MAX_NEW_FACTS',
    'NEGATIVE_KEYWORD_CHECK_CONTEXT_ITEMS',
    'AGENT_HISTORY_TURNS',
    'TASK_DETAIL_MAX_TOKENS',
    'TASK_SUMMARY_MAX_TOKENS',
    'TASK_LARGE_DETAIL_MAX_TOKENS',
    'TASK_ERROR_MAX_TOKENS',
    'AGENT_TASK_TRACKER_MAX_RECORDS',
    'AGENT_RECENT_CTX_PER_ITEM_TOKENS',
    'AGENT_RECENT_CTX_TOTAL_TOKENS',
    'AGENT_PLUGIN_DESC_BM25_THRESHOLD',
    'AGENT_PLUGIN_SHORTDESC_MAX_TOKENS',
    'AGENT_PLUGIN_COARSE_MAX_TOKENS',
    'AGENT_UNIFIED_ASSESS_MAX_TOKENS',
    'AGENT_PLUGIN_FULL_MAX_TOKENS',
    'PLUGIN_INPUT_DESC_MAX_TOKENS',
    'COMPUTER_USE_MAX_TOKENS',
    'LLM_PING_MAX_TOKENS',
    'OPENCLAW_MAGIC_INTENT_MAX_TOKENS',
    'SESSION_ARCHIVE_TRIGGER_TOKENS',
    'SESSION_TURN_THRESHOLD',
    'AVATAR_INTERACTION_DEDUPE_MAX_ITEMS',
    'AVATAR_INTERACTION_DEDUPE_WINDOW_MS',
    'AVATAR_INTERACTION_CONTEXT_MAX_TOKENS',
    'PENDING_USER_IMAGES_MAX',
    'OMNI_RECENT_RESPONSES_MAX',
    'OMNI_WS_FRAME_LIMIT_BYTES',
    'PROACTIVE_PHASE1_FETCH_PER_SOURCE',
    'PROACTIVE_PHASE1_TOTAL_TOPICS',
    'PROACTIVE_EXTERNAL_PER_ITEM_MAX_TOKENS',
    'PROACTIVE_EXTERNAL_TOTAL_MAX_TOKENS',
    'PROACTIVE_PHASE2_OUTPUT_MAX_TOKENS',
    'PROACTIVE_PHASE2_GENERATE_MAX_TOKENS',
    'PROACTIVE_PHASE1_UNIFIED_MAX_TOKENS',
    'PROACTIVE_CHAT_HISTORY_MAX',
    'PROACTIVE_SOURCE_HARD_SKIP_SECONDS',
    'PROACTIVE_SOURCE_HALF_LIFE_BY_KIND',
    'PROACTIVE_SOURCE_HALF_LIFE_DEFAULT',
    'PROACTIVE_SOURCE_FORGET_P',
    'EMOTION_ANALYSIS_MAX_TOKENS',
    'PLUGIN_USER_CONTEXT_MAX_ITEMS',
    'TRANSLATION_OUTPUT_MAX_TOKENS',
    'TRANSLATION_CHUNK_MAX_CHARS_SHORT',
    'TRANSLATION_CHUNK_MAX_CHARS_LONG',
    'VISION_ANALYSIS_MAX_TOKENS',
    'CONNECTIVITY_TEST_MAX_TOKENS',
    'MCP_TOOL_RESULT_MAX_TOKENS',
    'EVIDENCE_PROMOTE_RETRY_BACKOFF_MINUTES',
    'EVIDENCE_PROMOTE_MAX_RETRIES',
    'EVIDENCE_REIN_HALF_LIFE_DAYS',
    'EVIDENCE_DISP_HALF_LIFE_DAYS',
    'REFLECTION_SYNTHESIS_CONTEXT_ABSORBED_COUNT',
    'REFLECTION_SYNTHESIS_CONTEXT_ABSORBED_DAYS',
    'EVIDENCE_EXTRACT_FACTS_MODEL_TIER',
    'EVIDENCE_DETECT_SIGNALS_MODEL_TIER',
    'EVIDENCE_NEGATIVE_TARGET_MODEL_TIER',
    'EVIDENCE_PROMOTION_MERGE_MODEL_TIER',
    'VECTORS_ENABLED',
    'VECTORS_EMBEDDING_DIM',
    'VECTORS_QUANTIZATION',
    'VECTORS_MIN_RAM_GB',
    'VECTORS_MODEL_PROFILE_ID',
    'VECTORS_WARMUP_DELAY_SECONDS',
]
