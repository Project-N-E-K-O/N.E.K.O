"""结构化日志系统

提供JSON格式的结构化日志，支持请求追踪和敏感信息脱敏。
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional


class StructuredLogger:
    """结构化日志记录器

    提供JSON格式的结构化日志输出，支持：
    - 请求ID追踪
    - 敏感信息脱敏
    - 多种日志级别（DEBUG、INFO、WARNING、ERROR、CRITICAL）
    - 自动添加时间戳
    """

    def __init__(self, name: str):
        """初始化结构化日志记录器

        Args:
            name: 日志记录器名称，通常使用模块名
        """
        self.logger = logging.getLogger(name)
        self._request_id: Optional[str] = None

    def set_request_id(self, request_id: Optional[str] = None) -> None:
        """设置请求ID用于追踪

        Args:
            request_id: 请求ID，如果为None则自动生成UUID
        """
        self._request_id = request_id or str(uuid.uuid4())

    def _format_message(self, message: str, extra: Optional[Dict[str, Any]] = None) -> str:
        """格式化日志消息为JSON格式

        Args:
            message: 日志消息内容
            extra: 额外的上下文信息

        Returns:
            JSON格式的日志字符串
        """
        log_data: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "message": message,
            "request_id": self._request_id,
        }

        if extra:
            # 脱敏处理
            sanitized = self._sanitize(extra)
            log_data.update(sanitized)

        return json.dumps(log_data, ensure_ascii=False)

    def _sanitize(self, data: Any) -> Any:
        """脱敏处理敏感信息

        对包含敏感关键字的字段进行脱敏处理，替换为"***"。
        敏感关键字包括：token、password、ssecurity、service_token等。
        递归处理嵌套字典和列表。

        Args:
            data: 需要脱敏的数据（字典、列表或原始值）

        Returns:
            脱敏后的数据
        """
        sensitive_keys = ["token", "password", "ssecurity", "service_token"]

        if isinstance(data, dict):
            sanitized: Dict[str, Any] = {}
            for key, value in data.items():
                if any(sk in key.lower() for sk in sensitive_keys):
                    sanitized[key] = "***"
                else:
                    sanitized[key] = self._sanitize(value)
            return sanitized
        elif isinstance(data, list):
            return [self._sanitize(item) for item in data]
        else:
            return data

    def debug(self, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        """记录DEBUG级别日志

        Args:
            message: 日志消息
            extra: 额外的上下文信息
        """
        self.logger.debug(self._format_message(message, extra))

    def info(self, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        """记录INFO级别日志

        Args:
            message: 日志消息
            extra: 额外的上下文信息
        """
        self.logger.info(self._format_message(message, extra))

    def warning(self, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        """记录WARNING级别日志

        Args:
            message: 日志消息
            extra: 额外的上下文信息
        """
        self.logger.warning(self._format_message(message, extra))

    def error(
        self, message: str, extra: Optional[Dict[str, Any]] = None, exc_info: Any = None
    ) -> None:
        """记录ERROR级别日志

        Args:
            message: 日志消息
            extra: 额外的上下文信息
            exc_info: 异常信息，可以是异常对象或True（自动获取当前异常）
        """
        self.logger.error(self._format_message(message, extra), exc_info=exc_info)

    def critical(
        self, message: str, extra: Optional[Dict[str, Any]] = None, exc_info: Any = None
    ) -> None:
        """记录CRITICAL级别日志

        Args:
            message: 日志消息
            extra: 额外的上下文信息
            exc_info: 异常信息，可以是异常对象或True（自动获取当前异常）
        """
        self.logger.critical(self._format_message(message, extra), exc_info=exc_info)


def get_logger(name: str) -> StructuredLogger:
    """获取结构化日志记录器

    工厂函数，用于创建StructuredLogger实例。

    Args:
        name: 日志记录器名称，通常使用__name__

    Returns:
        StructuredLogger实例

    Example:
        >>> logger = get_logger(__name__)
        >>> logger.set_request_id()
        >>> logger.info("用户登录成功", {"user_id": "12345"})
    """
    return StructuredLogger(name)
