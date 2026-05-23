from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameSummarizeSceneMixin:
    @plugin_entry(
        id="galgame_summarize_scene",
        name=tr("entries.galgame_summarize_scene.name", default='总结当前场景'),
        description=tr("entries.galgame_summarize_scene.description", default='总结当前场景或指定 scene_id 的最近剧情进展。'),
        input_schema={
            "type": "object",
            "properties": {"scene_id": {"type": "string", "default": ""}},
        },
        timeout=45.0,
        llm_result_fields=["summary", "diagnostic"],
    )
    async def galgame_summarize_scene(self, scene_id: str = "", **_):
        if self._llm_gateway is None:
            return Err(SdkError("galgame_plugin llm_gateway is not initialized"))
        local = self._snapshot_state(include_private_context=True)
        scene_id_normalized = str(scene_id or "").strip()
        from .. import build_summarize_context as _build_summarize_context

        context = _build_summarize_context(local, scene_id=scene_id_normalized, config=self._cfg)
        snapshot = context.get("current_snapshot") if isinstance(context.get("current_snapshot"), dict) else {}
        if not list(context.get("recent_lines") or []) and not str(snapshot.get("text") or ""):
            return Ok(
                build_summarize_degraded_result(
                    context,
                    diagnostic=build_ocr_context_diagnostic(local),
                )
            )
        payload = apply_input_degraded_result(
            await self._llm_gateway.summarize_scene(context),
            context=context,
        )
        payload["scene_id"] = str(context.get("scene_id") or "")
        try:
            await asyncio.to_thread(self._persist_context_snapshot_from_summary, context, payload)
        except Exception as exc:
            self.logger.warning(
                "persist context snapshot from scene summary failed: {}",
                exc,
            )
        return Ok(payload)
