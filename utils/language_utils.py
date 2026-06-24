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

"""
Language detection and translation utility module
Detects the language of text and translates it into a target language
Priority: Google Translate (googletrans) -> translatepy (only services reachable from mainland China, free) -> LLM translation

Also includes global language management:
- maintains the global language variable, priority: Steam settings > system settings
- decides Chinese region vs non-Chinese region
"""
import re
import locale
import threading
import asyncio
import os
import hashlib
from collections import OrderedDict
from typing import Optional, Tuple, List, Any, Dict
from utils.llm_client import SystemMessage, HumanMessage, create_chat_llm_async
from utils.config_manager import get_config_manager
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type
from utils.steam_state import get_steamworks

logger = get_module_logger(__name__)

# ============================================================================
# 全局语言管理部分（原 global_language.py）
# ============================================================================

# 全局语言变量（线程安全）
_global_language: Optional[str] = None
_global_language_full: Optional[str] = None  # 保留完整语言代码（如 'zh-TW'），用于区分简繁体
_global_language_lock = threading.RLock()
_global_language_initialized = False

# 全局区域标识（中文区/非中文区）
_global_region: Optional[str] = None  # 'china' 或 'non-china'


def _matches_lang_code(lang_lower: str, code: str, aliases: Optional[set] = None) -> bool:
    """Check whether a language string matches the given language code; supports exact codes, locale suffixes (`xx-XX`/`xx_XX`) and explicit aliases.

    Usage: avoids loose matching like `startswith('es')` misclassifying `estonian` or `esperanto` as Spanish.
    Pass an aliases set (e.g. `{'spanish', 'latam'}`) to explicitly accept Steam/system aliases.
    """
    aliases = aliases or set()
    return (
        lang_lower == code
        or lang_lower.startswith(f'{code}-')
        or lang_lower.startswith(f'{code}_')
        or lang_lower in aliases
    )


_SUPPORTED_LANGUAGE_CODES: tuple = ('zh', 'en', 'ja', 'ko', 'ru', 'es', 'pt')
_SUPPORTED_STEAM_LITERALS: frozenset = frozenset({
    'schinese', 'tchinese', 'english', 'japanese',
    'koreana', 'korean', 'russian', 'spanish', 'latam',
    'portuguese', 'brazilian',
})


def is_supported_language_code(raw: Any) -> bool:
    """Decide whether the raw input falls within the supported set that ``normalize_language_code`` truly recognizes.

    Background: ``normalize_language_code`` silently falls back to ``'en'`` for
    unrecognized input, letting garbage (``'undefined'`` / ``'estonian'`` / blanks) be
    silently treated as English and written into downstream state (the global cache or
    ``mgr.user_language``). Every entry point that "accepts a request-body language and
    writes state back" (e.g. ``refresh_global_language`` / ``_absorb_request_language``)
    must use this helper to fence the input out first, then call normalization.

    Supported set = the codes / Steam literals that ``normalize_language_code`` actually
    recognizes; uses ``_matches_lang_code`` instead of ``startswith`` so meaningless
    prefixes like ``estonian`` / ``ptsd`` / ``essential`` don't pass validation.
    """
    if not raw:
        return False
    try:
        s = str(raw).strip().lower()
    except Exception:
        return False
    if not s:
        return False
    if s in _SUPPORTED_STEAM_LITERALS:
        return True
    return any(_matches_lang_code(s, code) for code in _SUPPORTED_LANGUAGE_CODES)


def _is_china_region() -> bool:
    """
    Decide whether the current system is in the Chinese region
    
    Returns:
        True for the Chinese region, False otherwise
    """
    try:
        system_locale = locale.getlocale()[0]
        if system_locale:
            system_locale_lower = system_locale.lower()
            if system_locale_lower.startswith('zh'):
                return True
            if 'chinese' in system_locale_lower and 'china' in system_locale_lower:
                return True
        
        lang_env = os.environ.get('LANG', '').lower()
        if lang_env.startswith('zh'):
            return True
        
        return False
    except Exception as e:
        logger.warning(f"判断系统区域失败: {e}，默认使用非中文区")
        return False


def _get_windows_locale() -> Optional[str]:
    """
    Get the user locale (e.g. 'en-US', 'zh-CN') via the Windows API GetUserDefaultLocaleName.
    Only effective on Windows; returns None on other platforms.

    locale.getlocale() only reflects the locale already set within the Python process;
    Windows doesn't inject it automatically at startup, so on Windows it almost always
    returns (None, None). This function reads the Windows user locale state directly.
    """
    import platform
    if platform.system() != 'Windows':
        return None
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(85)
        ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85)
        return buf.value or None
    except Exception:
        return None


def _get_system_language() -> str:
    """
    Get the language from system settings

    Returns:
        Language code ('zh', 'en', 'ja', 'ko', 'ru'), defaults to 'zh'
    """
    def _parse_locale(s: str) -> Optional[str]:
        s = s.lower()
        if s.startswith('zh') or 'chinese' in s:
            return 'zh'
        if s.startswith('ja') or 'japanese' in s:
            return 'ja'
        if s.startswith('ko') or 'korean' in s:
            return 'ko'
        if s.startswith('ru') or 'russian' in s:
            return 'ru'
        if _matches_lang_code(s, 'es', {'spanish', 'latam'}):
            return 'es'
        if _matches_lang_code(s, 'pt', {'portuguese', 'brazilian'}):
            return 'pt'
        if s.startswith('en') or 'english' in s:
            return 'en'
        return None

    try:
        # 优先：Windows API（locale.getlocale() 在 Windows 上几乎总是 (None, None)）
        windows_locale = _get_windows_locale()
        if windows_locale:
            lang = _parse_locale(windows_locale)
            if lang:
                return lang

        # 次选：Python locale（Unix / 少数 Windows 场景有效）
        system_locale = locale.getlocale()[0]
        if system_locale:
            lang = _parse_locale(system_locale)
            if lang:
                return lang

        # 末选：LANG 环境变量（Unix 常见）
        lang_env = os.environ.get('LANG', '')
        if lang_env:
            lang = _parse_locale(lang_env)
            if lang:
                return lang

        return 'en'  # 默认英文
    except Exception as e:
        logger.warning(f"获取系统语言失败: {e}，使用默认英文")
        return 'en'


