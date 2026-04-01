"""依赖注入工厂

提供便捷的工厂函数，自动创建和组装所有依赖组件。
"""

import os
from pathlib import Path
from typing import Any, Optional

from .api_client import AsyncMijiaAPI, MijiaAPI
from .core.config import ConfigManager
from .core.logging import get_logger
from .domain.models import Credential
from .infrastructure.cache_manager import CacheManager
from .infrastructure.credential_provider import CredentialProvider
from .infrastructure.credential_store import FileCredentialStore, ICredentialStore
from .infrastructure.crypto_service import CryptoService
from .infrastructure.http_client import HttpClient
from .repositories.device_repository import DeviceRepositoryImpl
from .repositories.device_spec_repository import DeviceSpecRepositoryImpl
from .repositories.home_repository import HomeRepositoryImpl
from .repositories.scene_repository import SceneRepositoryImpl
from .services.auth_service import AuthService
from .services.device_service import DeviceService
from .services.scene_service import SceneService
from .services.statistics_service import StatisticsService

logger = get_logger(__name__)


def _find_project_root() -> Path:
    """查找项目根目录
    
    从当前文件向上查找，直到找到包含 pyproject.toml 的目录。
    如果找不到，则使用当前工作目录。
    
    Returns:
        项目根目录路径
    """
    current = Path(__file__).resolve()
    
    # 向上查找，最多查找10层
    for _ in range(10):
        current = current.parent
        if (current / "pyproject.toml").exists():
            return current
        # 到达文件系统根目录
        if current.parent == current:
            break
    
    # 如果找不到，使用当前工作目录
    return Path.cwd()


def create_config_manager(config_path: Optional[Path] = None) -> ConfigManager:
    """创建配置管理器

    Args:
        config_path: 配置文件路径（可选）

    Returns:
        配置管理器实例
    """
    if config_path is None:
        # SDK包的配置文件路径（相对于当前文件）
        sdk_config = Path(__file__).parent.parent / "configs" / "mijiaAPI.toml"
        
        # 尝试从默认位置加载配置（优先级从高到低）
        default_paths = [
            Path("configs/mijiaAPI.toml"),  # 项目根目录的configs目录
            Path("config.toml"),  # 项目根目录
            Path.home() / ".mijia" / "config.toml",  # 用户主目录
            sdk_config,  # SDK自带的默认配置（最低优先级）
        ]

        for path in default_paths:
            if path.exists():
                config_path = path
                logger.info(f"使用配置文件: {config_path}")
                break

    return ConfigManager(config_path)


def create_api_client(
    credential: Credential,
    config_path: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
    redis_client: Optional[Any] = None,
) -> MijiaAPI:
    """创建米家API客户端（同步版本）

    自动创建和组装所有依赖组件，包括：
    - 配置管理器
    - 加密服务
    - HTTP客户端
    - 缓存管理器
    - 仓储层
    - 服务层

    Args:
        credential: 用户凭据对象
        config_path: 配置文件路径（可选）
        cache_dir: 缓存目录（可选）
        redis_client: Redis客户端（可选，用于分布式缓存）

    Returns:
        配置好的MijiaAPI实例

    Example:
        >>> # 基本使用
        >>> credential = load_credential()
        >>> api = create_api_client(credential)
        >>> devices = api.get_devices(home_id="123456")

        >>> # 使用自定义配置
        >>> api = create_api_client(
        ...     credential,
        ...     config_path=Path("my_config.toml")
        ... )

        >>> # 使用Redis缓存
        >>> from mijiaAPI_V2.infrastructure.redis_client import RedisClient
        >>> config = create_config_manager()
        >>> redis = RedisClient(config) if config.get("REDIS_ENABLED") else None
        >>> api = create_api_client(credential, redis_client=redis)
    """
    # 1. 创建配置管理器
    config = create_config_manager(config_path)

    # 2. 创建基础设施组件
    crypto_service = CryptoService()
    http_client = HttpClient(config, crypto_service)
    cache_manager = CacheManager(cache_dir=cache_dir, redis_client=redis_client)

    # 3. 创建仓储层
    home_repo = HomeRepositoryImpl(http_client, cache_manager)
    device_repo = DeviceRepositoryImpl(http_client, cache_manager)
    scene_repo = SceneRepositoryImpl(http_client)
    spec_repo = DeviceSpecRepositoryImpl(http_client, cache_manager)

    # 4. 创建服务层
    device_service = DeviceService(device_repo, spec_repo, cache_manager)
    scene_service = SceneService(scene_repo)
    statistics_service = StatisticsService(device_repo)

    # 5. 创建API客户端
    api = MijiaAPI(
        credential=credential,
        device_service=device_service,
        scene_service=scene_service,
        statistics_service=statistics_service,
        home_repository=home_repo,
        cache_manager=cache_manager,
    )

    logger.info(f"API客户端创建成功，用户ID: {credential.user_id}")

    return api


