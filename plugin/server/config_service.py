"""
插件配置服务

提供插件配置的读取和更新功能。
"""
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
from contextlib import contextmanager

from fastapi import HTTPException

from plugin.settings import PLUGIN_CONFIG_ROOT

# 跨平台文件锁支持
try:
    if sys.platform == 'win32':
        import msvcrt
        _has_file_lock = True
    else:
        import fcntl
        _has_file_lock = True
except ImportError:
    _has_file_lock = False

logger = logging.getLogger("user_plugin_server")


@contextmanager
def file_lock(file_obj):
    """
    跨平台文件锁上下文管理器
    
    使用文件锁保护文件操作，避免并发写入冲突。
    在 Unix/Linux/macOS 上使用 fcntl，在 Windows 上使用 msvcrt。
    
    Args:
        file_obj: 文件对象
    """
    if not _has_file_lock:
        # 如果没有文件锁支持，直接返回（不锁定）
        yield
        return
    
    try:
        if sys.platform == 'win32':
            # Windows 使用 msvcrt 锁定整个文件
            # 获取文件大小以锁定整个文件
            file_obj.seek(0, 2)  # 移动到文件末尾
            file_size = file_obj.tell()
            file_obj.seek(0)  # 回到文件开头
            if file_size > 0:
                msvcrt.locking(file_obj.fileno(), msvcrt.LK_LOCK, file_size)
            else:
                # 空文件锁定至少 1 个字节以提供基本保护
                # 注意：这会在文件开头锁定 1 个字节，即使文件是空的
                msvcrt.locking(file_obj.fileno(), msvcrt.LK_LOCK, 1)
        else:
            # Unix/Linux/macOS 使用 fcntl
            fcntl.flock(file_obj.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if sys.platform == 'win32':
            try:
                file_obj.seek(0, 2)
                file_size = file_obj.tell()
                file_obj.seek(0)
                if file_size > 0:
                    msvcrt.locking(file_obj.fileno(), msvcrt.LK_UNLCK, file_size)
                else:
                    # 解锁空文件的 1 字节锁
                    msvcrt.locking(file_obj.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
        else:
            try:
                fcntl.flock(file_obj.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

try:
    import tomli_w
except ImportError:
    tomli_w = None


def get_plugin_config_path(plugin_id: str) -> Path:
    """获取插件的配置文件路径"""
    config_file = PLUGIN_CONFIG_ROOT / plugin_id / "plugin.toml"
    if not config_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin_id}' configuration not found"
        )
    return config_file


def load_plugin_config(plugin_id: str) -> Dict[str, Any]:
    """
    加载插件配置
    
    Args:
        plugin_id: 插件ID
    
    Returns:
        配置数据
    """
    if tomllib is None:
        raise HTTPException(
            status_code=500,
            detail="TOML library not available"
        )
    
    config_path = get_plugin_config_path(plugin_id)
    
    try:
        with open(config_path, 'rb') as f:
            config_data = tomllib.load(f)
        
        stat = config_path.stat()
        
        return {
            "plugin_id": plugin_id,
            "config": config_data,
            "last_modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "config_path": str(config_path)
        }
    except Exception as e:
        logger.exception(f"Failed to load config for plugin {plugin_id}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load config: {str(e)}"
        ) from e


def deep_merge(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    """深度合并字典"""
    result = base.copy()
    for key, value in updates.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def update_plugin_config(plugin_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    更新插件配置
    
    Args:
        plugin_id: 插件ID
        updates: 要更新的配置部分
    
    Returns:
        更新后的配置
    """
    if tomllib is None or tomli_w is None:
        raise HTTPException(
            status_code=500,
            detail="TOML library not available"
        )
    
    config_path = get_plugin_config_path(plugin_id)
    
    try:
        # 读取现有配置
        with open(config_path, 'rb') as f:
            with file_lock(f):
                current_config = tomllib.load(f)
        
        # 深度合并
        merged_config = deep_merge(current_config, updates)
        
        # 使用临时文件 + 原子性 rename 的方式，确保配置持久化的可靠性
        # 这样即使写入过程中出问题，原文件也不会损坏
        config_dir = config_path.parent
        temp_fd, temp_path = tempfile.mkstemp(
            suffix='.toml',
            prefix='.plugin_config_',
            dir=config_dir
        )
        
        try:
            # 写入临时文件
            with os.fdopen(temp_fd, 'wb') as temp_file:
                tomli_w.dump(merged_config, temp_file)
                temp_file.flush()  # 确保数据从 Python 缓冲区写入操作系统
                os.fsync(temp_file.fileno())  # 确保数据立即写入磁盘
            
            # 原子性地替换原文件
            # 在大多数文件系统上，rename 是原子操作
            os.replace(temp_path, config_path)
            
            # 确保目录的元数据也同步到磁盘
            config_dir_fd = os.open(config_dir, os.O_DIRECTORY)
            try:
                os.fsync(config_dir_fd)
            finally:
                os.close(config_dir_fd)
        
        except Exception:
            # 如果写入失败，清理临时文件
            try:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            except Exception:
                pass
            raise
        
        # 重新加载配置
        updated = load_plugin_config(plugin_id)
        
        logger.info(f"Updated config for plugin {plugin_id}")
        return {
            "success": True,
            "plugin_id": plugin_id,
            "config": updated["config"],
            "requires_reload": True,  # 配置更新通常需要重载插件
            "message": "Config updated successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to update config for plugin {plugin_id}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update config: {str(e)}"
        ) from e

