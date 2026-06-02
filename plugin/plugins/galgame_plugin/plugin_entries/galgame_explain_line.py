from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameExplainLineMixin:
    @plugin_entry(
        id="galgame_explain_line",
        name=tr("entries.galgame_explain_line.name", default='解释当前或指定台词'),
        description=tr("entries.galgame_explain_line.description", default='对当前快照或指定 line_id 对应的台词进行解释。'),
        input_schema={
            "type": "object",
            "properties": {"line_id": {"type": "string", "default": ""}},
        },
        timeout=45.0,
        llm_result_fields=["explanation", "diagnostic"],
    )
    async def galgame_explain_line(self, line_id: str = "", **_):
        if self._llm_gateway is None:
            return Err(SdkError("galgame_plugin llm_gateway is not initialized"))
        local = self._snapshot_state(include_private_context=True)
        normalized_line_id = str(line_id or "").strip()
        try:
            context = build_explain_context(local, line_id=normalized_line_id, config=self._cfg)
        except ValueError as exc:
            context = {
                "line_id": "",
                "speaker": "",
                "text": "",
                "scene_id": "",
                "route_id": "",
                "evidence": [],
            }
            return Ok(
                build_explain_degraded_result(
                    context,
                    diagnostic=str(exc) or build_ocr_context_diagnostic(local),
                )
            )
        payload = apply_input_degraded_result(
            await self._llm_gateway.explain_line(context),
            context=context,
        )
        payload["line_id"] = str(context.get("line_id") or "")
        payload["speaker"] = str(context.get("speaker") or "")
        payload["text"] = str(context.get("text") or "")
        return Ok(payload)
