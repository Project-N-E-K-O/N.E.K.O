"""异步设备服务

封装设备相关的异步业务逻辑。
"""

import logging
from typing import Any, Dict, List, Optional

from ..domain.exceptions import (
    DeviceNotFoundError,
    PropertyReadOnlyError,
    ValidationError,
)
from ..domain.models import Credential, Device, DeviceProperty
from ..infrastructure.cache_manager import CacheManager
from ..repositories.interfaces import IAsyncDeviceRepository, IDeviceSpecRepository

logger = logging.getLogger(__name__)


class AsyncDeviceService:
    """异步设备服务

    使用异步仓储提供非阻塞的设备操作。
    """

    def __init__(
        self,
        device_repo: IAsyncDeviceRepository,
        spec_repo: IDeviceSpecRepository,
        cache_manager: CacheManager,
    ) -> None:
        self._device_repo = device_repo
        self._spec_repo = spec_repo
        self._cache = cache_manager

    async def get_devices(self, home_id: str, credential: Credential) -> List[Device]:
        return await self._device_repo.get_all(home_id, credential)

    async def get_device_by_id(self, device_id: str, credential: Credential) -> Optional[Device]:
        return await self._device_repo.get_by_id(device_id, credential)

    async def set_device_property(
        self, device_id: str, siid: int, piid: int, value: Any, credential: Credential
    ) -> bool:
        device = await self.get_device_by_id(device_id, credential)
        if not device:
            raise DeviceNotFoundError(f"设备不存在: {device_id}")

        try:
            spec = await self._spec_repo.get_spec(device.model)
            if spec:
                prop = next((p for p in spec.properties if p.siid == siid and p.piid == piid), None)
                if prop:
                    if not prop.is_writable():
                        raise PropertyReadOnlyError(f"属性只读: {prop.name}")
                    if not prop.validate_value(value):
                        raise ValidationError(f"属性值无效: {value}")
        except (PropertyReadOnlyError, ValidationError):
            raise
        except Exception as e:
            logger.debug(f"设备规格获取失败，跳过验证: {e}")

        return await self._device_repo.set_property(device_id, siid, piid, value, credential)

    async def call_device_action(
        self, device_id: str, siid: int, aiid: int, params: List[Any], credential: Credential,
    ) -> Any:
        return await self._device_repo.call_action(device_id, siid, aiid, params, credential)

    async def batch_control_devices(
        self, requests: List[Dict[str, Any]], credential: Credential
    ) -> List[Dict[str, Any]]:
        normalized_requests = []
        for req in requests:
            if not req.get("did") and not req.get("device_id"):
                continue
            normalized = dict(req)
            if "device_id" in normalized and "did" not in normalized:
                normalized["did"] = normalized.pop("device_id")
            normalized_requests.append(normalized)

        batch_size = 20
        results: List[Dict[str, Any]] = []
        for i in range(0, len(normalized_requests), batch_size):
            batch = normalized_requests[i : i + batch_size]
            batch_results = await self._device_repo.batch_set_properties(batch, credential)
            results.extend(batch_results)

        return results

    async def get_device_spec(self, model: str) -> Any:
        return await self._spec_repo.get_spec(model)

    async def batch_get_properties(
        self, requests: List[Dict[str, Any]], credential: Credential
    ) -> List[Dict[str, Any]]:
        return await self._device_repo.batch_get_properties(requests, credential)