def _get_steam_language() -> Optional[str]:
    """
    Get the language from Steam settings
    
    Returns:
        Language code ('zh', 'en', 'ja', 'ko', 'ru'), or None if unavailable
    """
    try:
        steamworks = get_steamworks()
        if steamworks is None:
            return None

        # Steam 语言代码到我们的语言代码的映射
        STEAM_TO_LANG_MAP = {
            'schinese': 'zh',
            'tchinese': 'zh-TW',
            'english': 'en',
            'japanese': 'ja',
            'ja': 'ja',
            'koreana': 'ko',
            'korean': 'ko',
            'ko': 'ko',
            'russian': 'ru',
            'ru': 'ru',
            'spanish': 'es',
            'latam': 'es',
            'es': 'es',
            'portuguese': 'pt',
            'brazilian': 'pt',
            'pt': 'pt',
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
    Initialize the global language variable (priority: Steam settings > system settings)
    
    Returns:
        The initialized language code ('zh', 'en', 'ja', 'ko')
    """
    global _global_language, _global_language_full, _global_region, _global_language_initialized
    
    with _global_language_lock:
        if _global_language_initialized:
            return _global_language or 'en'
        
        # 判断区域
        if _is_china_region():
            _global_region = 'china'
        else:
            _global_region = 'non-china'
        logger.info(f"系统区域判断: {_global_region}")
        
        # 优先级1：尝试从 Steam 获取
        steam_lang = _get_steam_language()
        if steam_lang:
            # 归一化 Steam 语言代码为短格式
            _global_language = normalize_language_code(steam_lang, format='short')
            _global_language_full = normalize_language_code(steam_lang, format='full')
            logger.info(f"全局语言已初始化（来自Steam）: {_global_language} (full: {_global_language_full})")
            _global_language_initialized = True
            return _global_language
        
        # 优先级2：从系统设置获取
        system_lang = _get_system_language()
        _global_language = normalize_language_code(system_lang, format='short')
        _global_language_full = normalize_language_code(system_lang, format='full')
        logger.info(f"全局语言已初始化（来自系统设置）: {_global_language}")
        _global_language_initialized = True
        return _global_language


def get_global_language() -> str:
    """
    Get the global language variable
    
    Returns:
        Language code ('zh', 'en', 'ja', 'ko', 'ru', 'es', 'pt'), defaults to 'zh'
    """
    global _global_language
    
    with _global_language_lock:
        if not _global_language_initialized:
            return initialize_global_language()
        
        return _global_language or 'en'


def get_global_language_full() -> str:
    """
    Get the global language variable (full format, keeping distinctions like zh-TW)
    
    Difference from get_global_language(): the latter returns the short form ('zh'),
    this function keeps the full code ('zh-TW') for scenarios distinguishing Simplified/Traditional.
    
    Returns:
        Language code ('zh', 'zh-TW', 'en', 'ja', 'ko', 'ru'), defaults to 'zh'
    """
    with _global_language_lock:
        if not _global_language_initialized:
            initialize_global_language()
        
        return _global_language_full or _global_language or 'en'


def set_global_language(language: str) -> None:
    """
    Set the global language variable (manual setting, overrides auto detection)
    
    Args:
        language: language code ('zh', 'en', 'ja', 'ko')
    """
    global _global_language, _global_language_full, _global_language_initialized
    
    # 归一化语言代码
    lang_lower = language.lower()
    if lang_lower.startswith('zh'):
        normalized_lang = 'zh'
    elif lang_lower.startswith('ja'):
        normalized_lang = 'ja'
    elif lang_lower.startswith('ko'):
        normalized_lang = 'ko'
    elif lang_lower.startswith('ru'):
        normalized_lang = 'ru'
    elif _matches_lang_code(lang_lower, 'es', {'spanish', 'latam'}):
        normalized_lang = 'es'
    elif _matches_lang_code(lang_lower, 'pt', {'portuguese', 'brazilian'}):
        normalized_lang = 'pt'
    elif lang_lower.startswith('en'):
        normalized_lang = 'en'
    else:
        logger.warning(f"不支持的语言代码: {language}，保持当前语言")
        return
    
    full_lang = normalize_language_code(language, format='full')
    
    with _global_language_lock:
        _global_language = normalized_lang
        _global_language_full = full_lang
        _global_language_initialized = True
        logger.info(f"全局语言已手动设置为: {_global_language} (full: {_global_language_full})")


def refresh_global_language(language: str) -> bool:
    """Recalibrate the global language cache (for late-arriving truth, e.g. obtained only after a Steam SDK startup race failed).

    Differences from ``set_global_language``:
    - silent no-op: if the normalized value equals the current cache, return ``False``
      directly without an INFO log (the frontend i18n bootstrap calls
      ``/api/config/steam_language``, triggering a refresh on every cold start; it
      shouldn't spam each time).
    - only overwrites and logs when the value differs / the cache is uninitialized.

    Motivation: ``initialize_global_language`` runs only once at process startup; if the
    Steam SDK isn't ready it degrades to the system locale and **caches it for life**;
    the frontend's ``/api/config/steam_language`` endpoint re-reads Steam every time and
    gets the right value, but had no path to write it back to the global cache — so all
    downstream consumers of ``get_global_language()`` (memory / reflection / tts /
    soccer fallback, etc.) kept using the wrong English. This function is that
    write-back path.

    Returns:
        ``True`` when a real change happened; ``False`` when already current or the
        argument is invalid.
    """
    global _global_language, _global_language_full, _global_region, _global_language_initialized

    if not language:
        return False

    # 用 ``is_supported_language_code`` 把 garbage / ``'undefined'`` / ``'estonian'``
    # 等未识别值挡在外面：normalize 对未识别输入会默认回退到 ``'en'``，会静默把缓存
    # 误覆盖，违背"无效就 no-op"的契约。
    if not is_supported_language_code(language):
        return False
    raw = language.strip().lower()

    short = normalize_language_code(raw, format='short')
    full = normalize_language_code(raw, format='full')
    if not short:
        return False

    with _global_language_lock:
        # ``_global_region is not None`` 也要 hold，否则 ``set_global_language`` 这条
        # pre-existing 路径（只置 ``_global_language_initialized=True``、不碰 region）
        # 留下的 ``language=short, region=None`` 状态会让本次 refresh 走早 return，
        # 漏掉 region 自愈，``get_global_region`` 永久卡 ``'non-china'`` fallback。
        if (_global_language_initialized
                and _global_language == short
                and _global_language_full == full
                and _global_region is not None):
            return False
        prev_short = _global_language
        prev_full = _global_language_full
        _global_language = short
        _global_language_full = full
        # _global_region 必须和 _global_language_initialized 同时建立：``get_global_region``
        # 看到 region 为 None 才会再调 ``initialize_global_language``，但后者一旦看到
        # initialized=True 就 early-return，会把 region 永久卡在 None → 'non-china'
        # fallback。startup 路径正常会先初始化 region；但 startup 异常 / 测试 / 子进程
        # 等场景下若 refresh 先于 init 跑到这里，必须自补 region 来维持不变量。
        if _global_region is None:
            try:
                _global_region = 'china' if _is_china_region() else 'non-china'
                logger.info(f"系统区域判断（refresh 路径补齐）: {_global_region}")
            except Exception:
                _global_region = 'non-china'
                logger.debug("refresh_global_language 补齐 region 失败，回落 non-china", exc_info=True)
        _global_language_initialized = True
        logger.info(
            f"全局语言已刷新（晚到真值覆盖）: {prev_short} -> {short} "
            f"(full: {prev_full} -> {full})"
        )
    return True


def get_global_region() -> str:
    """
    Get the global region identifier
    
    Returns:
        'china' or 'non-china'
    """
    global _global_region
    
    with _global_language_lock:
        if _global_region is None:
            # 如果区域未初始化，先初始化语言（会同时初始化区域）
            initialize_global_language()
        
        return _global_region or 'non-china'


def is_china_region() -> bool:
    """
    Decide whether we are currently in the Chinese region
    
    Returns:
        True for the Chinese region, False otherwise
    """
    return get_global_region() == 'china'


def reset_global_language() -> None:
    """
    Reset the global language variable (re-initialize)
    """
    global _global_language, _global_language_full, _global_region, _global_language_initialized
    
    with _global_language_lock:
        _global_language = None
        _global_language_full = None
        _global_region = None
        _global_language_initialized = False
        logger.info("全局语言变量已重置")


def normalize_language_code(lang: str, format: str = 'short') -> str:
    """
    Normalize a language code (uniformly handles 'zh', 'zh-CN', Steam language codes, etc.)
    
    This function is a public API for reuse by other modules.
    
    Supported input formats:
    - standard language codes: 'zh', 'zh-CN', 'zh-TW', 'en', 'en-US', 'ja', 'ja-JP', 'ko', 'ko-KR', etc.
    - Steam language codes: 'schinese', 'tchinese', 'english', 'japanese', etc.
    
    Args:
        lang: input language code
        format: output format
            - 'short': returns the short form ('zh', 'en', 'ja', 'ko')
            - 'full': returns the full form ('zh-CN', 'zh-TW', 'en', 'ja', 'ko')
        
    Returns:
        The normalized language code, or the default ('zh' or 'zh-CN') when unrecognizable
    """
    if not lang:
        return 'en'
    
    lang_lower = lang.lower().strip()
    
    # Steam 语言代码映射
    # 参考: https://partner.steamgames.com/doc/store/localization/languages
    STEAM_LANG_MAP = {
        'schinese': 'zh',      # 简体中文
        'tchinese': 'zh-TW',   # 繁体中文
        'english': 'en',       # 英文
        'japanese': 'ja',      # 日语
        'koreana': 'ko',       # 韩语
        'korean': 'ko',        # 兼容
        'russian': 'ru',       # 俄语
        'spanish': 'es',       # 西班牙语（欧洲）
        'latam': 'es',         # 西班牙语（拉美）— 归一到 es
        'portuguese': 'pt',    # 葡萄牙语（欧洲）
        'brazilian': 'pt',     # 葡萄牙语（巴西）— 归一到 pt
    }
    
    # 先检查是否是 Steam 语言代码
    if lang_lower in STEAM_LANG_MAP:
        normalized = STEAM_LANG_MAP[lang_lower]
        # 对 Steam 映射结果也应用短格式归一化
        if format == 'short':
            if normalized.startswith('zh'):
                return 'zh'
            elif normalized.startswith('ja'):
                return 'ja'
            elif normalized.startswith('en'):
                return 'en'
            elif normalized.startswith('ko'):
                return 'ko'
            elif normalized.startswith('ru'):
                return 'ru'
            elif normalized.startswith('es'):
                return 'es'
            elif normalized.startswith('pt'):
                return 'pt'
        elif format == 'full' and normalized == 'zh':
            return 'zh-CN'
        return normalized
    
    # 标准语言代码处理
    if lang_lower.startswith('zh'):
        # 区分简体和繁体中文
        if 'tw' in lang_lower or 'hant' in lang_lower or 'hk' in lang_lower:
            if format == 'full':
                return 'zh-TW'
            else:
                return 'zh'
        else:
            if format == 'short':
                return 'zh'
            else:
                return 'zh-CN'
    elif lang_lower.startswith('ja'):
        return 'ja'
    elif lang_lower.startswith('ko'):
        return 'ko'
    elif lang_lower.startswith('ru'):
        return 'ru'
    elif _matches_lang_code(lang_lower, 'es', {'spanish', 'latam'}):
        return 'es'
    elif _matches_lang_code(lang_lower, 'pt', {'portuguese', 'brazilian'}):
        return 'pt'
    elif lang_lower.startswith('en'):
        return 'en'
    else:
        # 无法识别的语言代码，返回默认值
        logger.debug(f"无法识别的语言代码: {lang}，返回默认值")
        return 'en'


# ============================================================================
# 语言检测和翻译部分（原 language_utils.py）
# ============================================================================

# 翻译后端懒加载：translatepy（~0.2s，含各翻译器的语言数据表）和 googletrans
# 都只在真正翻译时才需要，不在 greeting 链上。改成首次使用时再 import，并由
# utils.module_warmup 在 ready 后预热，使启动链不付这笔钱。
Translator = None  # googletrans.Translator
TranslatepyTranslator = None
CHINA_ACCESSIBLE_SERVICES = None
GOOGLETRANS_AVAILABLE: bool | None = None  # None = 尚未尝试导入
TRANSLATEPY_AVAILABLE: bool | None = None


def _ensure_googletrans() -> bool:
    """Import googletrans on first call and cache the result. Returns availability."""
    global Translator, GOOGLETRANS_AVAILABLE
    # 显式强制不可用优先 → 降级。
    if GOOGLETRANS_AVAILABLE is False:
        return False
    # 已注入/导入过 Translator → 信任，不重导入。
    if Translator is not None:
        GOOGLETRANS_AVAILABLE = True
        return True
    try:
        from googletrans import Translator as _GTrans
        # 只补缺失，保住测试可能注入的 Translator mock。
        if Translator is None:
            Translator = _GTrans
        GOOGLETRANS_AVAILABLE = True
        logger.debug("googletrans 导入成功")
    except ImportError as e:
        GOOGLETRANS_AVAILABLE = False
        logger.warning(f"googletrans 导入失败（未安装）: {e}，将跳过 Google 翻译")
    except Exception as e:
        GOOGLETRANS_AVAILABLE = False
        logger.warning(f"googletrans 导入失败（其他错误）: {e}，将跳过 Google 翻译")
    return GOOGLETRANS_AVAILABLE


def _ensure_translatepy() -> bool:
    """Import translatepy and the translators reachable from mainland China on first call; cache the result."""
    global TranslatepyTranslator, CHINA_ACCESSIBLE_SERVICES, TRANSLATEPY_AVAILABLE
    # 显式强制不可用优先 → 降级。
    if TRANSLATEPY_AVAILABLE is False:
        return False
    # 已注入/导入过（翻译器 + 服务列表都就位）→ 信任，不重导入。
    if TranslatepyTranslator is not None and CHINA_ACCESSIBLE_SERVICES is not None:
        TRANSLATEPY_AVAILABLE = True
        return True
    try:
        from translatepy import Translator as _TPyTrans
        # 导入在中国大陆可直接访问的翻译服务
        from translatepy.translators.microsoft import MicrosoftTranslate
        from translatepy.translators.bing import BingTranslate
        from translatepy.translators.reverso import ReversoTranslate
        from translatepy.translators.libre import LibreTranslate
        from translatepy.translators.mymemory import MyMemoryTranslate
        from translatepy.translators.translatecom import TranslateComTranslate
        # 只补缺失，保住测试可能注入的 TranslatepyTranslator / 服务列表 mock。
        if TranslatepyTranslator is None:
            TranslatepyTranslator = _TPyTrans
        if CHINA_ACCESSIBLE_SERVICES is None:
            # 中国大陆可直接访问的翻译服务（排除需要代理的 Google、Yandex、DeepL）
            CHINA_ACCESSIBLE_SERVICES = [
                MicrosoftTranslate,
                BingTranslate,
                ReversoTranslate,
                LibreTranslate,
                MyMemoryTranslate,
                TranslateComTranslate,
            ]
        TRANSLATEPY_AVAILABLE = True
        logger.debug("translatepy 导入成功，已配置中国大陆可访问的翻译服务")
    except ImportError as e:
        TRANSLATEPY_AVAILABLE = False
        logger.warning(f"translatepy 导入失败（未安装）: {e}，将跳过 translatepy 翻译")
    except Exception as e:
        TRANSLATEPY_AVAILABLE = False
        logger.warning(f"translatepy 导入失败（其他错误）: {e}，将跳过 translatepy 翻译")
    return TRANSLATEPY_AVAILABLE

# 进程级 Google 翻译失败标记：一旦 Google 在本进程内失败过一次，
# 后续直接跳过 Google，避免每个请求都等满超时。前端的 skip_google
# 仅是会话级（刷新即丢），这里补足后端的进程级持久化，跨请求生效。
_google_translate_failed_flag = False
_google_translate_failed_lock = threading.Lock()


def _is_google_marked_failed() -> bool:
    with _google_translate_failed_lock:
        return _google_translate_failed_flag


def _mark_google_failed() -> None:
    global _google_translate_failed_flag
    with _google_translate_failed_lock:
        if not _google_translate_failed_flag:
            _google_translate_failed_flag = True
            logger.info("⛔ [翻译服务] Google 翻译已被标记为不可用，本进程后续请求将直接跳过")


def reset_google_translate_failure_flag() -> None:
    """Reset the Google Translate failure flag (for tests or after network recovery)"""
    global _google_translate_failed_flag
    with _google_translate_failed_lock:
        _google_translate_failed_flag = False
        logger.info("🔄 [翻译服务] 已清除 Google 翻译失败标记")


# 语言检测正则表达式
CHINESE_PATTERN = re.compile(r'[\u4e00-\u9fff]')
JAPANESE_PATTERN = re.compile(r'[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]')  # 平假名、片假名、汉字
ENGLISH_PATTERN = re.compile(r'[a-zA-Z]')
KOREAN_PATTERN = re.compile(r'[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]')  # 谚文
RUSSIAN_PATTERN = re.compile(r'[\u0400-\u04ff]')  # 西里尔字母（俄语）
# 西班牙语/葡萄牙语特征字符
# 策略：西/葡共享 Latin 脚本，无法纯靠字符集精准区分。
# 只匹配"强指示字符"（es: ñ ¡ ¿；pt: ã õ），命中则判定；否则归为 en/unknown，
# 交由 translate_text() 中 Google/translatepy 的 source='auto' 再精确识别。
SPANISH_STRONG_PATTERN = re.compile(r'[ñÑ¡¿]')
PORTUGUESE_STRONG_PATTERN = re.compile(r'[ãÃõÕ]')

# TTS 开头几个字符的轻量语言检测
# 场景：WS bistream TTS provider（CosyVoice/Qwen/Step）在建立 session 前
# 扫描缓冲的首批文本，把语言提示传给服务端。命中假名直接判日语，其他语言
# 交由服务端自动识别（provider 一般支持中/英/韩/粤等自动识别，但对日语在
# 上下文不够时容易判错）。
_TTS_KANA_RE = re.compile(r'[\u3040-\u309f\u30a0-\u30ff]')
TTS_LANG_DETECT_MIN_CHARS = 6


def detect_tts_language_hint(text: str) -> Optional[str]:
    """Scan the text; returns 'ja' on kana hits, otherwise None (leaving auto-detection to the server).

    The return value is a provider-agnostic language code ('ja'). Each TTS provider
    worker maps the code to its own field:
      - CosyVoice: language_hints=["ja"]
      - Qwen:      session.language_type="Japanese"
      - lanlan.tech (step free): voice_label={"language": "日语"}
      - lanlan.app (step free):  language_code="ja-JP"
    """  # noqa: DOCSTRING_CJK
    if text and _TTS_KANA_RE.search(text):
        return 'ja'
    return None


# lanlan.app（海外免费 Gemini 代理）streaming-TTS / realtime 用的 language_code。
# BCP-47 风格（cmn=普通话）。TTS server 与 core/realtime 两条路共用，建 session
# 时一次性指定。日语另由 lang_hint 覆盖成 'ja-JP'。
TTS_LANGUAGE_CODE_MAP = {
    'zh':    'cmn-CN',
    'zh-CN': 'cmn-CN',
    'zh-TW': 'cmn-tw',
    'en':    'en-US',
    'ja':    'ja-JP',
    'ko':    'ko-KR',
    'pt':    'pt-BR',
    'es':    'es-ES',
    'fr':    'fr-FR',
    'de':    'de-DE',
    'it':    'it-IT',
    'ru':    'ru-RU',
    'tr':    'tr-TR',
}


def get_tts_language_code() -> str:
    """Return the language_code required by lanlan.app for the current global language, defaulting to 'cmn-CN'."""
    try:
        lang = normalize_language_code(get_global_language_full(), format='full')
    except Exception:
        lang = 'zh-CN'
    return TTS_LANGUAGE_CODE_MAP.get(lang, 'cmn-CN')


def _find_token_chunk_end(text: str, start: int, max_tokens: int) -> int:
    """Return the largest character end offset whose slice fits max_tokens."""
    from utils.tokenize import count_tokens

    low = start + 1
    high = len(text)
    best = start
    while low <= high:
        mid = (low + high) // 2
        candidate = text[start:mid]
        if count_tokens(candidate) <= max_tokens:
            best = mid
            low = mid + 1
        else:
            high = mid - 1

    return best


def _split_text_into_token_chunks(text: str, max_tokens: int) -> List[str]:
    """
    Split text into token-bounded chunks, trying to split at periods, newlines, etc.
    
    Args:
        text: text to split
        max_tokens: maximum tokens per chunk
        
    Returns:
        List of text chunks
    """
    from utils.tokenize import count_tokens

    if not text:
        return [text]
    if max_tokens <= 0:
        return [text]

    if count_tokens(text) <= max_tokens:
        return [text]
    
    chunks: List[str] = []
    start = 0
    while start < len(text):
        chunk_end = _find_token_chunk_end(text, start, max_tokens)
        if chunk_end <= start:
            # A single code point already exceeds max_tokens. Emit it as its own
            # (over-budget but indivisible) chunk and advance, rather than
            # silently dropping the rest of the text.
            chunks.append(text[start:start + 1])
            start += 1
            continue

        current_chunk = text[start:chunk_end]
        # 尝试在句号、换行符等位置分割，避免把一句话砍在中间。
        last_period = max(
            current_chunk.rfind('。'),
            current_chunk.rfind('.'),
            current_chunk.rfind('！'),
            current_chunk.rfind('!'),
            current_chunk.rfind('？'),
            current_chunk.rfind('?'),
            current_chunk.rfind('\n')
        )
        if last_period > len(current_chunk) * 0.7:
            split_at = last_period + 1
            chunks.append(text[start:start + split_at])
            start += split_at
        else:
            chunks.append(current_chunk)
            start = chunk_end
    
    return chunks


async def translate_with_translatepy(text: str, source_lang: str, target_lang: str) -> Optional[str]:
    """
    Translate using translatepy (only translation services directly reachable from mainland China; free, no API key needed)
    
    Supported services (by priority):
    - MicrosoftTranslate (Microsoft Translator)
    - BingTranslate (Bing Translator)
    - ReversoTranslate (Reverso)
    - LibreTranslate (open-source service)
    - MyMemoryTranslate (MyMemory)
    - TranslateComTranslate (Translate.com)
    
    Excludes services that require a proxy: Google, Yandex, DeepL
    
    Args:
        text: text to translate
        source_lang: source language code (our format, e.g. 'zh', 'en', 'ja', 'ko')
        target_lang: target language code (our format, e.g. 'zh', 'en', 'ja', 'ko')
        
    Returns:
        Translated text, or None on failure
    """
    if not text or not text.strip() or not _ensure_translatepy():
        return None

    try:
        # translatepy 的语言代码映射（translatepy 支持多种语言名称和代码）
        TRANSLATEPY_LANG_MAP = {
            'zh': 'Chinese',  # 简体中文
            'en': 'English',
            'ja': 'Japanese',
            'ko': 'Korean',
            'ru': 'Russian',
            'es': 'Spanish',
            'pt': 'Portuguese',
            'auto': 'auto'
        }
        
        if source_lang != 'unknown':
            translatepy_source = TRANSLATEPY_LANG_MAP.get(source_lang, source_lang)
        else:
            translatepy_source = 'auto'
        translatepy_target = TRANSLATEPY_LANG_MAP.get(target_lang, target_lang)
        
        # 如果源语言和目标语言相同，不需要翻译
        if translatepy_source == translatepy_target and translatepy_source != 'auto':
            return None
        
        # translatepy 是同步的，需要在线程池中运行以避免阻塞
        def _translate_sync(text_to_translate: str, target: str, source: Optional[str] = None) -> Optional[str]:
            """Synchronous translation function, run in a thread pool; only uses translation services reachable from mainland China"""
            try:
                # 创建 Translator 实例，并指定只使用中国大陆可访问的服务
                translator = TranslatepyTranslator()
                # 修改 services 属性，只使用可访问的服务
                translator.services = CHINA_ACCESSIBLE_SERVICES
                
                # 按优先级尝试各个服务
                for service_class in CHINA_ACCESSIBLE_SERVICES:
                    try:
                        # 创建单个服务实例进行翻译
                        service_instance = service_class()
                        # 如果 source 是 None，使用 'auto'
                        if source:
                            source_param = source
                        else:
                            source_param = 'auto'
                        result = service_instance.translate(text_to_translate, destination_language=target, source_language=source_param)
                        if result and hasattr(result, 'result') and result.result:
                            return result.result
                    except Exception:
                        continue
                
                # 如果所有单个服务都失败，尝试使用 Translator 的自动选择（但只使用可访问的服务）
                if source:
                    source_param = source
                else:
                    source_param = 'auto'
                result = translator.translate(text_to_translate, destination_language=target, source_language=source_param)
                if result and hasattr(result, 'result') and result.result:
                    return result.result
                else:
                    return None
            except Exception:
                return None
        
        # 如果文本太长，分段翻译
        from config import TRANSLATION_CHUNK_MAX_TOKENS_SHORT
        max_chunk_tokens = TRANSLATION_CHUNK_MAX_TOKENS_SHORT
        chunks = _split_text_into_token_chunks(text, max_chunk_tokens)
        
        if len(chunks) > 1:
            # 在线程池中翻译每个分段
            loop = asyncio.get_running_loop()
            translated_chunks = []
            for chunk in chunks:
                try:
                    if translatepy_source != 'auto':
                        chunk_source = translatepy_source
                    else:
                        chunk_source = None
                    chunk_result = await loop.run_in_executor(
                        None, 
                        _translate_sync, 
                        chunk, 
                        translatepy_target, 
                        chunk_source
                    )
                    if chunk_result:
                        translated_chunks.append(chunk_result)
                    else:
                        logger.warning("translatepy 分段翻译返回空结果")
                        return None
                except Exception as chunk_error:
                    logger.warning(f"translatepy 分段翻译异常: {type(chunk_error).__name__}: {chunk_error}")
                    return None
            
            translated_text = ''.join(translated_chunks)
        else:
            # 单次翻译，在线程池中运行
            loop = asyncio.get_running_loop()
            if translatepy_source != 'auto':
                chunk_source = translatepy_source
            else:
                chunk_source = None
            translated_text = await loop.run_in_executor(
                None, 
                _translate_sync, 
                text, 
                translatepy_target, 
                chunk_source
            )
        
        if translated_text and translated_text.strip():
            return translated_text
        else:
            return None
            
    except Exception:
        return None


def detect_language(text: str) -> str:
    """
    Detect the primary language of a text
    
    Args:
        text: text to examine
        
    Returns:
        'zh' (Chinese), 'ja' (Japanese), 'ko' (Korean), 'en' (English), or 'unknown'
    """
    if not text or not text.strip():
        return 'unknown'

    # 统计各语言字符数量
    chinese_count = len(CHINESE_PATTERN.findall(text))
    japanese_count = len(JAPANESE_PATTERN.findall(text)) - chinese_count  # 减去汉字（因为中日共用）
    korean_count = len(KOREAN_PATTERN.findall(text))
    english_count = len(ENGLISH_PATTERN.findall(text))
    russian_count = len(RUSSIAN_PATTERN.findall(text))
    spanish_strong = SPANISH_STRONG_PATTERN.search(text) is not None
    portuguese_strong = PORTUGUESE_STRONG_PATTERN.search(text) is not None

    # 如果包含日文假名，优先判断为日语
    if japanese_count > 0:
        if japanese_count >= chinese_count * 0.2:
            return 'ja'

    # 判断主要语言
    # 注意：如果包含假名已经在上面返回 'ja' 了，这里只需要判断中文和英文
    if korean_count >= chinese_count and korean_count >= english_count and korean_count >= russian_count and korean_count > 0:
        return 'ko'
    if russian_count >= chinese_count and russian_count >= english_count and russian_count > 0:
        return 'ru'
    # 西/葡只在"强指示字符"命中时判定，避免把纯英文/法文等误判
    if portuguese_strong and english_count > 0:
        return 'pt'
    if spanish_strong and english_count > 0:
        return 'es'
    if chinese_count >= english_count and chinese_count > 0:
        return 'zh'
    elif english_count > 0:
        return 'en'
    else:
        return 'unknown'


async def translate_text(text: str, target_lang: str, source_lang: Optional[str] = None, skip_google: bool = False) -> Tuple[str, bool]:
    """
    Translate text into the target language

    Picks the translation service priority by system region:
    - Chinese region: translatepy directly → LLM translation (no Google attempt, avoiding waiting out the full timeout every time)
    - non-Chinese region: Google Translate → LLM translation (once Google fails, a process-level flag makes later requests skip it)

    Args:
        text: text to translate
        target_lang: target language code ('zh', 'en', 'ja', 'ko', 'ru')
        source_lang: source language code; auto-detected when None
        skip_google: whether to skip Google Translate (legacy-caller compatibility, ORed with the process-level flag)

    Returns:
        (translated_text, google_failed): returns the original text on failure;
        google_failed indicates whether Google Translate was marked unavailable this time (or earlier)
    """
    if not text or not text.strip():
        return text, _is_google_marked_failed()

    # 自动检测源语言
    auto_detected_source = source_lang is None
    if auto_detected_source:
        source_lang = detect_language(text)

    # 西/葡与英文共享拉丁脚本：detect_language 在没有 ñ¡¿/ãõ 等强特征字符时
    # 会回退到 'en'。此时如果目标是 es/pt/en，固定 src='en' 会让翻译器把
    # "Hola como estas" 当英文返回原文。改为下游用 'auto' 让 Google/translatepy
    # 自己再检测一次喵。
    ambiguous_latin_source = (
        auto_detected_source
        and source_lang == 'en'
        and target_lang in {'en', 'es', 'pt'}
    )

    # 如果源语言和目标语言相同，不需要翻译（歧义拉丁语料除外，仍要让翻译器做一次）
    if (source_lang == target_lang and not ambiguous_latin_source) or source_lang == 'unknown':
        logger.debug(f"跳过翻译: 源语言({source_lang}) == 目标语言({target_lang}) 或源语言未知")
        return text, _is_google_marked_failed()

    # 判断当前区域，决定翻译服务优先级
    try:
        is_china = is_china_region()
    except Exception as e:
        logger.warning(f"获取区域信息失败: {e}，默认使用非中文区优先级")
        is_china = False

    region_str = '中文区' if is_china else '非中文区'
    logger.debug(f"🔄 [翻译服务] 开始翻译流程: {source_lang} -> {target_lang}, 文本长度: {len(text)}, 区域: {region_str}")

    # 中文区：完全跳过 Google；非中文区：综合调用方意愿与进程级标记
    skip_google_effective = is_china or skip_google or _is_google_marked_failed()
    google_failed = _is_google_marked_failed()
    
    # 语言代码映射：我们的代码 -> Google Translate 代码
    GOOGLE_LANG_MAP = {
        'zh': 'zh-cn',  # 简体中文
        'en': 'en',
        'ja': 'ja',
        'ko': 'ko',
        'ru': 'ru',
        'es': 'es',
        'pt': 'pt',
    }
    
    google_target = GOOGLE_LANG_MAP.get(target_lang, target_lang)
    if ambiguous_latin_source:
        # 拉丁脚本歧义文本：让 Google 自己再检测一次源语言
        google_source = 'auto'
    elif source_lang != 'unknown':
        google_source = GOOGLE_LANG_MAP.get(source_lang, source_lang)
    else:
        google_source = 'auto'
    
    # 辅助函数：尝试 Google 翻译（带超时机制）
    async def _try_google_translate(timeout: float = 5.0) -> Optional[str]:
        """
        Try Google Translate; returns the translation or None
        
        Args:
            timeout: timeout in seconds, default 5. On timeout Google Translate is considered unavailable and we degrade immediately
        
        Returns:
            Translation result, or None (on timeout or failure)
        """
        if not _ensure_googletrans():
            return None

        try:
            translator = Translator()
            
            # 使用 asyncio.wait_for 实现超时机制
            async def _translate_internal():
                # 如果文本太长，分段翻译
                from config import TRANSLATION_CHUNK_MAX_TOKENS_LONG
                max_chunk_tokens = TRANSLATION_CHUNK_MAX_TOKENS_LONG
                chunks = _split_text_into_token_chunks(text, max_chunk_tokens)
                
                if len(chunks) > 1:
                    # 翻译每个分段（第一个分段使用auto检测，后续使用已检测的源语言）
                    translated_chunks = []
                    for i, chunk in enumerate(chunks):
                        # 第一个分段可以使用auto，后续分段使用已检测的源语言
                        if i > 0 or source_lang != 'unknown':
                            chunk_source = google_source
                        else:
                            chunk_source = 'auto'
                        # googletrans 4.0+ 的 translate 方法返回协程，需要使用 await
                        result = await translator.translate(chunk, src=chunk_source, dest=google_target)
                        translated_chunks.append(result.text)
                    
                    return ''.join(translated_chunks)
                else:
                    # 单次翻译
                    # googletrans 4.0+ 的 translate 方法返回协程，需要使用 await
                    result = await translator.translate(text, src=google_source, dest=google_target)
                    return result.text
            
            # 使用超时机制：如果 Google 翻译在指定时间内没有响应，立即返回 None
            translated_text = await asyncio.wait_for(_translate_internal(), timeout=timeout)
            return translated_text
            
        except asyncio.TimeoutError:
            logger.debug(f"⏱️ [翻译服务] Google翻译超时（{timeout}秒），认为不可用，立即降级")
            return None
        except Exception as e:
            logger.debug(f"❌ [翻译服务] Google翻译失败: {type(e).__name__}")
            return None
    
    # 根据区域选择不同的优先级
    if is_china:
        # 中文区：直接走 translatepy（不再尝试 Google，避免每次都等满超时）
        logger.debug("⏭️ [翻译服务] 中文区，直接使用 translatepy")
        if _ensure_translatepy():
            # 拉丁脚本歧义文本：传 'unknown' 让 translatepy 走 auto-detect
            translatepy_source = 'unknown' if ambiguous_latin_source else source_lang
            logger.debug(f"🌐 [翻译服务] 尝试 translatepy (中文区): {translatepy_source} -> {target_lang}")
            try:
                translated_text = await translate_with_translatepy(text, translatepy_source, target_lang)
                if translated_text:
                    logger.info(f"✅ [翻译服务] translatepy翻译成功: {source_lang} -> {target_lang}")
                    return translated_text, google_failed
                else:
                    logger.debug("❌ [翻译服务] translatepy翻译返回空结果，回退到 LLM 翻译")
            except Exception as e:
                logger.debug(f"❌ [翻译服务] translatepy翻译异常: {type(e).__name__}，回退到 LLM 翻译")
        else:
            logger.debug("⚠️ [翻译服务] translatepy 不可用（未安装），回退到 LLM 翻译")
    else:
        # 非中文区：Google 翻译 → LLM 翻译（简化流程，去掉 translatepy）
        # skip_google_effective 综合了调用方意愿与本进程的失败标记
        if skip_google_effective:
            logger.debug("⏭️ [翻译服务] 跳过 Google 翻译（已被标记不可用 / 调用方要求），直接使用 LLM 翻译")
        elif _ensure_googletrans():
            logger.debug(f"🌐 [翻译服务] 尝试 Google 翻译 (非中文区): {source_lang} -> {target_lang}")
            translated_text = await _try_google_translate()
            if translated_text:
                logger.info(f"✅ [翻译服务] Google翻译成功: {source_lang} -> {target_lang}")
                return translated_text, google_failed
            else:
                logger.debug("❌ [翻译服务] Google翻译失败，回退到 LLM 翻译")
                _mark_google_failed()  # 进程级标记，本进程后续请求不再尝试
                google_failed = True
        else:
            logger.debug("⚠️ [翻译服务] Google 翻译不可用（googletrans 未安装），回退到 LLM 翻译")
    
    # 优先级3：回退到 LLM 翻译
    logger.debug(f"🔄 [翻译服务] 回退到 LLM 翻译: {source_lang} -> {target_lang}")
    try:
        config_manager = get_config_manager()
        # 复用emotion模型配置
        emotion_config = config_manager.get_model_api_config('emotion')
        
        from config.prompts.prompts_sys import (
            _loc, TRANSLATION_WATERMARK_START, TRANSLATION_WATERMARK_END,
            TRANSLATION_INSTRUCTION, TRANSLATION_REQUIREMENTS, TRANSLATION_LANG_NAMES,
        )
        lang = get_global_language()
        lang_names = TRANSLATION_LANG_NAMES.get(lang, TRANSLATION_LANG_NAMES['en'])
        source_name = lang_names.get(source_lang, source_lang)
        target_name = lang_names.get(target_lang, target_lang)

        from config import LLM_OUTPUT_GUARD_MAX_TOKENS
        llm = await create_chat_llm_async(
            emotion_config['model'], emotion_config['base_url'],
            emotion_config['api_key'],
            timeout=10.0,
            # Generous guard, not a tight cap: this translate_text LLM path can
            # receive long user text, so a small cap would truncate the
            # translation mid-stream and return it as a "success".
            max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,
            provider_type=emotion_config.get('provider_type'),
        )

        instruction = _loc(TRANSLATION_INSTRUCTION, lang).format(
            source_name=source_name, target_name=target_name)
        requirements = _loc(TRANSLATION_REQUIREMENTS, lang)
        system_prompt = f"{instruction}\n{TRANSLATION_WATERMARK_START}\n{requirements}\n{TRANSLATION_WATERMARK_END}"

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=text)
        ]

        set_call_type("translation")
        # ad-hoc 客户端，每次请求新建 → 必须 aclose 释放底层 httpx 连接池，
        # 与 memory/ 其它调用点的 try/finally 收尾对偶（缓存版客户端在
        # TranslationService._llm_client 里复用，不走这条路径）。
        try:
            response = await llm.ainvoke(messages)  # noqa: LLM_INPUT_BUDGET  # user-submitted translation text; intentionally uncapped here (best-effort LLM path), output bounded by the generous guard above.
            translated_text = response.content.strip()
        finally:
            await llm.aclose()

        logger.info(f"✅ [翻译服务] LLM翻译成功: {source_lang} -> {target_lang}")
        return translated_text, google_failed
        
    except Exception as e:
        logger.warning(f"❌ [翻译服务] LLM翻译失败: {type(e).__name__}, 返回原文")
        return text, google_failed


def get_user_language() -> str:
    """
    Get the user's language preference
    
    Returns:
        User language code ('zh', 'en', 'ja', 'ko'), defaults to 'en'
    """
    try:
        return get_global_language()
    except Exception as e:
        logger.warning(f"获取全局语言失败: {e}，使用默认英文")
        return 'en'


async def get_user_language_async() -> str:
    """
    Get the user's language preference asynchronously (uses the global language management module)

    Returns:
        User language code ('zh', 'en', 'ja', 'ko'), defaults to 'en'
    """
    try:
        return get_global_language()
    except Exception as e:
        logger.warning(f"获取全局语言失败: {e}，使用默认英文")
        return 'en'


# ============================================================================
# 面向内部组件的强稳定翻译服务（原 translation_service.py）
# ============================================================================



# 缓存配置
CACHE_MAX_SIZE = 1000
SUPPORTED_LANGUAGES = ['zh', 'zh-CN', 'en', 'ja', 'ko', 'ru', 'es', 'pt']
DEFAULT_LANGUAGE = 'en'

class TranslationService:
    """Translation service class"""
    
    def __init__(self, config_manager):
        """
        Initialize the translation service
        
        Args:
            config_manager: config manager instance, used to obtain API config
        """
        self.config_manager = config_manager
        self._llm_client = None
        self._cache = OrderedDict()
        self._cache_lock = None  # 懒加载：在首次使用时创建异步锁
        self._cache_lock_init_lock = threading.Lock()  # 用于保护异步锁的创建过程

    async def _get_llm_client(self):
        """Get the LLM client (for translation, reusing the emotion model config)"""
        try:
            config = self.config_manager.get_model_api_config('emotion')
            
            if not config.get('api_key') or not config.get('model') or not config.get('base_url'):
                logger.warning("翻译服务：API配置不完整（缺少 api_key、model 或 base_url），无法进行翻译")
                return None
            
            if self._llm_client is not None:
                return self._llm_client

            async with self._get_cache_lock():
                if self._llm_client is not None:
                    return self._llm_client

                from config import TRANSLATION_OUTPUT_MAX_TOKENS
                self._llm_client = await create_chat_llm_async(
                    config['model'], config['base_url'], config['api_key'],
                    max_completion_tokens=TRANSLATION_OUTPUT_MAX_TOKENS,
                    timeout=30.0,
                    provider_type=config.get('provider_type'),
                )

                return self._llm_client
        except Exception as e:
            logger.error(f"翻译服务：初始化LLM客户端失败: {e}")
            return None
    
    async def _get_from_cache(self, text: str, target_lang: str) -> Optional[str]:
        """Get a translation result from the cache"""
        async with self._get_cache_lock():
            cache_key = self._get_cache_key(text, target_lang)
            return self._cache.get(cache_key)
    
    def _get_cache_lock(self):
        """Lazily obtain the cache lock"""
        if self._cache_lock is None:
            with self._cache_lock_init_lock:
                if self._cache_lock is None:
                    self._cache_lock = asyncio.Lock()
        return self._cache_lock
    
    async def _save_to_cache(self, text: str, target_lang: str, translated: str):
        """Save a translation result to the cache"""
        async with self._get_cache_lock():
            if len(self._cache) >= CACHE_MAX_SIZE:
                first_key = next(iter(self._cache))
                del self._cache[first_key]
                
            cache_key = self._get_cache_key(text, target_lang)
            self._cache[cache_key] = translated
    
    def _normalize_language_code(self, lang: str) -> str:
        """Normalize a language code"""
        if not lang:
            return DEFAULT_LANGUAGE
        return normalize_language_code(lang, format='full')
    
    def _get_cache_key(self, text: str, target_lang: str) -> str:
        """Build a cache key"""
        normalized_lang = self._normalize_language_code(target_lang)
        text_hash = hashlib.md5(text.encode('utf-8')).hexdigest()
        return f"{normalized_lang}:{text_hash}"

    def _detect_language(self, text: str) -> str:
        """Detect the language of a text"""
        lang = detect_language(text)
        if lang == 'zh':
            return 'zh-CN'
        elif lang == 'unknown':
            return 'en'
        return lang
    
    async def translate_text_robust(self, text: str, target_lang: str) -> str:
        """
        Robust text translation service (used by core internal components)
        """
        if not text or not text.strip():
            return text
        
        target_lang_normalized = self._normalize_language_code(target_lang)
        
        if target_lang_normalized not in SUPPORTED_LANGUAGES:
            logger.warning(f"翻译服务：不支持的目标语言 {target_lang} (归一化后: {target_lang_normalized})，返回原文")
            return text
        
        detected_lang = self._detect_language(text)
        detected_lang_normalized = self._normalize_language_code(detected_lang)
        if detected_lang_normalized == target_lang_normalized:
            return text
        
        cached = await self._get_from_cache(text, target_lang_normalized)
        if cached is not None:
            return cached
        
        llm = await self._get_llm_client()
        if llm is None:
            logger.warning("翻译服务：LLM客户端不可用，返回原文")
            return text
        
        try:
            # 统一的语言显示名（detected -> 人类可读）
            _LANG_DISPLAY = {
                'zh-CN': 'Chinese',
                'en': 'English',
                'ja': 'Japanese',
                'ko': 'Korean',
                'ru': 'Russian',
                'es': 'Spanish',
                'pt': 'Portuguese',
            }
            def _src_name(lang: str) -> str:
                return _LANG_DISPLAY.get(lang, 'the source language')

            if target_lang_normalized == 'en':
                target_lang_name = "English"
                source_lang_name = _src_name(detected_lang_normalized)
            elif target_lang_normalized == 'ja':
                target_lang_name = "Japanese"
                source_lang_name = _src_name(detected_lang_normalized)
            elif target_lang_normalized == 'ko':
                target_lang_name = "Korean"
                source_lang_name = _src_name(detected_lang_normalized)
            elif target_lang_normalized == 'ru':
                target_lang_name = "Russian"
                source_lang_name = _src_name(detected_lang_normalized)
            elif target_lang_normalized == 'es':
                target_lang_name = "Spanish"
                source_lang_name = _src_name(detected_lang_normalized)
            elif target_lang_normalized == 'pt':
                target_lang_name = "Portuguese"
                source_lang_name = _src_name(detected_lang_normalized)
            else:  # zh-CN
                target_lang_name = "简体中文"
                source_lang_name = _src_name(detected_lang_normalized)
            
            system_prompt = f"""You are a professional translator. Translate the given text from {source_lang_name} to {target_lang_name}.

======以下为规则======
1. Keep the meaning and tone exactly the same
2. Maintain any special formatting (like commas, spaces)
3. For character names or nicknames, translate naturally
4. Return ONLY the translated text, no explanations or additional text
5. If the text is already in {target_lang_name}, return it unchanged
======以上为规则======"""

            set_call_type("translation")
            response = await llm.ainvoke([  # noqa: LLM_INPUT_BUDGET  # user-submitted translation text; intentionally uncapped here (best-effort LLM path).
                SystemMessage(content=system_prompt),
                HumanMessage(content=text)
            ])

            translated = response.content.strip()
            if not translated:
                # 原文/译文都不写 logger
                logger.warning(f"翻译服务：LLM返回空结果，使用原文 (text_len={len(text)})")
                print(f"[翻译] LLM 空结果，原文: '{text[:50]}...'")
                return text
            await self._save_to_cache(text, target_lang_normalized, translated)

            logger.debug(f"翻译服务：text_len={len(text)} -> translated_len={len(translated)} ({target_lang})")
            print(f"[翻译] '{text[:50]}...' -> '{translated[:50]}...' ({target_lang})")
            return translated
            
        except Exception as e:
            logger.error(f"翻译服务：翻译失败: {e}，返回原文")
            return text
    
    async def translate_dict(
        self,
        data: Dict[str, Any],
        target_lang: str,
        fields_to_translate: Optional[list] = None
    ) -> Dict[str, Any]:
        """
        Translate the specified fields in a dict
        """
        if not data:
            return data
        
        result = data.copy()
        
        if fields_to_translate is None:
            translate_all = True
            fields_set = set()
        elif len(fields_to_translate) == 0:
            translate_all = False
            fields_set = set()
        else:
            translate_all = False
            fields_set = set(fields_to_translate)
        
        for key, value in result.items():
            should_translate = translate_all or key in fields_set
            
            if should_translate and isinstance(value, str) and value.strip():
                if key in {'昵称', 'nickname'} and ', ' in value:
                    items = [item.strip() for item in value.split(', ')]
                    translated_items = await asyncio.gather(*[
                        self.translate_text_robust(item, target_lang) for item in items
                    ])
                    result[key] = ', '.join(translated_items)
                else:
                    result[key] = await self.translate_text_robust(value, target_lang)
            elif isinstance(value, dict):
                if should_translate:
                    result[key] = await self.translate_dict(value, target_lang, fields_to_translate)
            elif isinstance(value, list):
                if should_translate and value and all(isinstance(item, str) for item in value):
                    result[key] = await asyncio.gather(*[
                        self.translate_text_robust(item, target_lang) for item in value
                    ])
        return result

# 全局翻译服务实例（延迟初始化）
_translation_service_instance: Optional[TranslationService] = None
_instance_lock = threading.Lock()

def get_translation_service(config_manager) -> TranslationService:
    """Get the translation service instance (singleton)"""
    global _translation_service_instance
    if _translation_service_instance is None:
        with _instance_lock:
            if _translation_service_instance is None:
                _translation_service_instance = TranslationService(config_manager)
    elif _translation_service_instance.config_manager is not config_manager:
        logger.warning("get_translation_service: 传入了不同的 config_manager，但会使用第一次创建时的实例")
    return _translation_service_instance
