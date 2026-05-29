from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameGetSnapshotMixin:
    @plugin_entry(
        id="cosplay_get_snapshot",
        name=tr("entries.cosplay_get_snapshot.name", default='获取 cosplay 快照'),
        description=tr("entries.cosplay_get_snapshot.description", default='返回当前游戏快照和 stale 状态。'),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["snapshot"],
    )
    async def cosplay_get_snapshot(self, **_):
        state_snapshot = self._snapshot_state()
        payload = build_snapshot_payload(SimpleNamespace(**state_snapshot))
        return Ok(payload)