def create_async_api_client(
    credential: Credential,
    config_path: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
    redis_client: Optional[Any] = None,
) -> AsyncMijiaAPI:
    """创建米家API客户端（异步版本）

    自动创建和组装所有依赖组件，提供异步API接口。

    Args:
        credential: 用户凭据对象
        config_path: 配置文件路径（可选）
        cache_dir: 缓存目录（可选）
        redis_client: Redis客户端（可选）

    Returns:
        配置好的AsyncMijiaAPI实例

    Example:
        >>> import asyncio
        >>>
        >>> async def main():
        ...     credential = load_credential()
        ...     api = create_async_api_client(credential)
        ...     devices = await api.get_devices(home_id="123456")
        ...     print(f"找到 {len(devices)} 个设备")
        >>>
        >>> asyncio.run(main())
    """
    # 1. 创建配置管理器
    config = create_config_manager(config_path)

    # 2. 创建基础设施组件
    # 注意：异步API客户端当前使用同步服务层 + asyncio.to_thread
    # AsyncHttpClient 已实现，可用于未来的完全异步架构扩展
    crypto_service = CryptoService()
    http_client = HttpClient(config, crypto_service)
    cache_manager = CacheManager(cache_dir=cache_dir, redis_client=redis_client)

    # 3. 创建仓储层
    home_repo = HomeRepositoryImpl(http_client, cache_manager)
    device_repo = DeviceRepositoryImpl(http_client, cache_manager)
    scene_repo = SceneRepositoryImpl(http_client)
    spec_repo = DeviceSpecRepositoryImpl(http_client, cache_manager)

    # 4. 创建服务层
    device_service = DeviceService(device_repo, spec_repo, cache_manager)
    scene_service = SceneService(scene_repo)
    statistics_service = StatisticsService(device_repo)

    # 5. 创建异步API客户端
    api = AsyncMijiaAPI(
        credential=credential,
        device_service=device_service,
        scene_service=scene_service,
        statistics_service=statistics_service,
        home_repository=home_repo,
        cache_manager=cache_manager,
    )

    logger.info(f"异步API客户端创建成功，用户ID: {credential.user_id}")

    return api


def create_auth_service(
    config_path: Optional[Path] = None, credential_store: Optional[ICredentialStore] = None,
    credential_path: Optional[Path] = None,
) -> AuthService:
    """创建认证服务

    用于获取、刷新和管理用户凭据。

    Note:
        默认凭据路径为 ``.mijia/credential.json``（相对于项目根目录），
        与插件入口 ``self.data_path("credential.json")`` 的默认路径不同。
        如需统一，可通过 ``MIJIA_CREDENTIAL_PATH`` 环境变量或
        ``credential_path`` 参数显式指定同一路径。

    Args:
        config_path: 配置文件路径（可选）
        credential_store: 凭据存储实现（可选，默认使用文件存储）

    Returns:
        认证服务实例

    Example:
        >>> # 二维码登录
        >>> auth_service = create_auth_service()
        >>> credential = auth_service.login_by_qrcode()
        >>> auth_service.save_credential(credential)

        >>> # 加载已保存的凭据
        >>> credential = auth_service.load_credential()
        >>> if credential and credential.is_expired():
        ...     credential = auth_service.refresh_credential(credential)
        ...     auth_service.save_credential(credential)
    """
    # 创建配置管理器
    config = create_config_manager(config_path)

    # 创建凭据提供者
    provider = CredentialProvider(config)

    # 创建凭据存储
    if credential_store is None:
        # 从环境变量 > 配置 > 默认值 依次读取凭据路径
        credential_path_str = (
            os.environ.get("MIJIA_CREDENTIAL_PATH")
            or config.get("CREDENTIAL_PATH")
            or ".mijia/credential.json"
        )
        credential_path = Path(credential_path_str)
        
        # 如果是相对路径，相对于项目根目录
        if not credential_path.is_absolute() and not str(credential_path).startswith("~"):
            # 查找项目根目录
            project_root = _find_project_root()
            credential_path = project_root / credential_path
        # 如果是用户目录路径，展开 ~
        elif str(credential_path).startswith("~"):
            credential_path = credential_path.expanduser()
        
        credential_store = FileCredentialStore(default_path=credential_path)

    # 创建认证服务
    auth_service = AuthService(provider, credential_store)

    logger.info("认证服务创建成功")

    return auth_service


