"""HTTP客户端协议

定义同步和异步HTTP客户端的统一接口。
"""

from typing import Any, Dict, Protocol

from ..domain.models import Credential


class HttpClientProtocol(Protocol):
    """HTTP客户端协议（同步）"""

    def post(
        self, path: str, json: Dict[str, Any], credential: Credential, **kwargs: Any
    ) -> Dict[str, Any]:
        """发送POST请求

        Args:
            path: API路径
            json: 请求数据
            credential: 用户凭据
            **kwargs: 其他参数

        Returns:
            响应数据
        """
        ...

    def close(self) -> None:
        """关闭客户端"""
        ...


class AsyncHttpClientProtocol(Protocol):
    """HTTP客户端协议（异步）"""

    async def post(
        self, path: str, json: Dict[str, Any], credential: Credential, **kwargs: Any
    ) -> Dict[str, Any]:
        """发送异步POST请求

        Args:
            path: API路径
            json: 请求数据
            credential: 用户凭据
            **kwargs: 其他参数

        Returns:
            响应数据
        """
        ...

    async def close(self) -> None:
        """关闭异步客户端"""
        ...
