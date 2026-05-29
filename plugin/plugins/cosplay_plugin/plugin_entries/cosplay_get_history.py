from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _CosplayGetHistoryMixin:
    @plugin_entry(
        id="cosplay_get_history",
        name=tr("entries.cosplay_get_history.name", default='获取 cosplay 历史'),
        description=tr("entries.cosplay_get_history.description", default='返回最近事件、稳定台词历史和选项历史。'),
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50, "minimum": 1},
                "include_events": {"type": "boolean", "default": True},
            },
        },
        llm_result_fields=["stable_lines", "observed_lines", "choices"],
    )
    async def cosplay_get_history(self, limit: int = 50, include_events: bool = True, **_):
        sanitized_limit = _coerce_int_range(
            limit,
            default=50,
            minimum=1,
            maximum=500,
        )
        sanitized_include_events = _coerce_bool(include_events, default=True)
        with self._state_lock:
            payload = build_history_payload(
                self._state,
                limit=sanitized_limit,
                include_events=sanitized_include_events,
            )
        return Ok(payload)
