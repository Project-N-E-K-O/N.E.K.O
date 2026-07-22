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

"""Default character, avatar, lighting, and localized profile configuration."""

from copy import deepcopy
from types import MappingProxyType

from .prompts.prompts_chara import (
    get_lanlan_prompt,
    is_default_prompt as is_default_prompt,
    lanlan_prompt,
)
from .application import logger

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

    Translates content values based on the Steam language setting.
    Note: legacy key names remain unchanged because internal code depends on them.
    Only used when characters.json is created for the first time.

    Args:
        language: Language code ('en', 'ja', 'zh', 'zh-CN', 'zh-TW').
                  If None, fetched from Steam or defaults to 'zh-CN'.

    Returns:
        Localized copy of DEFAULT_CHARACTERS_CONFIG
    """
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
