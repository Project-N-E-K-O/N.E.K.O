# -*- coding: utf-8 -*-
"""
API配置加载器
从JSON文件加载API服务商配置和默认模型配置
"""
import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Dict, Any, Optional

from config import (
    DEFAULT_CORE_API_PROFILES,
    DEFAULT_ASSIST_API_PROFILES,
    DEFAULT_ASSIST_API_KEY_FIELDS,
)
logger = logging.getLogger(__name__)

# 配置缓存
_config_cache: Optional[Dict[str, Any]] = None


def _get_default_core_api_profiles() -> Dict[str, Dict[str, Any]]:
    return deepcopy(DEFAULT_CORE_API_PROFILES)


def _get_default_assist_api_profiles() -> Dict[str, Dict[str, Any]]:
    return deepcopy(DEFAULT_ASSIST_API_PROFILES)


def _get_default_assist_api_key_fields() -> Dict[str, str]:
    return deepcopy(DEFAULT_ASSIST_API_KEY_FIELDS)


def _get_config_file_path() -> Path:
    """
    获取配置文件路径
    
    Returns:
        Path: api_providers.json 文件路径
    """
    # 获取项目根目录下的 config 目录
    if hasattr(__import__('sys'), 'frozen'):
        import sys
        if getattr(sys, 'frozen', False):
            # 打包后的exe
            if hasattr(sys, '_MEIPASS'):
                app_dir = Path(sys._MEIPASS)
            else:
                app_dir = Path(sys.executable).parent
        else:
            # 脚本运行
            app_dir = Path.cwd()
    else:
        app_dir = Path.cwd()
    
    return app_dir / "config" / "api_providers.json"


def _load_json_config() -> Dict[str, Any]:
    """
    加载JSON配置文件
    
    Returns:
        Dict: 配置字典
        
    Raises:
        FileNotFoundError: 配置文件不存在
        json.JSONDecodeError: JSON格式错误
    """
    config_path = _get_config_file_path()
    
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        logger.info(f"成功加载配置文件: {config_path}")
        return config
    except json.JSONDecodeError as e:
        logger.error(f"JSON格式错误: {config_path}, 错误: {e}")
        raise
    except Exception as e:
        logger.error(f"加载配置文件失败: {config_path}, 错误: {e}")
        raise


def _convert_core_api_profile(json_profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    将JSON格式的核心API配置转换为Python代码使用的格式
    
    Args:
        json_profile: JSON格式的配置
        
    Returns:
        Dict: Python代码使用的格式（字段名大写）
    """
    result = {}
    
    # 转换字段名：snake_case -> UPPER_SNAKE_CASE
    field_mapping = {
        'core_url': 'CORE_URL',
        'core_model': 'CORE_MODEL',
        'core_api_key': 'CORE_API_KEY',
        'is_free_version': 'IS_FREE_VERSION',
    }
    
    for json_key, python_key in field_mapping.items():
        if json_key in json_profile:
            result[python_key] = json_profile[json_key]
    
    return result


def _convert_assist_api_profile(json_profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    将JSON格式的辅助API配置转换为Python代码使用的格式
    
    Args:
        json_profile: JSON格式的配置
        
    Returns:
        Dict: Python代码使用的格式（字段名大写）
    """
    result = {}
    
    # 转换字段名：snake_case -> UPPER_SNAKE_CASE
    field_mapping = {
        'openrouter_url': 'OPENROUTER_URL',
        'conversation_model': 'CONVERSATION_MODEL',
        'summary_model': 'SUMMARY_MODEL',
        'correction_model': 'CORRECTION_MODEL',
        'emotion_model': 'EMOTION_MODEL',
        'vision_model': 'VISION_MODEL',
        'agent_model': 'AGENT_MODEL',
        'audio_api_key': 'AUDIO_API_KEY',
        'openrouter_api_key': 'OPENROUTER_API_KEY',
        'is_free_version': 'IS_FREE_VERSION',
    }
    
    for json_key, python_key in field_mapping.items():
        if json_key in json_profile:
            result[python_key] = json_profile[json_key]
    
    return result


def get_config(force_reload: bool = False) -> Dict[str, Any]:
    """
    获取配置（带缓存）
    
    Args:
        force_reload: 是否强制重新加载
        
    Returns:
        Dict: 配置字典
    """
    global _config_cache
    
    if _config_cache is None or force_reload:
        try:
            _config_cache = _load_json_config()
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"加载配置失败，使用空配置: {e}")
            _config_cache = {}
    
    return _config_cache


