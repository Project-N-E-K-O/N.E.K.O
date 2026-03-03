"""
Pydantic 模型定义：用于 API 请求/响应和核心数据结构。
"""
from __future__ import annotations

import base64
from datetime import datetime
from typing import Any, Dict, Literal, Optional, List, Union

from pydantic import BaseModel, Field, field_serializer, model_validator

from .version import SDK_VERSION


# /runs (Run Protocol)
RunStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "canceled",
    "timeout",
    "cancel_requested",
]


class RunCreateRequest(BaseModel):
    plugin_id: str
    entry_id: str
    args: Dict[str, Any] = Field(default_factory=dict)
    task_id: Optional[str] = None
    trace_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class RunCreateResponse(BaseModel):
    run_id: str
    status: RunStatus
    run_token: Optional[str] = None
    expires_at: Optional[int] = None


# 核心数据结构
class PluginAuthor(BaseModel):
    """插件作者信息"""
    name: Optional[str] = None
    email: Optional[str] = None


class PluginDependency(BaseModel):
    """
    插件依赖信息
    
    支持多种依赖方式：
    1. 依赖特定插件ID：id = "plugin_id"
    2. 依赖特定入口点：entry = "entry_id" 或 entry = "plugin_id:entry_id"（只能引用 @plugin_entry）
    3. 依赖特定自定义事件：custom_event = "event_type:event_id" 或 custom_event = "plugin_id:event_type:event_id"（只能引用 @custom_event）
    4. 依赖多个候选插件：providers = ["plugin1", "plugin2"]（任一满足即可）
    
    注意：
    - id、entry、custom_event、providers 至少需要提供一个
    - entry 和 custom_event 互斥（不能同时使用）
    """
    id: Optional[str] = None  # 依赖特定插件ID
    entry: Optional[str] = None  # 依赖特定入口点（格式：entry_id 或 plugin_id:entry_id，只能引用 @plugin_entry）
    custom_event: Optional[str] = None  # 依赖特定自定义事件（格式：event_type:event_id 或 plugin_id:event_type:event_id，只能引用 @custom_event）
    providers: Optional[List[str]] = None  # 多个候选插件ID列表（任一满足即可）
    recommended: Optional[str] = None
    supported: Optional[str] = None
    untested: Optional[str] = None  # 如果使用依赖配置，此字段是必须的
    conflicts: Optional[Union[List[str], bool]] = None  # 可以是版本范围列表，或 true（表示冲突）

    @model_validator(mode="after")
    def validate_dependency_constraints(self) -> "PluginDependency":
        if not any([self.id, self.entry, self.custom_event, self.providers]):
            raise ValueError("至少需要提供 id、entry、custom_event 或 providers 中的一个")

        if self.entry is not None and self.custom_event is not None:
            raise ValueError("entry 和 custom_event 不能同时使用")

        # 简化冲突格式（conflicts=true）可不要求 untested
        if self.conflicts is True:
            return self
        if self.untested is None:
            raise ValueError("使用依赖配置时必须提供 untested 版本范围")

        return self


PluginType = Literal["plugin", "extension", "script", "adapter"]


class PluginMeta(BaseModel):
    """插件元数据"""
    id: str
    name: str
    type: PluginType = "plugin"  # 插件类型: plugin(完整插件) | extension(扩展插件) | script(脚本)
    description: str = ""
    version: str = "0.1.0"
    sdk_version: str = SDK_VERSION
    sdk_recommended: Optional[str] = None
    sdk_supported: Optional[str] = None
    sdk_untested: Optional[str] = None
    sdk_conflicts: List[str] = Field(default_factory=list)
    input_schema: Dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    author: Optional[PluginAuthor] = None
    dependencies: List[PluginDependency] = Field(default_factory=list)
    host_plugin_id: Optional[str] = None  # extension 类型的宿主插件 ID


class HealthCheckResponse(BaseModel):
    """健康检查响应"""
    alive: bool
    exitcode: Optional[int] = None
    pid: Optional[int] = None
    status: Literal["running", "stopped", "crashed", "not_started"]
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
    unsafe: bool = Field(default=False, description="为 True 时允许主进程跳过严格 schema 校验（用于高性能场景）")

    @model_validator(mode="after")
    def validate_message_type_payload(self) -> "PluginPushMessageRequest":
        mt = self.message_type
        if mt in ("text", "url"):
            if not isinstance(self.content, str) or not self.content:
                raise ValueError("content is required when message_type is 'text' or 'url'")
            return self
        if mt == "binary":
            if not isinstance(self.binary_data, (bytes, bytearray)):
                raise ValueError("binary_data is required when message_type is 'binary'")
            return self
        if mt == "binary_url":
            if not isinstance(self.binary_url, str) or not self.binary_url:
                raise ValueError("binary_url is required when message_type is 'binary_url'")
            return self
        return self


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
