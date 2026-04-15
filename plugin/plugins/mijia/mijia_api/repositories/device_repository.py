"""设备仓储实现

基于HTTP的设备仓储实现，支持缓存。
"""

from typing import Any, Dict, List, Optional

from ..domain.models import Credential, Device, DeviceProperty, DeviceStatus
from ..infrastructure.cache_manager import CacheManager
from ..infrastructure.http_client import HttpClient
from .interfaces import IDeviceRepository


class DeviceRepositoryImpl(IDeviceRepository):
    """设备仓储实现

    基于HTTP API的设备仓储实现，集成缓存管理。
    """

    def __init__(self, http_client: HttpClient, cache_manager: CacheManager):
        """初始化设备仓储

        Args:
            http_client: HTTP客户端
            cache_manager: 缓存管理器
        """
        self._http = http_client
        self._cache = cache_manager

    def get_all(self, home_id: str, credential: Credential) -> List[Device]:
        """获取家庭下所有设备

        Args:
            home_id: 家庭ID
            credential: 用户凭据

        Returns:
            设备列表
        """
        # 检查缓存
        cache_key = f"devices:{home_id}"
        cached = self._cache.get(cache_key, namespace=credential.user_id)
        if cached:
            return [Device.model_validate(d) for d in cached]

        # 从API获取（使用旧项目的端点和参数）
        # 首先需要获取home_owner（家庭所有者的UID）
        home_owner = self._get_home_owner(home_id, credential)
        
        # 分页获取所有设备
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
            
            response = self._http.post(uri, json=data, credential=credential)
            result = response.get("result", {})
            
            # 获取设备列表
            device_info = result.get("device_info", [])
            if device_info:
                all_devices.extend(device_info)
                start_did = result.get("max_did", "")
                has_more = result.get("has_more", False) and start_did != ""
            else:
                has_more = False

        # 解析设备列表
        devices = []
        for device_data in all_devices:
            # 映射API字段到领域模型
            device = Device(
                did=device_data.get("did", ""),
                name=device_data.get("name", ""),
                model=device_data.get("model", ""),
                home_id=home_id,
                room_id=device_data.get("room_id"),
                status=self._parse_device_status(device_data.get("isOnline")),
                parent_id=device_data.get("parent_id"),
                parent_model=device_data.get("parent_model"),
            )
            devices.append(device)

        # 缓存结果（TTL=300秒）
        self._cache.set(
            cache_key, [d.model_dump() for d in devices], ttl=300, namespace=credential.user_id
        )

        return devices
    
    def _get_home_owner(self, home_id: str, credential: Credential) -> int:
        """获取家庭所有者的UID
        
        Args:
            home_id: 家庭ID
            credential: 用户凭据
            
        Returns:
            家庭所有者的UID
            
        Raises:
            ValueError: 如果找不到对应的家庭
        """
        # 从缓存或API获取家庭列表
        cache_key = "homes"
        cached = self._cache.get(cache_key, namespace=credential.user_id)
        
        if cached:
            homes = cached
        else:
            # 需要调用家庭列表API
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
            response = self._http.post(uri, json=data, credential=credential)
            homes = response.get("result", {}).get("homelist", [])
            # 缓存家庭列表（使用较长的TTL，因为家庭信息变化较少）
            self._cache.set(
                cache_key, homes, ttl=3600, namespace=credential.user_id
            )
        
        # 查找对应的家庭
        for home in homes:
            if str(home.get("id", "")) == str(home_id):
                return int(home.get("uid", 0))
        
        raise ValueError(f"未找到 home_id={home_id} 的家庭信息")

    def get_by_id(self, device_id: str, credential: Credential) -> Optional[Device]:
        """根据ID获取设备

        Args:
            device_id: 设备ID
            credential: 用户凭据

        Returns:
            设备对象，不存在返回None
        """
        # 检查缓存
        cache_key = f"device:{device_id}"
        cached = self._cache.get(cache_key, namespace=credential.user_id)
        if cached:
            return Device.model_validate(cached)

        # 需要遍历所有家庭查找设备
        # 首先获取家庭列表
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
        response = self._http.post(uri, json=data, credential=credential)
        homes = response.get("result", {}).get("homelist", [])
        
        # 遍历每个家庭查找设备
        for home in homes:
            home_id = str(home.get("id", ""))
            devices = self.get_all(home_id, credential)
            for device in devices:
                if device.did == device_id:
                    # 缓存设备信息
                    self._cache.set(
                        cache_key, device.model_dump(), ttl=300, namespace=credential.user_id
                    )
                    return device
        
        return None

    def get_properties(self, device_id: str, credential: Credential) -> List[DeviceProperty]:
        """获取设备属性

        Args:
            device_id: 设备ID
            credential: 用户凭据

        Returns:
            设备属性列表
        """
        # 检查缓存
        cache_key = f"device:{device_id}:properties"
        cached = self._cache.get(cache_key, namespace=credential.user_id)
        if cached:
            return [DeviceProperty.model_validate(p) for p in cached]

        # 从API获取
        response = self._http.post(
            "/miotspec/prop/get", json={"did": device_id}, credential=credential
        )

        # 解析属性列表
        properties_data = response.get("result", [])
        properties = []
        for prop_data in properties_data:
            # 这里需要根据实际API响应格式解析
            # 暂时使用简化的映射
            prop = DeviceProperty(
                siid=prop_data.get("siid", 0),
                piid=prop_data.get("piid", 0),
                name=prop_data.get("name", ""),
                type=prop_data.get("type", "string"),
                access=prop_data.get("access", "read_write"),
                value=prop_data.get("value"),
            )
            properties.append(prop)

        # 缓存结果（TTL=30秒，设备状态变化较快）
        self._cache.set(
            cache_key,
            [p.model_dump() for p in properties],
            ttl=30,
            namespace=credential.user_id,
        )

        return properties

    def set_property(
        self, device_id: str, siid: int, piid: int, value: Any, credential: Credential
    ) -> bool:
        """设置设备属性

        Args:
            device_id: 设备ID
            siid: 服务ID
            piid: 属性ID
            value: 属性值
            credential: 用户凭据

        Returns:
            设置成功返回True，失败返回False
        """
        # 调用API设置属性（使用旧项目的参数格式）
        params = [{"did": device_id, "siid": siid, "piid": piid, "value": value}]
        response = self._http.post(
            "/miotspec/prop/set",
            json={"params": params},
            credential=credential,
        )

        # 检查结果（返回的是列表）
        result = response.get("result", [])
        if result and len(result) > 0:
            success = result[0].get("code") == 0
        else:
            success = False

        if success:
            # 失效相关缓存
            self._cache.invalidate_pattern(f"{credential.user_id}:device:{device_id}")

        return success

    def call_action(
        self, device_id: str, siid: int, aiid: int, params: List[Any], credential: Credential
    ) -> Any:
        """调用设备操作

        Args:
            device_id: 设备ID
            siid: 服务ID
            aiid: 操作ID
            params: 操作参数列表
            credential: 用户凭据

        Returns:
            操作结果
        """
        # 参数已经是列表格式
        param_values = params if params else []
        
        # 调用API执行操作（与异步版保持一致的请求格式）
        response = self._http.post(
            "/miotspec/action",
            json={"did": device_id, "siid": siid, "aiid": aiid, "in": param_values},
            credential=credential,
        )

        # 失效相关缓存（操作可能改变设备状态）
        self._cache.invalidate_pattern(f"{credential.user_id}:device:{device_id}")

        return response.get("result")

    def batch_get_properties(
        self, requests: List[Dict[str, Any]], credential: Credential
    ) -> List[Dict[str, Any]]:
        """批量获取属性

        Args:
            requests: 请求列表，每个请求包含did、siid、piid
            credential: 用户凭据

        Returns:
            结果列表
        """
        # 调用批量获取API（使用旧项目的参数格式）
        response = self._http.post(
            "/miotspec/prop/get", 
            json={"params": requests, "datasource": 1}, 
            credential=credential
        )

        result: List[Dict[str, Any]] = response.get("result", [])
        return result

    def batch_set_properties(
        self, requests: List[Dict[str, Any]], credential: Credential
    ) -> List[Dict[str, Any]]:
        """批量设置属性

        Args:
            requests: 请求列表，每个请求包含did、siid、piid、value
            credential: 用户凭据

        Returns:
            结果列表
        """
        # 调用批量设置API（使用旧项目的参数格式）
        response = self._http.post(
            "/miotspec/prop/set", 
            json={"params": requests}, 
            credential=credential
        )

        # 失效所有相关设备的缓存
        device_ids = {req.get("did") for req in requests if req.get("did")}
        for device_id in device_ids:
            self._cache.invalidate_pattern(f"{credential.user_id}:device:{device_id}")

        result: List[Dict[str, Any]] = response.get("result", [])
        return result

    def _parse_device_status(self, is_online: Any) -> DeviceStatus:
        """解析设备在线状态

        Args:
            is_online: API返回的在线状态（可能是bool、int或其他类型）

        Returns:
            DeviceStatus枚举值
        """
        if is_online is None:
            return DeviceStatus.UNKNOWN
        if isinstance(is_online, bool):
            return DeviceStatus.ONLINE if is_online else DeviceStatus.OFFLINE
        if isinstance(is_online, int):
            return DeviceStatus.ONLINE if is_online == 1 else DeviceStatus.OFFLINE
        return DeviceStatus.UNKNOWN
