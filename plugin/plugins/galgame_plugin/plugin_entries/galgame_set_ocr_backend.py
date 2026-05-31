from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameSetOcrBackendMixin:
    @plugin_entry(
        id="galgame_set_ocr_backend",
        name=tr("entries.galgame_set_ocr_backend.name", default='设置 OCR / 截图后端'),
        description=tr("entries.galgame_set_ocr_backend.description", default='切换 OCR 文本识别后端和截图后端。只影响 OCR 读取，不改变 Agent 点击安全策略。'),
        input_schema={
            "type": "object",
            "properties": {
                "backend_selection": {
                    "type": "string",
                    "enum": sorted(_OCR_BACKEND_SELECTIONS),
                },
                "capture_backend": {
                    "type": "string",
                    "enum": sorted(_OCR_CAPTURE_BACKEND_SELECTIONS),
                },
            },
        },
        llm_result_fields=["summary"],
    )
    async def galgame_set_ocr_backend(
        self,
        backend_selection: str | None = None,
        capture_backend: str | None = None,
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError(self._not_configured_message()))
        normalized_backend = str(backend_selection or "").strip().lower() or None
        normalized_capture = str(capture_backend or "").strip().lower() or None
        # Accept legacy "imagegrab" from external callers but normalize to "mss"
        # before validation so the schema set can drop the deprecated value.
        if normalized_capture == "imagegrab":
            normalized_capture = "mss"
        if normalized_backend is None and normalized_capture is None:
            return Err(SdkError("backend_selection or capture_backend is required"))
        if normalized_backend is not None and normalized_backend not in _OCR_BACKEND_SELECTIONS:
            return Err(SdkError(f"invalid OCR backend: {backend_selection!r}"))
        if normalized_capture is not None and normalized_capture not in _OCR_CAPTURE_BACKEND_SELECTIONS:
            return Err(SdkError(f"invalid OCR capture backend: {capture_backend!r}"))

        old_backend = self._cfg.ocr_reader_backend_selection
        old_capture = self._cfg.ocr_reader_capture_backend
        old_backend_explicit = bool(
            getattr(self._cfg, "ocr_reader_backend_selection_explicit", False)
        )
        old_capture_explicit = bool(
            getattr(self._cfg, "ocr_reader_capture_backend_explicit", False)
        )
        backend_changed = normalized_backend is not None and normalized_backend != old_backend
        capture_changed = normalized_capture is not None and normalized_capture != old_capture
        if normalized_backend is not None:
            self._cfg.ocr_reader_backend_selection = normalized_backend
            self._cfg.ocr_reader_backend_selection_explicit = True
        if normalized_capture is not None:
            self._cfg.ocr_reader_capture_backend = normalized_capture
            self._cfg.ocr_reader_capture_backend_explicit = True
        if self._ocr_reader_manager is not None:
            try:
                self._ocr_reader_manager.update_config(self._cfg)
            except Exception as exc:
                if normalized_backend is not None:
                    self._cfg.ocr_reader_backend_selection = old_backend
                    self._cfg.ocr_reader_backend_selection_explicit = old_backend_explicit
                if normalized_capture is not None:
                    self._cfg.ocr_reader_capture_backend = old_capture
                    self._cfg.ocr_reader_capture_backend_explicit = old_capture_explicit
                return Err(SdkError(f"apply OCR backend failed: {exc}"))

        with self._state_lock:
            self._state.next_poll_at_monotonic = 0.0
            self._state_dirty = True
            self._cached_snapshot = None

        try:
            self._config_service.persist_ocr_backend_selection(
                backend_selection=normalized_backend,
                capture_backend=normalized_capture,
            )
        except Exception as exc:
            self._cfg.ocr_reader_backend_selection = old_backend
            self._cfg.ocr_reader_capture_backend = old_capture
            self._cfg.ocr_reader_backend_selection_explicit = old_backend_explicit
            self._cfg.ocr_reader_capture_backend_explicit = old_capture_explicit
            if self._ocr_reader_manager is not None:
                try:
                    self._ocr_reader_manager.update_config(self._cfg)
                except Exception as rollback_exc:
                    _log_plugin_noncritical(
                        self.logger,
                        "warning",
                        "galgame OCR backend rollback update_config failed: {}",
                        rollback_exc,
                    )
            return Err(SdkError(f"persist OCR backend failed: {exc}"))

        if self._ocr_reader_manager is not None and (backend_changed or capture_changed):
            reset_capture_runtime = getattr(
                self._ocr_reader_manager,
                "reset_capture_runtime_diagnostics",
                None,
            )
            if callable(reset_capture_runtime):
                try:
                    reset_capture_runtime()
                except Exception as exc:
                    _log_plugin_noncritical(
                        self.logger,
                        "warning",
                        "galgame OCR backend switch diagnostic reset failed: {}",
                        exc,
                    )

        self._start_background_bridge_poll()
        payload = {
            "backend_selection": self._cfg.ocr_reader_backend_selection,
            "capture_backend": self._cfg.ocr_reader_capture_backend,
            "summary": (
                f"OCR backend={self._cfg.ocr_reader_backend_selection} "
                f"capture_backend={self._cfg.ocr_reader_capture_backend}"
            ),
        }
        return Ok(payload)
