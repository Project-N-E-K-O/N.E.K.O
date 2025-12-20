"""
插件日志服务

提供插件日志和服务器日志的读取和查询功能。
"""
import logging
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from collections import deque

from fastapi import HTTPException

from plugin.settings import PLUGIN_CONFIG_ROOT

logger = logging.getLogger("user_plugin_server")

# 服务器日志的特殊 ID
SERVER_LOG_ID = "_server"


def get_plugin_log_dir(plugin_id: str) -> Path:
    """获取插件的日志目录"""
    # 如果是服务器日志，使用应用日志目录（log文件夹）
    if plugin_id == SERVER_LOG_ID:
        try:
            from utils.logger_config import RobustLoggerConfig
            config = RobustLoggerConfig(service_name="PluginServer")
            # get_log_directory_path() 返回字符串，需要转换为 Path
            log_dir_str = config.get_log_directory_path()
            log_dir = Path(log_dir_str)
            # 确保目录存在
            log_dir.mkdir(parents=True, exist_ok=True)
            return log_dir
        except Exception as e:
            logger.warning(f"Failed to get server log directory, using fallback: {e}")
            # 降级方案：使用项目根目录下的 log 文件夹
            fallback_dir = PLUGIN_CONFIG_ROOT.parent / "log"
            fallback_dir.mkdir(parents=True, exist_ok=True)
            return fallback_dir
    
    # 插件日志：优先使用项目根目录下的 log/plugins/{plugin_id} 目录
    try:
        project_root = PLUGIN_CONFIG_ROOT.parent
        log_dir = project_root / "log" / "plugins" / plugin_id
        log_dir.mkdir(parents=True, exist_ok=True)
        # 测试目录是否可写
        test_file = log_dir / ".test_write"
        try:
            test_file.write_text("test")
            test_file.unlink()
            return log_dir
        except (OSError, PermissionError):
            # 如果不可写，使用降级方案：插件目录下的logs子目录
            plugin_dir = PLUGIN_CONFIG_ROOT / plugin_id
            log_dir = plugin_dir / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            return log_dir
    except Exception as e:
        logger.warning(f"Failed to use project log directory, using plugin directory: {e}")
        # 降级方案：使用插件目录下的logs子目录
        plugin_dir = PLUGIN_CONFIG_ROOT / plugin_id
        log_dir = plugin_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir


def get_plugin_log_files(plugin_id: str) -> List[Dict[str, Any]]:
    """
    获取插件的日志文件列表
    
    Args:
        plugin_id: 插件ID（或 SERVER_LOG_ID 表示服务器日志）
    
    Returns:
        日志文件列表
    """
    log_dir = get_plugin_log_dir(plugin_id)
    
    if not log_dir.exists():
        return []
    
    log_files = []
    
    # 服务器日志使用不同的文件名模式
    if plugin_id == SERVER_LOG_ID:
        # 服务器日志文件名格式：N.E.K.O_PluginServer_YYYYMMDD.log
        pattern = "N.E.K.O_PluginServer_*.log*"
    else:
        # 插件日志文件名格式：{plugin_id}_YYYYMMDD_HHMMSS.log
        pattern = f"{plugin_id}_*.log*"
    
    for log_file in log_dir.glob(pattern):
        try:
            stat = log_file.stat()
            log_files.append({
                "filename": log_file.name,
                "size": stat.st_size,
                "modified": stat.st_mtime,
            })
        except OSError:
            continue
    
    # 按修改时间排序（最新的在前）
    log_files.sort(key=lambda x: x["modified"], reverse=True)
    return log_files


def parse_log_line(line: str) -> Optional[Dict[str, Any]]:
    """
    解析日志行
    
    支持多种日志格式：
    1. 插件日志格式: 2024-01-01 00:00:00 - [plugin.xxx] - INFO - file.py:123 - message
    2. 服务器日志格式: 2024-01-01 00:00:00,123 - user_plugin_server - INFO - message
    3. 标准日志格式: 2024-01-01 00:00:00 - INFO - message
    """
    line = line.strip()
    if not line:
        return None
    
    # 模式1: 插件日志格式 - 2024-01-01 00:00:00 - [plugin.xxx] - INFO - file.py:123 - message
    pattern1 = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - \[([^\]]+)\] - (\w+) - ([^:]+):(\d+) - (.+)'
    match = re.match(pattern1, line)
    if match:
        timestamp, name, level, file, line_num, message = match.groups()
        return {
            "timestamp": timestamp,
            "level": level,
            "file": file,
            "line": int(line_num),
            "message": message
        }
    
    # 模式2: 服务器日志格式 - 2024-01-01 00:00:00,123 - user_plugin_server - INFO - message
    pattern2 = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d+)?) - ([^-]+) - (\w+) - (.+)'
    match = re.match(pattern2, line)
    if match:
        timestamp, name, level, message = match.groups()
        return {
            "timestamp": timestamp.strip(),
            "level": level.strip(),
            "file": name.strip(),
            "line": 0,
            "message": message.strip()
        }
    
    # 模式3: 标准日志格式 - 2024-01-01 00:00:00 - INFO - message
    pattern3 = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d+)?) - (\w+) - (.+)'
    match = re.match(pattern3, line)
    if match:
        timestamp, level, message = match.groups()
        return {
            "timestamp": timestamp.strip(),
            "level": level.strip(),
            "file": "",
            "line": 0,
            "message": message.strip()
        }
    
    # 如果格式不匹配，返回原始行
    return {
        "timestamp": "",
        "level": "UNKNOWN",
        "file": "",
        "line": 0,
        "message": line
    }