def create_multi_user_clients(
    credentials: dict[str, Credential],
    config_path: Optional[Path] = None,
    redis_client: Optional[Any] = None,
) -> dict[str, MijiaAPI]:
    """创建多用户API客户端

    为多个用户创建独立的API客户端实例，每个用户的状态和缓存完全隔离。
    适用于多用户平台场景。

    Args:
        credentials: 用户凭据字典，key为用户标识，value为Credential对象
        config_path: 配置文件路径（可选）
        redis_client: Redis客户端（可选，用于跨进程缓存共享）

    Returns:
        用户API客户端字典，key为用户标识，value为MijiaAPI实例

    Example:
        >>> # 多用户场景
        >>> credentials = {
        ...     "user_a": load_credential("user_a.json"),
        ...     "user_b": load_credential("user_b.json")
        ... }
        >>>
        >>> clients = create_multi_user_clients(credentials)
        >>>
        >>> # 用户A的操作
        >>> devices_a = clients["user_a"].get_devices(home_id="123")
        >>>
        >>> # 用户B的操作（完全隔离）
        >>> devices_b = clients["user_b"].get_devices(home_id="456")
    """
    clients = {}

    for user_id, credential in credentials.items():
        # 为每个用户创建独立的缓存目录（如果不使用Redis）
        cache_dir = None
        if redis_client is None:
            # 用 user_id 的哈希作为目录名，防止路径穿越
            import hashlib
            safe_name = hashlib.sha256(user_id.encode()).hexdigest()[:16]
            cache_dir = Path.home() / ".mijia" / "cache" / safe_name

        # 创建API客户端
        client = create_api_client(
            credential=credential,
            config_path=config_path,
            cache_dir=cache_dir,
            redis_client=redis_client,
        )

        clients[user_id] = client
        logger.info(f"为用户 {user_id} 创建API客户端")

    logger.info(f"多用户API客户端创建完成，共 {len(clients)} 个用户")

    return clients


# 便捷函数：从文件加载凭据并创建客户端
def create_api_client_from_file(
    credential_path: Optional[Path] = None,
    config_path: Optional[Path] = None,
    redis_client: Optional[Any] = None,
) -> MijiaAPI:
    """从文件加载凭据并创建API客户端

    Args:
        credential_path: 凭据文件路径（可选，默认从配置文件读取）
        config_path: 配置文件路径（可选）
        redis_client: Redis客户端（可选）

    Returns:
        配置好的MijiaAPI实例

    Raises:
        FileNotFoundError: 凭据文件不存在
        ValueError: 凭据无效或已过期

    Example:
        >>> # 使用默认路径（从配置文件读取）
        >>> api = create_api_client_from_file()
        >>>
        >>> # 使用自定义路径
        >>> api = create_api_client_from_file(
        ...     credential_path=Path("my_credential.json")
        ... )
    """
    # 创建配置管理器
    config = create_config_manager(config_path)
    
    # 如果未指定凭据路径，从环境变量 > 配置 > 默认值 依次读取
    if credential_path is None:
        credential_path_str = (
            os.environ.get("MIJIA_CREDENTIAL_PATH")
            or config.get("CREDENTIAL_PATH")
            or ".mijia/credential.json"
        )
        credential_path = Path(credential_path_str)
        
        # 如果是相对路径，相对于项目根目录
        if not credential_path.is_absolute() and not str(credential_path).startswith("~"):
            project_root = _find_project_root()
            credential_path = project_root / credential_path
        # 如果是用户目录路径，展开 ~
        elif str(credential_path).startswith("~"):
            credential_path = credential_path.expanduser()
    
    # 加载凭据
    store = FileCredentialStore(default_path=credential_path)
    credential = store.load()

    if credential is None:
        raise FileNotFoundError(f"凭据文件不存在: {credential_path}，请先登录")

    if credential.is_expired():
        raise ValueError("凭据已过期，请刷新凭据")

    # 创建API客户端
    return create_api_client(
        credential=credential, config_path=config_path, redis_client=redis_client
    )
