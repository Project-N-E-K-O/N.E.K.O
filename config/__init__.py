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

"""Configuration constants exposed by the config package."""

from copy import deepcopy
import json
import logging
import os
import platform
import uuid
from types import MappingProxyType

from config.prompts.prompts_chara import lanlan_prompt, get_lanlan_prompt, is_default_prompt

# 应用程序名称与版本配置
APP_NAME = "N.E.K.O"
APP_VERSION = "0.8.3"
logger = logging.getLogger(f"{APP_NAME}.{__name__}")

# GPT-SoVITS voice_id 前缀(角色管理中使用 "gsv:<voice_id>" 格式标识 GPT-SoVITS 声音)
GSV_VOICE_PREFIX = "gsv:"

# GeoIP 区域判定的调试开关（ConfigManager._check_non_mainland 读取）：
#   None  → 正常走真实检测（HTTP IP geo + Steam geo 双判），生产默认值
#   True  → 强制判定为非中国大陆（走 lanlan.app 免费路径）
#   False → 强制判定为中国大陆
# 调试时改这里即可，不用动 config_manager 的检测逻辑；上线保持 None。
GEOIP_FORCE_NON_MAINLAND = None

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
    "live2d_idle_animation",
    "item_id",
    "idleAnimation",
    "idleAnimations",
    "mmd",
    "mmd_animation",
    "mmd_idle_animation",
    "mmd_idle_animations",
    "touch_set",
    "_field_order",
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
    """Return the reserved character-profile fields (deduplicated, ordered)."""
    return CHARACTER_RESERVED_FIELDS


