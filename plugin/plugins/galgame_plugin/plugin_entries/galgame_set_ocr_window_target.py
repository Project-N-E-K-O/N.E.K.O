from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameSetOcrWindowTargetMixin:
    @plugin_entry(
        id="galgame_set_ocr_window_target",
        name=tr("entries.galgame_set_ocr_window_target.name", default='设置 OCR 目标窗口'),
        description=tr("entries.galgame_set_ocr_window_target.description", default='锁定或清除 OCR Reader 的手动目标窗口。'),
        input_schema={
            "type": "object",
            "properties": {
                "window_key": {"type": "string", "default": ""},
                "clear": {"type": "boolean", "default": False},
            },
        },
        llm_result_fields=["summary"],
    )
    async def galgame_set_ocr_window_target(
        self,
        window_key: str = "",
        clear: bool = False,
        **_,
    ):
        if self._ocr_reader_manager is None:
            return Err(SdkError("ocr_reader manager is not initialized"))

        if clear:
            target_payload = {
                "mode": "auto",
                "window_key": "",
                "process_name": "",
                "normalized_title": "",
                "pid": 0,
                "last_known_hwnd": 0,
                "selected_at": "",
            }
            summary = "OCR window target cleared; waiting for manual lock"
        else:
            try:
                target_payload = await asyncio.to_thread(
                    self._ocr_reader_manager.resolve_manual_window_target,
                    window_key,
                )
            except ValueError as exc:
                return Err(SdkError(str(exc)))
            except Exception as exc:
                return Err(SdkError(f"resolve OCR window target failed: {exc}"))
            summary = (
                f"OCR window target locked to {target_payload.get('process_name') or '(unknown)'}"
            )

        with self._state_lock:
            previous_state_target = json_copy(self._state.ocr_window_target)
            previous_state_dirty = bool(self._state_dirty)
            previous_cached_snapshot = self._cached_snapshot
        previous_persisted_target = json_copy(previous_state_target)
        try:
            restored, _warnings = self._persist.load()
            if isinstance(restored, dict):
                previous_persisted_target = json_copy(
                    restored.get(STORE_OCR_WINDOW_TARGET, previous_state_target)
                )
        except Exception as exc:  # noqa: BLE001
            _log_plugin_noncritical(
                self.logger,
                "warning",
                "galgame OCR window target previous store read failed: {}",
                exc,
            )

        try:
            self._persist.persist_ocr_window_target(target_payload)
        except Exception as exc:
            return Err(SdkError(f"persist OCR window target failed: {exc}"))

        with self._state_lock:
            self._state.ocr_window_target = json_copy(target_payload)
            self._state_dirty = True
            self._cached_snapshot = None
        try:
            self._ocr_reader_manager.update_window_target(target_payload)
            background_poll_started = self._start_background_bridge_poll()
        except Exception as exc:
            try:
                self._persist.persist_ocr_window_target(previous_persisted_target)
            except Exception as rollback_exc:  # noqa: BLE001
                _log_plugin_noncritical(
                    self.logger,
                    "warning",
                    "galgame OCR window target persist rollback failed: {}",
                    rollback_exc,
                )
            with self._state_lock:
                self._state.ocr_window_target = json_copy(previous_state_target)
                self._state_dirty = previous_state_dirty
                self._cached_snapshot = previous_cached_snapshot
            return Err(SdkError(f"apply OCR window target runtime update failed: {exc}"))
        return Ok(
            {
                "window_target": json_copy(target_payload),
                "cleared": bool(clear),
                "summary": summary,
                "background_poll_started": background_poll_started,
            }
        )
