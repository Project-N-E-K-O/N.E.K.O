"""Pydantic models for the Quick Actions Panel.

Defines the ``ActionDescriptor`` that describes a single action item,
plus request/response envelopes for the execute endpoint.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ActionDescriptor(BaseModel):
    """描述一个快捷操作面板中的操作项。"""

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
        "plugin_toggle",
        "entry_toggle",
    ] | None = None
    current_value: object = None
    options: list[str] | None = None  # dropdown only
    min: float | None = None  # number/slider only
    max: float | None = None  # number/slider only
    step: float | None = None  # number/slider only
    disabled: bool = False

    # Chat inject fields (type == "chat_inject")
    inject_text: str | None = None

    # Navigation fields (type == "navigation")
    target: str | None = None
    open_in: Literal["new_tab", "same_tab"] | None = None


class ActionExecuteRequest(BaseModel):
    """POST /chat/actions/{action_id}/execute 请求体。"""

    value: object = None


class ActionExecuteResponse(BaseModel):
    """POST /chat/actions/{action_id}/execute 响应体。"""

    success: bool
    action: ActionDescriptor | None = None
    message: str = ""


__all__ = [
    "ActionDescriptor",
    "ActionExecuteRequest",
    "ActionExecuteResponse",
]
