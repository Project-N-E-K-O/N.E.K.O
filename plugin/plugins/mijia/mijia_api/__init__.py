"""米家API SDK - 重构版本

这是米家智能家居Python SDK的重构版本，采用分层架构设计。

核心特性：
- 凭据与操作完全分离
- 支持多用户并发场景
- 清晰的分层架构（领域层、仓储层、服务层、基础设施层）
- 完整的类型注解
- 异步API支持
- 智能缓存管理
- 结构化日志系统

基本使用：
    from mijiaAPI_V2 import create_api_client
    from mijiaAPI_V2.infrastructure.credential_provider import CredentialProvider

    # 获取凭据
    provider = CredentialProvider()
    credential = provider.login_by_qrcode()

    # 创建API客户端
    api = create_api_client(credential)

    # 获取设备列表
    devices = api.get_devices(home_id="your_home_id")
"""

__version__ = "2.0.0"
__author__ = "MijiaAPI Contributors"

from .api_client import AsyncMijiaAPI, MijiaAPI
from .domain.exceptions import (
    AuthenticationError,
    DeviceError,
    MijiaAPIException,
    NetworkError,
    ValidationError,
)
from .domain.models import (
    Credential,
    Device,
    DeviceAction,
    DeviceProperty,
    Home,
    Scene,
)
from .factory import (
    create_api_client,
    create_api_client_from_file,
    create_async_api_client,
    create_auth_service,
    create_multi_user_clients,
)

__all__ = [
    # 版本信息
    "__version__",
    "__author__",
    # API客户端
    "MijiaAPI",
    "AsyncMijiaAPI",
    # 工厂函数
    "create_api_client",
    "create_async_api_client",
    "create_auth_service",
    "create_multi_user_clients",
    "create_api_client_from_file",
    # 领域模型
    "Credential",
    "Device",
    "DeviceProperty",
    "DeviceAction",
    "Home",
    "Scene",
    # 异常类
    "MijiaAPIException",
    "AuthenticationError",
    "DeviceError",
    "NetworkError",
    "ValidationError",
]
