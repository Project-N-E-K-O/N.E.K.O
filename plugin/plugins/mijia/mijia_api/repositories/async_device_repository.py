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
        """异步获取设备列表（带分页，与同步版保持一致）

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

        # 获取家庭所有者的 UID（分页端点需要）
        home_owner = await self._get_home_owner(home_id, credential)

        # 分页获取所有设备（与同步版使用相同端点）
        uri = "/home/home_device_list"
        start_did = ""
        has_more = True
        all_devices = []

        while has_more:
            data = {
                "home_owner": home_owner,
                "home_id": int(home_id),
                "limit": 200,
                "start_did": start_did,
                "get_split_device": True,
                "support_smart_home": True,
                "get_cariot_device": True,
                "get_third_device": True,
            }
            response = await self._http.post(uri, data, credential)
            result = response.get("result", {})

            device_info = result.get("device_info", [])
            if device_info:
                all_devices.extend(device_info)
                start_did = result.get("max_did", "")
                has_more = result.get("has_more", False) and start_did != ""
            else:
                has_more = False

        devices = [self._parse_device(d, home_id) for d in all_devices]

        # 缓存结果
        self._cache.set(
            cache_key,
            [d.model_dump() for d in devices],
            ttl=300,
            namespace=credential.user_id,
        )

        logger.info(f"获取设备列表成功: {len(devices)} 个设备")
        return devices

    async def _get_home_owner(self, home_id: str, credential: Credential) -> int:
        """获取家庭所有者的 UID

        Args:
            home_id: 家庭ID
            credential: 用户凭据

        Returns:
            家庭所有者的 UID

        Raises:
            ValueError: 找不到对应的家庭
        """
        cache_key = "homes"
        cached = self._cache.get(cache_key, namespace=credential.user_id)

        # 使用 is not None 显式检查，避免空列表被误判为缓存未命中
        if cached is not None:
            homes = cached
        else:
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
            response = await self._http.post(uri, data, credential)
            homes = response.get("result", {}).get("homelist", [])
            self._cache.set(cache_key, homes, ttl=3600, namespace=credential.user_id)

        for home in homes:
            if str(home.get("id", "")) == str(home_id):
                return int(home.get("uid", 0))

        raise ValueError(f"未找到 home_id={home_id} 的家庭信息")

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
        response = await self._http.post(uri, data, credential)
        homes = response.get("result", {}).get("homelist", [])
        # 遍历每个家庭查找设备
        for home in homes:
            home_id = str(home.get("id", ""))
            devices = await self.get_all(home_id, credential)
            for device in devices:
                if device.did == device_id:
                    return device
        return None

    async def get_property(
        self, device_id: str, siid: int, piid: int, credential: Credential
    ) -> Any:
        """异步获取单个设备属性值

        Args:
            device_id: 设备ID
            siid: 服务ID
            piid: 属性ID
            credential: 用户凭据

        Returns:
            属性值
        """
        params = [{"did": device_id, "siid": siid, "piid": piid}]
        response = await self._http.post(
            "/miotspec/prop/get",
            {"params": params, "datasource": 1},
            credential,
        )
        result = response.get("result", [])
        if result and len(result) > 0:
            return result[0].get("value")
        return None

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
        params = [{"did": device_id, "siid": siid, "piid": piid, "value": value}]
        response = await self._http.post(
            "/miotspec/prop/set",
            {"params": params},
            credential,
        )

        # 失效相关缓存（限制到当前用户范围）
        self._cache.invalidate_pattern(f"{credential.user_id}:devices:")

        result = response.get("result", [])
        if result and len(result) > 0:
            return result[0].get("code") == 0
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
            "/miotspec/prop/get",
            {"params": requests, "datasource": 1},
            credential,
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
            "/miotspec/prop/set",
            {"params": requests},
            credential
        )

        # 失效所有相关设备的缓存（与同步版保持一致，逐设备精确失效）
        device_ids = {req.get("did") for req in requests if req.get("did")}
        for device_id in device_ids:
            self._cache.invalidate_pattern(f"{credential.user_id}:device:{device_id}")

        return response.get("result", [])

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
