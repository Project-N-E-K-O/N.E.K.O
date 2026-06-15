from __future__ import annotations

from .entry_common import (
    asyncio,
    Ok,
    StudyConfig,
    _entry_exception_error,
    plugin_entry,
    tr,
    ui,
    build_open_ui_payload,
)


def _settings_config_payload(config: StudyConfig) -> dict:
    return {
        "study": {
            "default_mode": config.default_mode,
            "auto_open_ui": config.auto_open_ui,
        },
        "ocr_reader": {
            "enabled": config.ocr_enabled,
            "languages": config.ocr_languages,
        },
        "llm": {
            "llm_call_timeout_seconds": config.llm_call_timeout_seconds,
            "llm_vision_enabled": config.llm_vision_enabled,
            "llm_vision_max_image_px": config.llm_vision_max_image_px,
        },
    }


def _coerce_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
        return default
    return bool(value)


def _coerce_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _apply_settings_config(current: StudyConfig, raw: dict) -> StudyConfig:
    next_values = current.to_dict()
    study = raw.get("study") if isinstance(raw.get("study"), dict) else {}
    ocr = raw.get("ocr_reader") if isinstance(raw.get("ocr_reader"), dict) else {}
    llm = raw.get("llm") if isinstance(raw.get("llm"), dict) else {}

    if "default_mode" in study:
        next_values["default_mode"] = study.get("default_mode")
    if "auto_open_ui" in study:
        next_values["auto_open_ui"] = _coerce_bool(
            study.get("auto_open_ui"), current.auto_open_ui
        )
    if "enabled" in ocr:
        next_values["ocr_enabled"] = _coerce_bool(
            ocr.get("enabled"), current.ocr_enabled
        )
    if "languages" in ocr:
        next_values["ocr_languages"] = str(ocr.get("languages") or "").strip()
    if "llm_call_timeout_seconds" in llm:
        next_values["llm_call_timeout_seconds"] = llm.get(
            "llm_call_timeout_seconds"
        )
    if "llm_vision_enabled" in llm:
        next_values["llm_vision_enabled"] = _coerce_bool(
            llm.get("llm_vision_enabled"), current.llm_vision_enabled
        )
    if "llm_vision_max_image_px" in llm:
        next_values["llm_vision_max_image_px"] = _coerce_int(
            llm.get("llm_vision_max_image_px"), current.llm_vision_max_image_px
        )
    return StudyConfig(**next_values)


class _StatusEntriesMixin:
    @ui.context(id="study", title="Study Companion")
    async def study_hosted_ui_context(self, **_):
        return {"ready": True}

    @plugin_entry(
        id="study_open_ui",
        name=tr("entries.open_ui.name", default="Open Study Companion UI"),
        description=tr(
            "entries.open_ui.description",
            default="Return the static UI path for study_companion.",
        ),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["available", "path", "message_key"],
    )
    async def study_open_ui(self, **_):
        return Ok(
            build_open_ui_payload(
                plugin_id=self.plugin_id,
                available=self.get_static_ui_config() is not None,
            )
        )

    @plugin_entry(
        id="study_get_settings_config",
        name=tr(
            "entries.get_settings_config.name",
            default="Get Study Companion Settings",
        ),
        description=tr(
            "entries.get_settings_config.description",
            default="Return the running study companion settings used by the static UI.",
        ),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["config"],
    )
    async def study_get_settings_config(self, **_):
        return Ok({"config": _settings_config_payload(self._cfg)})

    @plugin_entry(
        id="study_update_settings_config",
        name=tr(
            "entries.update_settings_config.name",
            default="Update Study Companion Settings",
        ),
        description=tr(
            "entries.update_settings_config.description",
            default="Persist editable study companion settings and apply them to the running plugin.",
        ),
        input_schema={
            "type": "object",
            "properties": {"config": {"type": "object"}},
            "required": ["config"],
        },
        llm_result_fields=["config"],
    )
    async def study_update_settings_config(self, config: dict | None = None, **_):
        try:
            raw_config = config if isinstance(config, dict) else {}
            next_config = _apply_settings_config(self._cfg, raw_config)
            self._cfg = next_config
            if self._ocr_pipeline is not None:
                self._ocr_pipeline.update_config(next_config)
            if self._agent is not None:
                self._agent.update_config(next_config)
            if self._pomodoro_timer is not None:
                self._pomodoro_timer.config = next_config.pomodoro
                self._pomodoro_timer.auto_derive_from_session = (
                    next_config.checkin.auto_derive_from_session
                )
                self._pomodoro_timer.checkin_timezone = next_config.checkin.streak_timezone
            if self._supervision is not None:
                self._supervision.config = next_config.supervision
                self._supervision.set_enabled(next_config.supervision.enabled)
            if self._checkin_manager is not None:
                self._checkin_manager.makeup_window_days = (
                    next_config.checkin.makeup_window_days
                )
            await self._refresh_dependency_status()
            await self._persist_state()
            return Ok({"config": _settings_config_payload(next_config)})
        except Exception as exc:
            return _entry_exception_error(
                self, exc, operation="study_update_settings_config"
            )

    @ui.action()
    @plugin_entry(
        id="study_status",
        name=tr("entries.status.name", default="Study Companion Status"),
        description=tr(
            "entries.status.description",
            default="Return runtime status, dependencies, and recent study interactions.",
        ),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=[
            "status",
            "active_mode",
            "screen_classification",
            "current_question",
            "last_answer_evaluation",
        ],
    )
    async def study_status(self, **_):
        try:
            payload = await asyncio.to_thread(self._status_payload)
            return Ok(payload)
        except Exception as exc:
            return _entry_exception_error(self, exc, operation="study_status")

    @plugin_entry(
        id="study_neko_communication_status",
        name=tr(
            "entries.neko_communication_status.name",
            default="Neko Communication Status",
        ),
        description=tr(
            "entries.neko_communication_status.description",
            default="Return whether real-time neko communication is active.",
        ),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["available", "events_emitted", "events_blocked"],
    )
    async def study_neko_communication_status(self, **_):
        bus = self._event_bus
        return Ok(
            {
                "available": bus is not None,
                "events_emitted": bus.emit_count if bus is not None else 0,
                "events_blocked": bus.block_count if bus is not None else 0,
            }
        )

    @ui.action()
    @plugin_entry(
        id="study_memory_habit_status",
        name=tr(
            "entries.memory_habit_status.name", default="Memory Habit Bridge Status"
        ),
        description=tr(
            "entries.memory_habit_status.description",
            default="Return whether memory deck habit integration is available.",
        ),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=[
            "available",
            "supports_deck_goals",
            "supports_deck_focus",
            "error",
        ],
    )
    async def study_memory_habit_status(self, **_):
        try:
            self._require_habit_components()
            return Ok(self._require_memory_habit_bridge().status())
        except Exception as exc:
            return Ok({"available": False, "error": str(exc)})
