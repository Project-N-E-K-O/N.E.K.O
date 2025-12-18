# -*- coding: utf-8 -*-
"""
翻译服务模块

提供文本翻译功能，支持根据用户语言自动翻译系统消息和人设数据。
使用辅助API进行翻译，支持缓存以提高性能。
"""

import asyncio
import logging
import re
import hashlib
import threading
from collections import OrderedDict
from typing import Optional, Dict, Any
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger(__name__)

# 支持的语言列表
SUPPORTED_LANGUAGES = ['zh-CN', 'en']
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
    
    def _get_llm_client(self) -> Optional[ChatOpenAI]:
        """获取LLM客户端（用于翻译）"""
        try:
            # 使用辅助API配置（correction模型通常用于文本处理）
            correction_config = self.config_manager.get_model_api_config('correction')
            
            if not correction_config.get('api_key') or not correction_config.get('model') or not correction_config.get('base_url'):
                logger.warning("翻译服务：辅助API配置不完整（缺少 api_key、model 或 base_url），无法进行翻译")
                return None
            
            # 懒加载：如果客户端已存在，直接返回（注意：不会检测配置变化）
            if self._llm_client is not None:
                return self._llm_client
            
            self._llm_client = ChatOpenAI(
                model=correction_config.get('model', 'qwen-turbo'),
                base_url=correction_config.get('base_url'),
                api_key=correction_config.get('api_key'),
                temperature=0.3,  # 低温度保证翻译准确性
                max_tokens=2000,  # 增加令牌数以支持更长文本
                timeout=30.0,  # 增加超时时间
            )
            
            return self._llm_client
        except Exception as e:
            logger.error(f"翻译服务：初始化LLM客户端失败: {e}")
            return None
    
    def _get_from_cache(self, text: str, target_lang: str) -> Optional[str]:
        """从缓存获取翻译结果"""
        cache_key = self._get_cache_key(text, target_lang)
        return self._cache.get(cache_key)
    
    def _save_to_cache(self, text: str, target_lang: str, translated: str):
        """保存翻译结果到缓存"""
        # 简单的FIFO缓存：如果缓存过大，删除最早加入的条目
        
        if len(self._cache) >= CACHE_MAX_SIZE:
            # 删除第一个条目（FIFO）
            first_key = next(iter(self._cache))
            del self._cache[first_key]
        
        cache_key = self._get_cache_key(text, target_lang)
        self._cache[cache_key] = translated
    
    def _get_cache_key(self, text: str, target_lang: str) -> str:
        """生成缓存键"""
        # 使用稳定哈希以支持未来的缓存持久化
        text_hash = hashlib.md5(text.encode('utf-8')).hexdigest()
        return f"{target_lang}:{text_hash}"
    def _detect_language(self, text: str) -> str:
        """
        简单检测文本语言（中文/英文）
        
        Returns:
            'zh-CN' 或 'en'
        """
        # 简单检测：如果包含中文字符，认为是中文
        if re.search(r'[\u4e00-\u9fff]', text):
            return 'zh-CN'
        return 'en'
    
    async def translate_text(
        self, 
        text: str, 
        target_lang: str,
    ) -> str:
        """
        翻译文本
        
        Args:
            text: 要翻译的文本
            target_lang: 目标语言 ('zh-CN' 或 'en')
            
        
        Returns:
            翻译后的文本，如果翻译失败则返回原文
        """
        if not text or not text.strip():
            return text
        
        # 检查目标语言是否支持
        if target_lang not in SUPPORTED_LANGUAGES:
            logger.warning(f"翻译服务：不支持的目标语言 {target_lang}，返回原文")
            return text
        
        # 如果目标语言是默认语言（中文），且文本已经是中文，不需要翻译
        if target_lang == DEFAULT_LANGUAGE:
            detected = self._detect_language(text)
            if detected == 'zh-CN':
                return text
        
        # 如果目标语言是英文，且文本已经是英文，不需要翻译
        if target_lang == 'en':
            detected = self._detect_language(text)
            if detected == 'en':
                return text
        
        # 检查缓存
        cached = self._get_from_cache(text, target_lang)
        if cached is not None:
            return cached
        
        # 获取LLM客户端
        llm = self._get_llm_client()
        if llm is None:
            logger.warning("翻译服务：LLM客户端不可用，返回原文")
            return text
        
        try:
            # 构建翻译提示
            if target_lang == 'en':
                target_lang_name = "English"
                source_lang_name = "Chinese"
            else:
                target_lang_name = "简体中文"
                source_lang_name = "English"
            
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
            
            # 保存到缓存
            self._save_to_cache(text, target_lang, translated)
            
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
            fields_to_translate: 需要翻译的字段列表，如果为None则翻译所有字符串值
        
        Returns:
            翻译后的字典
        """
        if not data:
            return data
        
        result = data.copy()
        
        # 默认要翻译的字段（人设相关）
        if fields_to_translate is None:
            fields_to_translate = ['档案名', '昵称', '性别', '年龄']
        
        for key, value in result.items():
            if key in fields_to_translate and isinstance(value, str) and value.strip():
                # 处理字符串：如果是逗号分隔的字符串（如昵称 "T酱, 小T"），先分割再翻译
                if ',' in value:
                    items = [item.strip() for item in value.split(',')]
                    translated_items = await asyncio.gather(*[
                        self.translate_text(item, target_lang) for item in items
                    ])
                    result[key] = ', '.join(translated_items)
                else:
                    # 普通字符串直接翻译
                    result[key] = await self.translate_text(value, target_lang)
            elif isinstance(value, dict):
                # 递归翻译嵌套字典
                result[key] = await self.translate_dict(value, target_lang, fields_to_translate)
            elif isinstance(value, list):
                # 处理列表：如果是字符串列表，翻译每个元素
                if value and all(isinstance(item, str) for item in value):
                    result[key] = await asyncio.gather(*[
                        self.translate_text(item, target_lang) for item in value
                    ])
        return result


# 全局翻译服务实例（延迟初始化）
_translation_service_instance: Optional[TranslationService] = None
_instance_lock = threading.Lock()

def get_translation_service(config_manager) -> TranslationService:
    """获取翻译服务实例（单例模式）"""
    global _translation_service_instance
    if _translation_service_instance is None:
        with _instance_lock:
            # 双重检查锁定模式
            if _translation_service_instance is None:
                _translation_service_instance = TranslationService(config_manager)
    return _translation_service_instance

