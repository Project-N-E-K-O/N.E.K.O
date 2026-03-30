"""设备服务

封装设备相关的业务逻辑。
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
from ..repositories.interfaces import IDeviceRepository, IDeviceSpecRepository

logger = logging.getLogger(__name__)


class DeviceService:
    """设备服务

    封装设备相关的业务逻辑，包括设备查询、属性控制、操作调用等。
    """

    def __init__(
        self,
        device_repo: IDeviceRepository,
        spec_repo: IDeviceSpecRepository,
        cache_manager: CacheManager,
    ) -> None:
        """初始化设备服务

        Args:
            device_repo: 设备仓储接口
            spec_repo: 设备规格仓储接口
            cache_manager: 缓存管理器
        """
        self._device_repo = device_repo
        self._spec_repo = spec_repo
        self._cache = cache_manager

    def get_devices(self, home_id: str, credential: Credential) -> List[Device]:
        """获取设备列表

        Args:
            home_id: 家庭ID
            credential: 用户凭据

        Returns:
            设备列表
        """
        return self._device_repo.get_all(home_id, credential)

    def get_device_by_id(self, device_id: str, credential: Credential) -> Optional[Device]:
        """获取单个设备

        Args:
            device_id: 设备ID
            credential: 用户凭据

        Returns:
            设备对象，不存在返回None
        """
        return self._device_repo.get_by_id(device_id, credential)

    def get_device_properties(self, device_id: str, credential: Credential) -> List[DeviceProperty]:
        """获取设备属性

        Args:
            device_id: 设备ID
            credential: 用户凭据

        Returns:
            设备属性列表
        """
        return self._device_repo.get_properties(device_id, credential)

    def set_device_property(
        self, device_id: str, siid: int, piid: int, value: Any, credential: Credential
    ) -> bool:
        """设置设备属性

        包含完整的验证流程：
        1. 检查设备是否存在
        2. 获取设备规格
        3. 验证属性是否可写
        4. 验证属性值是否有效

        Args:
            device_id: 设备ID
            siid: 服务ID
            piid: 属性ID
            value: 属性值
            credential: 用户凭据

        Returns:
            设置成功返回True，失败返回False

        Raises:
            DeviceNotFoundError: 设备不存在
            PropertyReadOnlyError: 属性只读
            ValidationError: 属性值无效
        """
        # 1. 检查设备是否存在
        device = self.get_device_by_id(device_id, credential)
        if not device:
            raise DeviceNotFoundError(f"设备不存在: {device_id}")

        # 2. 获取设备规格进行验证（可选，如果获取失败不影响控制）
        try:
            spec = self._spec_repo.get_spec(device.model)
            if spec:
                # 查找属性定义
                prop = next((p for p in spec.properties if p.siid == siid and p.piid == piid), None)
                if prop:
                    # 3. 检查属性是否可写
                    if not prop.is_writable():
                        raise PropertyReadOnlyError(f"属性只读: {prop.name}")

                    # 4. 验证属性值
                    if not prop.validate_value(value):
                        raise ValidationError(f"属性值无效: {value}")
        except (PropertyReadOnlyError, ValidationError):
            # 验证错误需要抛出
            raise
        except Exception as e:
            # 规格获取失败不影响控制，但记录日志便于调试
            logger.debug(f"设备规格获取失败，跳过验证: {e}")
            pass

        # 5. 调用仓储层设置属性
        return self._device_repo.set_property(device_id, siid, piid, value, credential)

    def call_device_action(
        self,
        device_id: str,
        siid: int,
        aiid: int,
        params: List[Any],
        credential: Credential,
    ) -> Any:
        """调用设备操作

        Args:
            device_id: 设备ID
            siid: 服务ID
            aiid: 操作ID
            params: 操作参数列表
            credential: 用户凭据

        Returns:
            操作执行结果
        """
        return self._device_repo.call_action(device_id, siid, aiid, params, credential)

    def batch_control_devices(
        self, requests: List[Dict[str, Any]], credential: Credential
    ) -> List[Dict[str, Any]]:
        """批量控制设备

        自动分组处理，每组最多20个请求，避免单次请求过大。

        Args:
            requests: 批量请求列表，每个请求包含device_id/did、siid、piid、value等信息
            credential: 用户凭据

        Returns:
            批量操作结果列表
        """
        # 规范化请求：将 device_id 转换为 did
        normalized_requests = []
        for req in requests:
            normalized = dict(req)  # 复制一份避免修改原数据
            # 支持 device_id 或 did 作为设备ID字段
            if "device_id" in normalized and "did" not in normalized:
                normalized["did"] = normalized.pop("device_id")
            normalized_requests.append(normalized)

        # 分组处理，每组最多20个请求
        batch_size = 20
        results: List[Dict[str, Any]] = []

        for i in range(0, len(normalized_requests), batch_size):
            batch = normalized_requests[i : i + batch_size]
            batch_results = self._device_repo.batch_set_properties(batch, credential)
            results.extend(batch_results)

        return results

    def get_device_spec(self, model: str) -> Any:
        """获取设备规格

        Args:
            model: 设备型号

        Returns:
            设备规格对象
        """
        return self._spec_repo.get_spec(model)

    def batch_get_properties(
        self, requests: List[Dict[str, Any]], credential: Credential
    ) -> List[Dict[str, Any]]:
        """批量获取设备属性

        Args:
            requests: 请求列表，每个请求包含did、siid、piid
            credential: 用户凭据

        Returns:
            结果列表，每个结果包含code、siid、piid、value
        """
        return self._device_repo.batch_get_properties(requests, credential)
