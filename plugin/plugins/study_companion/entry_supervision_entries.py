from __future__ import annotations

from .entry_common import (
    Err,
    Ok,
    SdkError,
    _entry_exception_error,
    plugin_entry,
)


class _SupervisionEntriesMixin:
    @plugin_entry(
        id="study_supervision_status",
        name="Study Supervision Status",
        description="Return focus supervision state and sensor availability.",
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["enabled", "sensor_available", "reminder_level"],
    )
    async def study_supervision_status(self, **_):
        try:
            _, _, _, supervision = self._require_habit_components()
            return Ok(supervision.status())
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_supervision_status")

    @plugin_entry(
        id="study_supervision_toggle",
        name="Toggle Study Supervision",
        description="Enable or disable low-frequency focus supervision reminders.",
        input_schema={
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}},
            "required": ["enabled"],
        },
        llm_result_fields=["enabled", "reminder_level"],
    )
    async def study_supervision_toggle(self, enabled: bool, **_):
        try:
            _, _, _, supervision = self._require_habit_components()
            if not bool(enabled) and not self._cfg.supervision.allow_disable_by_chat:
                return Err(SdkError("study supervision disable is blocked by config"))
            return Ok(supervision.set_enabled(bool(enabled)))
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_supervision_toggle")
