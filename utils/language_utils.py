# -*- coding: utf-8 -*-
"""
语言检测和翻译工具模块
用于检测文本语言并翻译到目标语言
优先级：Google 翻译 (googletrans) -> translatepy (仅使用中国大陆可访问的服务，免费) -> LLM 翻译
"""
import re
import logging
import asyncio
from typing import Optional, Tuple
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from utils.config_manager import get_config_manager

logger = logging.getLogger(__name__)

# 尝试导入 googletrans
try:
    from googletrans import Translator
    GOOGLETRANS_AVAILABLE = True
    logger.debug("googletrans 导入成功")
except ImportError as e:
    GOOGLETRANS_AVAILABLE = False
    logger.warning(f"googletrans 导入失败（未安装）: {e}，将跳过 Google 翻译")
except Exception as e:
    GOOGLETRANS_AVAILABLE = False
    logger.warning(f"googletrans 导入失败（其他错误）: {e}，将跳过 Google 翻译")

# 尝试导入 translatepy
try:
    from translatepy import Translator as TranslatepyTranslator
    # 导入在中国大陆可直接访问的翻译服务
    from translatepy.translators.microsoft import MicrosoftTranslate
    from translatepy.translators.bing import BingTranslate
    from translatepy.translators.reverso import ReversoTranslate
    from translatepy.translators.libre import LibreTranslate
    from translatepy.translators.mymemory import MyMemoryTranslate
    from translatepy.translators.translatecom import TranslateComTranslate
    # 定义在中国大陆可直接访问的翻译服务列表（排除需要代理的 Google、Yandex、DeepL）
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

# 语言检测正则表达式
CHINESE_PATTERN = re.compile(r'[\u4e00-\u9fff]')
JAPANESE_PATTERN = re.compile(r'[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff]')  # 平假名、片假名、汉字
ENGLISH_PATTERN = re.compile(r'[a-zA-Z]')


async def translate_with_translatepy(text: str, source_lang: str, target_lang: str) -> Optional[str]:
    """
    使用 translatepy 进行翻译（只使用中国大陆可直接访问的翻译服务，免费，不需要 API key）
    
    支持的服务（按优先级）：
    - MicrosoftTranslate (Microsoft Translator)
    - BingTranslate (Bing Translator)
    - ReversoTranslate (Reverso)
    - LibreTranslate (开源服务)
    - MyMemoryTranslate (MyMemory)
    - TranslateComTranslate (Translate.com)
    
    排除需要代理的服务：Google、Yandex、DeepL
    
    Args:
        text: 要翻译的文本
        source_lang: 源语言代码（我们的格式，如 'zh', 'en', 'ja'）
        target_lang: 目标语言代码（我们的格式，如 'zh', 'en', 'ja'）
        
    Returns:
        翻译后的文本，失败时返回 None
    """
    if not text or not text.strip() or not TRANSLATEPY_AVAILABLE:
        return None
    
    try:
        # translatepy 的语言代码映射（translatepy 支持多种语言名称和代码）
        TRANSLATEPY_LANG_MAP = {
            'zh': 'Chinese',  # 简体中文
            'en': 'English',
            'ja': 'Japanese',
            'auto': 'auto'
        }
        
        translatepy_source = TRANSLATEPY_LANG_MAP.get(source_lang, source_lang) if source_lang != 'unknown' else 'auto'
        translatepy_target = TRANSLATEPY_LANG_MAP.get(target_lang, target_lang)
        
        # 如果源语言和目标语言相同，不需要翻译
        if translatepy_source == translatepy_target and translatepy_source != 'auto':
            return None
        
        # translatepy 是同步的，需要在线程池中运行以避免阻塞
        def _translate_sync(text_to_translate: str, target: str, source: Optional[str] = None) -> Optional[str]:
            """同步翻译函数，在线程池中运行，只使用中国大陆可访问的翻译服务"""
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
                        source_param = source if source else 'auto'
                        result = service_instance.translate(text_to_translate, destination_language=target, source_language=source_param)
                        if result and hasattr(result, 'result') and result.result:
                            return result.result
                    except Exception:
                        continue
                
                # 如果所有单个服务都失败，尝试使用 Translator 的自动选择（但只使用可访问的服务）
                source_param = source if source else 'auto'
                result = translator.translate(text_to_translate, destination_language=target, source_language=source_param)
                if result and hasattr(result, 'result') and result.result:
                    return result.result
                else:
                    return None
            except Exception:
                return None
        
        # 如果文本太长（超过5000字符），分段翻译
        max_chunk_size = 5000
        if len(text) > max_chunk_size:
            # 分段翻译
            chunks = []
            current_chunk = ""
            for char in text:
                current_chunk += char
                if len(current_chunk) >= max_chunk_size:
                    # 尝试在句号、换行符等位置分割
                    last_period = max(
                        current_chunk.rfind('。'),
                        current_chunk.rfind('.'),
                        current_chunk.rfind('！'),
                        current_chunk.rfind('!'),
                        current_chunk.rfind('？'),
                        current_chunk.rfind('?'),
                        current_chunk.rfind('\n')
                    )
                    if last_period > max_chunk_size * 0.7:  # 如果找到合适的分割点
                        chunks.append(current_chunk[:last_period + 1])
                        current_chunk = current_chunk[last_period + 1:]
                    else:
                        chunks.append(current_chunk)
                        current_chunk = ""
            if current_chunk:
                chunks.append(current_chunk)
            
            # 在线程池中翻译每个分段
            loop = asyncio.get_event_loop()
            translated_chunks = []
            for chunk in chunks:
                try:
                    chunk_result = await loop.run_in_executor(
                        None, 
                        _translate_sync, 
                        chunk, 
                        translatepy_target, 
                        translatepy_source if translatepy_source != 'auto' else None
                    )
                    if chunk_result:
                        translated_chunks.append(chunk_result)
                    else:
                        logger.warning(f"translatepy 分段翻译返回空结果")
                        return None
                except Exception as chunk_error:
                    logger.warning(f"translatepy 分段翻译异常: {type(chunk_error).__name__}: {chunk_error}")
                    return None
            
            translated_text = ''.join(translated_chunks)
        else:
            # 单次翻译，在线程池中运行
            loop = asyncio.get_event_loop()
            translated_text = await loop.run_in_executor(
                None, 
                _translate_sync, 
                text, 
                translatepy_target, 
                translatepy_source if translatepy_source != 'auto' else None
            )
        
        if translated_text and translated_text.strip():
            return translated_text
        else:
            return None
            
    except Exception:
        return None


