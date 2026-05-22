from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameGetStatusMixin:
    @plugin_entry(
        id="galgame_get_status",
        name=tr("entries.galgame_get_status.name", default='获取 galgame 插件状态'),
        description=tr("entries.galgame_get_status.description", default='返回当前 bridge 连接状态、绑定游戏、最近错误与模式。'),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["summary"],
    )
    async def galgame_get_status(self, **_):
        if self._cfg is None:
            return Err(SdkError(self._not_configured_message()))
        return Ok(await self._build_status_payload_async())
