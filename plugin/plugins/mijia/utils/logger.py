"""统一的日志组件

提供标准化的日志配置和初始化功能，避免多处重复初始化。
"""

import logging
import sys
from pathlib import Path
from typing import Optional

# 全局logger实例缓存
_loggers = {}
_initialized = False
_log_file_path: Optional[Path] = None

def setup_logging(log_level: str = "INFO", log_dir: Optional[Path] = None) -> Path:
    """设置全局日志配置
    
    Args:
        log_level: 日志级别 (DEBUG, INFO, WARNING, ERROR)
        log_dir: 日志目录，默认为 ~/.miot-mcp
        
    Returns:
        Path: 日志文件路径
    """
    global _initialized, _log_file_path
    
    if _initialized:
        return _log_file_path
    
    # 创建日志目录
    if log_dir is None:
        log_dir = Path.home() / '.miot-mcp'
    log_dir.mkdir(exist_ok=True)
    _log_file_path = log_dir / 'run.log'
    
    # 创建格式化器
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # 获取根logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))
    
    # 清除现有处理器
    root_logger.handlers.clear()
    
    # 添加stderr处理器（重要：MCP使用stderr而不是stdout）
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    root_logger.addHandler(stderr_handler)
    
    # 添加文件处理器
    file_handler = logging.FileHandler(_log_file_path, encoding='utf-8')
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    
    _initialized = True
    
    # 记录日志文件位置
    logger = get_logger(__name__)
    logger.info(f"日志系统已初始化，日志文件: {_log_file_path}")
    
    return _log_file_path

def get_logger(name: str) -> logging.Logger:
    """获取指定名称的logger实例
    
    Args:
        name: logger名称，通常使用 __name__
        
    Returns:
        logging.Logger: logger实例
    """
    global _loggers
    
    if name not in _loggers:
        _loggers[name] = logging.getLogger(name)
    
    return _loggers[name]

def get_log_file_path() -> Optional[Path]:
    """获取当前日志文件路径
    
    Returns:
        Optional[Path]: 日志文件路径，如果未初始化则返回None
    """
    return _log_file_path

def is_initialized() -> bool:
    """检查日志系统是否已初始化
    
    Returns:
        bool: 是否已初始化
    """
    return _initialized

def reset_logging():
    """重置日志系统（主要用于测试）"""
    global _initialized, _loggers, _log_file_path
    
    # 清除所有处理器
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    
    # 重置状态
    _initialized = False
    _loggers.clear()
    _log_file_path = None