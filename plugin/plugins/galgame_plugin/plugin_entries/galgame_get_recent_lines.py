from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameGetRecentLinesMixin:
    @plugin_entry(
        id="galgame_get_recent_lines",
        name=tr(
            "entries.galgame_get_recent_lines.name",
            default="查询最近台词",
        ),
        description=tr(
            "entries.galgame_get_recent_lines.description",
            default="获取最近 N 句原始台词内容（上限 20）。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "n": {"type": "integer", "default": 10, "minimum": 1, "maximum": 20},
            },
        },
        timeout=5.0,
        llm_result_fields=["summary"],
    )
    async def galgame_get_recent_lines(self, n: int = 10, **_):
        if self._cfg is None:
            return Err(SdkError(self._not_configured_message()))
        throttled = self._check_query_rate_limit("galgame_get_recent_lines")
        if throttled is not None:
            return Ok(throttled)
        try:
            requested = int(n)
        except (TypeError, ValueError):
            requested = 10
        requested = max(1, min(20, requested))
        with self._state_lock:
            history = [dict(line) for line in (self._state.history_lines or [])]
        recent = history[-requested:]
        return Ok(
            {
                "lines": recent,
                "count": len(recent),
                "summary": f"{len(recent)} line(s) returned",
            }
        )
