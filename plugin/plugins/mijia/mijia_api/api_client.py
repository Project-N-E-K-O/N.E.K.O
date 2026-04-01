"""米家API客户端

提供同步和异步的API客户端实现。
"""

from typing import Any, Dict, List, Optional

from .domain.models import Credential, Device, Home, Scene
from .services.device_service import DeviceService
from .services.scene_service import SceneService
from .services.statistics_service import StatisticsService


class MijiaAPI:
    """米家API客户端（同步版本）

    无状态的API客户端，所有操作都需要使用初始化时传入的Credential。
    支持多用户场景，每个用户创建独立的客户端实例。
    """

    def __init__(
        self,
        credential: Credential,
        device_service: DeviceService,
        scene_service: SceneService,
        statistics_service: Optional[StatisticsService] = None,
        home_repository: Optional[Any] = None,
        cache_manager: Optional[Any] = None,
    ):
        """初始化API客户端

        Args:
            credential: 用户凭据对象
            device_service: 设备服务
            scene_service: 智能服务
            statistics_service: 统计服务（可选）
            home_repository: 家庭仓储（可选）
            cache_manager: 缓存管理器（可选）
        """
        self._credential = credential
        self._device_service = device_service
        self._scene_service = scene_service
        self._statistics_service = statistics_service
        self._home_repository = home_repository
        self._cache_manager = cache_manager

    def get_homes(self) -> List[Home]:
        """获取家庭列表

        Returns:
            家庭列表

        Raises:
            TokenExpiredError: 凭据已过期
            NetworkError: 网络错误
            RuntimeError: 家庭仓储未初始化
        """
        if not self._home_repository:
            raise RuntimeError("家庭仓储未初始化，请使用工厂函数创建API客户端")

        return self._home_repository.get_all(self._credential)

    def get_devices(self, home_id: str) -> List[Device]:
        """获取设备列表

        Args:
            home_id: 家庭ID

        Returns:
            设备列表

        Raises:
            TokenExpiredError: 凭据已过期
            NetworkError: 网络错误
        """
        return self._device_service.get_devices(home_id, self._credential)

    def get_device(self, device_id: str) -> Optional[Device]:
        """获取单个设备

        Args:
            device_id: 设备ID

        Returns:
            设备对象，不存在返回None

        Raises:
            TokenExpiredError: 凭据已过期
            NetworkError: 网络错误
        """
        return self._device_service.get_device_by_id(device_id, self._credential)

    def control_device(
        self, device_id: str, siid: int, piid: int, value: Any, refresh_cache: bool = True
    ) -> bool:
        """控制设备属性

        控制设备后默认会刷新缓存，确保下次获取的是最新状态。

        Args:
            device_id: 设备ID
            siid: 服务ID
            piid: 属性ID
            value: 属性值
            refresh_cache: 是否在控制后刷新缓存，默认为True

        Returns:
            是否成功

        Raises:
            TokenExpiredError: 凭据已过期
            DeviceNotFoundError: 设备不存在
            PropertyReadOnlyError: 属性只读
            ValidationError: 属性值无效
            NetworkError: 网络错误

        Example:
            >>> # 控制设备并自动刷新缓存（推荐）
            >>> api.control_device("device_123", 2, 1, True)
            >>>
            >>> # 控制设备但不刷新缓存（高频操作时使用）
            >>> api.control_device("device_123", 2, 1, True, refresh_cache=False)
        """
        result = self._device_service.set_device_property(
            device_id, siid, piid, value, self._credential
        )

        # 控制成功后刷新缓存
        if result and refresh_cache and self._cache_manager:
            # 获取设备信息以确定所属家庭
            device = self._device_service.get_device_by_id(device_id, self._credential)
            if device:
                # 刷新该家庭的设备缓存
                self._cache_manager.invalidate_pattern(
                    f"{self._credential.user_id}:devices:{device.home_id}"
                )
            else:
                # 设备未查到时，退化为清理当前用户的全部设备缓存
                self._cache_manager.invalidate_pattern(
                    f"{self._credential.user_id}:devices:*"
                )

        return result

    def call_device_action(
        self,
        device_id: str,
        siid: int,
        aiid: int,
        params: Optional[List[Any]] = None,
        refresh_cache: bool = True,
    ) -> Any:
        """调用设备操作

        调用设备操作后默认会刷新缓存，确保下次获取的是最新状态。

        Args:
            device_id: 设备ID
            siid: 服务ID
            aiid: 操作ID
            params: 操作参数列表（可选）
            refresh_cache: 是否在操作后刷新缓存，默认为True

        Returns:
            操作结果

        Raises:
            TokenExpiredError: 凭据已过期
            DeviceNotFoundError: 设备不存在
            NetworkError: 网络错误

        Example:
            >>> # 调用操作并自动刷新缓存（推荐）
            >>> api.call_device_action("device_123", 2, 1, ["auto"])
            >>>
            >>> # 调用操作但不刷新缓存
            >>> api.call_device_action("device_123", 2, 1, ["auto"], refresh_cache=False)
        """
        result = self._device_service.call_device_action(
            device_id, siid, aiid, params or [], self._credential
        )

        # 操作成功后刷新缓存
        if refresh_cache and self._cache_manager:
            # 获取设备信息以确定所属家庭
            device = self._device_service.get_device_by_id(device_id, self._credential)
            if device:
                # 刷新该家庭的设备缓存
                self._cache_manager.invalidate_pattern(
                    f"{self._credential.user_id}:devices:{device.home_id}"
                )
            else:
                # 设备未查到时，退化为清理当前用户的全部设备缓存
                self._cache_manager.invalidate_pattern(
                    f"{self._credential.user_id}:devices:*"
                )

        return result

    def batch_control_devices(
        self, requests: List[Dict[str, Any]], refresh_cache: bool = True
    ) -> List[Dict[str, Any]]:
        """批量控制设备

        批量控制设备后默认会刷新缓存，确保下次获取的是最新状态。

        Args:
            requests: 批量请求列表，每个请求包含device_id、siid、piid、value
            refresh_cache: 是否在控制后刷新缓存，默认为True

        Returns:
            批量操作结果列表

        Raises:
            TokenExpiredError: 凭据已过期
            NetworkError: 网络错误

        Example:
            >>> requests = [
            ...     {"device_id": "device_1", "siid": 2, "piid": 1, "value": True},
            ...     {"device_id": "device_2", "siid": 2, "piid": 1, "value": False},
            ... ]
            >>> # 批量控制并自动刷新缓存（推荐）
            >>> results = api.batch_control_devices(requests)
            >>>
            >>> # 批量控制但不刷新缓存（高频操作时使用）
            >>> results = api.batch_control_devices(requests, refresh_cache=False)
        """
        results = self._device_service.batch_control_devices(requests, self._credential)

        # 批量控制成功后刷新缓存
        if refresh_cache and self._cache_manager:
            # 收集所有涉及的家庭ID
            home_ids = set()
            for request in requests:
                device_id = request.get("device_id")
                if device_id:
                    device = self._device_service.get_device_by_id(device_id, self._credential)
                    if device:
                        home_ids.add(device.home_id)

            # 刷新所有涉及家庭的缓存
            if home_ids:
                for home_id in home_ids:
                    self._cache_manager.invalidate_pattern(
                        f"{self._credential.user_id}:devices:{home_id}"
                    )
            else:
                # 所有设备均未查到时，退化为清理当前用户的全部设备缓存
                self._cache_manager.invalidate_pattern(
                    f"{self._credential.user_id}:devices:*"
                )

        return results

    def get_scenes(self, home_id: str) -> List[Scene]:
        """获取智能列表

        Args:
            home_id: 家庭ID

        Returns:
            智能列表

        Raises:
            TokenExpiredError: 凭据已过期
            NetworkError: 网络错误
        """
        return self._scene_service.get_scenes(home_id, self._credential)

    def execute_scene(self, scene_id: str, home_id: str) -> bool:
        """执行智能

        Args:
            scene_id: 智能ID
            home_id: 家庭ID

        Returns:
            是否成功

        Raises:
            TokenExpiredError: 凭据已过期
            NetworkError: 网络错误
        """
        result = self._scene_service.execute_scene(scene_id, home_id, self._credential)
        # 场景执行可能改变设备状态，失效缓存（键格式与 get_device_list 保持一致）
        self._cache_manager.invalidate_pattern(f"{self._credential.user_id}:devices:{home_id}")
        return result

    def get_device_statistics(self, home_id: str) -> Dict[str, Any]:
        """获取设备统计信息

        Args:
            home_id: 家庭ID

        Returns:
            统计信息字典，包含总数、在线数、离线数、按型号统计等

        Raises:
            TokenExpiredError: 凭据已过期
            NetworkError: 网络错误
        """
        if not self._statistics_service:
            raise RuntimeError("统计服务未初始化")

        return self._statistics_service.get_device_statistics(home_id, self._credential)
    
    def get_device_spec(self, model: str) -> Optional[Any]:
        """获取设备规格

        Args:
            model: 设备型号

        Returns:
            设备规格对象，不存在返回None

        Raises:
            MijiaAPIException: 网络或服务器错误（非规格不存在的情况）
        """
        from .domain.exceptions import SpecNotFoundError, MijiaAPIException

        try:
            return self._device_service.get_device_spec(model)
        except SpecNotFoundError:
            # 规格确实不存在，返回None
            return None
        except MijiaAPIException:
            # 网络/服务器错误，继续抛出
            raise
        except Exception as e:
            # 其他未知错误，包装后抛出
            raise MijiaAPIException(f"获取设备规格失败: {e}") from e
    
    def get_device_properties(self, requests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """批量获取设备属性

        Args:
            requests: 请求列表，每个请求包含did、siid、piid

        Returns:
            结果列表，每个结果包含code、siid、piid、value

        Example:
            >>> requests = [
            >>>     {"did": "device_123", "siid": 2, "piid": 1},
            >>>     {"did": "device_123", "siid": 2, "piid": 2},
            >>> ]
            >>> results = api.get_device_properties(requests)
        """
        # 通过设备服务批量获取属性
        return self._device_service.batch_get_properties(requests, self._credential)

    def update_credential(self, credential: Credential) -> None:
        """更新凭据

        当凭据被刷新后，可以更新客户端使用的凭据。

        Args:
            credential: 新的凭据对象
        """
        self._credential = credential

    @property
    def credential(self) -> Credential:
        """获取当前使用的凭据

        Returns:
            凭据对象
        """
        return self._credential

    def refresh_cache(self, home_id: Optional[str] = None) -> None:
        """刷新缓存

        主动刷新缓存，强制从API重新获取数据。

        Args:
            home_id: 家庭ID，如果指定则只刷新该家庭的缓存，否则刷新当前用户的所有缓存

        Example:
            >>> # 刷新特定家庭的缓存
            >>> api.refresh_cache(home_id="123456")
            >>>
            >>> # 刷新当前用户的所有缓存
            >>> api.refresh_cache()
        """
        if not self._cache_manager:
            return

        if home_id:
            # 刷新特定家庭的缓存
            self._cache_manager.invalidate_pattern(f"{self._credential.user_id}:devices:{home_id}")
            self._cache_manager.invalidate_pattern(f"{self._credential.user_id}:scenes:{home_id}")
        else:
            # 刷新当前用户的所有缓存
            self._cache_manager.clear(namespace=self._credential.user_id)

    def clear_all_cache(self) -> None:
        """清空所有缓存

        清空所有用户的所有缓存数据。谨慎使用！

        Example:
            >>> api.clear_all_cache()
        """
        if self._cache_manager:
            self._cache_manager.clear()

    def close(self) -> None:
        """关闭API客户端，释放底层HTTP连接池资源。

        应在不再使用客户端时调用（如插件关闭时）。
        """
        try:
            # 关闭底层 HttpClient（持有 httpx.Client 连接池）
            # DeviceRepositoryImpl 使用 _http 属性存储 HttpClient
            repo = getattr(self._device_service, '_device_repo', None)
            if repo:
                http_client = getattr(repo, '_http', None)
                if http_client and hasattr(http_client, 'close'):
                    http_client.close()
        except Exception:
            pass  # 关闭时忽略错误

    def __enter__(self):
        """上下文管理器入口"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口，自动关闭资源"""
        self.close()


class AsyncMijiaAPI:
    """米家API客户端（异步版本）

    提供与同步版本相同的接口，但所有方法都是异步的。
    适用于异步应用场景，如异步Web框架、并发设备控制等。

    实现说明：
        当前版本使用 asyncio.to_thread 在后台线程中执行同步服务层调用。
        这是一个实用且高效的方案，可以避免阻塞事件循环，适用于大多数场景。

        AsyncHttpClient 已实现，未来可以扩展为完全异步的架构：
        AsyncHttpClient -> 异步仓储层 -> 异步服务层 -> AsyncMijiaAPI
    """

    def __init__(
        self,
        credential: Credential,
        device_service: DeviceService,
        scene_service: SceneService,
        statistics_service: Optional[StatisticsService] = None,
        home_repository: Optional[Any] = None,
        cache_manager: Optional[Any] = None,
    ):
        """初始化异步API客户端

        Args:
            credential: 用户凭据对象
            device_service: 设备服务
            scene_service: 智能服务
            statistics_service: 统计服务（可选）
            home_repository: 家庭仓储（可选）
            cache_manager: 缓存管理器（可选）
        """
        self._credential = credential
        self._device_service = device_service
        self._scene_service = scene_service
        self._statistics_service = statistics_service
        self._home_repository = home_repository
        self._cache_manager = cache_manager

    async def get_homes(self) -> List[Home]:
        """异步获取家庭列表

        Returns:
            家庭列表

        Raises:
            TokenExpiredError: 凭据已过期
            NetworkError: 网络错误
            RuntimeError: 家庭仓储未初始化
        """
        if not self._home_repository:
            raise RuntimeError("家庭仓储未初始化，请使用工厂函数创建API客户端")

        import asyncio
        return await asyncio.to_thread(
            self._home_repository.get_all, self._credential
        )

    async def get_devices(self, home_id: str) -> List[Device]:
        """异步获取设备列表

        Args:
            home_id: 家庭ID

        Returns:
            设备列表

        Raises:
            TokenExpiredError: 凭据已过期
            NetworkError: 网络错误
        """
        import asyncio
        return await asyncio.to_thread(
            self._device_service.get_devices, home_id, self._credential
        )

    async def get_device(self, device_id: str) -> Optional[Device]:
        """异步获取单个设备

        Args:
            device_id: 设备ID

        Returns:
            设备对象，不存在返回None
        """
        import asyncio
        return await asyncio.to_thread(
            self._device_service.get_device_by_id, device_id, self._credential
        )

    async def control_device(
        self, device_id: str, siid: int, piid: int, value: Any, refresh_cache: bool = True
    ) -> bool:
        """异步控制设备属性

        控制设备后默认会刷新缓存，确保下次获取的是最新状态。

        Args:
            device_id: 设备ID
            siid: 服务ID
            piid: 属性ID
            value: 属性值
            refresh_cache: 是否在控制后刷新缓存，默认为True

        Returns:
            是否成功

        Example:
            >>> # 控制设备并自动刷新缓存（推荐）
            >>> await api.control_device("device_123", 2, 1, True)
            >>>
            >>> # 控制设备但不刷新缓存
            >>> await api.control_device("device_123", 2, 1, True, refresh_cache=False)
        """
        import asyncio

        result = await asyncio.to_thread(
            self._device_service.set_device_property,
            device_id,
            siid,
            piid,
            value,
            self._credential,
        )

        # 控制成功后刷新缓存
        if result and refresh_cache and self._cache_manager:
            device = await asyncio.to_thread(
                self._device_service.get_device_by_id, device_id, self._credential
            )
            if device:
                await asyncio.to_thread(
                    self._cache_manager.invalidate_pattern,
                    f"{self._credential.user_id}:devices:{device.home_id}",
                )
            else:
                await asyncio.to_thread(
                    self._cache_manager.invalidate_pattern,
                    f"{self._credential.user_id}:devices:*",
                )

        return result

    async def call_device_action(
        self,
        device_id: str,
        siid: int,
        aiid: int,
        params: Optional[List[Any]] = None,
        refresh_cache: bool = True,
    ) -> Any:
        """异步调用设备操作

        调用设备操作后默认会刷新缓存，确保下次获取的是最新状态。

        Args:
            device_id: 设备ID
            siid: 服务ID
            aiid: 操作ID
            params: 操作参数列表（可选）
            refresh_cache: 是否在操作后刷新缓存，默认为True

        Returns:
            操作结果

        Example:
            >>> # 调用操作并自动刷新缓存（推荐）
            >>> await api.call_device_action("device_123", 2, 1, ["auto"])
            >>>
            >>> # 调用操作但不刷新缓存
            >>> await api.call_device_action("device_123", 2, 1, ["auto"], refresh_cache=False)
        """
        import asyncio

        result = await asyncio.to_thread(
            self._device_service.call_device_action,
            device_id,
            siid,
            aiid,
            params or [],
            self._credential,
        )

        # 操作成功后刷新缓存
        if refresh_cache and self._cache_manager:
            device = await asyncio.to_thread(
                self._device_service.get_device_by_id, device_id, self._credential
            )
            if device:
                await asyncio.to_thread(
                    self._cache_manager.invalidate_pattern,
                    f"{self._credential.user_id}:devices:{device.home_id}",
                )
            else:
                await asyncio.to_thread(
                    self._cache_manager.invalidate_pattern,
                    f"{self._credential.user_id}:devices:*",
                )

        return result

    async def batch_control_devices(
        self, requests: List[Dict[str, Any]], refresh_cache: bool = True
    ) -> List[Dict[str, Any]]:
        """异步批量控制设备

        批量控制设备后默认会刷新缓存，确保下次获取的是最新状态。

        Args:
            requests: 批量请求列表，每个请求包含device_id、siid、piid、value
            refresh_cache: 是否在控制后刷新缓存，默认为True

        Returns:
            批量操作结果列表

        Raises:
            TokenExpiredError: 凭据已过期
            NetworkError: 网络错误

        Example:
            >>> requests = [
            ...     {"device_id": "device_1", "siid": 2, "piid": 1, "value": True},
            ...     {"device_id": "device_2", "siid": 2, "piid": 1, "value": False},
            ... ]
            >>> # 批量控制并自动刷新缓存（推荐）
            >>> results = await api.batch_control_devices(requests)
            >>>
            >>> # 批量控制但不刷新缓存（高频操作时使用）
            >>> results = await api.batch_control_devices(requests, refresh_cache=False)
        """
        import asyncio

        results = await asyncio.to_thread(
            self._device_service.batch_control_devices, requests, self._credential
        )

        # 批量控制成功后刷新缓存
        if refresh_cache and self._cache_manager:
            # 收集所有涉及的家庭ID
            home_ids = set()
            for request in requests:
                device_id = request.get("device_id")
                if device_id:
                    device = await asyncio.to_thread(
                        self._device_service.get_device_by_id, device_id, self._credential
                    )
                    if device:
                        home_ids.add(device.home_id)

            # 刷新所有涉及家庭的缓存
            if home_ids:
                for home_id in home_ids:
                    await asyncio.to_thread(
                        self._cache_manager.invalidate_pattern,
                        f"{self._credential.user_id}:devices:{home_id}",
                    )
            else:
                await asyncio.to_thread(
                    self._cache_manager.invalidate_pattern,
                    f"{self._credential.user_id}:devices:*",
                )

        return results

    async def get_scenes(self, home_id: str) -> List[Scene]:
        """异步获取智能列表

        Args:
            home_id: 家庭ID

        Returns:
            智能列表
        """
        import asyncio
        return await asyncio.to_thread(
            self._scene_service.get_scenes, home_id, self._credential
        )

    async def execute_scene(self, scene_id: str, home_id: str) -> bool:
        """异步执行智能

        Args:
            scene_id: 智能ID
            home_id: 家庭ID

        Returns:
            是否成功
        """
        import asyncio
        result = await asyncio.to_thread(
            self._scene_service.execute_scene, scene_id, home_id, self._credential
        )
        # 场景执行可能改变设备状态，失效缓存（键格式与 get_device_list 保持一致）
        await asyncio.to_thread(
            self._cache_manager.invalidate_pattern, f"{self._credential.user_id}:devices:{home_id}"
        )
        return result

    async def get_device_statistics(self, home_id: str) -> Dict[str, Any]:
        """异步获取设备统计信息

        Args:
            home_id: 家庭ID

        Returns:
            统计信息字典
        """
        if not self._statistics_service:
            raise RuntimeError("统计服务未初始化")

        import asyncio
        return await asyncio.to_thread(
            self._statistics_service.get_device_statistics, home_id, self._credential
        )

    async def get_device_spec(self, model: str) -> Optional[Any]:
        """异步获取设备规格

        Args:
            model: 设备型号

        Returns:
            设备规格对象，不存在返回None
        """
        if not model:
            return None
        import asyncio

        from .domain.exceptions import SpecNotFoundError, MijiaAPIException

        def _get_spec():
            try:
                return self._device_service.get_device_spec(model)
            except SpecNotFoundError:
                # 规格确实不存在，返回None
                return None
            except MijiaAPIException:
                # 网络/服务器错误，继续抛出
                raise
            except Exception as e:
                # 其他未知错误，包装后抛出
                raise MijiaAPIException(f"获取设备规格失败: {e}") from e

        return await asyncio.to_thread(_get_spec)

    async def get_device_properties(self, requests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """异步批量获取设备属性

        Args:
            requests: 请求列表，每个请求包含did、siid、piid

        Returns:
            结果列表，每个结果包含code、siid、piid、value

        Example:
            >>> requests = [
            >>>     {"did": "device_123", "siid": 2, "piid": 1},
            >>>     {"did": "device_123", "siid": 2, "piid": 2},
            >>> ]
            >>> results = await api.get_device_properties(requests)
        """
        import asyncio
        return await asyncio.to_thread(
            self._device_service.batch_get_properties, requests, self._credential
        )

    def update_credential(self, credential: Credential) -> None:
        """更新凭据

        Args:
            credential: 新的凭据对象
        """
        self._credential = credential

    @property
    def credential(self) -> Credential:
        """获取当前使用的凭据

        Returns:
            凭据对象
        """
        return self._credential

    async def refresh_cache(self, home_id: Optional[str] = None) -> None:
        """异步刷新缓存

        主动刷新缓存，强制从API重新获取数据。

        Args:
            home_id: 家庭ID，如果指定则只刷新该家庭的缓存，否则刷新当前用户的所有缓存

        Example:
            >>> # 刷新特定家庭的缓存
            >>> await api.refresh_cache(home_id="123456")
            >>>
            >>> # 刷新当前用户的所有缓存
            >>> await api.refresh_cache()
        """
        if not self._cache_manager:
            return

        import asyncio
        if home_id:
            # 刷新特定家庭的缓存
            await asyncio.to_thread(
                self._cache_manager.invalidate_pattern,
                f"{self._credential.user_id}:devices:{home_id}"
            )
            await asyncio.to_thread(
                self._cache_manager.invalidate_pattern,
                f"{self._credential.user_id}:scenes:{home_id}"
            )
        else:
            # 刷新当前用户的所有缓存
            await asyncio.to_thread(
                self._cache_manager.clear,
                namespace=self._credential.user_id
            )

    async def clear_all_cache(self) -> None:
        """异步清空所有缓存

        清空所有用户的所有缓存数据。谨慎使用！

        Example:
            >>> await api.clear_all_cache()
        """
        if self._cache_manager:
            import asyncio
            await asyncio.to_thread(self._cache_manager.clear)

    async def close(self) -> None:
        """关闭API客户端，释放底层HTTP连接池资源。

        应在不再使用客户端时调用（如插件关闭时）。
        """
        import asyncio
        try:
            # 关闭底层 HttpClient（持有 httpx.AsyncClient 连接池）
            # HttpClient.close() 是同步方法，AsyncHttpClient.close() 是协程
            repo = getattr(self._device_service, '_device_repo', None)
            if repo:
                http_client = getattr(repo, '_http', None)
                if http_client and hasattr(http_client, 'close'):
                    if asyncio.iscoroutinefunction(http_client.close):
                        # AsyncHttpClient：协程方法，直接 await
                        await http_client.close()
                    else:
                        # HttpClient：同步方法，用 to_thread 避免阻塞
                        await asyncio.to_thread(http_client.close)
        except Exception:
            pass  # 关闭时忽略错误
