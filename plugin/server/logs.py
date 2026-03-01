"""
插件日志服务

提供插件日志和服务器日志的读取和查询功能。
支持 WebSocket 实时推送日志更新。
"""
import re
import asyncio
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
from collections import deque

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from loguru import logger

from plugin.settings import PLUGIN_CONFIG_ROOT

# 服务器日志的特殊 ID
SERVER_LOG_ID = "_server"


def _validate_plugin_id(plugin_id: str) -> None:
    """
    验证 plugin_id 是否安全（防止路径遍历攻击）
    
    Args:
        plugin_id: 插件ID
    
    Raises:
        HTTPException: 如果 plugin_id 不安全
    """
    # 服务器日志ID是特殊的，允许通过
    if plugin_id == SERVER_LOG_ID:
        return
    
    # 验证 plugin_id 只包含安全字符（防止路径遍历攻击）
    if not re.match(r'^[a-zA-Z0-9_-]+$', plugin_id):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid plugin_id: '{plugin_id}'. Only alphanumeric characters, underscores, and hyphens are allowed."
        )
    
    # 额外检查：确保不包含路径遍历符号
    if '..' in plugin_id or '/' in plugin_id or '\\' in plugin_id:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid plugin_id: '{plugin_id}'. Path traversal characters are not allowed."
        )


