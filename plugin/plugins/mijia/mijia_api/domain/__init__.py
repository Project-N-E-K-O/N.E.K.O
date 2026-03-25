"""领域层

包含核心业务实体和领域模型，不依赖外部服务或基础设施。

模块：
- models: 领域实体模型（Credential、Device、Home、Scene等）
- exceptions: 异常层次结构
"""

from ..domain.exceptions import (
    AuthenticationError,
    ConnectionError,
    DeviceError,
    DeviceNotFoundError,
    DeviceOfflineError,
    LoginFailedError,
    MijiaAPIException,
    NetworkError,
    PropertyReadOnlyError,
    TimeoutError,
    TokenExpiredError,
    ValidationError,
)
from ..domain.models import (
    ConsumableItem,
    Credential,
    Device,
    DeviceAction,
    DeviceProperty,
    DeviceStatus,
    Home,
    PropertyAccess,
    PropertyType,
    Scene,
)

__all__ = [
    # 实体模型
    "Credential",
    "Device",
    "DeviceProperty",
    "DeviceAction",
    "Home",
    "Scene",
    "ConsumableItem",
    # 枚举类型
    "DeviceStatus",
    "PropertyType",
    "PropertyAccess",
    # 异常类
    "MijiaAPIException",
    "AuthenticationError",
    "LoginFailedError",
    "TokenExpiredError",
    "DeviceError",
    "DeviceOfflineError",
    "DeviceNotFoundError",
    "PropertyReadOnlyError",
    "NetworkError",
    "TimeoutError",
    "ConnectionError",
    "ValidationError",
]
