# -*- coding: utf-8 -*-
"""
翻译服务模块

提供文本翻译功能，支持根据用户语言自动翻译系统消息和人设数据。
使用辅助API进行翻译，支持缓存以提高性能。
"""

import asyncio
import logging
import hashlib
import threading
from collections import OrderedDict
from typing import Optional, Dict, Any
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

# 复用 language_utils 的公共函数，避免重复实现
from utils.language_utils import detect_language as _detect_language_impl, normalize_language_code

logger = logging.getLogger(__name__)

# 支持的语言列表（支持多种格式以兼容不同调用方式）
SUPPORTED_LANGUAGES = ['zh', 'zh-CN', 'en', 'ja']
DEFAULT_LANGUAGE = 'zh-CN'

# 缓存配置
CACHE_MAX_SIZE = 1000

class TranslationService:
    """翻译服务类"""
    
    def __init__(self, config_manager):
        """
        初始化翻译服务
        
        Args:
            config_manager: 配置管理器实例，用于获取API配置
        """
        self.config_manager = config_manager
        self._llm_client = None
        self._cache = OrderedDict()
        self._cache_lock = None  # 懒加载：在首次使用时创建异步锁
        self._cache_lock_init_lock = threading.Lock()  # 用于保护异步锁的创建过程
    def _get_llm_client(self) -> Optional[ChatOpenAI]:
        """
        获取LLM客户端（用于翻译）
        
        注意：当前使用辅助API配置作为回退方案。
        未来应该添加独立的 'translation' 模型配置（如 qwen-mt-turbo），
        而不是复用其他任务的模型配置。
        """
        try:
            # 尝试使用独立的翻译模型配置（如果存在）
            # TODO: 在 config_manager 中添加 'translation' 模型类型支持
            try:
                translation_config = self.config_manager.get_model_api_config('translation')
                config = translation_config
            except (ValueError, KeyError):
                # 回退到辅助API配置（使用 emotion 模型，因为它也是文本处理任务）
                # 注意：这是临时方案，未来应该使用独立的翻译模型配置
                emotion_config = self.config_manager.get_model_api_config('emotion')
                config = emotion_config
            
            if not config.get('api_key') or not config.get('model') or not config.get('base_url'):
                logger.warning("翻译服务：API配置不完整（缺少 api_key、model 或 base_url），无法进行翻译")
                return None
            
            # 懒加载：如果客户端已存在，直接返回（注意：不会检测配置变化）
            if self._llm_client is not None:
                return self._llm_client
            
            # 使用翻译任务的专用参数
            self._llm_client = ChatOpenAI(
                model=config.get('model', 'qwen-turbo'),
                base_url=config.get('base_url'),
                api_key=config.get('api_key'),
                temperature=0.3,  # 低温度保证翻译准确性
                max_tokens=2000,  # 增加令牌数以支持更长文本
                timeout=30.0,  # 增加超时时间
            )
            
            return self._llm_client
        except Exception as e:
            logger.error(f"翻译服务：初始化LLM客户端失败: {e}")
            return None
    
    async def _get_from_cache(self, text: str, target_lang: str) -> Optional[str]:
        """从缓存获取翻译结果（使用锁保护以避免数据竞争）"""
        async with self._get_cache_lock():
            cache_key = self._get_cache_key(text, target_lang)
            return self._cache.get(cache_key)
    
    def _get_cache_lock(self):
        """
        懒加载获取缓存锁（确保在事件循环运行后创建）
        
        使用双重检查锁定模式避免多协程环境下的竞态条件
        """
        if self._cache_lock is None:
            with self._cache_lock_init_lock:
                # 双重检查：在获取线程锁后再次检查，避免多个协程同时创建锁
                if self._cache_lock is None:
                    self._cache_lock = asyncio.Lock()
        return self._cache_lock
    
    async def _save_to_cache(self, text: str, target_lang: str, translated: str):
        """保存翻译结果到缓存"""
        # 简单的FIFO缓存：如果缓存过大，删除最早加入的条目
        async with self._get_cache_lock():
            if len(self._cache) >= CACHE_MAX_SIZE:
                # 删除第一个条目（FIFO）
                first_key = next(iter(self._cache))
                del self._cache[first_key]
                
            cache_key = self._get_cache_key(text, target_lang)
            self._cache[cache_key] = translated
    
    def _normalize_language_code(self, lang: str) -> str:
        """
        归一化语言代码（复用 language_utils.normalize_language_code）
        
        Args:
            lang: 语言代码
            
        Returns:
            归一化后的语言代码 ('zh-CN', 'en', 'ja')
        """
        if not lang:
            return DEFAULT_LANGUAGE  # 默认中文
        return normalize_language_code(lang, format='full')
    
    def _get_cache_key(self, text: str, target_lang: str) -> str:
        """生成缓存键（使用归一化后的语言代码）"""
        # 先归一化语言代码，确保缓存键一致性
        normalized_lang = self._normalize_language_code(target_lang)
        # 使用稳定哈希以支持未来的缓存持久化
        text_hash = hashlib.md5(text.encode('utf-8')).hexdigest()
        return f"{normalized_lang}:{text_hash}"

    def _detect_language(self, text: str) -> str:
        """
        检测文本语言（复用 language_utils.detect_language）
        
        Returns:
            'zh-CN'、'ja' 或 'en'
        """
        # 使用 language_utils 的实现，并转换格式
        lang = _detect_language_impl(text)
        # 将 'zh' 转换为 'zh-CN'，'unknown' 转换为 'en'
        if lang == 'zh':
            return 'zh-CN'
        elif lang == 'unknown':
            return 'en'
        return lang
    
    async def translate_text(
        self, 
        text: str, 
        target_lang: str,
    ) -> str:
        """
        翻译文本
        
        Args:
            text: 要翻译的文本
            target_lang: 目标语言 ('zh', 'zh-CN', 'en', 'ja')
            
        
        Returns:
            翻译后的文本，如果翻译失败则返回原文
        """
        if not text or not text.strip():
            return text
        
        # 归一化目标语言代码（统一处理 'zh' 和 'zh-CN'）
        # 注意：必须在缓存操作之前归一化，确保缓存键一致性
        target_lang_normalized = self._normalize_language_code(target_lang)
        
        # 检查目标语言是否支持
        if target_lang_normalized not in SUPPORTED_LANGUAGES:
            logger.warning(f"翻译服务：不支持的目标语言 {target_lang} (归一化后: {target_lang_normalized})，返回原文")
            return text
        
        # 检测源语言，如果和目标语言相同则不需要翻译
        detected_lang = self._detect_language(text)
        # 归一化检测到的语言代码以便比较
        detected_lang_normalized = self._normalize_language_code(detected_lang)
        if detected_lang_normalized == target_lang_normalized:
            return text
        
        # 检查缓存（使用归一化后的语言代码）
        cached = await self._get_from_cache(text, target_lang_normalized)
        if cached is not None:
            return cached
        
        # 获取LLM客户端
        llm = self._get_llm_client()
        if llm is None:
            logger.warning("翻译服务：LLM客户端不可用，返回原文")
            return text
        
        try:
            # 构建翻译提示（根据归一化后的语言代码）
            if target_lang_normalized == 'en':
                target_lang_name = "English"
                if detected_lang_normalized == 'zh-CN':
                    source_lang_name = "Chinese"
                elif detected_lang_normalized == 'ja':
                    source_lang_name = "Japanese"
                else:
                    source_lang_name = "the source language"
            elif target_lang_normalized == 'ja':
                target_lang_name = "Japanese"
                if detected_lang_normalized == 'zh-CN':
                    source_lang_name = "Chinese"
                elif detected_lang_normalized == 'en':
                    source_lang_name = "English"
                else:
                    source_lang_name = "the source language"
            else:  # zh-CN
                target_lang_name = "简体中文"
                if detected_lang_normalized == 'en':
                    source_lang_name = "English"
                elif detected_lang_normalized == 'ja':
                    source_lang_name = "Japanese"
                else:
                    source_lang_name = "the source language"
            
            system_prompt = f"""You are a professional translator. Translate the given text from {source_lang_name} to {target_lang_name}.

Rules:
1. Keep the meaning and tone exactly the same
2. Maintain any special formatting (like commas, spaces)
3. For character names or nicknames, translate naturally
4. Return ONLY the translated text, no explanations or additional text
5. If the text is already in {target_lang_name}, return it unchanged"""

            user_prompt = text
            
            # 调用LLM进行翻译
            response = await llm.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt)
            ])
            
            translated = response.content.strip()
            # 验证翻译结果不为空
            if not translated:
                logger.warning(f"翻译服务：LLM返回空结果，使用原文: '{text[:50]}...'")
                return text            
            # 保存到缓存（使用归一化后的语言代码）
            await self._save_to_cache(text, target_lang_normalized, translated)
            
            logger.debug(f"翻译服务：'{text[:50]}...' -> '{translated[:50]}...' ({target_lang})")
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
        翻译字典中的指定字段
        
        Args:
            data: 要翻译的字典
            target_lang: 目标语言
            fields_to_translate: 需要翻译的字段列表
                - None: 翻译所有字符串值
                - [] (空列表): 不翻译任何字段
                - [字段名, ...]: 只翻译列表中的字段
        
        Returns:
            翻译后的字典
        """
        if not data:
            return data
        
        result = data.copy()
        
        # 处理 fields_to_translate 参数语义：
        # - None: 翻译所有字段
        # - []: 不翻译任何字段（明确表示"空列表就是不翻译"）
        # - 非空列表: 只翻译列表中的字段
        if fields_to_translate is None:
            # None 表示翻译所有字符串值
            translate_all = True
            fields_set = set()
        elif len(fields_to_translate) == 0:
            # 空列表表示不翻译任何字段
            translate_all = False
            fields_set = set()
        else:
            # 非空列表表示只翻译列表中的字段
            translate_all = False
            fields_set = set(fields_to_translate)
        
        for key, value in result.items():
            # 检查字段是否应该被翻译
            should_translate = translate_all or key in fields_set
            
            if should_translate and isinstance(value, str) and value.strip():
                # 只对特定字段（如昵称）进行逗号分隔处理
                # 使用 ", " (逗号+空格) 作为分隔符更可靠，避免误拆分普通文本中的逗号
                if key in {'昵称', 'nickname'} and ', ' in value:
                    items = [item.strip() for item in value.split(', ')]
                    translated_items = await asyncio.gather(*[
                        self.translate_text(item, target_lang) for item in items
                    ])
                    result[key] = ', '.join(translated_items)
                else:
                    # 普通字符串直接翻译
                    result[key] = await self.translate_text(value, target_lang)
            elif isinstance(value, dict):
                # 递归翻译嵌套字典（只有当字段在 fields_to_translate 中或 fields_to_translate 为 None 时才翻译）
                if should_translate:
                    result[key] = await self.translate_dict(value, target_lang, fields_to_translate)
            elif isinstance(value, list):
                # 处理列表：如果是字符串列表，翻译每个元素（只有当字段在 fields_to_translate 中或 fields_to_translate 为 None 时才翻译）
                if should_translate and value and all(isinstance(item, str) for item in value):
                    result[key] = await asyncio.gather(*[
                        self.translate_text(item, target_lang) for item in value
                    ])
        return result


# 全局翻译服务实例（延迟初始化）
_translation_service_instance: Optional[TranslationService] = None
_instance_lock = threading.Lock()

def get_translation_service(config_manager) -> TranslationService:
    """
    获取翻译服务实例（单例模式）
    
    注意：如果传入不同的 config_manager，会使用第一次创建时的实例。
    建议始终传入同一个 config_manager 实例以确保配置一致性。
    """
    global _translation_service_instance
    if _translation_service_instance is None:
        with _instance_lock:
            # 双重检查锁定模式
            if _translation_service_instance is None:
                _translation_service_instance = TranslationService(config_manager)
    elif _translation_service_instance.config_manager is not config_manager:
        logger.warning("get_translation_service: 传入了不同的 config_manager，但会使用第一次创建时的实例")
    return _translation_service_instance