def get_plugin_log_dir(plugin_id: str) -> Path:
    """
    获取插件的日志目录
    
    安全措施：
    1. 验证 plugin_id 只包含安全字符（防止路径遍历攻击）
    2. 使用 resolve() 和路径检查确保路径在安全目录内
    
    Args:
        plugin_id: 插件ID（或 SERVER_LOG_ID 表示服务器日志）
    
    Returns:
        日志目录路径
    
    Raises:
        HTTPException: 如果 plugin_id 不安全
    """
    # 验证 plugin_id 安全性（防止路径遍历攻击）
    _validate_plugin_id(plugin_id)
    
    # 如果是服务器日志，使用应用日志目录（log文件夹）
    if plugin_id == SERVER_LOG_ID:
        try:
            try:
                from utils.logger_config import RobustLoggerConfig
            except Exception:
                import importlib.util

                project_root = PLUGIN_CONFIG_ROOT.parent.parent
                logger_config_path = project_root / "utils" / "logger_config.py"
                spec = importlib.util.spec_from_file_location("utils.logger_config", logger_config_path)
                if spec is None or spec.loader is None:
                    raise
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                RobustLoggerConfig = getattr(mod, "RobustLoggerConfig")
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
            # PLUGIN_CONFIG_ROOT 是 plugin/plugins，需要向上两级到项目根目录
            project_root = PLUGIN_CONFIG_ROOT.parent.parent
            fallback_dir = project_root / "log"
            fallback_dir.mkdir(parents=True, exist_ok=True)
            return fallback_dir
    
    # 插件日志：优先使用项目根目录下的 log/plugins/{plugin_id} 目录
    try:
        # PLUGIN_CONFIG_ROOT 是 plugin/plugins，需要向上两级到项目根目录
        project_root = PLUGIN_CONFIG_ROOT.parent.parent
        log_dir = project_root / "log" / "plugins" / plugin_id
        
        # 解析路径并验证它在安全目录内（防止路径遍历攻击）
        try:
            resolved_path = log_dir.resolve()
            root_resolved = project_root.resolve()
            resolved_str = str(resolved_path)
            root_str = str(root_resolved)
            if not resolved_str.startswith(root_str):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid plugin_id: '{plugin_id}'. Path traversal detected."
                )
        except (OSError, ValueError) as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid plugin_id: '{plugin_id}'. {str(e)}"
            ) from e
        
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
            
            # 再次验证降级路径的安全性
            try:
                resolved_plugin_dir = plugin_dir.resolve()
                config_root_resolved = PLUGIN_CONFIG_ROOT.resolve()
                resolved_plugin_str = str(resolved_plugin_dir)
                config_root_str = str(config_root_resolved)
                if not resolved_plugin_str.startswith(config_root_str):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid plugin_id: '{plugin_id}'. Path traversal detected in fallback path."
                    )
            except (OSError, ValueError) as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid plugin_id: '{plugin_id}'. {str(e)}"
                ) from e
            
            log_dir = plugin_dir / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            return log_dir
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Failed to use project log directory, using plugin directory: {e}")
        # 降级方案：使用插件目录下的logs子目录
        plugin_dir = PLUGIN_CONFIG_ROOT / plugin_id
        
        # 验证降级路径的安全性
        try:
            resolved_plugin_dir = plugin_dir.resolve()
            config_root_resolved = PLUGIN_CONFIG_ROOT.resolve()
            resolved_plugin_str = str(resolved_plugin_dir)
            config_root_str = str(config_root_resolved)
            if not resolved_plugin_str.startswith(config_root_str):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid plugin_id: '{plugin_id}'. Path traversal detected in fallback path."
                )
        except (OSError, ValueError) as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid plugin_id: '{plugin_id}'. {str(e)}"
            ) from e
        
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
    4. 管道分隔格式: 2024-01-01 00:00:00 | INFO | module:function:123 | message
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

    pattern3b = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(.+)'
    match = re.match(pattern3b, line)
    if match:
        timestamp, level, message = match.groups()
        return {
            "timestamp": timestamp.strip(),
            "level": level.strip(),
            "file": "",
            "line": 0,
            "message": message.strip(),
        }
    
    # 模式4: 管道分隔格式 - 2024-01-01 00:00:00 | INFO | module:function:123 | message
    # 支持格式: timestamp | level | location | message
    # location 可以是 module:function:line 或 module:function 或 module
    pattern4 = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*\|\s*(\w+)\s*\|\s*([^|]+)\s*\|\s*(.+)'
    match = re.match(pattern4, line)
    if match:
        timestamp, level, location, message = match.groups()
        # 解析 location: 可能是 module:function:line 或 module:function 或 module
        location_parts = location.strip().split(':')
        if len(location_parts) >= 3:
            # module:function:line
            file = location_parts[0]
            line_num = int(location_parts[-1]) if location_parts[-1].isdigit() else 0
        elif len(location_parts) == 2:
            # module:function
            file = location_parts[0]
            line_num = 0
        else:
            # module
            file = location_parts[0] if location_parts else ""
            line_num = 0
        
        return {
            "timestamp": timestamp.strip(),
            "level": level.strip(),
            "file": file.strip(),
            "line": line_num,
            "message": message.strip()
        }

    # 模式5: loguru 管道分隔格式 - 2024-01-01 00:00:00 | INFO | [Proc-xxx] message
    # 支持格式: timestamp | level | message
    pattern5 = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*\|\s*(\w+)\s*\|\s*(.+)'
    match = re.match(pattern5, line)
    if match:
        timestamp, level, message = match.groups()
        message = message.strip()

        file = ""
        # 尝试提取 loguru 前缀，如 [Proc-xxx] 或 [Plugin-xxx]
        prefix_match = re.match(r'^\[([^\]]+)\]\s*(.*)$', message)
        if prefix_match:
            file, message = prefix_match.groups()

        return {
            "timestamp": timestamp.strip(),
            "level": level.strip(),
            "file": (file or "").strip(),
            "line": 0,
            "message": (message or "").strip()
        }

    pattern6 = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.(\d+))?\s*\|\s*(\w+)\s*\|\s*([^|]+?)\s*-\s*(.+)'
    match = re.match(pattern6, line)
    if match:
        ts, ms, level, location, message = match.groups()
        timestamp = ts.strip()
        if isinstance(ms, str) and ms:
            timestamp = f"{timestamp}.{ms}"
        location = (location or "").strip()
        message = (message or "").strip()
        file = ""
        line_num = 0
        try:
            parts = location.split(":")
            if len(parts) >= 3 and parts[-1].isdigit():
                line_num = int(parts[-1])
                file = ":".join(parts[:-2]) if len(parts) > 3 else parts[0]
            else:
                file = parts[0] if parts else location
        except Exception:
            file = location
            line_num = 0
        return {
            "timestamp": timestamp,
            "level": level.strip(),
            "file": file.strip(),
            "line": line_num,
            "message": message,
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
        logger.exception(f"Failed to read log file {log_file}")
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
        logger.exception(f"Failed to find log files in {log_dir} with pattern {pattern}")
        return {
            "plugin_id": plugin_id,
            "logs": [],
            "total_lines": 0,
            "returned_lines": 0,
            "error": f"Failed to find log files"
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
        logger.exception(f"Failed to read log file {latest_log}")
        return {
            "plugin_id": plugin_id,
            "logs": [],
            "total_lines": 0,
            "returned_lines": 0,
            "error": f"Failed to read log file"
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


# ========== WebSocket 实时日志推送 ==========

# 全局日志监控器字典：{plugin_id: LogFileWatcher}
_log_watchers: Dict[str, 'LogFileWatcher'] = {}
# 保护 _log_watchers 的线程锁（用于异步环境下的并发访问）
_log_watchers_lock = threading.Lock()


def read_log_file_incremental(log_file: Path, last_position: int) -> tuple[List[Dict[str, Any]], int]:
    """
    从指定位置读取日志文件的增量内容
    
    Args:
        log_file: 日志文件路径
        last_position: 上次读取的位置（字节偏移）
    
    Returns:
        (新增的日志条目列表, 新的位置)
    """
    if not log_file.exists():
        return [], last_position
    
    try:
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            # 移动到上次读取的位置
            f.seek(last_position)
            
            # 读取新增内容
            new_lines = f.readlines()
            new_position = f.tell()
            
            # 解析新增的日志行
            new_logs = []
            for line in new_lines:
                log_entry = parse_log_line(line)
                if log_entry:
                    new_logs.append(log_entry)
            
            return new_logs, new_position
    except Exception as e:
        logger.exception(f"Failed to read incremental log from {log_file}")
        return [], last_position


class LogFileWatcher:
    """日志文件监控器，用于 WebSocket 实时推送"""
    
    def __init__(self, plugin_id: str):
        self.plugin_id = plugin_id
        self.clients: Set[WebSocket] = set()
        self.last_position: int = 0
        self.current_log_file: Optional[Path] = None
        self._watch_task: Optional[asyncio.Task] = None
        self._running = False
    
    def add_client(self, websocket: WebSocket):
        """添加 WebSocket 客户端"""
        self.clients.add(websocket)
        if not self._running:
            self._start_watching()
    
    def remove_client(self, websocket: WebSocket):
        """移除 WebSocket 客户端"""
        self.clients.discard(websocket)
        if not self.clients and self._running:
            self._stop_watching()
    
    def _start_watching(self):
        """开始监控日志文件"""
        if self._running:
            return
        
        self._running = True
        self._watch_task = asyncio.create_task(self._watch_loop())
    
    def _stop_watching(self):
        """停止监控日志文件"""
        self._running = False
        if self._watch_task:
            self._watch_task.cancel()
            self._watch_task = None
    
    async def _watch_loop(self):
        """监控循环：定期检查文件变化并推送新日志"""
        while self._running:
            try:
                # 获取最新的日志文件
                log_dir = get_plugin_log_dir(self.plugin_id)
                
                if self.plugin_id == SERVER_LOG_ID:
                    pattern = "N.E.K.O_PluginServer_*.log"
                else:
                    pattern = f"{self.plugin_id}_*.log"
                
                log_files = sorted(
                    log_dir.glob(pattern),
                    key=lambda f: f.stat().st_mtime,
                    reverse=True
                )
                
                if not log_files:
                    await asyncio.sleep(1)  # 没有日志文件，等待
                    continue
                
                latest_log = log_files[0]
                
                # 如果日志文件切换了，重置位置
                if self.current_log_file != latest_log:
                    self.current_log_file = latest_log
                    self.last_position = 0
                
                # 读取增量日志
                new_logs, new_position = read_log_file_incremental(
                    latest_log, self.last_position
                )
                
                if new_logs:
                    self.last_position = new_position
                    # 推送新日志给所有客户端
                    await self._broadcast_logs(new_logs)
                
                # 等待 0.5 秒后再次检查
                await asyncio.sleep(0.5)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Error in log watcher loop for {self.plugin_id}")
                await asyncio.sleep(1)
    
    async def _broadcast_logs(self, logs: List[Dict[str, Any]]):
        """广播日志给所有连接的客户端"""
        if not logs or not self.clients:
            return
        
        disconnected = []
        for client in self.clients:
            try:
                # 检查连接状态
                if hasattr(client, 'client_state'):
                    from starlette.websockets import WebSocketState
                    if client.client_state != WebSocketState.CONNECTED:
                        disconnected.append(client)
                        continue
                
                await client.send_json({
                    "type": "append",
                    "logs": logs
                })
            except (WebSocketDisconnect, ConnectionError, RuntimeError) as e:
                # 连接已断开或关闭，记录但不抛出异常
                logger.debug(f"Failed to send logs to client (disconnected): {e}")
                disconnected.append(client)
            except Exception as e:
                # 其他错误，记录并标记为断开
                logger.debug(f"Failed to send logs to client: {e}")
                disconnected.append(client)
        
        # 移除断开的客户端
        for client in disconnected:
            self.clients.discard(client)
    
    async def send_initial_logs(self, websocket: WebSocket, lines: int = 100):
        """发送初始日志（最后 N 行）"""
        try:
            result = get_plugin_logs(self.plugin_id, lines=lines)
            await websocket.send_json({
                "type": "initial",
                "logs": result.get("logs", []),
                "log_file": result.get("log_file"),
                "total_lines": result.get("total_lines", 0)
            })
            
            # 记录当前日志文件和位置
            log_dir = get_plugin_log_dir(self.plugin_id)
            if self.plugin_id == SERVER_LOG_ID:
                pattern = "N.E.K.O_PluginServer_*.log"
            else:
                pattern = f"{self.plugin_id}_*.log"
            
            log_files = sorted(
                log_dir.glob(pattern),
                key=lambda f: f.stat().st_mtime,
                reverse=True
            )
            
            if log_files:
                self.current_log_file = log_files[0]
                # 获取文件当前大小作为起始位置
                self.last_position = self.current_log_file.stat().st_size
        except Exception as e:
            logger.exception("Failed to send initial logs")


async def log_stream_endpoint(websocket: WebSocket, plugin_id: str):
    """
    WebSocket 端点：实时推送日志流
    
    Args:
        websocket: WebSocket 连接
        plugin_id: 插件ID（或 SERVER_LOG_ID 表示服务器日志）
    """
    await websocket.accept()
    
    # 获取或创建监控器（使用锁保护，避免并发访问问题）
    with _log_watchers_lock:
        if plugin_id not in _log_watchers:
            _log_watchers[plugin_id] = LogFileWatcher(plugin_id)
        watcher = _log_watchers[plugin_id]
    
    watcher.add_client(websocket)
    
    try:
        # 发送初始日志
        await watcher.send_initial_logs(websocket, lines=100)
        
        # 保持连接，等待客户端消息（可选）
        while True:
            try:
                # 接收客户端消息（如过滤条件变更等）
                _ = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                # 可以处理客户端消息，比如更新过滤条件
                # 目前暂时忽略
            except asyncio.TimeoutError:
                # 发送心跳保持连接
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    # 如果发送失败，连接可能已关闭，退出循环
                    break
            except WebSocketDisconnect:
                break
            except Exception as e:
                # 其他异常，记录日志并退出
                logger.debug(f"WebSocket receive error for {plugin_id}: {e}")
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        # 记录错误但不抛出，确保 finally 块执行
        logger.debug(f"Error in log stream endpoint for {plugin_id}: {e}")
    finally:
        # 确保清理客户端连接
        try:
            watcher.remove_client(websocket)
            # 如果没有客户端了，清理监控器（使用锁保护）
            with _log_watchers_lock:
                if not watcher.clients:
                    _log_watchers.pop(plugin_id, None)
        except Exception as e:
            logger.debug(f"Error cleaning up watcher for {plugin_id}: {e}")

