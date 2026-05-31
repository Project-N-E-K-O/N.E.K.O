from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameSetOcrTimingMixin:
    @plugin_entry(
        id="galgame_set_ocr_timing",
        name=tr("entries.galgame_set_ocr_timing.name", default='设置 OCR 识别时机'),
        description=tr("entries.galgame_set_ocr_timing.description", default='设置 OCR Reader 触发模式与轮询间隔；DXcam 截图后端会随 OCR 触发。'),
        input_schema={
            "type": "object",
            "properties": {
                "poll_interval_seconds": {
                    "type": "number",
                    "minimum": 0.5,
                    "maximum": 10.0,
                },
                "trigger_mode": {
                    "type": "string",
                    "enum": ["interval", "after_advance"],
                    "default": "interval",
                },
                "fast_loop_enabled": {"type": "boolean"},
            },
            "required": ["poll_interval_seconds"],
        },
        llm_result_fields=["summary"],
    )
    async def galgame_set_ocr_timing(
        self,
        poll_interval_seconds: float,
        trigger_mode: str | None = None,
        fast_loop_enabled: bool | None = None,
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError(self._not_configured_message()))
        try:
            normalized_interval = float(poll_interval_seconds)
        except (TypeError, ValueError):
            return Err(SdkError("poll_interval_seconds must be a number"))
        if normalized_interval < 0.5 or normalized_interval > 10.0:
            return Err(SdkError("poll_interval_seconds must be between 0.5 and 10.0"))
        try:
            normalized_trigger_mode = _normalize_ocr_trigger_mode(
                trigger_mode or self._cfg.ocr_reader_trigger_mode
            )
        except ValueError as exc:
            return Err(SdkError(str(exc)))

        old_interval = self._cfg.ocr_reader_poll_interval_seconds
        old_trigger_mode = self._cfg.ocr_reader_trigger_mode
        old_fast_loop = self._cfg.ocr_reader_fast_loop_enabled
        old_fast_loop_auto_enabled = self._fast_loop_auto_enabled
        if fast_loop_enabled is not None:
            self._fast_loop_auto_enabled = False
        self._cfg.ocr_reader_poll_interval_seconds = normalized_interval
        self._cfg.ocr_reader_trigger_mode = normalized_trigger_mode
        self._cfg.ocr_reader_fast_loop_enabled = (
            bool(fast_loop_enabled)
            if fast_loop_enabled is not None
            else old_fast_loop
        )
        if self._ocr_reader_manager is not None:
            try:
                self._ocr_reader_manager.update_config(self._cfg)
            except Exception as exc:
                self._cfg.ocr_reader_poll_interval_seconds = old_interval
                self._cfg.ocr_reader_trigger_mode = old_trigger_mode
                self._cfg.ocr_reader_fast_loop_enabled = old_fast_loop
                self._fast_loop_auto_enabled = old_fast_loop_auto_enabled
                return Err(SdkError(f"apply OCR timing failed: {exc}"))

        with self._state_lock:
            self._state.next_poll_at_monotonic = 0.0
            self._state_dirty = True
            self._cached_snapshot = None

        try:
            self._config_service.persist_ocr_timing(
                poll_interval_seconds=normalized_interval,
                trigger_mode=normalized_trigger_mode,
                fast_loop_enabled=(
                    fast_loop_enabled
                    if fast_loop_enabled is not None
                    else old_fast_loop
                ),
            )
        except Exception as exc:
            self._cfg.ocr_reader_poll_interval_seconds = old_interval
            self._cfg.ocr_reader_trigger_mode = old_trigger_mode
            self._cfg.ocr_reader_fast_loop_enabled = old_fast_loop
            self._fast_loop_auto_enabled = old_fast_loop_auto_enabled
            if self._ocr_reader_manager is not None:
                try:
                    self._ocr_reader_manager.update_config(self._cfg)
                except Exception as rollback_exc:
                    _log_plugin_noncritical(
                        self.logger,
                        "warning",
                        "galgame OCR timing rollback update_config failed: {}",
                        rollback_exc,
                    )
            return Err(SdkError(f"persist OCR timing failed: {exc}"))

        if fast_loop_enabled is not None:
            try:
                if bool(fast_loop_enabled) and not old_fast_loop:
                    self._start_ocr_fast_loop()
                elif not bool(fast_loop_enabled) and old_fast_loop:
                    await self._cancel_ocr_fast_loop()
            except Exception as exc:
                self._cfg.ocr_reader_poll_interval_seconds = old_interval
                self._cfg.ocr_reader_trigger_mode = old_trigger_mode
                self._cfg.ocr_reader_fast_loop_enabled = old_fast_loop
                self._fast_loop_auto_enabled = old_fast_loop_auto_enabled
                if self._ocr_reader_manager is not None:
                    try:
                        self._ocr_reader_manager.update_config(self._cfg)
                    except Exception as rollback_exc:
                        _log_plugin_noncritical(
                            self.logger,
                            "warning",
                            "galgame OCR timing rollback update_config failed: {}",
                            rollback_exc,
                        )
                try:
                    self._config_service.persist_ocr_timing(
                        poll_interval_seconds=old_interval,
                        trigger_mode=old_trigger_mode,
                        fast_loop_enabled=old_fast_loop,
                    )
                except Exception as rollback_exc:
                    _log_plugin_noncritical(
                        self.logger,
                        "warning",
                        "galgame OCR fast loop rollback persist failed: {}",
                        rollback_exc,
                    )
                return Err(SdkError(f"apply fast_loop_enabled failed: {exc}"))
        if normalized_trigger_mode != OCR_TRIGGER_MODE_AFTER_ADVANCE:
            self._clear_pending_ocr_advance_captures()
        await self._ensure_ocr_foreground_advance_monitor()
        self._start_background_bridge_poll()
        trigger_mode_label = (
            "点击对白后识别"
            if self._cfg.ocr_reader_trigger_mode == OCR_TRIGGER_MODE_AFTER_ADVANCE
            else "按间隔识别"
        )
        payload = {
            "poll_interval_seconds": self._cfg.ocr_reader_poll_interval_seconds,
            "trigger_mode": self._cfg.ocr_reader_trigger_mode,
            "fast_loop_enabled": self._cfg.ocr_reader_fast_loop_enabled,
            "summary": (
                f"OCR/DXcam {trigger_mode_label}；间隔="
                f"{self._cfg.ocr_reader_poll_interval_seconds:.1f}s；"
                f"Fast Loop={'开启' if self._cfg.ocr_reader_fast_loop_enabled else '关闭'}"
            ),
        }
        return Ok(payload)
