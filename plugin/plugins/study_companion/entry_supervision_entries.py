from __future__ import annotations

from .entry_common import (
    Err,
    Ok,
    SdkError,
    _entry_exception_error,
    plugin_entry,
    ui,
)


class _SupervisionEntriesMixin:
    @ui.action()
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

    @ui.action()
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
            if isinstance(enabled, bool):
                parsed_enabled = enabled
            elif isinstance(enabled, str):
                normalized = enabled.strip().lower()
                if normalized in {"true", "1", "yes", "on"}:
                    parsed_enabled = True
                elif normalized in {"false", "0", "no", "off"}:
                    parsed_enabled = False
                else:
                    raise ValueError("enabled must be a boolean value")
            elif isinstance(enabled, (int, float)) and enabled in (0, 1):
                parsed_enabled = bool(enabled)
            else:
                raise ValueError("enabled must be a boolean value")

            if not parsed_enabled and not self._cfg.supervision.allow_disable_by_chat:
                return Err(SdkError("study supervision disable is blocked by config"))
            return Ok(supervision.set_enabled(parsed_enabled))
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_supervision_toggle")
