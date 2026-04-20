"""ActionProvider protocol for the Quick Actions Panel.

Each provider is responsible for generating ``ActionDescriptor`` items
from a single source (PluginSettings, list_actions, system lifecycle, …).
"""

from __future__ import annotations

from typing import Protocol

from plugin.server.domain.action_models import ActionDescriptor


class ActionProvider(Protocol):
    """操作项提供者协议。

    每个 provider 负责从一个来源生成 ``ActionDescriptor`` 列表。
    """

    async def get_actions(
        self,
        plugin_id: str | None = None,
    ) -> list[ActionDescriptor]:
        """返回操作项列表。*plugin_id* 为 ``None`` 时返回所有。"""
        ...


__all__ = [
    "ActionProvider",
]
