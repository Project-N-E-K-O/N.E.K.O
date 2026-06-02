from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameGetSceneContextMixin:
    @plugin_entry(
        id="galgame_get_scene_context",
        name=tr(
            "entries.galgame_get_scene_context.name",
            default="查询场景上下文",
        ),
        description=tr(
            "entries.galgame_get_scene_context.description",
            default="获取当前或指定场景的完整上下文摘要 + 关键台词。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "scene_id": {"type": "string", "default": ""},
            },
        },
        timeout=5.0,
        llm_result_fields=["summary"],
    )
    async def galgame_get_scene_context(
        self,
        scene_id: str = "",
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError(self._not_configured_message()))
        throttled = self._check_query_rate_limit("galgame_get_scene_context")
        if throttled is not None:
            return Ok(throttled)
        scenes = self._layer1_scene_summaries()
        target = (scene_id or "").strip()
        entry: dict[str, Any] | None = None
        if target:
            for item in reversed(scenes):
                if str(item.get("scene_id") or "") == target:
                    entry = item
                    break
        elif scenes:
            entry = scenes[-1]
        if entry is None:
            return Ok(
                {
                    "scene_id": target,
                    "found": False,
                    "summary": "no scene context available",
                }
            )
        return Ok(
            {
                "scene_id": str(entry.get("scene_id") or ""),
                "route_id": str(entry.get("route_id") or ""),
                "found": True,
                "summary_text": str(entry.get("summary") or ""),
                "key_lines": list(entry.get("key_lines") or []),
                "ts": entry.get("ts") or "",
                "summary": (
                    f"scene={entry.get('scene_id') or '-'} "
                    f"lines={len(entry.get('key_lines') or [])}"
                ),
            }
        )
