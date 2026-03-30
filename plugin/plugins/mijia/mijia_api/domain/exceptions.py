"""异常层次结构

定义清晰的异常层次，便于准确识别和处理不同类型的错误。
"""

from typing import Any, Dict, Optional


class MijiaAPIException(Exception):
    """米家API基类异常"""

    def __init__(
        self, message: str, code: Optional[int] = None, context: Optional[Dict[str, Any]] = None
    ) -> None:
        """初始化异常

        Args:
            message: 错误消息
            code: 错误码
            context: 上下文信息
        """
        super().__init__(message)
        self.message = message
        self.code = code
        self.context = context or {}

    def __str__(self) -> str:
        """返回字符串表示"""
        if self.code:
            return f"[{self.code}] {self.message}"
        return self.message


class AuthenticationError(MijiaAPIException):
    """认证相关错误"""

    pass


class LoginFailedError(AuthenticationError):
    """登录失败错误"""

    pass


class TokenExpiredError(AuthenticationError):
    """Token过期错误"""

    def __init__(self, message: str = "Token已过期", code: Optional[int] = 401, context: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message, code=code, context=context)


class DeviceError(MijiaAPIException):
    """设备相关错误"""

    pass


class DeviceOfflineError(DeviceError):
    """设备离线错误"""

    pass


class DeviceNotFoundError(DeviceError):
    """设备不存在错误"""

    pass


class SpecNotFoundError(DeviceError):
    """设备规格不存在错误"""

    pass


class PropertyReadOnlyError(DeviceError):
    """属性只读错误"""

    pass


class NetworkError(MijiaAPIException):
    """网络相关错误"""

    pass


class MijiaTimeoutError(NetworkError):
    """超时错误"""

    pass


class MijiaConnectionError(NetworkError):
    """连接错误"""

    pass


# 保留旧名称的别名，用于向后兼容
TimeoutError = MijiaTimeoutError
ConnectionError = MijiaConnectionError


class ValidationError(MijiaAPIException):
    """参数验证错误"""

    pass


# 错误码到异常类型的映射
ERROR_CODE_MAPPING: Dict[int, type[MijiaAPIException]] = {
    401: TokenExpiredError,
    404: DeviceNotFoundError,
    408: MijiaTimeoutError,
    500: MijiaAPIException,
}


def get_exception_by_code(code: int, message: str) -> MijiaAPIException:
    """根据错误码获取对应的异常实例

    Args:
        code: 错误码
        message: 错误消息

    Returns:
        对应的异常实例
    """
    exception_class = ERROR_CODE_MAPPING.get(code, MijiaAPIException)
    return exception_class(message, code=code)
