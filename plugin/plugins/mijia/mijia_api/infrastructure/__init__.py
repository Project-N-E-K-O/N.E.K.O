"""基础设施层

提供技术基础设施组件，如HTTP客户端、缓存、加密、日志等。

模块：
- http_client: HTTP客户端（同步和异步）
- cache_manager: 缓存管理器
- redis_client: Redis客户端（可选，未实现）
- crypto_service: 加密服务
- credential_provider: 凭据提供者
- credential_store: 凭据存储
"""

from ..infrastructure.cache_manager import CacheManager
from ..infrastructure.credential_provider import CredentialProvider
from ..infrastructure.credential_store import (
    FileCredentialStore,
    ICredentialStore,
)
from ..infrastructure.crypto_service import CryptoService
from ..infrastructure.http_client import AsyncHttpClient, HttpClient

__all__ = [
    # HTTP客户端
    "HttpClient",
    "AsyncHttpClient",
    # 缓存管理
    "CacheManager",
    # 加密服务
    "CryptoService",
    # 凭据管理
    "CredentialProvider",
    "ICredentialStore",
    "FileCredentialStore",
]
