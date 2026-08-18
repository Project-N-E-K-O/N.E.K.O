from __future__ import annotations

from ._event_bus import StudyEventBus
from .entry_common import (
    asyncio,
    Ok,
    StudyConfig,
    _entry_exception_error,
    _plugin_lock,
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
        "communication": config.communication.to_dict(),
        "doc_export": config.doc_export.to_dict(),
    }


def _communication_status_payload(owner) -> dict[str, bool | int]:
    config = owner._cfg.communication
    bus = getattr(owner, "_event_bus", None)
    transport = getattr(owner, "_neko_command_transport", None)
    handler = getattr(owner, "_neko_command_handler", None)
    watcher = getattr(owner, "_neko_command_watcher", None)
    worker = getattr(owner, "_command_worker_task", None)
    return {
        "configured_enabled": bool(config.enabled),
        "solution_narration_enabled": bool(config.solution_narration_enabled),
        "available": bus is not None,
        "command_subscription_active": bool(
            watcher is not None or (transport is not None and handler is not None)
        ),
        "command_worker_active": bool(worker is not None and not worker.done()),
        "events_emitted": int(bus.emit_count if bus is not None else 0),
        "events_blocked": int(bus.block_count if bus is not None else 0),
    }


def _communication_settings_lock(owner) -> asyncio.Lock:
    lock = getattr(owner, "_communication_settings_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        owner._communication_settings_lock = lock
    return lock


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
    communication = (
        raw.get("communication")
        if isinstance(raw.get("communication"), dict)
        else {}
    )
    doc_export = (
        raw.get("doc_export") if isinstance(raw.get("doc_export"), dict) else {}
    )

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
    next_communication = dict(next_values.get("communication") or {})
    if "enabled" in communication:
        next_communication["enabled"] = _coerce_bool(
            communication.get("enabled"), current.communication.enabled
        )
    if "solution_narration_enabled" in communication:
        next_communication["solution_narration_enabled"] = _coerce_bool(
            communication.get("solution_narration_enabled"),
            current.communication.solution_narration_enabled,
        )
    if "general_narration_enabled" in communication:
        next_communication["general_narration_enabled"] = _coerce_bool(
            communication.get("general_narration_enabled"),
            current.communication.general_narration_enabled,
        )
    next_values["communication"] = next_communication
    if "enabled" in doc_export:
        next_doc_export = dict(next_values.get("doc_export") or {})
        next_doc_export["enabled"] = _coerce_bool(
            doc_export.get("enabled"), current.doc_export.enabled
        )
        next_values["doc_export"] = next_doc_export
    return StudyConfig(**next_values)


class _StatusEntriesMixin:
    async def _close_communication_runtime(self, event_bus) -> None:
        first_error: BaseException | None = None
        for cleanup in (
            event_bus.close,
            self._unsubscribe_neko_commands,
            self._cancel_command_worker,
        ):
            try:
                await cleanup()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
                else:
                    self.logger.warning(
                        "study communication runtime cleanup failed: {}", exc
                    )
        if first_error is not None:
            raise first_error

    async def _set_communication_runtime(self, enabled: bool) -> None:
        if enabled:
            if getattr(self, "_event_bus", None) is not None:
                return
            event_bus = StudyEventBus(plugin_ctx=self.ctx)
            self._event_bus = event_bus
            try:
                await self._subscribe_neko_commands()
                self._start_command_worker()
                self._start_review_due_task()
            except BaseException:
                if self._event_bus is event_bus:
                    self._event_bus = None
                try:
                    await self._close_communication_runtime(event_bus)
                except BaseException as cleanup_error:
                    self.logger.warning(
                        "study communication enable cleanup failed: {}",
                        cleanup_error,
                    )
                raise
            return

        event_bus = getattr(self, "_event_bus", None)
        if event_bus is None:
            return
        self._event_bus = None
        await self._cancel_review_due_task()
        await self._close_communication_runtime(event_bus)

    def _apply_runtime_settings_config(self, config: StudyConfig) -> None:
        self._cfg = config
        if self._ocr_pipeline is not None:
            self._ocr_pipeline.update_config(config)
        if self._agent is not None:
            self._agent.update_config(config)
        if self._pomodoro_timer is not None:
            self._pomodoro_timer.config = config.pomodoro
            self._pomodoro_timer.auto_derive_from_session = (
                config.checkin.auto_derive_from_session
            )
            self._pomodoro_timer.checkin_timezone = config.checkin.streak_timezone
        if self._supervision is not None:
            self._supervision.config = config.supervision
            self._supervision.set_enabled(config.supervision.enabled)
        if self._checkin_manager is not None:
            self._checkin_manager.makeup_window_days = (
                config.checkin.makeup_window_days
            )
        sync_doc_export_entry = getattr(self, "_sync_doc_export_entry", None)
        if callable(sync_doc_export_entry):
            sync_doc_export_entry()

    def _restore_runtime_settings_config(self, config: StudyConfig) -> None:
        self._cfg = config
        restore_steps: list[tuple[str, object]] = []
        sync_doc_export_entry = getattr(self, "_sync_doc_export_entry", None)
        if callable(sync_doc_export_entry):
            restore_steps.append(("doc_export", sync_doc_export_entry))
        if self._ocr_pipeline is not None:
            restore_steps.append(
                ("ocr", lambda: self._ocr_pipeline.update_config(config))
            )
        if self._agent is not None:
            restore_steps.append(("agent", lambda: self._agent.update_config(config)))

        def restore_pomodoro() -> None:
            self._pomodoro_timer.config = config.pomodoro
            self._pomodoro_timer.auto_derive_from_session = (
                config.checkin.auto_derive_from_session
            )
            self._pomodoro_timer.checkin_timezone = config.checkin.streak_timezone

        if self._pomodoro_timer is not None:
            restore_steps.append(("pomodoro", restore_pomodoro))

        def restore_supervision() -> None:
            self._supervision.config = config.supervision
            self._supervision.set_enabled(config.supervision.enabled)

        if self._supervision is not None:
            restore_steps.append(("supervision", restore_supervision))
        if self._checkin_manager is not None:
            restore_steps.append(
                (
                    "checkin",
                    lambda: setattr(
                        self._checkin_manager,
                        "makeup_window_days",
                        config.checkin.makeup_window_days,
                    ),
                )
            )
        for component_name, restore in restore_steps:
            try:
                restore()
            except BaseException as exc:
                self.logger.warning(
                    "study settings {} rollback failed: {}", component_name, exc
                )

    async def _rollback_settings_update(
        self,
        previous_config: StudyConfig,
        *,
        previous_runtime_enabled: bool,
        runtime_reconciled: bool,
        persist_previous_config: bool,
    ) -> None:
        self._restore_runtime_settings_config(previous_config)
        if runtime_reconciled:
            try:
                await self._set_communication_runtime(previous_runtime_enabled)
            except BaseException as exc:
                self.logger.warning(
                    "study communication runtime rollback failed: {}", exc
                )
        try:
            await self._refresh_dependency_status()
        except BaseException as exc:
            self.logger.warning("study settings dependency rollback failed: {}", exc)
        if persist_previous_config:
            try:
                await self._persist_state()
            except BaseException as exc:
                self.logger.warning("study settings persistence rollback failed: {}", exc)

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

    @ui.action()
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
        llm_result_fields=["config", "communication_status", "model_runtime"],
    )
    async def study_get_settings_config(self, **_):
        describe_model_runtimes = getattr(
            self._agent, "describe_model_runtimes", None
        )
        model_runtime = {}
        if callable(describe_model_runtimes):
            try:
                model_runtime = await describe_model_runtimes()
            except Exception as exc:
                self.logger.warning(
                    "study model runtime diagnostics unavailable: {}", exc
                )
        return Ok(
            {
                "config": _settings_config_payload(self._cfg),
                "communication_status": _communication_status_payload(self),
                "model_runtime": model_runtime,
            }
        )

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
        llm_result_fields=["config", "communication_status"],
    )
    async def study_update_settings_config(self, config: dict | None = None, **_):
        try:
            raw_config = config if isinstance(config, dict) else {}
            communication = (
                raw_config.get("communication")
                if isinstance(raw_config.get("communication"), dict)
                else {}
            )
            runtime_reconciled = "enabled" in communication
            async with _communication_settings_lock(self):
                previous_config = self._cfg
                previous_runtime_enabled = (
                    getattr(self, "_event_bus", None) is not None
                )
                next_config = _apply_settings_config(previous_config, raw_config)
                config_application_attempted = False
                try:
                    if runtime_reconciled:
                        await self._set_communication_runtime(
                            next_config.communication.enabled
                        )
                    config_application_attempted = True
                    self._apply_runtime_settings_config(next_config)
                    await self._refresh_dependency_status()
                    await self._persist_state()
                except BaseException:
                    await self._rollback_settings_update(
                        previous_config,
                        previous_runtime_enabled=previous_runtime_enabled,
                        runtime_reconciled=runtime_reconciled,
                        persist_previous_config=config_application_attempted,
                    )
                    raise
                return Ok(
                    {
                        "config": _settings_config_payload(next_config),
                        "communication_status": _communication_status_payload(self),
                    }
                )
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
        input_schema={
            "type": "object",
            "properties": {
                "locale": {
                    "type": "string",
                    "description": "Current locale of the plugin management page.",
                }
            },
        },
        llm_result_fields=[
            "status",
            "active_mode",
            "screen_classification",
            "current_question",
            "last_answer_evaluation",
        ],
        metadata={"result_kind": "event"},
    )
    async def study_status(self, locale: str = "", **_):
        try:
            del locale
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
        llm_result_fields=[
            "configured_enabled",
            "solution_narration_enabled",
            "available",
            "command_subscription_active",
            "command_worker_active",
            "events_emitted",
            "events_blocked",
        ],
    )
    async def study_neko_communication_status(self, **_):
        return Ok(_communication_status_payload(self))

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
