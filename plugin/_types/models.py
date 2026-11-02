"""
Pydantic model definitions for API requests/responses and core data structures.
"""
from __future__ import annotations

import base64
from typing import Any, Dict, Literal, Optional, List, Union

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from .version import SDK_VERSION
from .plugin_types import PluginType, require_supported_plugin_type


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


# Core data structures
class PluginAuthor(BaseModel):
    """Plugin author information"""
    name: Optional[str] = None
    email: Optional[str] = None


class PluginDependency(BaseModel):
    """
    Plugin dependency information
    
    Supports several dependency forms:
    1. Depend on a specific plugin ID: id = \"plugin_id\"
    2. Depend on a specific entry point: entry = \"entry_id\" or entry = \"plugin_id:entry_id\" (may reference only @plugin_entry)
    3. Depend on a specific custom event: custom_event = \"event_type:event_id\" or custom_event = \"plugin_id:event_type:event_id\" (may reference only @custom_event)
    4. Depend on multiple candidate plugins: providers = [\"plugin1\", \"plugin2\"] (any one is sufficient)
    
    Notes:
    - At least one of id, entry, custom_event, or providers is required
    - entry and custom_event are mutually exclusive (cannot be used together)
    """
    id: Optional[str] = None  # Depend on a specific plugin ID
    entry: Optional[str] = None  # Depend on a specific entry point (format: entry_id or plugin_id:entry_id; may reference only @plugin_entry)
    custom_event: Optional[str] = None  # Depend on a specific custom event (format: event_type:event_id or plugin_id:event_type:event_id; may reference only @custom_event)
    providers: Optional[List[str]] = None  # List of candidate plugin IDs (any one is sufficient)
    recommended: Optional[str] = None
    supported: Optional[str] = None
    untested: Optional[str] = None  # This field is required when dependency configuration is used
    conflicts: Optional[Union[List[str], bool]] = None  # May be a list of version ranges, or true (meaning conflict)

    @model_validator(mode="after")
    def validate_dependency_constraints(self) -> "PluginDependency":
        if not any([self.id, self.entry, self.custom_event, self.providers]):
            raise ValueError("至少需要提供 id、entry、custom_event 或 providers 中的一个")

        if self.entry is not None and self.custom_event is not None:
            raise ValueError("entry 和 custom_event 不能同时使用")

        # Simplified conflict format (conflicts=true) does not require untested
        if self.conflicts is True:
            return self
        if self.untested is None:
            raise ValueError("使用依赖配置时必须提供 untested 版本范围")

        return self


PluginUiSurfaceKind = Literal["panel", "guide", "docs"]
PluginUiSurfaceMode = Literal["static", "hosted-tsx", "markdown", "auto"]
PluginUiOpenIn = Literal["iframe", "new_tab", "same_tab"]


class PluginUiSurface(BaseModel):
    """A normalized plugin UI surface declaration."""
    id: str
    kind: PluginUiSurfaceKind
    mode: PluginUiSurfaceMode
    title: Optional[str] = None
    entry: Optional[str] = None
    url: Optional[str] = None
    ui_path: Optional[str] = None
    open_in: Optional[PluginUiOpenIn] = None
    context: Optional[str] = None
    permissions: List[str] = Field(default_factory=list)
    available: bool = True
    # True only for the host-generated static/index.html surface that keeps
    # legacy static UIs reachable after a plugin declares newer surfaces.
    legacy_static_compat: bool = False


class PluginUiWarning(BaseModel):
    """Structured UI manifest warning for developer-facing diagnostics."""
    path: str
    code: str
    message: str


class PluginUiSurfacesResponse(BaseModel):
    """Normalized plugin UI surfaces response."""
    plugin_id: str
    surfaces: List[PluginUiSurface] = Field(default_factory=list)
    warnings: List[PluginUiWarning] = Field(default_factory=list)


class PluginMeta(BaseModel):
    """Plugin metadata"""
    id: str
    name: str
    type: PluginType = "plugin"
    description: str = ""
    short_description: str = ""  # Short description (<300 characters) for the agent's two-stage plugin selection
    keywords: List[str] = Field(default_factory=list)  # List of keyword regular expressions for quick matching
    passive: bool = False  # Passive plugins (such as danmaku listeners and QQ auto-replies) do not participate in active agent dispatch
    version: str = "0.1.0"
    sdk_version: str = SDK_VERSION
    sdk_recommended: Optional[str] = None
    sdk_supported: Optional[str] = None
    sdk_untested: Optional[str] = None
    sdk_conflicts: List[str] = Field(default_factory=list)
    input_schema: Dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    author: Optional[PluginAuthor] = None
    dependencies: List[PluginDependency] = Field(default_factory=list)
    plugin_ui: Optional[Dict[str, Any]] = None
    i18n: Optional[Dict[str, Any]] = None

    @field_validator("type", mode="before")
    @classmethod
    def validate_plugin_type(cls, value: object) -> str:
        return require_supported_plugin_type(value)


