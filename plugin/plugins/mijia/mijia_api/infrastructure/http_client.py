"""HTTP客户端

提供统一的HTTP请求接口，支持重试、日志、加密等功能。
"""

import json as json_module
import time
from typing import Any, Dict

import httpx

from ..core.config import ConfigManager
from ..core.logging import get_logger
from ..domain.exceptions import (
    ConnectionError,
    DeviceNotFoundError,
    MijiaAPIException,
    NetworkError,
    TimeoutError,
    TokenExpiredError,
    get_exception_by_code,
)
from ..domain.models import Credential
from .crypto_service import CryptoService

logger = get_logger(__name__)


class HttpClient:
    """同步HTTP客户端

    提供统一的HTTP请求接口，支持：
    - 读接口自动重试（最多3次，指数退避），写接口禁用重试
    - 请求数据加密
    - 错误码到异常的转换
    - 结构化日志记录（请求URL、user_id、响应时间）
    - 连接池管理
    """

    def __init__(self, config: ConfigManager, crypto: CryptoService):
        """初始化HTTP客户端

        Args:
            config: 配置管理器
            crypto: 加密服务
        """
        self._config = config
        self._crypto = crypto

        # 创建httpx客户端，配置连接池和超时
        timeout = self._config.get("DEFAULT_TIMEOUT", 30)
        self._client = httpx.Client(
            timeout=timeout,
            limits=httpx.Limits(
                max_connections=100,  # 最大连接数
                max_keepalive_connections=20,  # 最大保持连接数
            ),
        )

    def _do_post(
        self, url: str, encrypted_params: Dict[str, str], headers: Dict[str, str], **kwargs: Any
    ) -> httpx.Response:
        """执行POST请求（内部方法，无重试）

        Args:
            url: 完整URL
            encrypted_params: 加密后的请求参数
            headers: 请求头
            **kwargs: 其他httpx.post参数

        Returns:
            httpx响应对象
        """
        response = self._client.post(url, data=encrypted_params, headers=headers, **kwargs)
        response.raise_for_status()
        return response

    def _do_post_with_retry(
        self, url: str, encrypted_params: Dict[str, str], headers: Dict[str, str], **kwargs: Any
    ) -> httpx.Response:
        """执行POST请求（带重试，仅用于读接口）

        Args:
            url: 完整URL
            encrypted_params: 加密后的请求参数
            headers: 请求头
            **kwargs: 其他httpx.post参数

        Returns:
            httpx响应对象
        """
        max_retries = 3
        last_exception: Exception | None = None
        for attempt in range(max_retries):
            try:
                return self._do_post(url, encrypted_params, headers, **kwargs)
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_exception = e
                if attempt < max_retries - 1:
                    wait_time = min(2 ** attempt, 10)
                    time.sleep(wait_time)
                    continue
        # 所有重试均失败
        if last_exception is not None:
            raise last_exception

    def post(
        self, path: str, json: Dict[str, Any], credential: Credential, **kwargs: Any
    ) -> Dict[str, Any]:
        """发送POST请求

        写接口（/miotspec/prop/set、/miotspec/action）禁用自动重试，
        以避免服务端已执行成功后因超时触发重复操作。

        Args:
            path: API路径（相对路径，如 "/home/device_list"）
            json: 请求数据字典
            credential: 用户凭据对象
            **kwargs: 其他httpx.post参数

        Returns:
            API响应数据字典

        Raises:
            TokenExpiredError: Token已过期
            DeviceNotFoundError: 设备不存在
            TimeoutError: 请求超时
            NetworkError: 网络错误
            MijiaAPIException: 其他API错误
        """
        # 构建完整URL
        base_url = self._config.get("API_BASE_URL", "https://api.io.mi.com/app")
        url = base_url + path

        # 构建请求头
        headers = {
            "User-Agent": credential.user_agent,
            "Content-Type": "application/x-www-form-urlencoded",
            "accept-encoding": "identity",
            "miot-accept-encoding": "GZIP",
            "miot-encrypt-algorithm": "ENCRYPT-RC4",
            "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2",
            "Cookie": f"cUserId={credential.c_user_id};"
                     f"yetAnotherServiceToken={credential.service_token};"
                     f"serviceToken={credential.service_token};"
                     f"PassportDeviceId={credential.device_id};",
        }

        # 加密请求参数
        encrypted_params = self._crypto.encrypt_params(path, json, credential.ssecurity)

        # 设置请求ID用于追踪
        logger.set_request_id()

        # 记录请求开始
        start_time = time.time()
        logger.info(
            f"发送请求: {path}",
            extra={"url": url, "user_id": credential.user_id, "path": path},
        )

        try:
            # 判断是否对写接口禁用重试（避免超时导致重复执行设备控制）
            should_retry = path not in ("/miotspec/prop/set", "/miotspec/action")
            # 发送POST请求
            if should_retry:
                response = self._do_post_with_retry(url, encrypted_params, headers, **kwargs)
            else:
                response = self._do_post(url, encrypted_params, headers, **kwargs)

            # 记录原始响应（用于调试）
            logger.debug(
                f"原始响应: {path}",
                extra={
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                    "content_length": len(response.content),
                    "content_preview": response.text[:200] if response.text else "(empty)",
                },
            )

            # 解密响应
            decrypted_text = self._crypto.decrypt_response(
                response.text, credential.ssecurity, encrypted_params["_nonce"]
            )

            # 解析JSON响应
            result: Dict[str, Any] = json_module.loads(decrypted_text)

            # 计算响应时间
            response_time = time.time() - start_time

            # 记录响应成功
            logger.info(
                f"请求成功: {path}",
                extra={
                    "url": url,
                    "user_id": credential.user_id,
                    "response_time": f"{response_time:.3f}s",
                    "status_code": response.status_code,
                },
            )

            # 检查业务错误码
            if result.get("code") != 0:
                self._handle_error(result)

            return result

        except httpx.TimeoutException as e:
            # 请求超时
            response_time = time.time() - start_time
            logger.error(
                f"请求超时: {path}",
                extra={
                    "url": url,
                    "user_id": credential.user_id,
                    "response_time": f"{response_time:.3f}s",
                },
            )
            raise TimeoutError(f"请求超时: {path}") from e

        except httpx.HTTPStatusError as e:
            # HTTP状态码错误
            response_time = time.time() - start_time
            logger.error(
                f"HTTP状态码错误: {path}",
                extra={
                    "url": url,
                    "user_id": credential.user_id,
                    "status_code": e.response.status_code,
                    "response_time": f"{response_time:.3f}s",
                },
            )
            raise NetworkError(f"HTTP错误: {e.response.status_code}") from e

        except httpx.ConnectError as e:
            # 连接错误
            response_time = time.time() - start_time
            logger.error(
                f"连接错误: {path}",
                extra={
                    "url": url,
                    "user_id": credential.user_id,
                    "response_time": f"{response_time:.3f}s",
                },
            )
            raise ConnectionError(f"连接失败: {str(e)}") from e

        except httpx.HTTPError as e:
            # 其他HTTP错误
            response_time = time.time() - start_time
            logger.error(
                f"HTTP错误: {path}",
                extra={
                    "url": url,
                    "user_id": credential.user_id,
                    "response_time": f"{response_time:.3f}s",
                },
                exc_info=e,
            )
            raise NetworkError(f"网络错误: {str(e)}") from e

    def _handle_error(self, result: Dict[str, Any]) -> None:
        """处理业务错误码

        将API返回的错误码转换为对应的业务异常。

        Args:
            result: API响应数据

        Raises:
            TokenExpiredError: code=401，Token已过期
            DeviceNotFoundError: code=404，设备不存在
            MijiaAPIException: 其他错误码
        """
        code = result.get("code")
        message = result.get("message", "未知错误")

        # 特殊日志处理（复用集中的错误码映射）
        if code == 401:
            logger.warning(f"Token已过期", extra={"error_code": code, "error_message": message})
            raise TokenExpiredError("Token已过期")
        elif code == 404:
            logger.warning(f"设备不存在: {message}", extra={"error_code": code})
            raise DeviceNotFoundError(message)
        else:
            exc = get_exception_by_code(code, message)
            logger.warning(f"API业务错误: {message}", extra={"error_code": code})
            raise exc

    def close(self) -> None:
        """关闭HTTP客户端

        释放连接池资源。应在不再使用客户端时调用。
        """
        logger.info("关闭HTTP客户端")
        self._client.close()

    def __enter__(self) -> "HttpClient":
        """上下文管理器入口"""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """上下文管理器退出，自动关闭客户端"""
        self.close()


