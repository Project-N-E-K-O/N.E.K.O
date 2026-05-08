"""异步设备规格仓储实现

从网络或缓存获取设备规格信息，全异步实现。
"""

from typing import Dict, List, Optional

import httpx

from ..core.logging import get_logger
from ..domain.exceptions import MijiaAPIException, SpecNotFoundError
from ..domain.models import (
    ActionParameter,
    DeviceAction,
    DeviceProperty,
    PropertyAccess,
    PropertyType,
)
from ..infrastructure.cache_manager import CacheManager
from ..infrastructure.http_client import AsyncHttpClient
from .interfaces import DeviceSpec, IDeviceSpecRepository

logger = get_logger(__name__)


class AsyncDeviceSpecRepositoryImpl(IDeviceSpecRepository):
    """异步设备规格仓储实现

    使用 AsyncHttpClient 和 httpx.AsyncClient 提供异步的设备规格访问。
    缓存策略与同步版一致：规格信息永久缓存，首次从网络获取。
    """

    def __init__(self, http_client: AsyncHttpClient, cache_manager: CacheManager):
        self._http = http_client
        self._cache = cache_manager
        self._async_client = httpx.AsyncClient(timeout=30)

    async def get_spec(self, model: str) -> Optional[DeviceSpec]:
        """异步获取设备规格"""
        cache_key = f"device_spec:{model}"
        cached = self._cache.get(cache_key, namespace="specs")

        if cached:
            logger.info(f"从缓存加载设备规格: {model}")
            try:
                return DeviceSpec.model_validate(cached)
            except Exception as e:
                logger.warning(f"缓存的设备规格解析失败: {e}", extra={"model": model})
                self._cache.invalidate(cache_key, namespace="specs")

        logger.info(f"从网络获取设备规格: {model}")
        try:
            spec = await self._fetch_spec_from_network(model)
            if spec:
                self.cache_spec(model, spec)
            return spec
        except MijiaAPIException:
            raise
        except Exception as e:
            logger.error(f"获取设备规格失败: {e}", extra={"model": model}, exc_info=e)
            raise MijiaAPIException(f"获取设备规格失败: {model}") from e

    def cache_spec(self, model: str, spec: DeviceSpec) -> None:
        """缓存设备规格"""
        cache_key = f"device_spec:{model}"
        self._cache.set(cache_key, spec.model_dump(), ttl=365 * 24 * 3600, namespace="specs")
        logger.info(f"设备规格已缓存: {model}")

    async def _get_instances_index(self) -> Dict[str, str]:
        """异步获取设备型号到type的映射索引"""
        cache_key = "miot_spec:instances_index"
        cached = self._cache.get(cache_key, namespace="specs")
        if cached:
            return cached

        try:
            instances_url = "https://miot-spec.org/miot-spec-v2/instances?status=released"
            headers = {"User-Agent": "mijiaAPI_V2/2.0.0"}

            response = await self._async_client.get(instances_url, headers=headers)
            response.raise_for_status()
            instances_data = response.json()

            index = {}
            for instance in instances_data.get("instances", []):
                m = instance.get("model")
                device_type = instance.get("type")
                if m and device_type:
                    index[m] = device_type

            self._cache.set(cache_key, index, ttl=7 * 24 * 3600, namespace="specs")
            logger.info(f"instances 索引已缓存，共 {len(index)} 个设备")

            return index

        except httpx.HTTPError as e:
            logger.error(f"获取 instances 索引网络失败: {e}")
            raise MijiaAPIException(f"规格索引服务不可用，请稍后重试: {str(e)}") from e
        except Exception as e:
            logger.error(f"获取 instances 索引失败: {e}")
            raise MijiaAPIException(f"规格索引解析失败: {str(e)}") from e

    async def _fetch_spec_from_network(self, model: str) -> Optional[DeviceSpec]:
        """异步从网络获取设备规格"""
        try:
            index = await self._get_instances_index()
            device_type = index.get(model)

            if not device_type:
                raise SpecNotFoundError(f"未找到设备型号 {model} 的规格定义")

            headers = {"User-Agent": "mijiaAPI_V2/2.0.0"}
            spec_url = f"https://miot-spec.org/miot-spec-v2/instance?type={device_type}"
            response = await self._async_client.get(spec_url, headers=headers)
            response.raise_for_status()
            spec_data = response.json()

            return self._parse_spec_standard(model, spec_data)

        except (SpecNotFoundError, MijiaAPIException):
            raise
        except httpx.HTTPError as e:
            logger.error(f"获取设备规格网络错误: {e}", extra={"model": model})
            raise MijiaAPIException(f"获取设备规格网络错误: {str(e)}") from e
        except Exception as e:
            logger.error(f"解析设备规格失败: {e}", extra={"model": model})
            raise MijiaAPIException(f"解析设备规格失败: {str(e)}") from e

    # -- 以下解析方法与同步版完全一致 --

    def _parse_spec_standard(self, model: str, spec_data: dict) -> DeviceSpec:
        try:
            device_name = spec_data.get("description", model)
            properties: List[DeviceProperty] = []
            actions: List[DeviceAction] = []

            for service in spec_data.get("services", []):
                siid = service.get("iid")
                if not siid:
                    continue
                service_desc = service.get("description", "")

                service_properties: Dict[int, DeviceProperty] = {}
                for prop in service.get("properties", []):
                    device_property = self._parse_property(siid, prop, service_desc)
                    if device_property:
                        properties.append(device_property)
                        service_properties[device_property.piid] = device_property

                for action in service.get("actions", []):
                    device_action = self._parse_action(siid, action, service_properties)
                    if device_action:
                        actions.append(device_action)

            return DeviceSpec(model=model, name=device_name, properties=properties, actions=actions)

        except Exception as e:
            logger.error(f"解析设备规格数据失败: {e}", extra={"model": model})
            raise MijiaAPIException(f"解析设备规格数据失败: {str(e)}") from e

    def _parse_property(self, siid: int, prop_data: dict, service_desc: str = "") -> Optional[DeviceProperty]:
        try:
            piid = prop_data.get("iid")
            if not piid:
                return None

            name = prop_data.get("description", f"property_{piid}")
            prop_type = self._parse_property_type(prop_data.get("format", "string"))
            access = self._parse_property_access(prop_data.get("access", []))

            value_range = None
            if "value-range" in prop_data:
                range_data = prop_data["value-range"]
                if isinstance(range_data, dict):
                    value_range = [range_data.get("min"), range_data.get("max")]
                    if "step" in range_data:
                        value_range.append(range_data.get("step"))
                elif isinstance(range_data, list):
                    value_range = range_data

            value_list = None
            if "value-list" in prop_data:
                value_list = [item.get("value") for item in prop_data["value-list"]]

            unit = prop_data.get("unit")

            return DeviceProperty(
                siid=siid, piid=piid, name=name, type=prop_type, access=access,
                value_range=value_range, value_list=value_list, unit=unit,
                service_description=service_desc or None,
            )
        except Exception as e:
            logger.warning(f"解析属性失败: {e}", extra={"siid": siid, "prop_data": prop_data})
            return None

    def _parse_property_type(self, format_str: str) -> PropertyType:
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
        has_read = "read" in access_list
        has_write = "write" in access_list
        if has_read and has_write:
            return PropertyAccess.READ_WRITE
        elif has_read:
            return PropertyAccess.READ_ONLY
        elif has_write:
            return PropertyAccess.WRITE_ONLY
        else:
            return PropertyAccess.READ_ONLY

    def _parse_action(
        self, siid: int, action_data: dict,
        properties_map: Optional[Dict[int, DeviceProperty]] = None,
    ) -> Optional[DeviceAction]:
        try:
            aiid = action_data.get("iid")
            if not aiid:
                return None

            name = action_data.get("description", f"action_{aiid}")
            parameters: List[ActionParameter] = []
            for param_iid in action_data.get("in", []):
                if properties_map and param_iid in properties_map:
                    prop = properties_map[param_iid]
                    parameters.append(ActionParameter(
                        name=prop.name, type=prop.type, required=True,
                    ))
                else:
                    parameters.append(ActionParameter(
                        name=f"param_{param_iid}", type=PropertyType.STRING, required=True,
                    ))

            return DeviceAction(siid=siid, aiid=aiid, name=name, parameters=parameters)
        except Exception as e:
            logger.warning(f"解析操作失败: {e}", extra={"siid": siid, "action_data": action_data})
            return None

    async def close(self) -> None:
        """关闭内部 httpx.AsyncClient"""
        await self._async_client.aclose()