# 角色保留字段 schema（v2）
# 所有系统保留字段统一收口到 `_reserved`，并按 avatar/live2d/vrm 分层。
RESERVED_FIELD_SCHEMA = {
    # voice_id 兼容两形态：旧扁平串 + 声音来源统一架构的结构对象 {source,provider,ref}
    # （并查集式惰性迁移，用户设音色时逐条迁移）。否则已迁移的角色每次 load 都被
    # validate_reserved_schema 误报 _reserved.voice_id 结构异常。
    "voice_id": (str, dict),
    "system_prompt": str,
    "field_order": list,
    "persona_override": {
        "preset_id": str,
        "selected_at": str,
        "source": str,
        "prompt_guidance": str,
        "profile": dict,
    },
    "ai_context": {
        "rename_events": list,
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


def _read_str_env(
    var_name: str, default: str, *, allowed: tuple[str, ...] | None = None,
) -> str:
    """Env override for string-typed config values. Key precedence matches the port
    settings: ``NEKO_<NAME>`` wins, bare ``<NAME>`` is kept for compatibility.
    When ``allowed`` is non-empty, out-of-range values are ignored with a warning
    (falling back to default) so a single typo cannot take the whole feature down.
    An empty string counts as unset."""
    for key in (f"NEKO_{var_name}", var_name):
        raw = os.getenv(key)
        if raw is None:
            continue
        val = raw.strip()
        if not val:
            continue
        if allowed is not None and val not in allowed:
            logger.warning(
                "Ignoring %s=%r (not in %s); using default %r",
                key, val, allowed, default,
            )
            continue
        return val
    return default


def _read_bool_env(var_name: str, default: bool) -> bool:
    """Env override for boolean config values. 1/true/yes/on → True; 0/false/no/off → False;
    anything else / unset → default. Key precedence as above."""
    for key in (f"NEKO_{var_name}", var_name):
        raw = os.getenv(key)
        if raw is None:
            continue
        val = raw.strip().lower()
        if val in ("1", "true", "yes", "on"):
            return True
        if val in ("0", "false", "no", "off"):
            return False
        if val:
            # 非空但不可识别（如 typo "ture"）：警告并回退，别静默吞掉让用户
            # 摸不着头脑"为什么开关没生效"。与 _read_str_env 的 allowed 行为一致。
            logger.warning(
                "Ignoring %s=%r (not a boolean); using default %s",
                key, raw, default,
            )
    return default


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

# ----------------------------------------------------------------------
# Debug flags（打包给用户调试时在源码里 flip，重新打包即可生效）
# ----------------------------------------------------------------------
# LLM prompt 审计：打开后每次发给 LLM 的请求体（messages、token 数、limit
# 字段）会写到 logs/llm_prompt_audit/YYYY-MM-DD.jsonl，用于诊断 prompt
# budget 占比。env var NEKO_LLM_PROMPT_AUDIT=1 同样可启用（任一为真即开）。
# 生产默认 False。
LLM_PROMPT_AUDIT_ENABLED = False

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

# Hide likely assistant/proactive speech that leaks back through microphone STT.
# Conservative by design: the runtime only suppresses non-empty voice transcripts
# that closely match recently displayed AI text; unrelated user barge-in remains
# visible and enters memory normally.
HIDE_DIRTY_VOICE_TRANSCRIPTS = True


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
    """Get a copy of the default VRM lighting config"""
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
    """Get a copy of the default MMD settings"""
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
    Get the localized default character configuration.

    Translates content values based on the Steam language setting (e.g. "哥哥"→"Brother").
    Note: key names stay in Chinese because internal code depends on them.
    Only used when characters.json is created for the first time.

    Args:
        language: Language code ('en', 'ja', 'zh', 'zh-CN', 'zh-TW').
                  If None, fetched from Steam or defaults to 'zh-CN'.

    Returns:
        Localized copy of DEFAULT_CHARACTERS_CONFIG
    """  # noqa: DOCSTRING_CJK
    # 获取语言代码
    if language is None:
        try:
            # Forwarded via config._runtime → utils.language_utils
            # (DI registered in app/runtime_bindings.py). When unbound (e.g.
            # cold tooling), resolve_steam_language returns None and we
            # default to zh-CN, matching the prior except branch.
            from config._runtime import resolve_steam_language, normalize_language_code
            steam_lang = resolve_steam_language()
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
        """Translate a value (only string types are translated)"""
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

# ── activity_guess 自适应退避门控 ──────────────────────────────────────
# 活动心跳 (main_logic/activity/tracker.py:_activity_guess_loop) 通过 emotion-tier
# LLM 把"用户在干嘛"叙述出来，只喂 proactive 搭话 prompt。这组旋钮约束「活动没
# 实质变化时」它多久刷一次——用户在两个 app 间来回切窗口曾让它每 ~40s 烧一次
# (静默, 无业务日志) 无限持续。详见 main_logic/activity/activity_guess_gate.py。
# 同一活动每被重述一次，下次重述间隔就 ×MULTIPLIER 增长，封顶 CAP：30→120→480→900。
# CAP 选 900s 对齐 AWAY_IDLE_SECONDS（state_machine.py，挂机 15min 进 away 后心跳硬 bail），
# 这样稳定活动退避到地板时差不多也该转 away 了。消费端 get_snapshot 读 cache 无 TTL
# 守卫，所以 CAP 放大不会让 proactive 拿到“过期”叙述（叙述只描述“在干嘛”，旧 = 仍准）。
ACTIVITY_GUESS_BACKOFF_BASE_SECONDS = 30.0   # 两次调用之间的硬地板 + 首次重述间隔
ACTIVITY_GUESS_BACKOFF_MULTIPLIER = 4.0      # 每次重述后退避间隔的增长倍数（必须 > 1）
ACTIVITY_GUESS_BACKOFF_CAP_SECONDS = 900.0   # 活动稳定后重述间隔的封顶（对齐 AWAY_IDLE 15min）
ACTIVITY_GUESS_SIG_CACHE_SIZE = 8            # 退避记忆的「不同活动签名」条数

# ── AI-aware Stage-1 (path B) ─────────────────────────────────────────
# 原 SignalLoop (path A) 只看 user 消息，导致 PR #1346 之后 AI 自我披露 + proactive
# 引入的屏幕/活动上下文全失明。Path B 走每 N 个 A tick 触发一次的 piggyback
# 节奏：A 跑完后 b_tick_counter++，达到 N 就跑 B；窗口下游边界用 A 实际处理过
# 的最晚 msg ts（不是 wall-clock now）保证 B 看的消息严格被 A 看过。
EVIDENCE_AI_AWARE_EVERY_N_A_TICKS = 3
"""Path B 每 N 次 A tick 触发一次（piggyback 在 A 循环里，不维护独立 wall-clock cadence）。
- 选 3：A 平均 5 min 一次 tick → B 平均 15 min 一次。tempo 跟着对话强度自适应——
  用户聊得越多 B 越频繁，符合"对话量大才需要补抓 AI fact"的直觉
- B cold start lookback 自动 = N × EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES = 15 min"""

MAX_AI_AWARE_WINDOW_MSGS = 200
"""Path B 单次窗口 SQL LIMIT 上限。挂机后重启 / 长 idle 突发 burst 可能让
[last_b_check_ts, last_a_msg_ts] 窗口跨越数小时百余条消息——cap 住防 prompt
爆炸。LIMIT 在 SQL 层执行（aretrieve_original_by_timeframe 的 limit_rows 参数），
ORDER BY ts ASC 取最早 N 条而不是最新（保 cursor 单调推进）。"""

MAX_KNOWN_POOL_FACTS = 30
"""Path B prompt 里塞的"已知 fact 池"上限（按 importance DESC 取前 N）。
- 30 × ~30 tok = ~900 tok overhead，控制在 prompt 总 budget 的 ~20%
- 作用：让 path B 的 LLM 知道哪些 fact 已被 path A 抽出，主动避免重抽 user 段
  内容；命中的 fact 通常带 source='user_observation'"""

# §3.5 / §6.5 Gate 4：归档扫描背景循环间隔
# 1 小时一次：sub_zero_days 计数本身按"自然日"防抖（每天最多 +1），
# 所以扫描频率 ≥ 一天即可保证不漏；选 1h 是为了让"score 跌穿 0 当天"
# 也能尽快被抓住而非等到次日 00:00。低频远低于 evidence 信号循环
# (40s)，对 IO/CPU 影响可以忽略。
EVIDENCE_ARCHIVE_SWEEP_INTERVAL_SECONDS = 3600

# §3.6 render budget（PR-3 使用，此处先占位）
PERSONA_RENDER_MAX_TOKENS = 2000         # 非-protected persona 预算
REFLECTION_RENDER_MAX_TOKENS = 2000      # reflection 渲染预算（pending+confirmed 总和）
PERSONA_RENDER_ENCODING = "o200k_base"   # tiktoken encoding

# ── 混合记忆召回（recall_memory 工具后端） ───────────────────────────────
# 模型决定调 recall_memory(query) 时，memory_server 在内存里并行跑 BM25 +
# cosine 召回，两路各自阈值过滤 + 限 top-K，RRF 融合后整体再限 N 条返回。
#
# 候选范围：
#   - BM25 池：     facts.json + reflections.json + facts_archive.json
#                  （BM25 对大池子廉价，archive 也能搜到罕见关键词命中）
#   - Embedding 池: facts.json + reflections.json
#                  （embedding 计算贵 + archive 已经超出常态记忆窗口；
#                   persona 整段不入池——它已经被常态渲染进 system prompt，
#                   再检索就是冗余）
#
# 阈值是经验值，跑起来再调；cosine 用 sentence-embedding 常见的相关性下限
# 0.3；BM25 用 0.1 接近 "any meaningful overlap"（零 overlap 早就被
# _bm25_rank 的 score > 0 卡掉了，0.1 主要挡偶发高频词碰瓷）。
#
# ⚠️ BM25 阈值不能定高：Okapi 公式在小 pool 下 IDF 系数自然就矮，
# 单 doc pool 即使 exact match 最高也就 ~0.72（``log((1-1+0.5)/(1+0.5)
# + 1) × (k1+1)``）；2-doc pool 两条都有词时 IDF 跌到 ~0.18。最初拍
# 1.0 是用大语料经验值，结果新用户 / 小语料 / 高频词查询全部被阈值
# 杀掉，BM25 兜底功能等于死掉（codex P1 review on PR #1385）。
HYBRID_RECALL_BUDGET_EACH = 4            # 每路（BM25 / embedding）top-K 上限
HYBRID_RECALL_BUDGET_TOTAL = 8           # RRF 融合后总条数上限（两路去重 + 取分前 N）
HYBRID_RECALL_TIME_BUDGET = 8            # 按时间回溯（recall_memory time 参数）返回的最接近条数上限
HYBRID_RECALL_COSINE_THRESHOLD = 0.3     # cosine < 阈值视为不相关
HYBRID_RECALL_BM25_THRESHOLD = 0.1       # BM25 < 阈值视为不相关（保 small-pool exact match）
HYBRID_RECALL_RRF_K = 60                 # RRF 常数（k=60 = Elastic / OpenSearch 默认）

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
#   *_MAX_CHARS                        → 字符数（仅非 prompt-facing 的 UI /
#                                         payload 防爆流程用，不作为 LLM input
#                                         budget 证据）
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

RECENT_COMPRESS_THRESHOLD_ITEMS = 20
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

RECENT_COMPRESS_INPUT_BUDGET_TOKENS = 8000
"""后台 best-effort 压缩的单段输入 token 预算（分段阈值）。
- 用途：待压积压渲染成文本后若超过此值，compress_history 走分段
  map-reduce——切成每段 ≤ 此值的小段分别压成中间摘要，再 reduce 成最终
  备忘录，减小单次 LLM 输入、避免输入过大导致超时。未超此值的正常压缩
  走原一次性路径，行为不变。
- 上游：积压对话渲染文本的 token 数。"""

RECENT_HARD_CAP_TOKENS = 60000
"""recent 历史的硬上限（最终兜底，平时不触发）。
- 用途：压缩持续失败（如持续 429，best-effort 后台也救不回）导致历史
  一直压不掉、无限膨胀时，update_history 保留完整历史前若总 token 超过
  此值，丢弃最旧的未压缩对话原文，保留近期若干条 + 备忘录，保证 prompt
  有界。设得很大，只作最后防线。
- 上游：未被压缩而累积的 recent 历史 token 数。"""

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

MEMORY_REFLECTION_SYNTHESIS_INTERVAL_SECONDS = 180
"""``_periodic_reflection_synthesis_loop`` 每轮轮询间隔（秒）。
- 用途：后端定期对每个角色调 ``reflection_engine.synthesize_reflections``。
- 设计依据：synthesize_reflections 内部对"同批 source_fact_ids → 同 rid"做
  幂等 short-circuit，无新 unabsorbed fact 时 LLM 不会被调，所以这层只是
  调度频率上限。**真 LLM 调用频率约等于"用户在 N 秒内新积了 ≥5 条 unabsorbed
  fact 的次数"**，与 SignalLoop 实际产出速率绑死、与本常量解耦——把间隔从
  600s 缩到 180s 不会按比例加 LLM 成本。
- 选 180s：对齐 ``AUTO_PROMOTE_CHECK_INTERVAL = 180s``。两条 loop 一个产
  pending、一个把 pending 推 confirmed，节奏对齐让 user-visible 状态机延迟
  最短（合成 → 下一 tick 内就能被 promote 看到）。也跟
  ``EVIDENCE_SIGNAL_CHECK_IDLE_MINUTES * 60 = 300s`` 错峰，让 SignalLoop 抽
  完一批 fact 后 1-2 个 reflection tick 内能消化掉。
- 历史：以前 reflection 合成挂在 ``/api/proactive_chat`` handler 里（PR #1015
  顺手塞的，见 main_routers/system_router.py 历史 blame），整套合成链路与
  前端 setTimeout 强耦合——前端不开 / proactive 不触发 → reflection 永远不
  增长。本常量配套的后端 loop 把合成从 HTTP/前端解耦，与其他 9 条 periodic
  loop（rebuttal / auto_promote / idle_maint / signal_extraction / archive /
  refine 等）对偶。"""

REFLECTION_RELATED_PER_QUERY_K = 3
"""Reflection synthesis 时，每条 unabsorbed fact 单独 query 召回的 absorbed
fact 数量上限。
- 上游：synthesize_reflections 调 ``MemoryRecallReranker.aretrieve_per_query_topk``
  时按本常量给每条 query 配独立预算。
- 设计依据（PR #1401 thread 拍板）：原先用 max-pool top-K (=6 全局预算)，
  20 条 unabsorbed 主题分散时冷门主题会被高频主题挤掉冷板凳。改成 per-query
  K=3 + 全局 cap，保证每条 unabsorbed 至少能拿到自己的 top-3 锚（除非这条
  query embed 失败 / 候选池没语义匹配）。
- 单条 query 拿 3 条而不是 1 条：考虑到主题边界模糊（用户聊 MC 同时聊到
  红石和挖矿，cosine top-1 可能只命中其中一条），多给两条让 LLM 能看出
  "主题群"的轮廓。"""

REFLECTION_RELATED_TOTAL_CAP = 20
"""``aretrieve_per_query_topk`` 跨 query union+dedup 后的最终上限。
- 设计依据：与 ``REFLECTION_SYNTHESIS_FACTS_MAX`` (=20) 同档，让 anchor 集
  最坏也能跟 source 集等量——但实际命中通常远小于此（query 间 nearest
  neighbor 大量重叠 + dedup）。典型 batch 10 条 unabsorbed × per_query=3
  = 30 候选 → dedup 后落在 ~10-15 anchor。
- 上界用于防御性截断：极端"20 条全主题不重叠"假设下，per_query=3 × 20 = 60
  候选，dedup 不能去重时砍到 20，避免 prompt token 爆。
- prompt 实际成本：20 × ~50 tok ≈ 1000 tok anchor + 20 × ~50 tok ≈ 1000 tok
  source = 2k 上限，summary tier 模型完全吃得下。"""

# ---- Memory: temporal scope (memory/temporal.py) ─────────────────────
# Reflection 用 4 档 temporal_scope（pattern / state / episode / past）做时间
# 衰减。state 与 episode 各有 TTL，超期自动进过时 block。pattern 永不过时。
# `past` 是历史兼容值（旧数据可能存了），render 时直接进过时 block。
MEMORY_STATE_PAST_DAYS = 7
"""state 类 reflection 距 event 多少天后被视为已过时。
- 用途：memory.temporal.is_past_for_render；render 时把此条移入过时 block。
- 上游：reflection synth LLM 标注 temporal_scope='state' 的条目。"""

MEMORY_EPISODE_PAST_DAYS = 3
"""episode 类 reflection 距 event 多少天后被视为已过时。
- 用途：同上，但 episode 是一次性事件，衰减更快。
- 上游：reflection synth LLM 标注 temporal_scope='episode' 的条目。"""

MEMORY_SCHEMA_VERSION_CURRENT = 2
"""fact / reflection 当前 schema 版本号。
- v1（缺失或显式 1）：旧 ontology（current/ongoing/None temporal_scope，无
  event_when）。
- v2：新 ontology（pattern/state/episode）+ event_start_at / event_end_at。
- 用途：背景循环找 schema_version < CURRENT 的条目慢慢重判升版本。"""

# ---- Memory: slow recheck loop (memory/temporal.py + memory_server.py) ─
MEMORY_RECHECK_ENABLED = True
"""慢速记忆重判循环总开关。
- 用途：app/memory_server.py _periodic_slow_memory_recheck_loop 启动门控。
- 关闭时老数据不会被升版本（render 兜底走 pattern 不淡出）。"""

MEMORY_RECHECK_INTERVAL_SECONDS = 30
"""慢速重判循环单条间隔。
- 用途：每 N 秒重判 1 条 reflection / fact。
- 上游：背景循环 sleep；设计参考 §3.5 archive_sweep（更慢、低 IO）。"""

MEMORY_RECHECK_INITIAL_DELAY_SECONDS = 180
"""慢速重判循环启动延迟（错峰）。
- 用途：和现有 6 个循环错峰，避开启动峰值。
- 现有 _INITIAL_DELAY_* 在 20s~250s，本值 180s 接近末尾。"""

MEMORY_RECHECK_MAX_ATTEMPTS = 5
"""单条 v1 entry 重判失败几次后放弃，避免饥饿后续合法 v1 条目。
- 失败定义：LLM 调用抛异常、返回非 dict、temporal_scope 不在合法集合
  （reflection 限定 pattern/state/episode）。
- 计数字段：reflection / fact entry 上的 `recheck_attempts` (int)。
- 命中阈值的条目仍保留 schema_version<2（不静默升版洗白），但被 filter
  排除，让循环把名额匀给其它 v1 条目。dev 可读 logger.debug 看积压。"""

MEMORY_LIVENESS_MAX_ATTEMPTS = 5
"""LLM 终态失败 N 次后强推 progress marker / dead-letter 的统一上限。
- 适用场景：所有"同点 input + 无 counter + LLM 永久失败 → 永久卡死"的后台
  路径。包括 signal extraction path A/B、rebuttal feedback、persona
  corrections resolve、fact dedup resolve、refine cluster、outbox handler。
- 治理思路：参考 `MEMORY_RECHECK_MAX_ATTEMPTS` (schema 重判 dead-letter) 的
  套路，把"同一 cursor / 队头 / cluster_hash / op 反复打 LLM"收敛掉，避免
  毒窗口 / 毒 payload 让整条 pipeline 哑火。
- 失败定义：LLM 返 None / 抛异常 / handler raise / parse 失败等终态。
- 5 跟 `MEMORY_RECHECK_MAX_ATTEMPTS` 同口径——按 40s 一轮算 3 分钟级窗口，
  跨过偶发 transient failure 够用；再多就属于真正 poison。"""

MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS = 5 * 60 * 60
"""dead-letter 的时间冷却自愈窗口（秒）。

- 问题：达 `MEMORY_LIVENESS_MAX_ATTEMPTS` 被冻结的 entry（reflection synth /
  schema recheck / refine cluster）只在"成功"或"输入变化"时才解冻。但当失败
  其实是**一次性持续故障**（correction 模型快照下线一直超时 / cloudsave 卡
  维护态 / FS 只读）时，故障期间会把一批无辜 entry 一路 bump 到 MAX 永久冻死，
  故障恢复后也不会自愈（内容没变、又进不了候选）。
- 治理：给这些 dead-letter 加时间冷却——冻结后每过本窗口放行**一次** probe。
  probe 成功 → 计数清零彻底恢复；probe 失败 → 重新计时、再等一个窗口。这样
  一次性故障 5h 后自愈，真正 poison 仍被压到"每 5h 一次"不空烧。
- **不适用 memory_review**：它的恢复机制是"对话尾部 fingerprint 变化即复位"
  （master 一发新消息就重试），不需要也不应该有时间自愈——挂机期间就该一直停。
- 5h：refine cron 30min 一轮 → 一次 >2.5h 的模型宕机会把 entry 顶到 MAX；
  5h 冷却确保宕机恢复后下一轮就能 probe，又远大于偶发抖动窗口。"""

# ---- Memory: followup picker (memory/reflection.py) ─
REFLECTION_FOLLOWUP_WEIGHTED = True
"""主动搭话 followup 候选采样是否按 evidence_score 加权随机。
- 用途：_filter_followup_candidates；False 时回退到旧行为（按落盘顺序取
  top-K）。
- 设计依据：候选池大时纯落盘顺序总取同一批，造成主动搭话内容雷同。"""

REFLECTION_FOLLOWUP_WEIGHT_BASE = 0.5
"""加权采样的最低权重（score=0 时也有此权重，避免全 0 score 时退化）。"""

# ---- Memory: summary stale prompt (memory/recent.py) ─
RECENT_SUMMARY_STALE_HOURS = 1
"""距上次"LLM 实际更新 past block 的时刻"超过此小时数，下一次 compress
时在 prompt 头部附加"时间已过 X"提示，让 LLM 主动把过时片段挪进 summary
内部的过时 block。
- 锚点：不是"上次 summary 时间"——summary 每轮压缩都会跑，跟着锚点会让
  stale hint 永远跟在最后一次压缩后 1 小时，无法形成"每隔 N 小时刷一次
  past block"的稳定节奏。改记"上次 hint 真正注入的时刻"，即 LLM 实际
  被要求更新 past block 的那一刻。
- 上游：recent_meta.json 里的 last_past_block_update_at 字段。
- 注意：summary 的过时 block 只在当前 session 临时降级，不持久化到
  reflection / persona。"""

# ---- Memory: persona ----
PERSONA_MERGE_POOL_MAX_TOKENS = 4000
"""promote-merge 时同 entity persona+reflection 池总 token 上限。
- 用途：_allm_call_promotion_merge 把同 entity 的所有 confirmed/promoted
  persona 和 reflection 全拼进 prompt，本 cap 防止该池失控。
- 上游：同一 entity 长期累积的 persona/reflection。
- 注意：这条不复用 PERSONA_RENDER_MAX_TOKENS（render 是给主对话看的，
  merge 是给 promotion LLM 看的，需要更大的池才能做合并判断）。"""

PERSONA_CORRECTION_BATCH_LIMIT = 10
"""单次 persona corrections resolve 处理的 batch 大小。
- 用途：_resolve_corrections_locked 从 pending_corrections 队列取前 N
  条丢给 LLM 做对错判断，剩下的下一轮再处理。
- 上游：pending_corrections 队列。"""

PERSONA_VERSION_HISTORY_MAX = 5
"""单条 persona entry 的 version_history 保留上限（Phase B-1）。
- 用途：每次 resolve_corrections 的 replace/merge 或 apply_refine_actions
  的 merge/modify append 后裁到最近 K 个，防长期运行无限累积。
- 老版本直接丢；version_history 是审计而非数据，超过 5 条价值极低。"""

MEMORY_LLM_HARD_TIMEOUT_SECONDS = 110
"""所有 memory 后台 LLM 调用的硬上限 timeout（秒）。
- 上游转发服务器 hard timeout 120s；client 必须留 ≥10s margin，否则会被
  转发层先 timeout 截断，连 response 都拿不到。**不能超过 110**。
- 覆盖：reflection synthesis / persona correction / memory_refine /
  recent review_history 等所有后台跑的 LLM 调用。
- 不适用：用户面前的 chat / realtime 路径有独立的更严 timeout 控制。"""

LLM_OUTPUT_GUARD_MAX_TOKENS = 4096
"""变长输出 LLM 调用的 max_completion_tokens **runaway guard**（不是紧 budget）。
- 用途：那些输出长度天然变动、没有紧的 task-specific budget 的调用——memory
  的结构化 JSON（reflection / recall / persona / facts / refine / dedup recheck）、
  fact dedup、card-assist、window-title 关键词等。
- 取值：4096。**必须保持在主流 provider 的输出上限之内**——`max_completion_tokens`
  是上限不是目标，但很多 provider（OpenAI 及兼容端点）会在请求时就校验它 >
  模型 max output 而直接 400，而不是退回默认值。这正是 `omni_offline_client.
  _budget_to_max_tokens` 对 unlimited 直接 **omit 字段**（"large fixed values get
  rejected as out-of-range by some providers"）的原因。8000 会打爆 max output<8000
  的自建/老模型；4096 是绝大多数 summary/correction/agent tier 模型都接受的安全档，
  同时对这些任务的正常输出（含 thinking reasoning）仍是宽裕兜底。
- 政策：LLM_OUTPUT_BUDGET lint 要求每个 client 构造都带 token budget；本常量是
  "无紧 budget 但仍需有上限"这类调用的统一来源（见 docs/design/llm-prompt-budget.md §0）。
- 不适用：有明确紧 budget 的调用（emotion / translation / vision / plugin 粗筛等）
  仍用各自的 *_MAX_TOKENS 常量，不要图省事换成本 guard。
- 残留边界：max output < 4096 的极老/极小模型仍可能 400；这类安装可下调本常量。
  彻底鲁棒需要 per-model 上限元数据（codebase 目前不跟踪），故取保守定值。"""

ICEBREAKER_FREE_TEXT_INTERPRETER_TIMEOUT_SECONDS = 20.0
"""新用户破冰自由输入解释器 LLM timeout（秒）。用户面前的短分类/短回复调用，卡住时应快速失败。"""

ICEBREAKER_FREE_TEXT_OUTPUT_MAX_TOKENS = 512
"""新用户破冰自由输入解释器输出 token 上限。输出固定 JSON，512 只作短任务上限。"""

ICEBREAKER_FREE_TEXT_ASSISTANT_LINE_MAX_TOKENS = 800
"""破冰自由输入解释器：当前 YUI 台词输入 token 上限。"""

ICEBREAKER_FREE_TEXT_USER_TEXT_MAX_TOKENS = 800
"""破冰自由输入解释器：用户自由输入 token 上限。"""

ICEBREAKER_FREE_TEXT_OPTION_LABEL_MAX_TOKENS = 200
"""破冰自由输入解释器：单个选项文案 token 上限。"""

ICEBREAKER_FREE_TEXT_HISTORY_TEXT_MAX_TOKENS = 240
"""破冰自由输入解释器：近期自由输入记录单段文本 token 上限。"""

ICEBREAKER_FREE_TEXT_HISTORY_MAX_ITEMS = 4
"""破冰自由输入解释器：近期自由输入记录最多带入条数。"""

ICEBREAKER_FREE_TEXT_REPLY_MAX_TOKENS = 240
"""破冰自由输入解释器：模型 reply 字段清洗后的 token 上限。"""

DIALOG_LLM_STREAM_TIMEOUT_SECONDS = 180
"""主对话流式 LLM client 的总请求 timeout（秒），作 hang-guard。
- 用途：OmniOfflineClient 的 streaming chat client（stream_text /
  prompt_ephemeral 共用同一个 self.llm）。SDK 的 timeout 是整次请求上限，
  对流式即"出完整条回复"的时间。
- 取值：刻意取大（180s）——正常 TTS 短回复 / summary 3000-token 长回复
  都远低于此，不会被截；只在上游真正卡死（既不出 token 也不断流）时兜底
  释放连接。比 MEMORY_LLM_HARD_TIMEOUT_SECONDS 大，因为主对话是用户面前
  路径，宁可多等也不能误截正常回复。
- 政策：LLM_OUTPUT_BUDGET lint 要求每个 client 构造都带 timeout；本常量是
  主对话流式路径的统一来源。"""

FOCUS_THINKING_EXTRA_TOKENS = 800
"""凝神（focus / thinking-on）轮次额外放宽的 max_completion_tokens。
- 背景：thinking 模型（Qwen enable_thinking / GLM·Kimi·Doubao thinking.type /
  OpenRouter reasoning.effort）的 reasoning token 与正式回复共享同一个
  max_completion_tokens 预算池（见 docs/design/llm-prompt-budget.md §0），
  凝神轮一开思考就会从回复额度里扣，把正式回复挤短。
- 作用：仅在 thinking_on 的那一轮，把 API 端 max_completion_tokens 临时
  抬高本值，给推理链单独留头寸，不动 Python-side 长度 guard（回复可见
  长度仍按 max_response_length 收口）。
- 路由：作为 per-call override 经 _focus_stream_overrides → astream →
  ChatOpenAI._params 透传，不改 self.llm 实例属性（与 extra_body 同一条
  per-call 路径，并发安全、下一轮自动复位）。
- 适用面：Claude 凝神保持 thinking-off（config/providers.py），本加值对其
  天然 no-op；Gemini thinking_budget 是独立字段（800），本余量也足够覆盖。
- 取值：扁平 800，不按 provider 分叉——只在真正开思考的轮次生效。"""

# ---- Memory: refine (Phase A-3) — MemoryRefineEngine 的 cron 参数 ----
# 通用 cosine 聚类 + LLM 决议管道，复用在 PERSONA_REFINE 和
# REFLECTION_REFINE 两条 cron 上。fact 不可变（只能作 merge/modify
# 的信息源，不能被 split/discard）。

MEMORY_REFINE_COSINE_THRESHOLD = 0.82
"""refine cluster 的 cosine 阈值。比 FACT_DEDUP 的 0.85 略松——persona
和 reflection 文本通常更长，cosine 难拉到 0.85+；同时这里是聚类找
"相关"而非 dedup 找"等价"，松一点更合适。"""

MEMORY_REFINE_TOPK_PER_ENTRY = 5
"""单个 entry 在邻接图上最多保留的近邻数（双 cap 的第二条）。防止某条
被高度引用的 hub entry 把一大坨弱相关条目都拉进同一 cluster。"""

MEMORY_REFINE_CLUSTER_SIZE_MAX = 6
"""单 cluster 内最多成员数。超过 6 LLM 难以一致处理；溢出的 cluster
按 cosine 强度截到前 6 条。"""

MEMORY_REFINE_REVISIT_AFTER_DAYS = 30
"""同一 cluster_hash 多久后允许重审（即使 hash 全员命中也不 skip）。
LLM 行为月级别可能漂移，1 个月重审一次成本可控。"""

MEMORY_REFINE_CLUSTERS_PER_PASS = 3
"""单次 cron 触发最多送 LLM 的 cluster 数。按饥饿度（cluster 内
min(last_refine_at)）升序取前 N。约 3 次 LLM call ≈ 60-90s 阻塞。"""

MEMORY_REFINE_CRON_INTERVAL_SECONDS = 1800
"""PERSONA_REFINE / REFLECTION_REFINE cron 的轮询间隔（秒）。
- 30 分钟一次；engine 内 cluster_hash skip 让"刚审过"的 cluster
  零成本跳过，所以高频触发也不会浪费 LLM token。
- 两条 cron 用同一间隔，靠 _INITIAL_DELAY_* 错峰起始。"""

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

AGENT_CALLBACK_TEXT_MAX_TOKENS = 1000
"""单条 agent callback 的 summary/detail 注入 LLM 的 token 上限（per-item）。
- 用途：`Core.enqueue_agent_callback` 落队前对每条 callback 的 summary/detail
  截断。task_result 类回流已在 _emit_task_result 用 TASK_*_MAX_TOKENS 截过
  （≤1000），本档对齐 TASK_LARGE_DETAIL 不会误伤；真正的兜底对象是
  **push_message / proactive_message** 这条 plugin 事件流——proactive_bridge
  直接聚合 text parts 写进 summary/detail，此前没有任何 cap。
- 上游：plugin SDK push_message() 的 text parts / 外部通知。"""

AGENT_CALLBACK_TOTAL_MAX_TOKENS = 3000
"""一次注入 LLM 的 agent callback 指令总和 token 上限（total）。
- 用途：`_build_callback_instruction`（文本轮 system_prefix / proactive 触发）
  和 `_render_pending_extra_replies_by_origin`（语音 hot-swap final_prime_text）
  渲染完成后对整段做兜底截断。防 N 条 callback 累加撑爆本轮 prompt。
- 与 per-item 配合：单条 cap 防长贴，total cap 防大量短条累加（见
  docs/design/llm-prompt-budget.md §2.1 三层防护）。"""

AGENT_CALLBACK_QUEUE_MAX_ITEMS = 50
"""pending_agent_callbacks / pending_extra_replies 队列长度上限（flood guard）。
- 用途：`enqueue_agent_callback` 落队后裁到最近 N 条，防 plugin 事件流灌爆
  内存（队列此前无容量上限，drain 时全量 snapshot）。丢最旧的（最新事件
  最相关）。
- 与 AGENT_TASK_TRACKER_MAX_RECORDS=50 同口径。"""

AGENT_DEDUP_CANDIDATES_MAX = 50
"""task deduper 单次比对的 existing-task 候选条数上限。
- 用途：`brain/deduper.py:_build_prompt` 只取前 N 条 candidate 拼 prompt，
  防 backlog/flood 下 `_collect_existing_task_descriptions` 把上百条任务全量
  塞进 dedup prompt。配合 per-item 头尾截断（TASK_DETAIL_MAX_TOKENS）给输入
  一个真实总上限。
- 与 FACT_DEDUP_BATCH_LIMIT=20 类似（LLM 配对决策的舒适 batch）；dedup 只做
  一次 N×1 比对，放宽到 50。"""

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

AGENT_EXTERNAL_GATE_ENABLED = True
"""廉价前置闸总开关（默认开）。
- 用途：开 = 用 master-emotion 在 input-time 顺带产出的 external_intent，在 agent
  侧 turn_end 评估前做一道零成本前置判断：若这一轮被自信地读成「不需要外部能力」
  （既没要求对外操作、也不需要外部/实时信息），且零 LLM 的确定性 shortcut（magic
  word 规则 + 插件关键词）也全静默，就跳过那 1~2 次大模型评估，省掉闲聊轮的
  analyzer 开销。关掉则每个 turn 照常全量评估。
- 闸是非对称的：external_intent 缺失（None）或确定性命中都不刹车，所以最坏只是多花
  一次评估，绝不漏真任务。
- 上游：DirectTaskExecutor._analyze_and_execute_inner 的前置判定。"""

AGENT_EXTERNAL_GATE_THRESHOLD = 0.2
"""external_intent 刹车阈值（0~1）。
- 用途：external_intent < 此值才视为「自信地不需要外部能力」、进入刹车候选；>= 此值
  或为 None 一律放行。
- 取保守低位（默认 0.2）：小模型只需可靠认出「显然只是闲聊、靠对话和常识就能答」
  这 90% 的易判 case，模棱两可的全 fail-open 到准确的大评估。调高 = 更激进省钱但
  漏判风险上升。"""

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

USER_DIRECTIVE_TTL_SECONDS = 3 * 86400
"""用户显式 ban-topic 指令（"别再提 X / stop saying X"）的存活时长。
- 用途：memory/user_directives.py 的 active 判定 + render_prompt_block
  注入到下次 session 启动的 system prompt。
- 设计依据：用户态度的有效期介于"本轮结束"和"永久偏好"之间——3 天足够覆盖
  连续几天的会话上下文又不至于把一时情绪固化成长期人设。
- 上游：main_logic/core.py:_build_initial_prompt 注入；
  memory/user_directives.py:UserDirectivesManager 内部判活 + 清理。"""

USER_DIRECTIVE_MAX_ACTIVE = 20
"""注入到 system prompt 的活跃 ban-topic 上限。
- 用途：UserDirectivesManager.get_active 截断到 last_seen 最新的 N 条。
- 设计依据：超过 20 个不同 ban-topic 同时活跃几乎一定是抽取出错或用户在
  故意刷指令——截断比把 prompt 塞爆好。"""

# ── 防复读（anti-repeat）BM25 相关 ─────────────────────────────────
ANTI_REPEAT_BG_WINDOW = 100
"""anti-repeat corpus 背景窗口长度（最近 N 条 AI 输出）。
- 用途：memory/anti_repeat.py 的滚动 corpus 保留最近 N 条文本算 DF。
- 设计依据：100 条 ≈ 用户半天到一天的对话量；窗口太短 IDF 不稳定，太长
  又会让一周前的偶发话题永远算"高 IDF unique"。"""

ANTI_REPEAT_FG_WINDOW = 5
"""anti-repeat 前景窗口长度（最近 N 条算"是否重复"）。
- 用途：BM25 评分把最近 N 条当 query corpus 算 TF；新 draft 与这 5 条比。
- 设计依据：5 条 ≈ 用户最近能感知到的复读窗口；7+ 已经记不清了。"""

ANTI_REPEAT_FG_TTL_SECONDS = 600.0
"""anti-repeat 前景窗口的时间新鲜度上限（秒）。仅作用于 FG（TF/复读判定），
不影响 BG（DF/IDF 词频背景，仍按 ANTI_REPEAT_BG_WINDOW 条数封顶）。
- 用途：memory/anti_repeat.py 的 score_draft / top_recent_topics 只把「最近
  ANTI_REPEAT_FG_TTL_SECONDS 内」的输出计入前景 TF；更早的条目照旧留在 BG
  里贡献 IDF。
- 设计依据：修复「空闲死锁」——主动搭话在用户空闲时才触发，而所有 drop 路径
  都不写 corpus、成功投递才写，于是空闲期 FG 窗被最近几条同话题（如屏幕解说）
  冻结，每轮打出同样的超高 BM25 → 永远 drop → 永远无法搭话。加了 TTL 后，空闲
  超此时长 FG 自然清空、bm25_score 命中 `not fg_docs` 返回 0，本轮放行。
- 取值：10 分钟。防复读本就只防「刚说过、又说一遍」的 back-to-back 复读；十分钟
  前说过的话题再提不算复读。BG（IDF 语境）不设 TTL，评分质量不受影响。"""

ANTI_REPEAT_INJECT_TOP_K = 6
"""注入 system prompt 的 "最近高频 topic 词" 数量。
- 用途：build_recent_topics_block 取 BM25 排名前 K 的 ngram。
- 设计依据：6 个词够覆盖"几个话题"，又不至于把 prompt 撑长。"""

ANTI_REPEAT_REGEN_THRESHOLD = 8.0
"""proactive 出口 BM25 总分超此值则触发 1 次 regen。
- 用途：system_router proactive 流式完成后评分；超阈值用 avoidance prompt
  重 sample 一次。
- 设计依据：经验起点；后续 testbench 调。"""

ANTI_REPEAT_DROP_THRESHOLD = 16.0
"""proactive regen 后仍超此值则放弃投递（不发）。
- 用途：避免 LLM 卡死在某个 topic 上连续复读。
- 设计依据：REGEN 的 2 倍，给 LLM 一次纠正机会。"""

ANTI_REPEAT_BM25_K1 = 1.5
"""BM25 k1 参数（控制 TF saturation 速度）。Robertson 经典推荐值。"""

ANTI_REPEAT_BM25_B = 0.75
"""BM25 b 参数（文档长度归一化强度）。Robertson 经典推荐值。"""

ANTI_REPEAT_MIN_DRAFT_TOKENS = 12
"""draft 短于此长度（tokens 数）就不评分，直接放行。
- 用途：避免"嗯。"、"好"这种短回复被错杀。
- 设计依据：~12 个 ngram token 才能形成稳定的 BM25 信号。"""

ANTI_REPEAT_EXEMPT_SOURCE_TAGS = frozenset({"MUSIC", "MEME"})
"""主动搭话里"复读判定从台词切到素材维度"的来源标签。
- 动机：BM25 防的是"话题/措辞复读"，但素材推送类 channel 的开场白天生模板
  化（推歌"换首歌 / 这旋律 / 听听看"、表情包"看这个 / 笑死"），台词长一个
  样、而推送的素材（曲目 / 表情包搜索关键词）却不同；用台词 BM25 判它属于
  天生误杀——博士连点几首后 FG 窗被音乐 intro 占满，分数爆表，后续自发推
  歌全被 drop，表现为"放音乐频率极低"。
- 语义：这类 channel 的复读按"素材本身"去重——MUSIC 看曲目、MEME 看搜索
  关键词（不是图片）。本轮素材与近期不雷同时，豁免台词级硬拦截（字面相似
  度 + BM25 regen/drop）直接放行；素材雷同（反复推同一曲目 / 同一关键词）
  才回落到正常台词判定，台词没雷同则依然能发。
- 另：这类 channel 的台词不录进 anti-repeat corpus（见 finish_proactive_
  delivery），免得模板化 intro 污染 FG 窗、漂移其它 channel 的复读基线；
  素材标识的近期去重走 system_router 的 _proactive_material_history。"""

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

# ── Master 情绪画像（基建）─────────────────────────────────────────────
# 用 emotion-tier 小模型即时分析「用户自己说的话」的情绪，产出二维 valence-arousal
# 瞬时读数（效价 -1~+1、唤醒 0~1）。这是一条独立基建：单一权威源，凝神（FocusScorer
# 的 emotion 信号）是第一个消费者，后续记忆/UI/主动反应可接同一个 state。绝不复用
# lanlan 头像那条 outward-emotion 管线（那是角色的脸，不是用户的情绪）。privacy-
# independent：输入是对话不是屏幕，不受隐私模式门控（同凝神，见 developer-notes 规则 6）。
MASTER_EMOTION_ENABLED = True
"""Master 情绪画像总开关（默认开）。
- 用途：开 = 每条用户消息（节流后）异步跑一次 VA 情绪分析、更新瞬时读数；关掉则
  不分析、读数恒空，凝神的 emotion 信号自动消失，退回 keyword+cadence。
- 上游：_note_user_turn 的 fire-and-forget 触发；FocusScorer.emotion 信号的可用性。"""

MASTER_EMOTION_MIN_INTERVAL_SEC = 6.0
"""两次 VA 分析的最小间隔（秒），节流防连发消息打爆 emotion tier。
- 用途：MasterEmotionTracker 内部按上次分析时间戳早退。
- 调小 = 更即时但更费 token；调大 = 更省但读数更陈。"""

MASTER_EMOTION_TIMEOUT_SEC = 8.0
"""单次 VA 分析的 emotion-tier 调用超时（秒）。
- 用途：传给 _invoke_emotion_tier 的 timeout；超时则本轮不更新读数、保留上一次。
- 注意：用的是独立的 emotion tier 模型，不是主对话模型，所以不受 Gemini Live 慢拖累。"""

MASTER_EMOTION_MAX_INPUT_CHARS = 500
"""送进 VA 分析的用户文本上限（字符），超出截断。
- 用途：情绪判断只需开头一段；截断防用户粘贴长文时把整段塞进 emotion tier
  （token / 成本 / 输入预算）。
- 上游：MasterEmotionTracker._invoke 拼 prompt 前截断。"""

MASTER_EMOTION_READING_TTL_SEC = 120.0
"""情绪读数的有效期（秒），超期视为过期、latest 返回 None。
- 用途：emotion 信号能单轮独立触发凝神，若读数无限有效，长停顿后一条中性消息
  会读到几分钟前的旧 distress 读数、误重入/维持 Focus。TTL 让陈旧读数失效，
  正常对话（turn 间隔几秒~几十秒）不受影响。
- 设 0 关闭老化。上游：MasterEmotionTracker.latest。"""

# ── Focus mode 凝神 (docs/design/focus-truename-mode.md) ───────────────
# 信号触发、用户无感的「这一轮开思考 + 换强模型」机制，兑现 90/10 产品命题
# 里的 10% 神明降临。以下全是 A/B 可调旋钮，集中在此便于灰度调参；情绪关键词
# 这类多语言词表按 i18n 规约放 config/prompts/prompts_focus.py，不在这里。
FOCUS_MODE_ENABLED = True
"""凝神总开关（默认开）。
- 用途：开 = FocusScorer 正常评分、SM 按累加电荷进入/退出 FOCUS，命中那一轮 inline
  升档开思考、proactive 路径按情节冷却；关掉则两条触发路径都退化回常规
  （proactive 仍 disable_thinking、stream_text 不升档），逐字节零行为变化。
- 历史：曾默认关「先 inert 落地」，因阈值未用真实信号分布调过、且 thinking-on 的
  端到端行为（内联推理文本在流式 content 里的泄露、各 provider 思考开销）未对真模型
  验证过。现转默认开，进入真实信号实测 + 调参阶段。详见 docs/design/focus-truename-mode.md。
- 上游：FocusScorer / SessionStateMachine 入口的早退判定。"""

# ── 累积进入模型（leaky 累加器）─────────────────────────────────────
# 进入不是「单轮分数越线」而是「逐轮累加的电荷值越线」：每轮
#   charge = charge × FOCUS_CHARGE_RETENTION + 本轮score
# charge ≥ ENTER 进入、< EXIT 退出（迟滞带）。这样零散漏出的脆弱信号能攒够进入，
# 而转中性后 charge 每轮按 retention 漏掉、自然退出（替代旧的「连续 K 轮低分」
# streak——streak 会被噪音单轮顶回而卡死，见 PR 实测）。
FOCUS_CHARGE_RETENTION = 0.5
"""电荷每轮的保留率（0~1）。
- 用途：charge = charge × 此值 + 本轮score。0.5 = 每轮留 50%、漏 50%。
- 调高（如 0.7/0.8）= 记性更长、零散信号更易累积进入、进去后更黏；
  调低（如 0.3）= 漏得快、难累积、退得利落。
- 稳态：持续每轮 score=s 时 charge → s/(1-retention)（如 retention=0.5、s=0.5 → 趋近 1.0）。
- 这是「敏感度」主旋钮。仅用于 inline（用户发声）路径。"""

# idle（proactive 主动搭话）冷却——proactive 绝不抬升 charge，只衰减；进入/维持凝神
# 只由 inline（用户自己说的话）驱动。原先分「开口/沉默」两档（开口更耗专注），现统一
# 为同一保留率：无论这一轮 proactive 有没有把话说出来，凝神都按同一速度温和降温，持续
# 长短由 proactive 触发频率主导而非单纯时间流逝。两个旋钮保留以便日后再拆，但须都 > 0、
# < 1，且 replied <= silent。
FOCUS_IDLE_SILENT_RETENTION = 0.8
"""proactive 本轮没把话说出来时的电荷保留率。
- 涵盖：action != chat（被 guard/接管挡下、内容空、[PASS]），以及 Phase 2 思考
  超时 / 流式异常导致 aborted（最终也归 action=pass）——开了思考模式却没能在限时内
  接住，同样按此档降温。
- 用途：charge = charge × 此值。0.8 = 每轮温和降温。当前与 replied 统一为 0.8，
  开口与沉默同速冷却。
- 调低 = 沉默 / 超时更快冷却。"""

FOCUS_IDLE_REPLIED_RETENTION = 0.8
"""proactive 本轮真开口了（action == chat：投递了主动搭话）时的电荷保留率。
- 用途：charge = charge × 此值。0.8 = 每开口一次温和消耗——cap=1.0(满电)起约需 6 次
  主动搭话才漏到 EXIT(0.3) 以下退出，凝神逗留更久。
- 须 <= FOCUS_IDLE_SILENT_RETENTION：开口不比沉默退得更慢。当前两档统一为 0.8。
- 上游：SM.update_focus 的 retention_override（idle 收尾按 action 选这两档之一）。"""

# 调参护栏：把两档冷却的约定变成 fail-fast 的硬校验，避免后续误配把语义反转——
# >= 1.0 会让 idle tick 不降反升（破坏「绝不抬升」），replied > silent 会让「开口」
# 比「沉默」退得更慢（快慢档颠倒）。允许两档相等（当前统一 0.8，开口/沉默同速）。
# 模块加载即校验，配错直接报错而非静默跑坏。
if not (0.0 < FOCUS_IDLE_REPLIED_RETENTION <= FOCUS_IDLE_SILENT_RETENTION < 1.0):
    raise ValueError(
        "Focus idle retentions must satisfy 0 < replied <= silent < 1 "
        f"(got replied={FOCUS_IDLE_REPLIED_RETENTION}, "
        f"silent={FOCUS_IDLE_SILENT_RETENTION})"
    )

FOCUS_CHARGE_ENTER = 0.6
"""进入凝神的电荷阈值，也是「完全激活」点。
- 用途：charge ≥ 此值 → REGULAR→FOCUS，同时前端边缘辉光在此处非线性跃升 + 起呼吸。
- 单个强信号即可单轮秒进：强 distress 情绪读数（emotion 满格 0.7、≥~0.86 时越阈）或
  满格复杂提问（question 1.0×0.6=0.6）。脆弱词单独不足以单轮进（keyword 满格 0.5 < 此阈，
  有意——词表是廉价信号），须叠加 emotion 或跨轮累积；零散信号靠 charge 累积逼近此值后进入。
  注：score 现为各信号加权和（无分母，见 FOCUS_SIGNAL_WEIGHTS）。
- charge 不再 cap 在此值——见 FOCUS_CHARGE_CAP，0.6 以上继续累积到 1.0（更亮更持久）。
- 时间衰减以此为界：charge < ENTER 衰减快（FOCUS_TIME_DECAY_PER_SEC），≥ ENTER（完全激活）
  衰减减半（FOCUS_TIME_DECAY_PER_SEC_ACTIVATED）→ 0.6 以上自然更持久。"""

FOCUS_CHARGE_CAP = 1.0
"""电荷上限（封顶）。
- 用途：charge 累积的天花板。ENTER(0.6) 是进入/完全激活点，0.6→CAP 只是「更深」——
  前端边缘辉光峰值随 charge 继续抬高直到此处封顶。
- 须 ≥ ENTER。"""

FOCUS_TIME_DECAY_PER_SEC = 0.02
"""未完全激活（charge < ENTER）时电荷的每秒时间衰减量。
- 用途：与按轮 retention 叠加的「双重衰减」之时间分量——即便没有新 turn，charge 也随
  wall-clock 真实流逝（惰性在 update_focus 计算、前端按同速率本地外推辉光）。
- 0.02/s ⇒ 从 0.6 漏到 0（如无新证据）约 30s 量级；调高 = 凉得更快。"""

FOCUS_TIME_DECAY_PER_SEC_ACTIVATED = 0.01
"""完全激活（charge ≥ ENTER）后的每秒时间衰减量（减半，更持久）。
- 用途：进入凝神后时间衰减放慢一半，「她降临后多停留一会」；charge 越高离 ENTER 越远、
  停留越久（0.6 以上更持久即源于此）。
- 地板：激活后时间衰减最多只能把 charge 降到 ENTER（0.6）为止，**绝不靠时间降到 0.6 以下**——
  退出激活必须靠一轮对话的 retention（见 _decay_charge_over_time）。0.6 以下才会被时间衰减到 0。
- 须 < FOCUS_TIME_DECAY_PER_SEC（激活后必须比激活前慢）。"""

if not (FOCUS_TIME_DECAY_PER_SEC_ACTIVATED < FOCUS_TIME_DECAY_PER_SEC):
    raise ValueError(
        "FOCUS_TIME_DECAY_PER_SEC_ACTIVATED must be < FOCUS_TIME_DECAY_PER_SEC "
        f"(got activated={FOCUS_TIME_DECAY_PER_SEC_ACTIVATED}, "
        f"base={FOCUS_TIME_DECAY_PER_SEC})"
    )

FOCUS_CHARGE_EXIT = 0.3
"""退出凝神的电荷阈值（迟滞低门，须 < ENTER）。
- 用途：FOCUS 期间 charge < 此值 → 退出。
- 转中性后从 cap 处按 retention 漏：retention=0.5/cap=ENTER(0.6) 时约 1~2 轮漏到 <0.3 退出
  （即「她降临后追一两轮才放下」）。调低 = 更黏、追更久。"""

FOCUS_HARD_CAP_TURNS = 8
"""单次凝神最多持续轮数 M（硬顶 backstop）。
- 用途：即使 charge 一直在 EXIT 以上（用户持续重话），满 M 轮也强制退出收个尾，
  防单个情节无限拖长。
- 上游：SM 的 focus_turn_count 计数器。"""

FOCUS_SIGNAL_WEIGHTS: dict[str, float] = {
    "keyword": 0.5,       # 用户消息命中脆弱情绪词（词表）
    "cadence": 0.2,       # 回复字数相对基线骤跌（仅在有 distress 证据时计入）
    "emotion": 0.7,       # master 情绪画像（主信号，**带符号**）：负效价 distress 为正、正效价 joy 为负，见 MasterEmotionTracker
    "question": 0.6,      # master 模型判定「正在问复杂客观问题（数学/逻辑/推理）」的认知加分项
}
"""FocusScorer 各信号的相对权重（仅 inline 路径——评分只看用户自己说的话）。
- 用途：scorer 对适用信号按权重**直接加权求和（不归一、无分母）** → 该轮 score（喂给
  累加器）。权重即每个信号的绝对贡献：present 信号加 weight×value 进 score，缺席信号贡献 0。
- 信号语义分两类：
  · keyword / emotion / question 是触发信号——缺席返回 None、不计入（贡献 0，不稀释别的）。
    emotion 是 keyword 词表的真模型升级（故词表权重 0.5 < 模型情绪 0.7），且**带符号**：
    负效价 distress 为正、正效价 joy 为负（neutral 返回 None）——开心会把 score/charge
    往下拉。question 是认知轴加分项（问复杂客观题——数学/逻辑/推理——也值得 thinking-on），
    与 distress 正交但并入同一 charge。
  · cadence 是行为信号——只在样本足够且**有 distress 证据**（keyword / question / emotion>0）
    时才计入（否则一句短的开心话会让 cadence 误推 focus）。无分母后它的 0.0（「字数没骤降」）
    贡献 0、等价于缺席，只有字数真的骤降才往 score 加分。
- ⚠️ 无分母 ⇒ score 不再封顶在 1.0：全信号满格 = 各权重之和（当前 0.5+0.2+0.7+0.6=2.0），
  下游由 FOCUS_CHARGE_CAP 截。调权重 = 直接调每信号绝对推力，也间接改相对 ENTER 的触发难度。
- ⚠️ 单信号能否单轮进 ENTER 取决于「权重×满格值 ≥ ENTER」：去分母后 keyword 满格仅 0.5、
  cadence 0.2，单独都越不过 ENTER(0.6)——脆弱词必须叠加 emotion 或跨轮累积才进（有意：词表
  是廉价信号）；只有 emotion（≥~0.86）与满格 question（1.0×0.6）能单信号单轮进。
- emotion 读 master 情绪画像（MasterEmotionTracker）已算好的最近 VA 读数，映射成
  distress = max(0,-valence) × (FOCUS_EMOTION_AROUSAL_FLOOR + (1-floor)×arousal)——
  负效价主导、arousal 带下限放大（见 FOCUS_EMOTION_AROUSAL_FLOOR）。**滞后一拍**
  （画像异步算，inline 拿上一轮读数）；
  MASTER_EMOTION_ENABLED 关或无读数/无 distress 时返回 None、自动退回 keyword+cadence。
- idle（proactive）路径不评分：它只用 FOCUS_IDLE_SILENT/REPLIED_RETENTION 让 charge
  衰减，绝不抬升，故不在此表里（凝神的进入/维持只由 inline 驱动）。
- 上游：各子信号各自归一化到 [0,1]。
- 设计依据：keyword/emotion 是最强的两个情绪信号故权重最高。改这里直接改触发性格，慎调。"""

FOCUS_KEYWORD_SATURATION = 3
"""脆弱情绪关键词命中数的饱和点。
- 用途：scan_vulnerability_keywords 返回的命中数 / 此值后截到 1.0 作为 keyword
  子信号——单个「累」是轻推，「撑不住 + 一个人 + 没意思」叠加才是满格。
- 上游：config/prompts/prompts_focus.scan_vulnerability_keywords 的命中计数。"""

FOCUS_EMOTION_AROUSAL_FLOOR = 0.5
"""emotion 信号里 arousal（唤醒度）作为放大器的下限。
- 映射：distress = max(0,-valence) × (floor + (1-floor) × arousal)。
- 语义：distress 由「负效价」主导触发，arousal 只在 [floor, 1] 区间缩放强度，
  不再当与门。脆弱/倾诉常是「低唤醒 + 强负效价」（默默难过、丧），旧的纯乘积
  distress = arousal × negativity 会被低 arousal 压到接近 0、漏掉这类安静型 distress；
  给 arousal 一个下限后，强负效价即使唤醒度低也能透过大部分分值。
- 取值：=0 退回旧的纯乘积（arousal 仍是与门）；=1 完全忽略 arousal、纯看 valence；
  0.5 折中——低唤醒保底过半、高唤醒满额放大。须 ∈ [0,1]。
- 上游：FocusScorer._signal_emotion。仅作用于 emotion 子信号，keyword/cadence 不受影响。"""

FOCUS_EMOTION_POSITIVE_SCALE = 0.5
"""正效价（用户开心）时 emotion 信号的「反凝神」幅度系数。
- emotion 信号现在是**带符号**的：负效价 → 正 distress（推进凝神，上限 +1）；正效价 → 负值
  （把 charge 往下拉、别打扰好心情），幅度上限为此系数。
- 映射（正效价侧）：emotion = -(positivity × m × 此系数)，其中 positivity=max(0,valence)，
  m = AROUSAL_FLOOR + (1-AROUSAL_FLOOR)×arousal（与 distress 侧同一 arousal 放大器，∈[0.5,1]）。
- 取 0.5 ⇒ 正效价侧最深为 -0.5（valence=+1、arousal=1，m=1）；valence=+1、arousal=0.3 时
  m=0.65 → emotion=-0.5×0.65=-0.325。即正效价拉力天花板只有负效价 distress 的一半。
- 须 ∈ [0,1]；=0 关闭「开心反凝神」、退回「正效价不投票（None）」。"""

FOCUS_CADENCE_BASELINE_WINDOW = 6
"""cadence 信号的基线窗口：取最近 N 条用户消息长度算中位数做基线。
- 用途：FocusScorer 内 per-session 滚动 buffer 的 maxlen。
- 上游：每条真用户消息的字符长度。"""

FOCUS_CADENCE_MIN_SAMPLES = 3
"""cadence 信号生效所需的最小样本数。
- 用途：buffer 内样本不足 N 时 cadence 子信号判为「不适用」（不进归一化），
  避免会话刚开头基线不稳就乱触发。
- 上游：滚动 buffer 当前长度。"""

FOCUS_CADENCE_DROP_RATIO = 0.4
"""cadence 满格所需的「当前长度 / 基线中位数」下跌比。
- 用途：当前消息长度 ≤ 此比 × 基线中位数 → cadence 子信号 = 1.0；≥ 基线 → 0.0；
  中间线性。例：基线 30 字、ratio 0.4，则 ≤12 字（「嗯。」「知道了。」）算满格。
- 上游：当前消息长度与基线中位数之比。"""

# NOTE: silence / open_thread 信号已移除——idle（proactive）路径不再评分，改为只用
# FOCUS_IDLE_SILENT/REPLIED_RETENTION 让 charge 衰减（凝神进入/维持只由 inline 驱动）。
# 故 FOCUS_SILENCE_MIN_SECONDS / FOCUS_SILENCE_FULL_SECONDS 一并退役，避免死配置。

# NOTE: FOCUS_IDLE_THRESHOLD_MULTIPLIER（凝神态下调低 idle 触发阈值「她降临一次后
# 主动追一两轮」）属 Path B 的 idle-threshold-drop 子特性，该特性尚未接线，故旋钮
# 暂不引入，待实现该 feature 时再随它一起加，避免留下死配置。设计见 blueprint。

# NOTE: FOCUS_EPISODE_MEMORY_ENABLED（凝神退出顺便批量整理 reflection/persona/
# facts/ban-list 的开关）同理暂不引入——FOCUS_EXIT → memory 订阅者特性尚未接线，
# 旋钮待该 PR 实现时随它加回，避免死配置。设计见 docs/design/focus-truename-mode.md。

MINI_GAME_INVITE_ENABLED = True
"""Mini-game 邀请短路通道总开关（默认开）。
- 用途：proactive_chat 在过完 propensity / skip_probability / restricted_screen_only
  这几道门后，按 MINI_GAME_INVITE_TRIGGER_PROBABILITY 概率短路成"邀请玩家来玩
  小游戏"，跳过 Phase 1/2 LLM。关掉此开关 = 永远不触发该分支，proactive_chat
  退化回纯 source-driven。
- 上游：main_routers/system_router._maybe_deliver_mini_game_invite。"""

MINI_GAME_INVITE_TRIGGER_PROBABILITY = 0.12
"""每次 eligible 主动搭话进入 mini-game 邀请短路的概率。
- 取值约定：[0.0, 1.0]，0.0=禁用（等价于 ENABLED=False），1.0=每次都邀请。
- 上游：random.random() < 此值 → 命中 → 走邀请短路。"""

MINI_GAME_INVITE_COOLDOWN_AFTER_ACCEPT_SECONDS = 2 * 3600
"""accept 后的最小静默秒数（默认 2h）。
- 配合 MINI_GAME_INVITE_COOLDOWN_CHATS：两条件都跨过才允许下次掷骰。
- 上游：_mini_game_invite_in_cooldown 时间侧判定（state.last_response_choice='accept'）。
- 历史：原统一 1h（PR follow-up #1 从 24h 降下来），后再拆成 accept/decline 双
  阈值——accept 体感"刚玩完一局"短一些（2h），decline 表达"不感兴趣"延长到 5h
  避免短期复扰；之间没有 chats 门差异，10 条仍共用。"""

MINI_GAME_INVITE_COOLDOWN_AFTER_DECLINE_SECONDS = 5 * 3600
"""decline 后的最小静默秒数（默认 5h）。
- 配合 MINI_GAME_INVITE_COOLDOWN_CHATS：两条件都跨过才允许下次掷骰。
- 上游：_mini_game_invite_in_cooldown 时间侧判定（state.last_response_choice='decline'）。
- 比 accept 长是因为 decline 是明确"不想玩"信号，短期复扰体感差；5h 跨过一般
  的"刚拒绝完几分钟又问"窗口，又不至于一整天彻底沉默。"""

MINI_GAME_INVITE_NEW_USER_FORCE_AT = 4
"""新用户在第 N 次「成功投递的主动搭话」时强制触发 mini-game 邀请。
- 「新用户」= ``state.delivered_at is None``（角色级，从未发过 invite）。
- N 是整数，>=1；当持久化计数 ``proactive_chat_total >= N - 1`` 时，
  本次投递走 force-trigger（绕开 10% 骰子，但仍尊重 propensity / 工作状态 /
  unfinished_thread / cooldown 等其它 gate）。
- 默认 4 = 用户成功收到 3 条普通主动搭话后，第 4 条强制变成游戏邀请；让
  从未玩过的人有一次确定的「被邀请」机会，不靠 10% 骰子赌。
- 上游：_maybe_deliver_mini_game_invite force-first 分支。"""

MINI_GAME_INVITE_AVAILABLE_GAMES: tuple[str, ...] = ("soccer", "badminton")
"""mini-game 邀请可选的 game_type 列表。
- 命中后从该列表 random.choice 选一个，文案从
  config.prompts.prompts_proactive.MINI_GAME_INVITE_LINES_BY_GAME[game_type] 取。
- 当前只有 soccer；badminton 后端与文案在本 PR 预埋，但实际邀请入口需要等
  页面路由和 Electron 窗口注册在后续 PR 落地后再启用。
- 顺序无意义（用 random.choice）；用 tuple 防止运行期被改写。"""

MINI_GAME_INVITE_COOLDOWN_CHATS = 10
"""一次邀请被回应后，需要再经过的"成功投递的主动搭话"次数。
- 与 MINI_GAME_INVITE_COOLDOWN_AFTER_{ACCEPT,DECLINE}_SECONDS 同时满足才解禁；
  任一不满足都继续抑制。chats 门 accept/decline 共用，不按 choice 拆。
- 上游：_mini_game_invite_in_cooldown 计数侧判定。"""

MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS = 5 * 60
"""用户选择「回头再说」后的短期再掷骰抑制秒数（默认 5min）。
- D2 语义：reset state（delivered_at/responded_at/chats_since_response 都清零，
  让 force-first 与普通 10% 掷骰都恢复正常）但加一个 ``suppressed_until`` 软门，
  这段时间内 ``_mini_game_invite_in_cooldown`` 仍返回 True 防止下一次 proactive
  立刻又邀请，体感上像"等等再问我"。过了这个窗口下次 proactive 才重新走骰子。
- 上游：endpoint /api/mini_game/invite/respond 的 'later' action。"""

MINI_GAME_LAUNCH_URL_BY_GAME: dict[str, str] = {
    'soccer': '/soccer_demo',
    'badminton': '/badminton_demo',
}
"""game_type → 实际打开的页面 URL。前端 `window.open(url)` 让 Electron 主进程
``setWindowOpenHandler`` 拦截开独立 BrowserWindow（普通浏览器是新 tab）；URL
会带上 ``?lanlan_name=...&session_id=...`` query。新 mini-game 加新 entry 即可。"""

MINI_GAME_INVITE_FORCE_GAME_TYPE: str | None = None
"""【调试用临时旗标】非 None 时，每次合格的主动搭话都强制走 mini-game 邀请短路，
且使用此值作为 game_type，跳过 activity_snapshot / propensity / away /
unfinished_thread / cooldown / probability / force-first / 用户级 toggle 等所有
gate；仅 ``MINI_GAME_INVITE_ENABLED`` 总开关仍生效作为最后 kill switch。
- 取值约定：None 关闭（生产默认）；'soccer' 等 ``MINI_GAME_INVITE_LINES_BY_GAME``
  里存在的合法 key。非法 key 会在投递时 warn + 跳过。
- 用途：本地手测三 context UI 时，不想等 force-first 凑齐 N-1 次主动搭话、也不
  想反复重启 fixture 调 cooldown。线上不要打开。
- 上游：``main_routers/system_router._maybe_deliver_mini_game_invite``。"""

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

TRANSLATION_CHUNK_MAX_TOKENS_SHORT = 2000
"""翻译短文本路径的分块 token 上限。
- 用途：单次翻译调用的输入 token 数；长文本被切成多块串行翻译。
- 上游：用户/系统传入的待翻译原文。"""

TRANSLATION_CHUNK_MAX_TOKENS_LONG = 5000
"""翻译长文本路径的分块 token 上限。
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
# 默认值不变；额外支持 env 覆盖（opt-in 逃生口，不设就走原 auto 策略）。
# 典型用途：无 AVX-VNNI 的老 CPU 上 auto 会自动关闭向量，用户可设
# NEKO_VECTORS_QUANTIZATION=int8 强制照跑 int8（慢但正确），无需重新打包。
VECTORS_ENABLED = _read_bool_env("VECTORS_ENABLED", True)        # master kill switch
VECTORS_EMBEDDING_DIM = "auto"               # "auto" | 32/64/128/256/512/768
VECTORS_QUANTIZATION = _read_str_env(        # "auto" | "int8" | "fp32" (fp32 needs model.onnx on disk)
    "VECTORS_QUANTIZATION", "auto", allowed=("auto", "int8", "fp32"),
)
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
    focus_extra_body,
    leaks_thinking_in_content,
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
    'focus_extra_body',
    'leaks_thinking_in_content',
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
    'HIDE_DIRTY_VOICE_TRANSCRIPTS',
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
    'EVIDENCE_AI_AWARE_EVERY_N_A_TICKS',
    'MAX_AI_AWARE_WINDOW_MSGS',
    'MAX_KNOWN_POOL_FACTS',
    'EVIDENCE_SIGNAL_CHECK_INTERVAL_SECONDS',
    'EVIDENCE_DETECT_SIGNALS_MAX_OBSERVATIONS',
    'EVIDENCE_ARCHIVE_SWEEP_INTERVAL_SECONDS',
    'ACTIVITY_GUESS_BACKOFF_BASE_SECONDS',
    'ACTIVITY_GUESS_BACKOFF_MULTIPLIER',
    'ACTIVITY_GUESS_BACKOFF_CAP_SECONDS',
    'ACTIVITY_GUESS_SIG_CACHE_SIZE',
    'PERSONA_RENDER_MAX_TOKENS',
    'REFLECTION_RENDER_MAX_TOKENS',
    'PERSONA_RENDER_ENCODING',
    # §3.7 LLM Context & Output Budget
    'RECENT_HISTORY_MAX_ITEMS',
    'RECENT_COMPRESS_THRESHOLD_ITEMS',
    'RECENT_SUMMARY_MAX_TOKENS',
    'RECENT_PER_MESSAGE_MAX_TOKENS',
    'RECENT_COMPRESS_INPUT_BUDGET_TOKENS',
    'RECENT_HARD_CAP_TOKENS',
    'REFLECTION_TEXT_MAX_TOKENS',
    'REFLECTION_SURFACE_TOP_K',
    'REFLECTION_SYNTHESIS_FACTS_MAX',
    'MEMORY_REFLECTION_SYNTHESIS_INTERVAL_SECONDS',
    'REFLECTION_RELATED_PER_QUERY_K',
    'REFLECTION_RELATED_TOTAL_CAP',
    'PERSONA_MERGE_POOL_MAX_TOKENS',
    'PERSONA_CORRECTION_BATCH_LIMIT',
    'PERSONA_VERSION_HISTORY_MAX',
    'MEMORY_LLM_HARD_TIMEOUT_SECONDS',
    'DIALOG_LLM_STREAM_TIMEOUT_SECONDS',
    'FOCUS_THINKING_EXTRA_TOKENS',
    'LLM_OUTPUT_GUARD_MAX_TOKENS',
    'ICEBREAKER_FREE_TEXT_INTERPRETER_TIMEOUT_SECONDS',
    'ICEBREAKER_FREE_TEXT_OUTPUT_MAX_TOKENS',
    'ICEBREAKER_FREE_TEXT_ASSISTANT_LINE_MAX_TOKENS',
    'ICEBREAKER_FREE_TEXT_USER_TEXT_MAX_TOKENS',
    'ICEBREAKER_FREE_TEXT_OPTION_LABEL_MAX_TOKENS',
    'ICEBREAKER_FREE_TEXT_HISTORY_TEXT_MAX_TOKENS',
    'ICEBREAKER_FREE_TEXT_HISTORY_MAX_ITEMS',
    'ICEBREAKER_FREE_TEXT_REPLY_MAX_TOKENS',
    'MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS',
    'MEMORY_REFINE_COSINE_THRESHOLD',
    'MEMORY_REFINE_TOPK_PER_ENTRY',
    'MEMORY_REFINE_CLUSTER_SIZE_MAX',
    'MEMORY_REFINE_REVISIT_AFTER_DAYS',
    'MEMORY_REFINE_CLUSTERS_PER_PASS',
    'MEMORY_REFINE_CRON_INTERVAL_SECONDS',
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
    'AGENT_CALLBACK_TEXT_MAX_TOKENS',
    'AGENT_CALLBACK_TOTAL_MAX_TOKENS',
    'AGENT_CALLBACK_QUEUE_MAX_ITEMS',
    'AGENT_DEDUP_CANDIDATES_MAX',
    'AGENT_TASK_TRACKER_MAX_RECORDS',
    'AGENT_RECENT_CTX_PER_ITEM_TOKENS',
    'AGENT_RECENT_CTX_TOTAL_TOKENS',
    'AGENT_PLUGIN_DESC_BM25_THRESHOLD',
    'AGENT_PLUGIN_SHORTDESC_MAX_TOKENS',
    'AGENT_PLUGIN_COARSE_MAX_TOKENS',
    'AGENT_UNIFIED_ASSESS_MAX_TOKENS',
    'AGENT_PLUGIN_FULL_MAX_TOKENS',
    'AGENT_EXTERNAL_GATE_ENABLED',
    'AGENT_EXTERNAL_GATE_THRESHOLD',
    'PLUGIN_INPUT_DESC_MAX_TOKENS',
    'COMPUTER_USE_MAX_TOKENS',
    'LLM_PING_MAX_TOKENS',
    'OPENCLAW_MAGIC_INTENT_MAX_TOKENS',
    'SESSION_ARCHIVE_TRIGGER_TOKENS',
    'SESSION_TURN_THRESHOLD',
    'USER_DIRECTIVE_TTL_SECONDS',
    'USER_DIRECTIVE_MAX_ACTIVE',
    'ANTI_REPEAT_BG_WINDOW',
    'ANTI_REPEAT_FG_WINDOW',
    'ANTI_REPEAT_FG_TTL_SECONDS',
    'ANTI_REPEAT_INJECT_TOP_K',
    'ANTI_REPEAT_REGEN_THRESHOLD',
    'ANTI_REPEAT_DROP_THRESHOLD',
    'ANTI_REPEAT_BM25_K1',
    'ANTI_REPEAT_BM25_B',
    'ANTI_REPEAT_MIN_DRAFT_TOKENS',
    'ANTI_REPEAT_EXEMPT_SOURCE_TAGS',
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
    'MINI_GAME_INVITE_ENABLED',
    'MINI_GAME_INVITE_TRIGGER_PROBABILITY',
    'MINI_GAME_INVITE_COOLDOWN_AFTER_ACCEPT_SECONDS',
    'MINI_GAME_INVITE_COOLDOWN_AFTER_DECLINE_SECONDS',
    'MINI_GAME_INVITE_COOLDOWN_CHATS',
    'MINI_GAME_INVITE_NEW_USER_FORCE_AT',
    'MINI_GAME_INVITE_AVAILABLE_GAMES',
    'MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS',
    'MINI_GAME_LAUNCH_URL_BY_GAME',
    'MINI_GAME_INVITE_FORCE_GAME_TYPE',
    'PROACTIVE_SOURCE_HARD_SKIP_SECONDS',
    'PROACTIVE_SOURCE_HALF_LIFE_BY_KIND',
    'PROACTIVE_SOURCE_HALF_LIFE_DEFAULT',
    'PROACTIVE_SOURCE_FORGET_P',
    'EMOTION_ANALYSIS_MAX_TOKENS',
    'PLUGIN_USER_CONTEXT_MAX_ITEMS',
    'TRANSLATION_OUTPUT_MAX_TOKENS',
    'TRANSLATION_CHUNK_MAX_TOKENS_SHORT',
    'TRANSLATION_CHUNK_MAX_TOKENS_LONG',
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
