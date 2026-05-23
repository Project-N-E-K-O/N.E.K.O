from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameGetStorySoFarMixin:
    @plugin_entry(
        id="galgame_get_story_so_far",
        name=tr(
            "entries.galgame_get_story_so_far.name",
            default="查询全局故事线",
        ),
        description=tr(
            "entries.galgame_get_story_so_far.description",
            default="获取从游戏开始到现在的完整故事线摘要（约 200 tokens）。",
        ),
        input_schema={"type": "object", "properties": {}},
        timeout=5.0,
        llm_result_fields=["summary"],
    )
    async def galgame_get_story_so_far(self, **_):
        if self._cfg is None:
            return Err(SdkError(self._not_configured_message()))
        throttled = self._check_query_rate_limit("galgame_get_story_so_far")
        if throttled is not None:
            return Ok(throttled)
        self._refresh_story_so_far_from_scene_summaries()
        story = (self._story_so_far or "").strip()
        if not story:
            return Ok(
                {
                    "story_so_far": "故事刚开始。",
                    "available": False,
                    "summary": "no story summary yet",
                }
            )
        return Ok(
            {
                "story_so_far": story,
                "available": True,
                "last_updated_seq": int(self._story_last_updated_seq or 0),
                "summary": f"story summary ({len(story)} chars)",
            }
        )
