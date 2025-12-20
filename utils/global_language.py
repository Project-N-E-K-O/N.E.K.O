# -*- coding: utf-8 -*-
"""
全局语言管理模块

维护全局语言变量，优先级：
1. Steam 设置
2. 系统设置（区分中文区和非中文区）

根据区域选择不同的翻译服务：
- 中文区：优先使用 translatepy
- 非中文区：优先使用 Google 翻译
"""

import locale
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# 全局语言变量（线程安全）
_global_language: Optional[str] = None
_global_language_lock = threading.Lock()
_global_language_initialized = False

# 全局区域标识（中文区/非中文区）
_global_region: Optional[str] = None  # 'china' 或 'non-china'


def _is_china_region() -> bool:
    """
    判断当前系统是否在中文区
    
    Returns:
        True 表示中文区，False 表示非中文区
    """
    try:
        # 获取系统 locale
        system_locale = locale.getdefaultlocale()[0]
        if system_locale:
            # 检查是否是中文 locale
            system_locale_lower = system_locale.lower()
            if system_locale_lower.startswith('zh'):
                return True
        
        # 如果无法从 locale 判断，尝试从系统语言环境变量判断
        import os
        lang_env = os.environ.get('LANG', '').lower()
        if lang_env.startswith('zh'):
            return True
        
        # 默认判断：如果系统 locale 不是中文，则认为是非中文区
        return False
    except Exception as e:
        logger.warning(f"判断系统区域失败: {e}，默认使用非中文区")
        return False


def _get_system_language() -> str:
    """
    从系统设置获取语言
    
    Returns:
        语言代码 ('zh', 'en', 'ja')，默认返回 'zh'
    """
    try:
        # 获取系统 locale
        system_locale = locale.getdefaultlocale()[0]
        if system_locale:
            system_locale_lower = system_locale.lower()
            if system_locale_lower.startswith('zh'):
                return 'zh'
            elif system_locale_lower.startswith('ja'):
                return 'ja'
            elif system_locale_lower.startswith('en'):
                return 'en'
        
        # 尝试从环境变量获取
        import os
        lang_env = os.environ.get('LANG', '').lower()
        if lang_env.startswith('zh'):
            return 'zh'
        elif lang_env.startswith('ja'):
            return 'ja'
        elif lang_env.startswith('en'):
            return 'en'
        
        return 'zh'  # 默认中文
    except Exception as e:
        logger.warning(f"获取系统语言失败: {e}，使用默认中文")
        return 'zh'


def _get_steam_language() -> Optional[str]:
    """
    从 Steam 设置获取语言
    
    Returns:
        语言代码 ('zh', 'en', 'ja')，如果无法获取则返回 None
    """
    try:
        from main_routers.shared_state import get_steamworks
        
        steamworks = get_steamworks()
        if steamworks is None:
            return None
        
        # Steam 语言代码到我们的语言代码的映射
        STEAM_TO_LANG_MAP = {
            'schinese': 'zh',
            'tchinese': 'zh',
            'english': 'en',
            'japanese': 'ja',
            'ja': 'ja'
        }
        
        # 获取 Steam 当前游戏语言
        steam_language = steamworks.Apps.GetCurrentGameLanguage()
        if isinstance(steam_language, bytes):
            steam_language = steam_language.decode('utf-8')
        
        user_lang = STEAM_TO_LANG_MAP.get(steam_language)
        if user_lang:
            logger.debug(f"从Steam获取用户语言: {steam_language} -> {user_lang}")
            return user_lang
        
        return None
    except Exception as e:
        logger.debug(f"从Steam获取语言失败: {e}")
        return None


def initialize_global_language() -> str:
    """
    初始化全局语言变量（优先级：Steam设置 > 系统设置）
    
    Returns:
        初始化后的语言代码 ('zh', 'en', 'ja')
    """
    global _global_language, _global_region, _global_language_initialized
    
    with _global_language_lock:
        if _global_language_initialized:
            return _global_language or 'zh'
        
        # 判断区域
        _global_region = 'china' if _is_china_region() else 'non-china'
        logger.info(f"系统区域判断: {_global_region}")
        
        # 优先级1：尝试从 Steam 获取
        steam_lang = _get_steam_language()
        if steam_lang:
            _global_language = steam_lang
            logger.info(f"全局语言已初始化（来自Steam）: {_global_language}")
            _global_language_initialized = True
            return _global_language
        
        # 优先级2：从系统设置获取
        system_lang = _get_system_language()
        _global_language = system_lang
        logger.info(f"全局语言已初始化（来自系统设置）: {_global_language}")
        _global_language_initialized = True
        return _global_language


def get_global_language() -> str:
    """
    获取全局语言变量
    
    Returns:
        语言代码 ('zh', 'en', 'ja')，默认返回 'zh'
    """
    global _global_language
    
    with _global_language_lock:
        if not _global_language_initialized:
            return initialize_global_language()
        
        return _global_language or 'zh'


def set_global_language(language: str) -> None:
    """
    设置全局语言变量（手动设置，会覆盖自动检测）
    
    Args:
        language: 语言代码 ('zh', 'en', 'ja')
    """
    global _global_language, _global_language_initialized
    
    # 归一化语言代码
    lang_lower = language.lower()
    if lang_lower.startswith('zh'):
        normalized_lang = 'zh'
    elif lang_lower.startswith('ja'):
        normalized_lang = 'ja'
    elif lang_lower.startswith('en'):
        normalized_lang = 'en'
    else:
        logger.warning(f"不支持的语言代码: {language}，保持当前语言")
        return
    
    with _global_language_lock:
        _global_language = normalized_lang
        _global_language_initialized = True
        logger.info(f"全局语言已手动设置为: {_global_language}")


def get_global_region() -> str:
    """
    获取全局区域标识
    
    Returns:
        'china' 或 'non-china'
    """
    global _global_region
    
    with _global_language_lock:
        if _global_region is None:
            # 如果区域未初始化，先初始化语言（会同时初始化区域）
            initialize_global_language()
        
        return _global_region or 'non-china'


def is_china_region() -> bool:
    """
    判断当前是否在中文区
    
    Returns:
        True 表示中文区，False 表示非中文区
    """
    return get_global_region() == 'china'


def reset_global_language() -> None:
    """
    重置全局语言变量（重新初始化）
    """
    global _global_language, _global_region, _global_language_initialized
    
    with _global_language_lock:
        _global_language = None
        _global_region = None
        _global_language_initialized = False
        logger.info("全局语言变量已重置")

