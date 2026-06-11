from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameSuggestChoiceMixin:
    @plugin_entry(
        id="galgame_suggest_choice",
        name=tr("entries.galgame_suggest_choice.name", default='建议当前选项'),
        description=tr("entries.galgame_suggest_choice.description", default='对当前可见选项给出推荐顺位与理由。'),
        input_schema={"type": "object", "properties": {}},
        timeout=45.0,
        llm_result_fields=["choices", "diagnostic"],
    )
    async def galgame_suggest_choice(self, **_):
        if self._llm_gateway is None:
            return Err(SdkError("galgame_plugin llm_gateway is not initialized"))
        local = self._snapshot_state(include_private_context=True)
        context = build_suggest_context(local, config=self._cfg)
        if not context["visible_choices"]:
            return Ok(
                apply_input_degraded_result(
                    build_suggest_degraded_result(
                        context,
                        diagnostic="gateway_unavailable: no visible choices",
                    ),
                    context=context,
                )
            )
        payload = apply_input_degraded_result(
            await self._llm_gateway.suggest_choice(context),
            context=context,
        )
        payload["scene_id"] = str(context.get("scene_id") or "")
        return Ok(payload)