class AsyncHttpClient:
    """异步HTTP客户端

    提供异步的HTTP请求接口，支持：
    - 读接口自动重试（最多3次，指数退避），写接口禁用重试
    - 请求数据加密
    - 错误码到异常的转换
    - 结构化日志记录（请求URL、user_id、响应时间）
    - 连接池管理
    """

    def __init__(self, config: ConfigManager, crypto: CryptoService):
        """初始化异步HTTP客户端

        Args:
            config: 配置管理器
            crypto: 加密服务
        """
        self._config = config
        self._crypto = crypto

        # 创建httpx异步客户端，配置连接池和超时
        timeout = self._config.get("DEFAULT_TIMEOUT", 30)
        self._client = httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(
                max_connections=100,  # 最大连接数
                max_keepalive_connections=20,  # 最大保持连接数
            ),
        )

    async def _do_post(
        self, url: str, encrypted_params: Dict[str, str], headers: Dict[str, str], **kwargs: Any
    ) -> httpx.Response:
        """执行POST请求（内部方法，无重试）"""
        response = await self._client.post(url, data=encrypted_params, headers=headers, **kwargs)
        response.raise_for_status()
        return response

    async def _do_post_with_retry(
        self, url: str, encrypted_params: Dict[str, str], headers: Dict[str, str], **kwargs: Any
    ) -> httpx.Response:
        """执行POST请求（带重试，仅用于读接口）"""
        import asyncio

        max_retries = 3
        last_exception: Exception | None = None
        for attempt in range(max_retries):
            try:
                return await self._do_post(url, encrypted_params, headers, **kwargs)
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_exception = e
                if attempt < max_retries - 1:
                    wait_time = min(2 ** attempt, 10)
                    await asyncio.sleep(wait_time)
                    continue
        if last_exception is not None:
            raise last_exception

    async def post(
        self, path: str, json: Dict[str, Any], credential: Credential, **kwargs: Any
    ) -> Dict[str, Any]:
        """发送异步POST请求

        Args:
            path: API路径（相对路径，如 "/home/device_list"）
            json: 请求数据字典
            credential: 用户凭据对象
            **kwargs: 其他httpx.post参数

        Returns:
            API响应数据字典

        Raises:
            TokenExpiredError: Token已过期
            DeviceNotFoundError: 设备不存在
            TimeoutError: 请求超时
            NetworkError: 网络错误
            MijiaAPIException: 其他API错误
        """
        # 构建完整URL
        base_url = self._config.get("API_BASE_URL", "https://api.io.mi.com/app")
        url = base_url + path

        # 构建请求头
        headers = {
            "User-Agent": credential.user_agent,
            "Content-Type": "application/x-www-form-urlencoded",
            "accept-encoding": "identity",
            "miot-accept-encoding": "GZIP",
            "miot-encrypt-algorithm": "ENCRYPT-RC4",
            "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2",
            "Cookie": f"cUserId={credential.c_user_id};"
                     f"yetAnotherServiceToken={credential.service_token};"
                     f"serviceToken={credential.service_token};"
                     f"PassportDeviceId={credential.device_id};",
        }

        # 加密请求参数
        encrypted_params = self._crypto.encrypt_params(path, json, credential.ssecurity)

        # 设置请求ID用于追踪
        logger.set_request_id()

        # 记录请求开始
        start_time = time.time()
        logger.info(
            f"发送异步请求: {path}",
            extra={"url": url, "user_id": credential.user_id, "path": path},
        )

        try:
            # 判断是否对写接口禁用重试（避免超时导致重复执行设备控制）
            should_retry = path not in ("/miotspec/prop/set", "/miotspec/action")
            if should_retry:
                response = await self._do_post_with_retry(url, encrypted_params, headers, **kwargs)
            else:
                response = await self._do_post(url, encrypted_params, headers, **kwargs)

            # 解密响应
            decrypted_text = self._crypto.decrypt_response(
                response.text, credential.ssecurity, encrypted_params["_nonce"]
            )

            # 解析JSON响应
            result: Dict[str, Any] = json_module.loads(decrypted_text)

            # 计算响应时间
            response_time = time.time() - start_time

            # 记录响应成功
            logger.info(
                f"异步请求成功: {path}",
                extra={
                    "url": url,
                    "user_id": credential.user_id,
                    "response_time": f"{response_time:.3f}s",
                    "status_code": response.status_code,
                },
            )

            # 检查业务错误码
            if result.get("code") != 0:
                self._handle_error(result)

            return result

        except httpx.TimeoutException as e:
            # 请求超时
            response_time = time.time() - start_time
            logger.error(
                f"异步请求超时: {path}",
                extra={
                    "url": url,
                    "user_id": credential.user_id,
                    "response_time": f"{response_time:.3f}s",
                },
            )
            raise TimeoutError(f"请求超时: {path}") from e

        except httpx.HTTPStatusError as e:
            # HTTP状态码错误
            response_time = time.time() - start_time
            logger.error(
                f"异步HTTP状态码错误: {path}",
                extra={
                    "url": url,
                    "user_id": credential.user_id,
                    "status_code": e.response.status_code,
                    "response_time": f"{response_time:.3f}s",
                },
            )
            raise NetworkError(f"HTTP错误: {e.response.status_code}") from e

        except httpx.ConnectError as e:
            # 连接错误
            response_time = time.time() - start_time
            logger.error(
                f"异步连接错误: {path}",
                extra={
                    "url": url,
                    "user_id": credential.user_id,
                    "response_time": f"{response_time:.3f}s",
                },
            )
            raise ConnectionError(f"连接失败: {str(e)}") from e

        except httpx.HTTPError as e:
            # 其他HTTP错误
            response_time = time.time() - start_time
            logger.error(
                f"异步HTTP错误: {path}",
                extra={
                    "url": url,
                    "user_id": credential.user_id,
                    "response_time": f"{response_time:.3f}s",
                },
                exc_info=e,
            )
            raise NetworkError(f"网络错误: {str(e)}") from e

    def _handle_error(self, result: Dict[str, Any]) -> None:
        """处理业务错误码

        将API返回的错误码转换为对应的业务异常。

        Args:
            result: API响应数据

        Raises:
            TokenExpiredError: code=401，Token已过期
            DeviceNotFoundError: code=404，设备不存在
            MijiaAPIException: 其他错误码
        """
        code = result.get("code")
        message = result.get("message", "未知错误")

        # 特殊日志处理（复用集中的错误码映射）
        if code == 401:
            logger.warning(f"Token已过期", extra={"error_code": code, "error_message": message})
            raise TokenExpiredError("Token已过期")
        elif code == 404:
            logger.warning(f"设备不存在: {message}", extra={"error_code": code})
            raise DeviceNotFoundError(message)
        else:
            exc = get_exception_by_code(code, message)
            logger.warning(f"API业务错误: {message}", extra={"error_code": code})
            raise exc

    async def close(self) -> None:
        """关闭异步HTTP客户端

        释放连接池资源。应在不再使用客户端时调用。
        """
        logger.info("关闭异步HTTP客户端")
        await self._client.aclose()

    async def __aenter__(self) -> "AsyncHttpClient":
        """异步上下文管理器入口"""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """异步上下文管理器退出，自动关闭客户端"""
        await self.close()