class HealthCheckResponse(BaseModel):
    """Health check response"""
    alive: bool
    exitcode: Optional[int] = None
    pid: Optional[int] = None
    status: Literal["running", "stopped", "crashed", "not_started"]
    communication: Dict[str, Any]


# Models related to plugin-pushed messages — v2 schema: plugin/sdk/shared/core/push_message_schema.py
# Deprecated fields (message_type / description / content / binary_data / binary_url /
# mime / delivery / reply / unsafe / fast_mode）
# Removed in v0.9 (see docs/changelog). The model no longer performs strict validation; host-side
# translate_push_message + proactive_bridge are the single authoritative translation and dispatch path.


class PluginPushMessageRequest(BaseModel):
    """Plugin-pushed message request (v2 + v1 compatibility).

    v2 fields: visibility / ai_behavior / parts.
    v1 fields (deprecated, removed in v0.9): message_type / content /
    binary_data / binary_url / mime / description / delivery / reply / unsafe /
    fast_mode. Both sets of fields coexist for compatibility with older callers; authoritative translation is in
    ``plugin.sdk.shared.core.push_message_schema``。
    """

    plugin_id: str
    source: str = Field(default="", description="插件自己标明的来源")
    priority: int = Field(default=0, description="优先级，数字越大优先级越高")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="额外的元数据")
    target_lanlan: Optional[str] = Field(default=None, description="路由到指定 session 的角色名")
    coalesce_key: Optional[str] = Field(
        default=None,
        description="可选的主动消息合并键 / Optional proactive-message coalescing key / 主動メッセージ用の任意の集約キー",
    )

    # v2 schema
    visibility: Optional[List[str]] = Field(default=None, description="parts 渲染目标，'chat' 和/或 'hud'")
    ai_behavior: Optional[str] = Field(default=None, description="LLM 行为：respond | read | blind")
    parts: Optional[List[Dict[str, Any]]] = Field(default=None, description="OpenAI 风格内容 parts 列表")

    # v1 legacy (deprecated, removed in v0.9)
    message_type: Optional[str] = Field(default=None, description="DEPRECATED: 旧的消息类型 discriminator")
    description: Optional[str] = Field(default=None, description="DEPRECATED")
    content: Optional[str] = Field(default=None, description="DEPRECATED: 用 parts=[{'type':'text',...}]")
    binary_data: Optional[bytes] = Field(default=None, description="DEPRECATED: 用 parts=[{'type':'image',...}]")
    binary_url: Optional[str] = Field(default=None, description="DEPRECATED: 用 parts=[{...,'url':...}]")
    mime: Optional[str] = Field(default=None, description="DEPRECATED")
    delivery: Optional[Union[str, bool]] = Field(default=None, description="DEPRECATED")
    reply: Optional[bool] = Field(default=None, description="DEPRECATED")
    unsafe: bool = Field(default=False, description="DEPRECATED")
    fast_mode: bool = Field(default=False, description="DEPRECATED")


class PluginPushMessage(BaseModel):
    """Plugin-pushed message (full message record in the main process, with v2 and v1 compatibility fields side by side)."""

    model_config = ConfigDict(populate_by_name=True)

    plugin_id: str
    source: str = ""
    priority: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)
    target_lanlan: Optional[str] = None
    schema_version: str = Field(default="push_message.v2", alias="schema")
    coalesce_key: str = ""
    timestamp: str = Field(..., description="消息推送时间（ISO格式）")
    message_id: str = Field(..., description="消息唯一ID")

    # v2 schema
    visibility: Optional[List[str]] = None
    ai_behavior: Optional[str] = None
    parts: Optional[List[Dict[str, Any]]] = None

    # v1 legacy compat
    message_type: Optional[str] = None
    description: Optional[str] = None
    content: Optional[str] = None
    # Usually None. push_message used to decode parts[].binary_base64 back into raw bytes and add another copy
    # here, making an image travel over the wire twice (~2.34x); the 256 KiB payload cap then only allowed
    # an image of about 110 KiB. That copy was removed: this field is now populated only in the single case where the caller passes both binary_data=
    # and explicit parts= — then it is the only carrier. In other cases, read
    # parts[].binary_base64。
    binary_data: Optional[bytes] = None
    binary_url: Optional[str] = None
    mime: Optional[str] = None
    unsafe: bool = False
    delivery: Optional[Union[str, bool]] = None
    reply: Optional[bool] = None

    @field_serializer("binary_data")
    def serialize_binary_data(self, value: Optional[bytes]) -> Optional[str]:
        if value is None:
            return None
        return base64.b64encode(value).decode("utf-8")


class PluginPushMessageResponse(BaseModel):
    """Push-message response"""
    success: bool
    message_id: str
    received_at: str
    error: Optional[str] = None
