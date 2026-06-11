from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameGetPushHistoryMixin:
    @plugin_entry(
        id="galgame_get_push_history",
        name=tr(
            "entries.galgame_get_push_history.name",
            default="查询推送历史",
        ),
        description=tr(
            "entries.galgame_get_push_history.description",
            default="返回最近 N 次推送的元数据（push_seq/mode/token/场景/角色），不含台词文本。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 20},
            },
        },
        timeout=5.0,
        llm_result_fields=["summary"],
    )
    async def galgame_get_push_history(self, limit: int = 10, **_):
        if self._cfg is None:
            return Err(SdkError(self._not_configured_message()))
        throttled = self._check_query_rate_limit("galgame_get_push_history")
        if throttled is not None:
            return Ok(throttled)
        try:
            requested = int(limit)
        except (TypeError, ValueError):
            requested = 10
        requested = max(1, min(20, requested))
        history = list(self._push_history)[-requested:]
        return Ok(
            {
                "entries": history,
                "count": len(history),
                "summary": f"{len(history)} push record(s) returned",
            }
        )
