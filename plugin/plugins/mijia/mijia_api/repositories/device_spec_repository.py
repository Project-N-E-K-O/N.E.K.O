"""设备规格仓储实现

从网络或缓存获取设备规格信息，并解析为标准化的设备规格模型。
"""

import json
import re
from typing import Dict, List, Optional

import httpx

from ..core.logging import get_logger
from ..domain.exceptions import MijiaAPIException, SpecNotFoundError
from ..domain.models import DeviceAction, DeviceProperty, PropertyAccess, PropertyType, ActionParameter
from ..infrastructure.cache_manager import CacheManager
from ..infrastructure.http_client import HttpClient
from .interfaces import DeviceSpec, IDeviceSpecRepository

logger = get_logger(__name__)


class DeviceSpecRepositoryImpl(IDeviceSpecRepository):
    """设备规格仓储实现

    负责从米家规格网站获取设备规格信息，解析并缓存到本地。
    设备规格信息包括：
    - 设备名称和型号
    - 属性列表（SIID、PIID、名称、类型、访问权限、值范围等）
    - 操作列表（SIID、AIID、名称、参数列表）

    缓存策略：
    - 设备规格信息永久缓存到文件（L3缓存）
    - 首次获取从网络加载，后续从缓存加载
    """

    def __init__(self, http_client: HttpClient, cache_manager: CacheManager):
        """初始化设备规格仓储

        Args:
            http_client: HTTP客户端
            cache_manager: 缓存管理器
        """
        self._http = http_client
        self._cache = cache_manager

    def get_spec(self, model: str) -> Optional[DeviceSpec]:
        """获取设备规格

        首先从缓存查找，如果不存在则从网络获取并缓存。

        Args:
            model: 设备型号（如 "xiaomi.light.ceiling1"）

        Returns:
            设备规格对象，获取失败返回None

        Raises:
            MijiaAPIException: 网络请求失败或解析失败
        """
        # 检查缓存（使用永久缓存，TTL设置为很大的值）
        cache_key = f"device_spec:{model}"
        cached = self._cache.get(cache_key, namespace="specs")

        if cached:
            logger.info(f"从缓存加载设备规格: {model}")
            try:
                return DeviceSpec.model_validate(cached)
            except Exception as e:
                logger.warning(f"缓存的设备规格解析失败: {e}", extra={"model": model})
                # 缓存数据损坏，清除缓存并重新获取
                self._cache.invalidate(cache_key, namespace="specs")

        # 从网络获取
        logger.info(f"从网络获取设备规格: {model}")
        try:
            spec = self._fetch_spec_from_network(model)
            if spec:
                # 缓存到文件（永久缓存，TTL设置为1年）
                self.cache_spec(model, spec)
            return spec
        except Exception as e:
            logger.error(f"获取设备规格失败: {e}", extra={"model": model}, exc_info=e)
            raise MijiaAPIException(f"获取设备规格失败: {model}") from e

    def cache_spec(self, model: str, spec: DeviceSpec) -> None:
        """缓存设备规格到文件

        Args:
            model: 设备型号
            spec: 设备规格对象
        """
        cache_key = f"device_spec:{model}"
        # 使用很长的TTL（1年）实现永久缓存
        self._cache.set(cache_key, spec.model_dump(), ttl=365 * 24 * 3600, namespace="specs")
        logger.info(f"设备规格已缓存: {model}")

    def _get_instances_index(self) -> Dict[str, str]:
        """获取设备型号到 type 的映射索引，带缓存

        Returns:
            {model: type} 的字典
        """
        cache_key = "miot_spec:instances_index"

        # 尝试从缓存获取
        cached = self._cache.get(cache_key, namespace="specs")
        if cached:
            return cached

        # 从网络获取
        try:
            instances_url = "https://miot-spec.org/miot-spec-v2/instances?status=released"
            headers = {"User-Agent": "mijiaAPI_V2/2.0.0"}

            response = httpx.get(instances_url, headers=headers, timeout=30)
            response.raise_for_status()
            instances_data = response.json()

            # 构建索引
            index = {}
            for instance in instances_data.get("instances", []):
                model = instance.get("model")
                device_type = instance.get("type")
                if model and device_type:
                    index[model] = device_type

            # 缓存索引（7天）
            self._cache.set(cache_key, index, ttl=7 * 24 * 3600, namespace="specs")
            logger.info(f"instances 索引已缓存，共 {len(index)} 个设备")

            return index

        except Exception as e:
            logger.error(f"获取 instances 索引失败: {e}")
            return {}

    def _fetch_spec_from_network(self, model: str) -> Optional[DeviceSpec]:
        """从网络获取设备规格

        Args:
            model: 设备型号

        Returns:
            设备规格对象，获取失败返回None

        Raises:
            MijiaAPIException: 网络请求失败或解析失败
        """
        try:
            # 步骤1: 从缓存的索引中查找设备的type
            index = self._get_instances_index()
            device_type = index.get(model)

            if not device_type:
                raise SpecNotFoundError(f"未找到设备型号 {model} 的规格定义")

            # 步骤2: 使用type获取完整规格
            headers = {"User-Agent": "mijiaAPI_V2/2.0.0"}
            spec_url = f"https://miot-spec.org/miot-spec-v2/instance?type={device_type}"
            response = httpx.get(spec_url, headers=headers, timeout=30)
            response.raise_for_status()
            spec_data = response.json()

            # 解析规格数据（使用标准miot-spec格式）
            return self._parse_spec_standard(model, spec_data)

        except httpx.HTTPError as e:
            logger.error(f"获取设备规格网络错误: {e}", extra={"model": model})
            raise MijiaAPIException(f"获取设备规格网络错误: {str(e)}") from e
        except Exception as e:
            logger.error(f"解析设备规格失败: {e}", extra={"model": model})
            raise MijiaAPIException(f"解析设备规格失败: {str(e)}") from e
    
    def _parse_spec_standard(self, model: str, spec_data: dict) -> DeviceSpec:
        """解析设备规格数据（标准miot-spec格式）

        Args:
            model: 设备型号
            spec_data: 从miot-spec.org获取的标准格式规格数据

        Returns:
            设备规格对象

        Raises:
            MijiaAPIException: 解析失败
        """
        try:
            # 提取设备名称
            device_name = spec_data.get("description", model)

            # 解析属性列表
            properties = []
            actions = []

            # 遍历服务列表
            services = spec_data.get("services", [])
            for service in services:
                siid = service.get("iid")
                if not siid:
                    continue

                # 解析属性
                for prop in service.get("properties", []):
                    device_property = self._parse_property(siid, prop)
                    if device_property:
                        properties.append(device_property)

                # 解析操作
                for action in service.get("actions", []):
                    device_action = self._parse_action(siid, action)
                    if device_action:
                        actions.append(device_action)

            return DeviceSpec(model=model, name=device_name, properties=properties, actions=actions)

        except Exception as e:
            logger.error(f"解析设备规格数据失败: {e}", extra={"model": model})
            raise MijiaAPIException(f"解析设备规格数据失败: {str(e)}") from e

    def _parse_spec(self, model: str, spec_data: dict) -> DeviceSpec:
        """解析设备规格数据

        Args:
            model: 设备型号
            spec_data: 从API获取的原始规格数据（home.miot-spec.com格式）

        Returns:
            设备规格对象

        Raises:
            MijiaAPIException: 解析失败
        """
        try:
            # 提取设备名称
            props = spec_data.get("props", {})
            if props.get("product"):
                device_name = props["product"].get("name", model)
            else:
                device_name = props.get("spec", {}).get("name", model)

            # 解析属性列表
            properties = []
            actions = []

            # 遍历服务列表（格式：{"1": {...}, "2": {...}}）
            services = props.get("spec", {}).get("services", {})
            for siid_str, service in services.items():
                siid = int(siid_str)

                # 解析属性
                service_props = service.get("properties", {})
                for piid_str, prop in service_props.items():
                    device_property = self._parse_property_v2(siid, int(piid_str), prop)
                    if device_property:
                        properties.append(device_property)

                # 解析操作
                service_actions = service.get("actions", {})
                for aiid_str, action in service_actions.items():
                    device_action = self._parse_action_v2(siid, int(aiid_str), action)
                    if device_action:
                        actions.append(device_action)

            return DeviceSpec(model=model, name=device_name, properties=properties, actions=actions)

        except Exception as e:
            logger.error(f"解析设备规格数据失败: {e}", extra={"model": model})
            raise MijiaAPIException(f"解析设备规格数据失败: {str(e)}") from e
    
    def _parse_property_v2(self, siid: int, piid: int, prop_data: dict) -> Optional[DeviceProperty]:
        """解析设备属性（home.miot-spec.com格式）

        Args:
            siid: 服务ID
            piid: 属性ID
            prop_data: 属性数据

        Returns:
            设备属性对象，解析失败返回None
        """
        try:
            # 属性名称
            name = prop_data.get("description", prop_data.get("name", f"property_{piid}"))

            # 属性类型
            format_str = prop_data.get("format", "string")
            if format_str.startswith("int"):
                prop_type = PropertyType.INT
            elif format_str.startswith("uint"):
                prop_type = PropertyType.UINT
            elif format_str == "bool":
                prop_type = PropertyType.BOOL
            elif format_str == "float":
                prop_type = PropertyType.FLOAT
            else:
                prop_type = PropertyType.STRING

            # 访问权限
            access_list = prop_data.get("access", [])
            readable = "read" in access_list
            writable = "write" in access_list

            # 值范围
            value_range = prop_data.get("value-range")
            if value_range:
                value_range = [value_range.get("min"), value_range.get("max")]

            # 可选值列表
            value_list = prop_data.get("value-list")

            # 单位
            unit = prop_data.get("unit")

            # 确定访问权限
            if readable and writable:
                access = PropertyAccess.READ_WRITE
            elif readable:
                access = PropertyAccess.READ_ONLY
            elif writable:
                access = PropertyAccess.WRITE_ONLY
            else:
                # 既没有 read 也没有 write，默认只读（避免误判为可写）
                access = PropertyAccess.READ_ONLY

            return DeviceProperty(
                siid=siid,
                piid=piid,
                name=name,
                type=prop_type,
                access=access,
                value_range=value_range,
                value_list=value_list,
                unit=unit,
            )

        except Exception as e:
            logger.warning(f"解析属性失败: {e}", extra={"siid": siid, "piid": piid})
            return None
    
    def _parse_action_v2(self, siid: int, aiid: int, action_data: dict) -> Optional[DeviceAction]:
        """解析设备操作（home.miot-spec.com格式）

        Args:
            siid: 服务ID
            aiid: 操作ID
            action_data: 操作数据

        Returns:
            设备操作对象，解析失败返回None
        """
        try:
            # 操作名称
            name = action_data.get("description", action_data.get("name", f"action_{aiid}"))

            # 输入参数
            parameters = []
            for param in action_data.get("in", []):
                param_name = param.get("description", f"param_{param.get('piid', 0)}")
                param_type = self._parse_property_type(param.get("type", "string"))
                parameters.append(ActionParameter(
                    name=param_name,
                    type=param_type,
                    required=True,
                ))

            return DeviceAction(
                siid=siid,
                aiid=aiid,
                name=name,
                parameters=parameters,
            )

        except Exception as e:
            logger.warning(f"解析操作失败: {e}", extra={"siid": siid, "aiid": aiid})
            return None

    def _parse_property(self, siid: int, prop_data: dict) -> Optional[DeviceProperty]:
        """解析设备属性（标准miot-spec格式）

        Args:
            siid: 服务ID
            prop_data: 属性数据

        Returns:
            设备属性对象，解析失败返回None
        """
        try:
            piid = prop_data.get("iid")
            if not piid:
                return None

            # 属性名称
            name = prop_data.get("description", f"property_{piid}")

            # 属性类型
            prop_type = self._parse_property_type(prop_data.get("format", "string"))

            # 访问权限
            access = self._parse_property_access(prop_data.get("access", []))

            # 值范围
            value_range = None
            if "value-range" in prop_data:
                range_data = prop_data["value-range"]
                # 处理两种格式：字典格式和列表格式
                if isinstance(range_data, dict):
                    # 字典格式: {"min": 0, "max": 100, "step": 1}
                    value_range = [range_data.get("min"), range_data.get("max")]
                    # 如果有步长，也添加进去
                    if "step" in range_data:
                        value_range.append(range_data.get("step"))
                elif isinstance(range_data, list):
                    # 列表格式: [min, max, step]
                    value_range = range_data

            # 枚举值列表
            value_list = None
            if "value-list" in prop_data:
                value_list = [item.get("value") for item in prop_data["value-list"]]

            # 单位
            unit = prop_data.get("unit")

            return DeviceProperty(
                siid=siid,
                piid=piid,
                name=name,
                type=prop_type,
                access=access,
                value_range=value_range,
                value_list=value_list,
                unit=unit,
            )

        except Exception as e:
            logger.warning(f"解析属性失败: {e}", extra={"siid": siid, "prop_data": prop_data})
            return None

    def _parse_property_type(self, format_str: str) -> PropertyType:
        """解析属性类型

        Args:
            format_str: 格式字符串（如 "bool", "int32", "uint8", "float", "string"）

        Returns:
            属性类型枚举
        """
        format_lower = format_str.lower()

        if format_lower == "bool":
            return PropertyType.BOOL
        elif "int" in format_lower and "uint" not in format_lower:
            return PropertyType.INT
        elif "uint" in format_lower:
            return PropertyType.UINT
        elif "float" in format_lower or "double" in format_lower:
            return PropertyType.FLOAT
        else:
            return PropertyType.STRING

    def _parse_property_access(self, access_list: list) -> PropertyAccess:
        """解析属性访问权限

        Args:
            access_list: 访问权限列表（如 ["read"], ["write"], ["read", "write"]）

        Returns:
            属性访问权限枚举
        """
        has_read = "read" in access_list
        has_write = "write" in access_list

        if has_read and has_write:
            return PropertyAccess.READ_WRITE
        elif has_read:
            return PropertyAccess.READ_ONLY
        elif has_write:
            return PropertyAccess.WRITE_ONLY
        else:
            # 默认为只读
            return PropertyAccess.READ_ONLY

    def _parse_action(self, siid: int, action_data: dict) -> Optional[DeviceAction]:
        """解析设备操作

        Args:
            siid: 服务ID
            action_data: 操作数据

        Returns:
            设备操作对象，解析失败返回None
        """
        try:
            aiid = action_data.get("iid")
            if not aiid:
                return None

            # 操作名称
            name = action_data.get("description", f"action_{aiid}")

            # 参数列表（暂时不解析参数，后续可以扩展）
            parameters: list[ActionParameter] = []

            return DeviceAction(siid=siid, aiid=aiid, name=name, parameters=parameters)

        except Exception as e:
            logger.warning(f"解析操作失败: {e}", extra={"siid": siid, "action_data": action_data})
            return None
