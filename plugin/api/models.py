"""
Pydantic 模型定义：用于 API 请求/响应和核心数据结构。
"""
from __future__ import annotations

import base64
from datetime import datetime
from typing import Any, Dict, Literal, Optional, List

from pydantic import BaseModel, Field, field_serializer
from plugin.sdk.version import SDK_VERSION


# API 请求/响应模型
class PluginTriggerRequest(BaseModel):
    """插件触发请求"""
    plugin_id: str
    entry_id: str
    args: Dict[str, Any] = {}
    task_id: Optional[str] = None


class PluginTriggerResponse(BaseModel):
    """插件触发响应"""
    success: bool
    plugin_id: str
    executed_entry: str
    args: Dict[str, Any]
    plugin_response: Any
    received_at: str
    plugin_forward_error: Optional[Dict[str, Any]] = None


# 核心数据结构
class PluginMeta(BaseModel):
    """插件元数据"""
    id: str
    name: str
    description: str = ""
    version: str = "0.1.0"
    sdk_version: str = SDK_VERSION
    sdk_recommended: Optional[str] = None
    sdk_supported: Optional[str] = None
    sdk_untested: Optional[str] = None
    sdk_conflicts: List[str] = Field(default_factory=list)
    input_schema: Dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class HealthCheckResponse(BaseModel):
    """健康检查响应"""
    alive: bool
    exitcode: Optional[int] = None
    pid: Optional[int] = None
    status: Literal["running", "stopped", "crashed"]
    communication: Dict[str, Any]


# 插件推送消息相关模型
class PluginPushMessageRequest(BaseModel):
    """插件推送消息请求（从插件进程发送到主进程）"""
    plugin_id: str
    source: str = Field(..., description="插件自己标明的来源")
    description: str = Field(default="", description="插件自己标明的描述")
    priority: int = Field(default=0, description="插件自己设定的优先级，数字越大优先级越高")
    message_type: Literal["text", "url", "binary", "binary_url"] = Field(..., description="消息类型")
    content: Optional[str] = Field(default=None, description="文本内容或URL（当message_type为text或url时）")
    binary_data: Optional[bytes] = Field(default=None, description="二进制数据（当message_type为binary时，仅用于小文件）")
    binary_url: Optional[str] = Field(default=None, description="二进制文件的URL（当message_type为binary_url时）")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="额外的元数据")


class PluginPushMessage(BaseModel):
    """插件推送消息（主进程中的完整消息）"""
    plugin_id: str
    source: str
    description: str
    priority: int
    message_type: Literal["text", "url", "binary", "binary_url"]
    content: Optional[str] = None
    binary_data: Optional[bytes] = None
    binary_url: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(..., description="消息推送时间（ISO格式）")
    message_id: str = Field(..., description="消息唯一ID")
    
    @field_serializer('binary_data')
    def serialize_binary_data(self, value: Optional[bytes]) -> Optional[str]:
        """将二进制数据序列化为 base64 字符串（用于 JSON 响应）"""
        if value is None:
            return None
        return base64.b64encode(value).decode('utf-8')


class PluginPushMessageResponse(BaseModel):
    """推送消息响应"""
    success: bool
    message_id: str
    received_at: str
    error: Optional[str] = None
