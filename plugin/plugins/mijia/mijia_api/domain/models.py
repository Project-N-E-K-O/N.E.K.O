"""领域模型

定义核心业务实体，使用Pydantic进行数据验证。
"""

from datetime import datetime
from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class DeviceStatus(str, Enum):
    """设备状态枚举"""

    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class PropertyType(str, Enum):
    """属性类型枚举"""

    BOOL = "bool"
    INT = "int"
    UINT = "uint"
    FLOAT = "float"
    STRING = "string"


class PropertyAccess(str, Enum):
    """属性访问权限"""

    READ_ONLY = "read"
    WRITE_ONLY = "write"
    READ_WRITE = "read_write"


class Credential(BaseModel):
    """用户凭据对象

    包含用户的认证信息，可独立获取、存储和传递。
    """

    user_id: str = Field(description="用户ID")
    service_token: str = Field(description="服务Token")
    ssecurity: str = Field(description="安全密钥")
    pass_token: Optional[str] = Field(default="", description="通行Token，用于刷新凭据")
    c_user_id: str = Field(description="Cookie用户ID")
    device_id: str = Field(description="设备ID")
    user_agent: str = Field(description="User-Agent")
    expires_at: datetime = Field(description="过期时间")

    def is_expired(self) -> bool:
        """检查凭据是否过期"""
        return datetime.now() >= self.expires_at

    def is_valid(self) -> bool:
        """检查凭据是否有效"""
        return not self.is_expired() and bool(self.service_token)

    def expires_in(self) -> int:
        """返回剩余有效时间（秒）"""
        if self.is_expired():
            return 0
        return int((self.expires_at - datetime.now()).total_seconds())

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典"""
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Credential":
        """从字典反序列化"""
        return cls.model_validate(data)


class Home(BaseModel):
    """家庭实体"""

    model_config = ConfigDict(frozen=False)

    id: str = Field(description="家庭ID")
    name: str = Field(description="家庭名称")
    uid: str = Field(description="用户ID")
    rooms: List[dict[str, Any]] = Field(default_factory=list, description="房间列表")


class Device(BaseModel):
    """设备实体"""

    model_config = ConfigDict(frozen=False)

    did: str = Field(description="设备ID")
    name: str = Field(description="设备名称")
    model: str = Field(description="设备型号")
    home_id: str = Field(description="所属家庭ID")
    room_id: Optional[str] = Field(default=None, description="所属房间ID")
    status: DeviceStatus = Field(default=DeviceStatus.UNKNOWN, description="设备状态")
    parent_id: Optional[str] = Field(default=None, description="父设备ID（网关）")
    parent_model: Optional[str] = Field(default=None, description="父设备型号")

    def is_online(self) -> bool:
        """检查设备是否在线"""
        return self.status == DeviceStatus.ONLINE


class DeviceProperty(BaseModel):
    """设备属性"""

    siid: int = Field(description="服务ID")
    piid: int = Field(description="属性ID")
    name: str = Field(description="属性名称")
    type: PropertyType = Field(description="属性类型")
    access: PropertyAccess = Field(description="访问权限")
    value: Optional[Any] = Field(default=None, description="属性值")
    value_range: Optional[List[Any]] = Field(default=None, description="值范围")
    value_list: Optional[List[Any]] = Field(default=None, description="枚举值列表")

    def is_readable(self) -> bool:
        """是否可读"""
        return self.access in [PropertyAccess.READ_ONLY, PropertyAccess.READ_WRITE]

    def is_writable(self) -> bool:
        """是否可写"""
        return self.access in [PropertyAccess.WRITE_ONLY, PropertyAccess.READ_WRITE]

    def validate_value(self, value: Any) -> bool:
        """验证值是否有效"""
        # 类型检查
        if self.type == PropertyType.BOOL and not isinstance(value, bool):
            return False
        if self.type in [PropertyType.INT, PropertyType.UINT] and not isinstance(value, int):
            return False
        if self.type == PropertyType.FLOAT and not isinstance(value, (int, float)):
            return False
        if self.type == PropertyType.STRING and not isinstance(value, str):
            return False

        # 范围检查
        if self.value_range and len(self.value_range) == 2:
            if value < self.value_range[0] or value > self.value_range[1]:
                return False

        # 枚举检查
        if self.value_list and value not in self.value_list:
            return False

        return True


class ActionParameter(BaseModel):
    """操作参数"""

    name: str = Field(description="参数名称")
    type: PropertyType = Field(description="参数类型")
    required: bool = Field(default=True, description="是否必需")
    value_range: Optional[List[Any]] = Field(default=None, description="值范围")


class DeviceAction(BaseModel):
    """设备操作"""

    siid: int = Field(description="服务ID")
    aiid: int = Field(description="操作ID")
    name: str = Field(description="操作名称")
    parameters: List[ActionParameter] = Field(default_factory=list, description="参数列表")


class Scene(BaseModel):
    """智能实体"""

    model_config = ConfigDict(frozen=False)

    scene_id: str = Field(description="智能ID")
    name: str = Field(description="智能名称")
    home_id: str = Field(description="所属家庭ID")
    icon: Optional[str] = Field(default=None, description="智能图标")


class ConsumableItem(BaseModel):
    """设备耗材"""

    name: str = Field(description="耗材名称")
    remaining: float = Field(description="剩余百分比")
    unit: str = Field(default="%", description="单位")