def detect_language(text: str) -> str:
    """
    检测文本的主要语言
    
    Args:
        text: 要检测的文本
        
    Returns:
        'zh' (中文), 'ja' (日语), 'en' (英文), 或 'unknown'
    """
    if not text or not text.strip():
        return 'unknown'
    
    # 统计各语言字符数量
    chinese_count = len(CHINESE_PATTERN.findall(text))
    japanese_count = len(JAPANESE_PATTERN.findall(text)) - chinese_count  # 减去汉字（因为中日共用）
    english_count = len(ENGLISH_PATTERN.findall(text))
    
    # 如果包含日文假名，优先判断为日语
    if japanese_count > 0:
        # 检查是否主要是日语（假名数量较多）
        hiragana_katakana = len(re.findall(r'[\u3040-\u309f\u30a0-\u30ff]', text))
        if hiragana_katakana > 0 and hiragana_katakana >= chinese_count * 0.3:
            return 'ja'
    
    # 判断主要语言
    if chinese_count > english_count and chinese_count > 0:
        return 'zh'
    elif japanese_count > 0 or (chinese_count > 0 and japanese_count > 0):
        return 'ja'
    elif english_count > 0:
        return 'en'
    else:
        return 'unknown'


async def translate_text(text: str, target_lang: str, source_lang: Optional[str] = None, skip_google: bool = False) -> Tuple[str, bool]:
    """
    翻译文本到目标语言
    
    根据系统区域选择不同的翻译服务优先级：
    - 中文区：Google 翻译（优先尝试，5秒超时，超时后立即降级）-> translatepy -> LLM 翻译
    - 非中文区：Google 翻译 -> LLM 翻译（简化流程，去掉 translatepy）
    
    降级机制说明：
    - 中文区使用超时机制（5秒）快速判断 Google 翻译是否可用
    - 如果 Google 翻译在 5 秒内没有响应，立即降级到 translatepy，避免长时间等待
    - 如果 skip_google=True，直接跳过 Google 翻译（用于会话级失败标记）
    
    Args:
        text: 要翻译的文本
        target_lang: 目标语言代码 ('zh', 'en', 'ja')
        source_lang: 源语言代码，如果为None则自动检测
        skip_google: 是否跳过 Google 翻译（会话级失败标记）
        
    Returns:
        (翻译后的文本, google_failed): 如果翻译失败则返回原文，google_failed 表示 Google 翻译是否失败
    """
    google_failed = False  # 记录 Google 翻译是否失败
    
    if not text or not text.strip():
        return text, google_failed
    
    # 自动检测源语言
    if source_lang is None:
        source_lang = detect_language(text)
    
    # 如果源语言和目标语言相同，不需要翻译
    if source_lang == target_lang or source_lang == 'unknown':
        logger.debug(f"跳过翻译: 源语言({source_lang}) == 目标语言({target_lang}) 或源语言未知")
        return text, google_failed
    
    # 判断当前区域，决定翻译服务优先级
    try:
        from utils.global_language import is_china_region
        is_china = is_china_region()
    except Exception as e:
        logger.warning(f"获取区域信息失败: {e}，默认使用非中文区优先级")
        is_china = False
    
    logger.debug(f"🔄 [翻译服务] 开始翻译流程: {source_lang} -> {target_lang}, 文本长度: {len(text)}, 区域: {'中文区' if is_china else '非中文区'}")
    
    # 语言代码映射：我们的代码 -> Google Translate 代码
    GOOGLE_LANG_MAP = {
        'zh': 'zh-cn',  # 简体中文
        'en': 'en',
        'ja': 'ja'
    }
    
    google_target = GOOGLE_LANG_MAP.get(target_lang, target_lang)
    google_source = GOOGLE_LANG_MAP.get(source_lang, source_lang) if source_lang != 'unknown' else 'auto'
    
    # 辅助函数：尝试 Google 翻译（带超时机制）
    async def _try_google_translate(timeout: float = 5.0) -> Optional[str]:
        """
        尝试使用 Google 翻译，返回翻译结果或 None
        
        Args:
            timeout: 超时时间（秒），默认 5 秒。如果超时则认为 Google 翻译不可用，立即降级
        
        Returns:
            翻译结果或 None（超时或失败时返回 None）
        """
        if not GOOGLETRANS_AVAILABLE:
            return None
        
        try:
            translator = Translator()
            
            # 使用 asyncio.wait_for 实现超时机制
            async def _translate_internal():
                # 如果文本太长（超过15k字符），分段翻译
                max_chunk_size = 15000
                if len(text) > max_chunk_size:
                    # 分段翻译
                    chunks = []
                    current_chunk = ""
                    for char in text:
                        current_chunk += char
                        if len(current_chunk) >= max_chunk_size:
                            # 尝试在句号、换行符等位置分割
                            last_period = max(
                                current_chunk.rfind('。'),
                                current_chunk.rfind('.'),
                                current_chunk.rfind('！'),
                                current_chunk.rfind('!'),
                                current_chunk.rfind('？'),
                                current_chunk.rfind('?'),
                                current_chunk.rfind('\n')
                            )
                            if last_period > max_chunk_size * 0.7:  # 如果找到合适的分割点
                                chunks.append(current_chunk[:last_period + 1])
                                current_chunk = current_chunk[last_period + 1:]
                            else:
                                chunks.append(current_chunk)
                                current_chunk = ""
                    if current_chunk:
                        chunks.append(current_chunk)
                    
                    # 翻译每个分段（第一个分段使用auto检测，后续使用已检测的源语言）
                    translated_chunks = []
                    for i, chunk in enumerate(chunks):
                        # 第一个分段可以使用auto，后续分段使用已检测的源语言
                        chunk_source = google_source if i > 0 or source_lang != 'unknown' else 'auto'
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
        # 中文区：先尝试 Google 翻译（带超时），确认不能用后再降级到 translatepy
        # 优先级1：尝试使用 Google 翻译（中文区优先尝试，5秒超时，超时后立即降级）
        # 如果 skip_google=True，直接跳过 Google 翻译
        if skip_google:
            logger.debug("⏭️ [翻译服务] 跳过 Google 翻译（会话级失败标记），直接使用 translatepy")
        elif GOOGLETRANS_AVAILABLE:
            logger.debug(f"🌐 [翻译服务] 尝试 Google 翻译 (中文区优先，5秒超时): {source_lang} -> {target_lang}")
            translated_text = await _try_google_translate(timeout=5.0)  # 5秒超时
            if translated_text:
                logger.info(f"✅ [翻译服务] Google翻译成功: {source_lang} -> {target_lang}")
                return translated_text, google_failed
            else:
                logger.debug(f"❌ [翻译服务] Google翻译不可用（超时或失败），立即降级到 translatepy")
                google_failed = True  # 标记 Google 翻译失败
        else:
            logger.debug("⚠️ [翻译服务] Google 翻译不可用（googletrans 未安装），尝试 translatepy")
        
        # 优先级2：尝试使用 translatepy（确认 Google 不能用后降级）
        if TRANSLATEPY_AVAILABLE:
            logger.debug(f"🌐 [翻译服务] 尝试 translatepy (中文区降级): {source_lang} -> {target_lang}")
            try:
                translated_text = await translate_with_translatepy(text, source_lang, target_lang)
                if translated_text:
                    logger.info(f"✅ [翻译服务] translatepy翻译成功: {source_lang} -> {target_lang}")
                    return translated_text, google_failed
                else:
                    logger.debug(f"❌ [翻译服务] translatepy翻译返回空结果，回退到 LLM 翻译")
            except Exception as e:
                logger.debug(f"❌ [翻译服务] translatepy翻译异常: {type(e).__name__}，回退到 LLM 翻译")
        else:
            logger.debug("⚠️ [翻译服务] translatepy 不可用（未安装），回退到 LLM 翻译")
    else:
        # 非中文区：Google 翻译 → LLM 翻译（简化流程，去掉 translatepy）
        # 优先级1：尝试使用 Google 翻译
        # 如果 skip_google=True，直接跳过 Google 翻译
        if skip_google:
            logger.debug("⏭️ [翻译服务] 跳过 Google 翻译（会话级失败标记），直接使用 LLM 翻译")
        elif GOOGLETRANS_AVAILABLE:
            logger.debug(f"🌐 [翻译服务] 尝试 Google 翻译 (非中文区): {source_lang} -> {target_lang}")
            translated_text = await _try_google_translate()
            if translated_text:
                logger.info(f"✅ [翻译服务] Google翻译成功: {source_lang} -> {target_lang}")
                return translated_text, google_failed
            else:
                logger.debug(f"❌ [翻译服务] Google翻译失败，回退到 LLM 翻译")
                google_failed = True  # 标记 Google 翻译失败
        else:
            logger.debug("⚠️ [翻译服务] Google 翻译不可用（googletrans 未安装），回退到 LLM 翻译")
    
    # 优先级3：回退到 LLM 翻译
    logger.debug(f"🔄 [翻译服务] 回退到 LLM 翻译: {source_lang} -> {target_lang}")
    try:
        config_manager = get_config_manager()
        # 使用correction模型配置（轻量级模型，适合翻译任务）
        correction_config = config_manager.get_model_api_config('correction')
        
        # 语言名称映射
        lang_names = {
            'zh': '中文',
            'en': '英文',
            'ja': '日语'
        }
        
        source_name = lang_names.get(source_lang, source_lang)
        target_name = lang_names.get(target_lang, target_lang)
        
        llm = ChatOpenAI(
            model=correction_config['model'],
            base_url=correction_config['base_url'],
            api_key=correction_config['api_key'],
            temperature=0.3,  # 低temperature保证翻译准确性
            timeout=10.0
        )
        
        system_prompt = f"""你是一个专业的翻译助手。请将用户提供的文本从{source_name}翻译成{target_name}。

要求：
1. 保持原文的语气和风格
2. 准确传达原文的意思
3. 只输出翻译结果，不要添加任何解释或说明
4. 如果文本包含emoji或特殊符号，请保留它们"""
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=text)
        ]
        
        response = await llm.ainvoke(messages)
        translated_text = response.content.strip()
        
        logger.info(f"✅ [翻译服务] LLM翻译成功: {source_lang} -> {target_lang}")
        return translated_text, google_failed
        
    except Exception as e:
        logger.warning(f"❌ [翻译服务] LLM翻译失败: {type(e).__name__}, 返回原文")
        return text, google_failed


def get_user_language() -> str:
    """
    获取用户的语言偏好
    
    Returns:
        用户语言代码 ('zh', 'en', 'ja')，默认返回 'zh'
    """
    try:
        from main_routers.config_router import get_steam_language
        import asyncio
        
        # 尝试从Steam获取语言设置
        # 注意：这是一个同步函数，但get_steam_language是异步的
        # 我们需要在调用时处理这个异步问题
        # 这里先返回默认值，实际使用时会在异步上下文中调用
        return 'zh'  # 默认中文
    except Exception:
        return 'zh'  # 默认中文


async def get_user_language_async() -> str:
    """
    异步获取用户的语言偏好（使用全局语言管理模块）
    
    Returns:
        用户语言代码 ('zh', 'en', 'ja')，默认返回 'zh'
    """
    try:
        from utils.global_language import get_global_language
        return get_global_language()
    except Exception as e:
        logger.warning(f"获取全局语言失败: {e}，使用默认中文")
        return 'zh'  # 默认中文

