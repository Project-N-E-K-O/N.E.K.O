"""仓储接口定义

定义数据访问的抽象接口，便于测试和替换实现。
"""

from abc import ABC, abstractmethod
from typing import Any, Coroutine, Dict, List, Optional

from pydantic import BaseModel, Field

from ..domain.models import (
    Credential,
    Device,
    DeviceAction,
    DeviceProperty,
    Home,
    Scene,
)


class DeviceSpec(BaseModel):
    """设备规格"""

    model: str = Field(description="设备型号")
    name: str = Field(description="设备名称")
    properties: List[DeviceProperty] = Field(default_factory=list, description="属性列表")
    actions: List[DeviceAction] = Field(default_factory=list, description="操作列表")


class IHomeRepository(ABC):
    """家庭仓储接口"""

    @abstractmethod
    def get_all(self, credential: Credential) -> List[Home]:
        """获取所有家庭"""
        pass

    @abstractmethod
    def get_by_id(self, home_id: str, credential: Credential) -> Optional[Home]:
        """根据ID获取家庭"""
        pass


class IDeviceRepository(ABC):
    """设备仓储接口"""

    @abstractmethod
    def get_all(self, home_id: str, credential: Credential) -> List[Device]:
        """获取家庭下所有设备"""
        pass

    @abstractmethod
    def get_by_id(self, device_id: str, credential: Credential) -> Optional[Device]:
        """根据ID获取设备"""
        pass

    @abstractmethod
    def get_properties(self, device_id: str, credential: Credential) -> List[DeviceProperty]:
        """获取设备属性"""
        pass

    @abstractmethod
    def set_property(
        self, device_id: str, siid: int, piid: int, value: Any, credential: Credential
    ) -> bool:
        """设置设备属性"""
        pass

    @abstractmethod
    def call_action(
        self, device_id: str, siid: int, aiid: int, params: Dict[str, Any], credential: Credential
    ) -> Any:
        """调用设备操作"""
        pass

    @abstractmethod
    def batch_get_properties(
        self, requests: List[Dict[str, Any]], credential: Credential
    ) -> List[Dict[str, Any]]:
        """批量获取属性"""
        pass

    @abstractmethod
    def batch_set_properties(
        self, requests: List[Dict[str, Any]], credential: Credential
    ) -> List[Dict[str, Any]]:
        """批量设置属性"""
        pass


class ISceneRepository(ABC):
    """智能仓储接口"""

    @abstractmethod
    def get_all(self, home_id: str, credential: Credential) -> List[Scene]:
        """获取家庭下所有智能"""
        pass

    @abstractmethod
    def execute(self, scene_id: str, home_id: str, credential: Credential) -> bool:
        """执行智能"""
        pass


class IDeviceSpecRepository(ABC):
    """设备规格仓储接口"""

    @abstractmethod
    def get_spec(self, model: str) -> Optional[DeviceSpec]:
        """获取设备规格"""
        pass

    @abstractmethod
    def cache_spec(self, model: str, spec: DeviceSpec) -> None:
        """缓存设备规格"""
        pass


class IAsyncDeviceRepository(ABC):
    """异步设备仓储接口"""

    @abstractmethod
    async def get_all(self, home_id: str, credential: Credential) -> List[Device]:
        """获取家庭下所有设备"""
        pass

    @abstractmethod
    async def get_by_id(self, device_id: str, home_id: str, credential: Credential) -> Optional[Device]:
        """根据ID获取设备"""
        pass

    @abstractmethod
    async def get_properties(
        self, device_id: str, siid: int, piid: int, credential: Credential
    ) -> Any:
        """获取设备属性"""
        pass

    @abstractmethod
    async def set_property(
        self, device_id: str, siid: int, piid: int, value: Any, credential: Credential
    ) -> bool:
        """设置设备属性"""
        pass

    @abstractmethod
    async def call_action(
        self, device_id: str, siid: int, aiid: int, params: List[Any], credential: Credential
    ) -> Any:
        """调用设备操作"""
        pass

    @abstractmethod
    async def batch_get_properties(
        self, requests: List[Dict[str, Any]], credential: Credential
    ) -> List[Any]:
        """批量获取属性"""
        pass

    @abstractmethod
    async def batch_set_properties(
        self, requests: List[Dict[str, Any]], credential: Credential
    ) -> List[bool]:
        """批量设置属性"""
        pass