def get_core_api_profiles(force_reload: bool = False) -> Dict[str, Dict[str, Any]]:
    """
    获取核心API配置（兼容原有的 CORE_API_PROFILES 格式）
    
    Args:
        force_reload: 是否强制重新加载配置
    
    Returns:
        Dict: 核心API配置字典，格式与 CORE_API_PROFILES 相同
    """
    config = get_config(force_reload=force_reload)
    core_providers = config.get('core_api_providers', {})
    
    result = {}
    for key, profile in core_providers.items():
        # 转换为Python代码使用的格式
        result[key] = _convert_core_api_profile(profile)
    
    if not result:
        return _get_default_core_api_profiles()
    
    return result


def get_assist_api_profiles(force_reload: bool = False) -> Dict[str, Dict[str, Any]]:
    """
    获取辅助API配置（兼容原有的 ASSIST_API_PROFILES 格式）
    
    Args:
        force_reload: 是否强制重新加载配置
    
    Returns:
        Dict: 辅助API配置字典，格式与 ASSIST_API_PROFILES 相同
    """
    # 首先获取默认配置作为基础
    defaults = _get_default_assist_api_profiles()
    
    config = get_config(force_reload=force_reload)
    assist_providers = config.get('assist_api_providers', {})
    
    if not assist_providers:
        return defaults
    
    result = {}
    for key, profile in assist_providers.items():
        # 转换为Python代码使用的格式
        converted = _convert_assist_api_profile(profile)
        
        # 与默认配置合并：默认配置作为基础，JSON配置覆盖
        if key in defaults:
            merged = dict(defaults[key])  # 复制默认配置
            merged.update(converted)  # JSON 配置覆盖
            result[key] = merged
        else:
            result[key] = converted
    
    # 添加默认配置中有但 JSON 中没有的 provider
    for key in defaults:
        if key not in result:
            result[key] = defaults[key]
    
    return result


def get_assist_api_key_fields() -> Dict[str, str]:
    """
    获取辅助API Key字段映射（兼容原有的 ASSIST_API_KEY_FIELDS 格式）
    
    Returns:
        Dict: API Key字段映射字典
    """
    config = get_config()
    result = config.get('assist_api_key_fields', {})
    if not result:
        return _get_default_assist_api_key_fields()
    return result


def get_default_models() -> Dict[str, str]:
    """
    获取默认模型配置
    
    Returns:
        Dict: 默认模型配置字典
    """
    config = get_config()
    return config.get('default_models', {})


def get_core_api_providers_for_frontend(force_reload: bool = False) -> list:
    """
    获取核心API服务商列表（供前端使用）
    
    Args:
        force_reload: 是否强制重新加载配置
    
    Returns:
        list: 包含服务商信息的列表，每个元素包含 key, name, description
    """
    config = get_config(force_reload=force_reload)
    core_providers = config.get('core_api_providers', {})
    
    result = []
    for key, profile in core_providers.items():
        result.append({
            'key': profile.get('key', key),
            'name': profile.get('name', key),
            'description': profile.get('description', ''),
        })
    
    return result


def get_assist_api_providers_for_frontend(force_reload: bool = False) -> list:
    """
    获取辅助API服务商列表（供前端使用）
    
    Args:
        force_reload: 是否强制重新加载配置
    
    Returns:
        list: 包含服务商信息的列表，每个元素包含 key, name, description
    """
    config = get_config(force_reload=force_reload)
    assist_providers = config.get('assist_api_providers', {})
    
    result = []
    for key, profile in assist_providers.items():
        result.append({
            'key': profile.get('key', key),
            'name': profile.get('name', key),
            'description': profile.get('description', ''),
        })
    
    return result


def reload_config():
    """
    重新加载配置（清除缓存）
    """
    global _config_cache
    _config_cache = None
    logger.info("配置缓存已清除，下次访问时将重新加载")

def get_free_voices() -> Dict[str, str]:
    """
    获取免费预设音色列表（从 api_providers.json 中读取 free_voices 字段）
    
    Returns:
        Dict[str, str]: {显示名: voice_id} 的映射字典
    """
    config = get_config()
    return config.get('free_voices', {})


# 导出主要函数
__all__ = [
    'get_core_api_profiles',
    'get_assist_api_profiles',
    'get_assist_api_key_fields',
    'get_default_models',
    'get_core_api_providers_for_frontend',
    'get_assist_api_providers_for_frontend',
    'reload_config',
    'get_config',
    'get_free_voices',
]
