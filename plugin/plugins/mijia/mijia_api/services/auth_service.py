"""认证服务

负责凭据的获取、刷新和管理，但不负责API调用。
"""

from typing import Optional

from ..domain.models import Credential
from ..infrastructure.credential_provider import CredentialProvider
from ..infrastructure.credential_store import ICredentialStore


class AuthService:
    """认证服务

    负责凭据的获取、刷新和管理。AuthService作为凭据管理的门面，
    协调CredentialProvider和ICredentialStore的工作。

    注意：
        - CredentialProvider负责从米家服务器获取和刷新凭据
        - ICredentialStore负责凭据的本地持久化存储
        - AuthService不负责API调用，只管理凭据生命周期
    """

    def __init__(self, provider: CredentialProvider, store: ICredentialStore):
        """初始化认证服务

        Args:
            provider: 凭据提供者，负责从服务器获取和刷新凭据
            store: 凭据存储接口，负责凭据的本地持久化
        """
        self._provider = provider
        self._store = store

    def login_by_qrcode(self) -> Credential:
        """通过二维码登录获取凭据

        委托给CredentialProvider执行二维码登录流程，包括：
        1. 生成二维码URL
        2. 显示二维码供用户扫描
        3. 轮询等待用户扫码确认
        4. 获取服务令牌
        5. 构建并返回Credential对象

        Returns:
            Credential: 包含用户认证信息的凭据对象

        Raises:
            LoginFailedError: 当登录失败时抛出
            NetworkError: 当网络请求失败时抛出

        示例:
            >>> auth_service = AuthService(provider, store)
            >>> credential = auth_service.login_by_qrcode()
            >>> print(f"登录成功，用户ID: {credential.user_id}")
        """
        return self._provider.login_by_qrcode()

    def refresh_credential(self, credential: Credential) -> Credential:
        """刷新凭据

        当凭据即将过期或已过期时，使用此方法获取新的凭据。
        委托给CredentialProvider执行刷新操作。

        Args:
            credential: 需要刷新的旧凭据对象

        Returns:
            Credential: 刷新后的新凭据对象

        Raises:
            TokenExpiredError: 当凭据无法刷新时抛出
            NetworkError: 当网络请求失败时抛出

        示例:
            >>> if credential.is_expired():
            ...     new_credential = auth_service.refresh_credential(credential)
            ...     auth_service.save_credential(new_credential)
        """
        return self._provider.refresh(credential)

    def save_credential(self, credential: Credential, path: Optional[str] = None) -> None:
        """保存凭据到存储

        将凭据持久化到本地存储（默认为文件系统）。
        委托给ICredentialStore执行存储操作。

        Args:
            credential: 要保存的凭据对象
            path: 可选的存储路径，如果未指定则使用默认路径

        Raises:
            Exception: 当存储操作失败时抛出异常

        示例:
            >>> credential = auth_service.login_by_qrcode()
            >>> auth_service.save_credential(credential)
            >>> # 或指定自定义路径
            >>> auth_service.save_credential(credential, "/path/to/credential.json")
        """
        self._store.save(credential, path)

    def load_credential(self, path: Optional[str] = None) -> Optional[Credential]:
        """从存储加载凭据

        从本地存储加载之前保存的凭据。
        委托给ICredentialStore执行加载操作。

        Args:
            path: 可选的存储路径，如果未指定则使用默认路径

        Returns:
            Optional[Credential]: 加载的凭据对象，如果不存在或加载失败则返回None

        示例:
            >>> credential = auth_service.load_credential()
            >>> if credential and credential.is_valid():
            ...     print("凭据有效，可以使用")
            ... else:
            ...     print("需要重新登录")
        """
        return self._store.load(path)

    def revoke_credential(self, credential: Credential) -> bool:
        """撤销凭据

        主动撤销凭据，使其在服务器端失效。
        委托给CredentialProvider执行撤销操作。

        Args:
            credential: 要撤销的凭据对象

        Returns:
            bool: 撤销是否成功

        Raises:
            NetworkError: 当网络请求失败时抛出

        示例:
            >>> if auth_service.revoke_credential(credential):
            ...     print("凭据已撤销")
            ...     auth_service.save_credential(None)  # 删除本地存储
        """
        return self._provider.revoke(credential)
    
    async def async_get_qrcode(self):
        return await self._provider.get_qrcode_async()

    async def async_poll_login(self, login_url: str, timeout: int = 120):
        return await self._provider.poll_login_result_async(login_url, timeout)

    async def async_refresh_credential(self, credential: Credential) -> Credential:
        """异步刷新凭据"""
        import asyncio
        return await asyncio.to_thread(self.refresh_credential, credential)