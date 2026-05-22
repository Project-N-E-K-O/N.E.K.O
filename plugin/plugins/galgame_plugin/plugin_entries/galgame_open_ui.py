from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameOpenUiMixin:
    @plugin_entry(
        id="galgame_open_ui",
        name=tr("entries.galgame_open_ui.name", default='打开 galgame UI'),
        description=tr("entries.galgame_open_ui.description", default='返回 galgame_plugin 静态 UI 的访问路径。'),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["message"],
    )
    async def galgame_open_ui(self, **_):
        payload = build_open_ui_payload(
            plugin_id=self.plugin_id,
            available=self.get_static_ui_config() is not None,
        )
        return Ok(payload)
