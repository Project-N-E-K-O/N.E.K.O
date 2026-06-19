from __future__ import annotations

from .entry_common import (
    Err,
    Ok,
    SdkError,
    _entry_exception_error,
    plugin_entry,
    tr,
    ui,
    MODE_COMPANION,
    MODE_INTERACTIVE,
    MODE_TEACHING,
    handle_user_intent,
)


class _ModeEntriesMixin:
    @plugin_entry(
        id="study_detect_mode_intent",
        name=tr("entries.detect_mode_intent.name", default="Detect Study Mode Intent"),
        description=tr(
            "entries.detect_mode_intent.description",
            default="Detect whether a text snippet contains a study mode switch intent.",
        ),
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string", "default": ""}},
        },
        llm_result_fields=["mode", "pure_switch", "transition_phrase"],
    )
    async def study_detect_mode_intent(self, text: str = "", **_):
        try:
            return Ok(handle_user_intent(text, language=self._cfg.language))
        except Exception as exc:
            return _entry_exception_error(
                self, exc, operation="study_detect_mode_intent"
            )

    @ui.action()
    @plugin_entry(
        id="study_set_mode",
        name=tr("entries.set_mode.name", default="Set Study Mode"),
        description=tr(
            "entries.set_mode.description",
            default="Switch the study companion between companion, interactive, and teaching modes.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": [MODE_COMPANION, MODE_INTERACTIVE, MODE_TEACHING],
                },
                "reason": {"type": "string", "default": "ui"},
            },
            "required": ["mode"],
        },
        llm_result_fields=["changed", "new_mode", "transition_phrase"],
    )
    async def study_set_mode(self, mode: str, reason: str = "ui", **_):
        try:
            result = await self._apply_mode_switch(
                mode, reason, language=self._cfg.language
            )
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_set_mode")
        return Ok(result)
