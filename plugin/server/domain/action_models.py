"""Pydantic models for the Command Palette.

Defines the ``ActionDescriptor`` that describes a single action item,
plus request/response envelopes for the execute endpoint.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ActionDescriptor(BaseModel):
    """描述一个命令面板中的操作项。"""

    action_id: str = Field(description="唯一标识，格式: {plugin_id}:{field_or_action_id}")
    type: Literal["instant", "chat_inject", "navigation"]
    label: str = Field(description="显示名称")
    description: str = Field(default="", description="描述文本")
    category: str = Field(description="分组名称，默认为插件 name")
    plugin_id: str = Field(description="来源插件 ID，系统级操作为 'system'")

    # Instant action fields (type == "instant")
    control: Literal[
        "toggle",
        "button",
        "dropdown",
        "number",
        "slider",
        "plugin_lifecycle",
        "entry_toggle",
    ] | None = None
    current_value: object = None
    options: list[str] | None = None  # dropdown only
    min: float | None = None  # number/slider only
    max: float | None = None  # number/slider only
    step: float | None = None  # number/slider only
    disabled: bool = False

    # Entry action fields (control == "button" with input_schema)
    input_schema: dict[str, object] | None = None

    # Chat inject fields (type == "chat_inject")
    inject_text: str | None = None

    # Navigation fields (type == "navigation")
    target: str | None = None
    open_in: Literal["new_tab", "same_tab"] | None = None

    # Command palette metadata
    keywords: list[str] = Field(default_factory=list, description="搜索关键词（多语言）")
    icon: str | None = Field(default=None, description="Emoji 或图标标识")
    priority: int = Field(default=0, description="排序权重，越大越靠前")
    quick_action: bool = Field(default=False, description="插件自注册的快捷操作，在面板中优先展示")
    section: Literal["pinned", "recent", "commands"] | None = Field(
        default=None, description="面板分区，由前端根据偏好计算",
    )


class ActionExecuteRequest(BaseModel):
    """POST /chat/actions/{action_id}/execute 请求体。"""

    value: object = None


class ActionExecuteResponse(BaseModel):
    """POST /chat/actions/{action_id}/execute 响应体。"""

    success: bool
    action: ActionDescriptor | None = None
    message: str = ""


class UserActionPreferences(BaseModel):
    """用户对命令面板操作项的个性化偏好。"""

    pinned: list[str] = Field(default_factory=list, description="置顶的 action_id 列表")
    hidden: list[str] = Field(default_factory=list, description="隐藏的 action_id 列表")
    recent: list[str] = Field(default_factory=list, description="最近使用的 action_id，最多 10 条")


__all__ = [
    "ActionDescriptor",
    "ActionExecuteRequest",
    "ActionExecuteResponse",
    "UserActionPreferences",
]
