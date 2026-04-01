"""异步设备仓储实现

提供异步的设备数据访问接口。

此模块实现了 IAsyncDeviceRepository 接口，提供完整的异步设备操作支持。
"""

from typing import Any, Dict, List, Optional

from ..core.logging import get_logger
from ..domain.models import Credential, Device, DeviceStatus
from ..infrastructure.cache_manager import CacheManager
from ..infrastructure.http_client import AsyncHttpClient
from .interfaces import IAsyncDeviceRepository

logger = get_logger(__name__)


class AsyncDeviceRepositoryImpl(IAsyncDeviceRepository):
    """异步设备仓储实现

    使用 AsyncHttpClient 提供异步的设备数据访问。
    """

    def __init__(self, http_client: AsyncHttpClient, cache_manager: CacheManager):
        """初始化异步设备仓储

        Args:
            http_client: 异步HTTP客户端
            cache_manager: 缓存管理器
        """
        self._http = http_client
        self._cache = cache_manager

    async def get_all(self, home_id: str, credential: Credential) -> List[Device]:
        """异步获取设备列表

        Args:
            home_id: 家庭ID
            credential: 用户凭据

        Returns:
            设备列表
        """
        # 检查缓存
        cache_key = f"devices:{home_id}"
        cached = self._cache.get(cache_key, namespace=credential.user_id)
        if cached is not None:
            logger.info(f"从缓存获取设备列表: {home_id}")
            return [Device(**d) for d in cached]

        # 从API获取
        response = await self._http.post(
            "/home/device_list",
            {"home_id": home_id},
            credential,
        )

        devices_data = response.get("result", {}).get("device_info", [])
        devices = [self._parse_device(d, home_id) for d in devices_data]

        # 缓存结果
        self._cache.set(
            cache_key,
            [d.model_dump() for d in devices],
            ttl=300,
            namespace=credential.user_id,
        )

        logger.info(f"获取设备列表成功: {len(devices)} 个设备")
        return devices

    async def get_by_id(
        self, device_id: str, credential: Credential
    ) -> Optional[Device]:
        """异步获取单个设备

        Args:
            device_id: 设备ID
            credential: 用户凭据

        Returns:
            设备对象，不存在返回None
        """
        # 遍历所有家庭查找设备
        uri = "/v2/homeroom/gethome_merged"
        data = {
            "fg": True,
            "fetch_share": True,
            "fetch_share_dev": True,
            "fetch_cariot": True,
            "limit": 300,
            "app_ver": 7,
            "plat_form": 0,
        }
        response = await self._client.post(uri, data, credential)
        homes = response.get("result", {}).get("homelist", [])
        # 遍历每个家庭查找设备
        for home in homes:
            home_id = str(home.get("id", ""))
            devices = await self.get_all(home_id, credential)
            for device in devices:
                if device.did == device_id:
                    return device
        return None

    async def get_properties(
        self, device_id: str, siid: int, piid: int, credential: Credential
    ) -> Any:
        """异步获取设备属性

        Args:
            device_id: 设备ID
            siid: 服务ID
            piid: 属性ID
            credential: 用户凭据

        Returns:
            属性值
        """
        response = await self._http.post(
            "/miotspec/prop/get",
            {"did": device_id, "siid": siid, "piid": piid},
            credential,
        )
        return response.get("result", {}).get("value")

    async def set_property(
        self, device_id: str, siid: int, piid: int, value: Any, credential: Credential
    ) -> bool:
        """异步设置设备属性

        Args:
            device_id: 设备ID
            siid: 服务ID
            piid: 属性ID
            value: 属性值
            credential: 用户凭据

        Returns:
            是否成功
        """
        response = await self._http.post(
            "/miotspec/prop/set",
            {"did": device_id, "siid": siid, "piid": piid, "value": value},
            credential,
        )

        # 失效相关缓存（限制到当前用户范围）
        self._cache.invalidate_pattern(f"{credential.user_id}:devices:")

        return response.get("code") == 0

    async def call_action(
        self, device_id: str, siid: int, aiid: int, params: List[Any], credential: Credential
    ) -> bool:
        """异步调用设备操作

        Args:
            device_id: 设备ID
            siid: 服务ID
            aiid: 操作ID
            params: 参数列表
            credential: 用户凭据

        Returns:
            是否成功
        """
        response = await self._http.post(
            "/miotspec/action",
            {"did": device_id, "siid": siid, "aiid": aiid, "in": params},
            credential,
        )
        return response.get("code") == 0

    async def batch_get_properties(
        self, requests: List[Dict[str, Any]], credential: Credential
    ) -> List[Any]:
        """异步批量获取属性

        Args:
            requests: 请求列表
            credential: 用户凭据

        Returns:
            结果列表
        """
        response = await self._http.post(
            "/miotspec/prop/get_batch", 
            {"params": requests}, 
            credential
        )
        return response.get("result", [])

    async def batch_set_properties(
        self, requests: List[Dict[str, Any]], credential: Credential
    ) -> List[bool]:
        """异步批量设置属性

        Args:
            requests: 请求列表
            credential: 用户凭据

        Returns:
            结果列表
        """
        response = await self._http.post(
            "/miotspec/prop/set_batch", 
            {"params": requests}, 
            credential
        )

        # 失效相关缓存（限制到当前用户范围）
        self._cache.invalidate_pattern(f"{credential.user_id}:devices:")

        results = response.get("result", [])
        return [r.get("code") == 0 for r in results]

    def _parse_device(self, data: Dict[str, Any], home_id: str) -> Device:
        """解析设备数据

        Args:
            data: 原始设备数据
            home_id: 家庭ID

        Returns:
            Device对象
        """
        # 解析设备状态
        status_value = data.get("isOnline")
        if isinstance(status_value, bool):
            status = DeviceStatus.ONLINE if status_value else DeviceStatus.OFFLINE
        elif isinstance(status_value, int):
            status = DeviceStatus.ONLINE if status_value == 1 else DeviceStatus.OFFLINE
        else:
            status = DeviceStatus.UNKNOWN

        return Device(
            did=data.get("did", ""),
            name=data.get("name", ""),
            model=data.get("model", ""),
            home_id=home_id,
            room_id=data.get("roomid"),
            status=status,
            parent_id=data.get("parent_id"),
            parent_model=data.get("parent_model"),
        )