def read_log_file_tail(log_file: Path, lines: int = 100) -> List[Dict[str, Any]]:
    """
    读取日志文件的最后N行
    
    Args:
        log_file: 日志文件路径
        lines: 要读取的行数
    
    Returns:
        解析后的日志条目列表
    """
    if not log_file.exists():
        return []
    
    try:
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            # 使用deque高效读取最后N行
            tail_lines = deque(maxlen=lines)
            for line in f:
                tail_lines.append(line)
            
            # 解析日志行
            parsed_logs = []
            for line in tail_lines:
                log_entry = parse_log_line(line)
                if log_entry:
                    parsed_logs.append(log_entry)
            
            return parsed_logs
    except Exception as e:
        logger.exception(f"Failed to read log file {log_file}: {e}")
        return []


def filter_logs(
    logs: List[Dict[str, Any]],
    level: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    search: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    过滤日志
    
    Args:
        logs: 日志列表
        level: 日志级别过滤
        start_time: 开始时间（ISO格式）
        end_time: 结束时间（ISO格式）
        search: 关键词搜索
    
    Returns:
        过滤后的日志列表
    """
    filtered = logs
    
    # 按级别过滤
    if level:
        level_upper = level.upper()
        filtered = [log for log in filtered if log.get("level") == level_upper]
    
    # 按时间过滤（简单实现，可以改进）
    if start_time or end_time:
        # TODO: 实现时间范围过滤
        pass
    
    # 关键词搜索
    if search:
        search_lower = search.lower()
        filtered = [
            log for log in filtered
            if search_lower in log.get("message", "").lower()
        ]
    
    return filtered


def get_plugin_logs(
    plugin_id: str,
    lines: int = 100,
    level: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    search: Optional[str] = None
) -> Dict[str, Any]:
    """
    获取插件日志或服务器日志
    
    Args:
        plugin_id: 插件ID（或 SERVER_LOG_ID 表示服务器日志）
        lines: 返回的行数
        level: 日志级别过滤
        start_time: 开始时间
        end_time: 结束时间
        search: 关键词搜索
    
    Returns:
        日志数据
    """
    log_dir = get_plugin_log_dir(plugin_id)
    
    if not log_dir.exists():
        return {
            "plugin_id": plugin_id,
            "logs": [],
            "total_lines": 0,
            "returned_lines": 0
        }
    
    # 根据日志类型选择文件模式
    if plugin_id == SERVER_LOG_ID:
        # 服务器日志文件名格式：N.E.K.O_PluginServer_YYYYMMDD.log
        pattern = "N.E.K.O_PluginServer_*.log"
    else:
        # 插件日志文件名格式：{plugin_id}_YYYYMMDD_HHMMSS.log
        pattern = f"{plugin_id}_*.log"
    
    # 找到最新的日志文件
    try:
        log_files = sorted(
            log_dir.glob(pattern),
            key=lambda f: f.stat().st_mtime,
            reverse=True
        )
    except Exception as e:
        logger.exception(f"Failed to find log files in {log_dir} with pattern {pattern}: {e}")
        return {
            "plugin_id": plugin_id,
            "logs": [],
            "total_lines": 0,
            "returned_lines": 0,
            "error": f"Failed to find log files: {str(e)}"
        }
    
    if not log_files:
        logger.info(f"No log files found in {log_dir} with pattern {pattern}")
        return {
            "plugin_id": plugin_id,
            "logs": [],
            "total_lines": 0,
            "returned_lines": 0
        }
    
    latest_log = log_files[0]
    
    # 读取日志
    try:
        logs = read_log_file_tail(latest_log, lines)
    except Exception as e:
        logger.exception(f"Failed to read log file {latest_log}: {e}")
        return {
            "plugin_id": plugin_id,
            "logs": [],
            "total_lines": 0,
            "returned_lines": 0,
            "error": f"Failed to read log file: {str(e)}"
        }
    
    # 过滤
    filtered_logs = filter_logs(logs, level, start_time, end_time, search)
    
    return {
        "plugin_id": plugin_id,
        "logs": filtered_logs,
        "total_lines": len(logs),
        "returned_lines": len(filtered_logs),
        "log_file": latest_log.name
    }

