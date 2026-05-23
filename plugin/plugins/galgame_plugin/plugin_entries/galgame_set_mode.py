from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameSetModeMixin:
    @plugin_entry(
        id="galgame_set_mode",
        name=tr("entries.galgame_set_mode.name", default='设置 galgame 模式'),
        description=tr("entries.galgame_set_mode.description", default='设置 silent / companion / choice_advisor 模式，并可选更新通知开关。'),
        input_schema={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": sorted(MODES)},
                "push_notifications": {"type": "boolean"},
                "advance_speed": {"type": "string", "enum": sorted(ADVANCE_SPEEDS)},
                "reader_mode": {"type": "string", "enum": sorted(READER_MODES)},
            },
            "required": ["mode"],
        },
        llm_result_fields=["summary"],
    )
    async def galgame_set_mode(
        self,
        mode: str,
        push_notifications: bool | None = None,
        advance_speed: str | None = None,
        reader_mode: str | None = None,
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError(self._not_configured_message()))
        if mode not in MODES:
            return Err(SdkError(f"invalid galgame mode: {mode!r}"))
        if advance_speed is not None and advance_speed not in ADVANCE_SPEEDS:
            return Err(SdkError(f"invalid advance speed: {advance_speed!r}"))
        try:
            normalized_reader_mode = _normalize_reader_mode(
                self._cfg.reader_mode if reader_mode is None else reader_mode
            )
        except ValueError as exc:
            return Err(SdkError(str(exc)))

        with self._state_lock:
            current_mode = str(self._state.mode or "")
            current_push_notifications = bool(self._state.push_notifications)
            current_advance_speed = str(self._state.advance_speed or ADVANCE_SPEED_MEDIUM)
        current_reader_mode = self._cfg.reader_mode
        requested_push_notifications = (
            bool(push_notifications)
            if push_notifications is not None
            else current_push_notifications
        )
        requested_advance_speed = (
            str(advance_speed)
            if advance_speed is not None
            else current_advance_speed
        )
        if (
            mode == current_mode
            and requested_push_notifications == current_push_notifications
            and requested_advance_speed == current_advance_speed
            and normalized_reader_mode == current_reader_mode
        ):
            return Ok(
                {
                    "mode": current_mode,
                    "push_notifications": current_push_notifications,
                    "advance_speed": current_advance_speed,
                    "reader_mode": current_reader_mode,
                    "summary": (
                        f"mode={current_mode} "
                        f"push_notifications={current_push_notifications} "
                        f"advance_speed={current_advance_speed} "
                        f"reader_mode={current_reader_mode}"
                    ),
                    "skipped": True,
                    "skip_reason": "already_applied",
                }
            )

        with self._state_lock:
            old_mode = str(self._state.mode or "")
            old_push_notifications = bool(self._state.push_notifications)
            old_advance_speed = str(self._state.advance_speed or ADVANCE_SPEED_MEDIUM)
            old_active_data_source = str(self._state.active_data_source or "")
            old_next_poll_at_monotonic = float(self._state.next_poll_at_monotonic or 0.0)
            old_pending_ocr_advance_captures = int(self._pending_ocr_advance_captures or 0)
            old_last_ocr_advance_capture_requested_at = float(
                self._last_ocr_advance_capture_requested_at or 0.0
            )
            old_last_ocr_advance_capture_reason = str(
                self._last_ocr_advance_capture_reason or ""
            )
        old_reader_mode = self._cfg.reader_mode

        def _restore_mode_runtime_state() -> None:
            self._cfg.reader_mode = old_reader_mode
            with self._state_lock:
                self._state.mode = old_mode
                self._state.push_notifications = old_push_notifications
                self._state.advance_speed = old_advance_speed
                self._state.active_data_source = old_active_data_source
                self._state.next_poll_at_monotonic = old_next_poll_at_monotonic
                self._pending_ocr_advance_captures = old_pending_ocr_advance_captures
                self._last_ocr_advance_capture_requested_at = (
                    old_last_ocr_advance_capture_requested_at
                )
                self._last_ocr_advance_capture_reason = old_last_ocr_advance_capture_reason
                self._state_dirty = True
                self._cached_snapshot = None
            for manager, label in (
                (self._memory_reader_manager, "memory reader"),
                (self._ocr_reader_manager, "OCR reader"),
            ):
                if manager is None:
                    continue
                try:
                    manager.update_config(self._cfg)
                except Exception as rollback_exc:
                    _log_plugin_noncritical(
                        self.logger,
                        "warning",
                        "galgame {} mode rollback update_config failed: {}",
                        label,
                        rollback_exc,
                    )

        # galgame_set_mode runs in the plugin's asyncio flow; simple config field
        # assignment is atomic here and readers use getattr fallbacks.
        self._cfg.reader_mode = normalized_reader_mode
        try:
            if self._memory_reader_manager is not None:
                self._memory_reader_manager.update_config(self._cfg)
            if self._ocr_reader_manager is not None:
                self._ocr_reader_manager.update_config(self._cfg)
        except Exception as exc:
            _restore_mode_runtime_state()
            return Err(SdkError(f"apply mode failed: {exc}"))
        with self._state_lock:
            self._state.mode = mode
            if push_notifications is not None:
                self._state.push_notifications = bool(push_notifications)
            if advance_speed is not None:
                self._state.advance_speed = advance_speed
            if normalized_reader_mode == READER_MODE_MEMORY:
                self._clear_pending_ocr_advance_captures_locked()
            if not self._state.active_session_id:
                self._state.active_data_source = _pending_data_source_for_reader_mode(
                    normalized_reader_mode,
                    memory_reader_allowed=normalized_reader_mode in {READER_MODE_AUTO, READER_MODE_MEMORY},
                    ocr_reader_allowed=normalized_reader_mode in {READER_MODE_AUTO, READER_MODE_OCR},
                    memory_reader_candidate_available=False,
                )
            self._state.next_poll_at_monotonic = 0.0
            self._state_dirty = True
            self._cached_snapshot = None
            payload = {
                "mode": self._state.mode,
                "push_notifications": self._state.push_notifications,
                "advance_speed": self._state.advance_speed,
                "reader_mode": self._cfg.reader_mode,
                "summary": (
                    f"mode={self._state.mode} "
                    f"push_notifications={self._state.push_notifications} "
                    f"advance_speed={self._state.advance_speed} "
                    f"reader_mode={self._cfg.reader_mode}"
                ),
            }
            bound_game_id = self._state.bound_game_id
            persist_push = self._state.push_notifications
            persist_advance_speed = self._state.advance_speed

        try:
            self._config_service.persist_preferences(
                bound_game_id=bound_game_id,
                mode=mode,
                push_notifications=persist_push,
                advance_speed=persist_advance_speed,
            )
        except Exception as exc:
            _restore_mode_runtime_state()
            return Err(SdkError(f"persist mode failed: {exc}"))
        try:
            self._config_service.persist_reader_mode(reader_mode=normalized_reader_mode)
        except Exception as exc:
            try:
                self._config_service.persist_preferences(
                    bound_game_id=bound_game_id,
                    mode=old_mode,
                    push_notifications=old_push_notifications,
                    advance_speed=old_advance_speed,
                )
            except Exception as rollback_exc:  # noqa: BLE001
                _log_plugin_noncritical(
                    self.logger,
                    "warning",
                    "galgame mode preference rollback failed: {}",
                    rollback_exc,
                )
            _restore_mode_runtime_state()
            return Err(SdkError(f"persist mode failed: {exc}"))
        await self._ensure_ocr_foreground_advance_monitor()
        if (
            mode_allows_agent_actuation(old_mode)
            and not mode_allows_agent_actuation(mode)
        ):
            self.request_ocr_after_advance_capture(reason="mode_change_to_read_only")
        self._start_background_bridge_poll()

        # 进入 choice_advisor 时默认启用 OCR fast loop；离开时仅关闭自动开启的。
        if mode == MODE_CHOICE_ADVISOR and old_mode != MODE_CHOICE_ADVISOR:
            if not self._ocr_fast_loop_should_run():
                if self._cfg is not None:
                    self._cfg.ocr_reader_fast_loop_enabled = True
                self._fast_loop_auto_enabled = True
                self._start_ocr_fast_loop()
        elif old_mode == MODE_CHOICE_ADVISOR and mode != MODE_CHOICE_ADVISOR:
            if self._fast_loop_auto_enabled:
                if self._cfg is not None:
                    self._cfg.ocr_reader_fast_loop_enabled = False
                self._fast_loop_auto_enabled = False
                await self._cancel_ocr_fast_loop()

        if self._game_agent is not None and not mode_allows_agent_actuation(mode):
            try:
                agent_payload = await self._game_agent.apply_mode_change(
                    self._snapshot_state(include_private_context=True)
                )
                payload["agent"] = json_copy(agent_payload)
            except Exception as exc:
                payload["agent_warning"] = f"apply_mode_change failed: {exc}"
        return Ok(payload)
