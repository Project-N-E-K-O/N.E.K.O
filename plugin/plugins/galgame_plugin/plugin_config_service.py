from __future__ import annotations

from typing import Any

from .models import (
    STORE_LLM_VISION_ENABLED,
    STORE_LLM_VISION_MAX_IMAGE_PX,
    STORE_OCR_BACKEND_SELECTION,
    STORE_OCR_CAPTURE_BACKEND,
    STORE_OCR_FAST_LOOP_ENABLED,
    STORE_OCR_POLL_INTERVAL_SECONDS,
    STORE_OCR_SCREEN_TEMPLATES,
    STORE_OCR_TRIGGER_MODE,
    STORE_RAPIDOCR_AUTO_DETECT_LANG,
    STORE_RAPIDOCR_AUTO_DETECT_LAST_LANG,
    STORE_RAPIDOCR_LANG_TYPE,
    STORE_RAPIDOCR_OCR_VERSION,
    STORE_READER_MODE,
    json_copy,
)
from .plugin_util_helpers import _migrate_legacy_capture_backend


class GalgamePluginConfigService:
    def __init__(self, plugin: Any) -> None:
        self._plugin = plugin

    def persist_preferences(
        self,
        *,
        bound_game_id: str,
        mode: str,
        push_notifications: bool,
        advance_speed: str,
    ) -> None:
        self._plugin._persist.persist_preferences(
            bound_game_id=bound_game_id,
            mode=mode,
            push_notifications=push_notifications,
            advance_speed=advance_speed,
        )

    def persist_ocr_backend_selection(
        self,
        *,
        backend_selection: str | None,
        capture_backend: str | None,
    ) -> None:
        if backend_selection is not None:
            self._plugin._persist.persist_config_override(
                STORE_OCR_BACKEND_SELECTION,
                backend_selection,
            )
        if capture_backend is not None:
            normalized_capture_backend = _migrate_legacy_capture_backend(capture_backend)
            self._plugin._persist.persist_config_override(
                STORE_OCR_CAPTURE_BACKEND,
                normalized_capture_backend,
            )

    def persist_rapidocr_lang(
        self,
        *,
        lang_type: str | None,
        ocr_version: str | None = None,
        auto_detect_lang: bool | None = None,
        auto_detect_last_lang: str | None = None,
    ) -> None:
        if lang_type is not None:
            self._plugin._persist.persist_config_override(
                STORE_RAPIDOCR_LANG_TYPE,
                lang_type,
            )
        if ocr_version is not None:
            self._plugin._persist.persist_config_override(
                STORE_RAPIDOCR_OCR_VERSION,
                ocr_version,
            )
        if auto_detect_lang is not None:
            self._plugin._persist.persist_config_override(
                STORE_RAPIDOCR_AUTO_DETECT_LANG,
                bool(auto_detect_lang),
            )
        if auto_detect_last_lang is not None:
            self._plugin._persist.persist_config_override(
                STORE_RAPIDOCR_AUTO_DETECT_LAST_LANG,
                auto_detect_last_lang,
            )

    def persist_reader_mode(self, *, reader_mode: str) -> None:
        self._plugin._persist.persist_config_override(STORE_READER_MODE, reader_mode)

    def persist_ocr_timing(
        self,
        *,
        poll_interval_seconds: float,
        trigger_mode: str,
        fast_loop_enabled: bool | None = None,
    ) -> None:
        self._plugin._persist.persist_config_override(
            STORE_OCR_POLL_INTERVAL_SECONDS,
            poll_interval_seconds,
        )
        self._plugin._persist.persist_config_override(
            STORE_OCR_TRIGGER_MODE,
            trigger_mode,
        )
        if fast_loop_enabled is not None:
            self._plugin._persist.persist_config_override(
                STORE_OCR_FAST_LOOP_ENABLED,
                bool(fast_loop_enabled),
            )

    def persist_llm_vision(
        self,
        *,
        vision_enabled: bool,
        vision_max_image_px: int,
    ) -> None:
        self._plugin._persist.persist_config_override(
            STORE_LLM_VISION_ENABLED,
            bool(vision_enabled),
        )
        self._plugin._persist.persist_config_override(
            STORE_LLM_VISION_MAX_IMAGE_PX,
            int(vision_max_image_px),
        )

    def persist_ocr_screen_templates(self, templates: list[dict[str, Any]]) -> None:
        self._plugin._persist.persist_config_override(
            STORE_OCR_SCREEN_TEMPLATES,
            json_copy(templates),
        )

    def persist_runtime_state(self, payload: dict[str, Any]) -> None:
        def _payload_int(key: str) -> int:
            try:
                return int(payload.get(key) or 0)
            except (TypeError, ValueError):
                return 0

        dedupe_window = payload.get("dedupe_window")
        last_error = payload.get("last_error")
        self._plugin._persist.persist_runtime(
            session_id=str(payload.get("active_session_id") or ""),
            events_byte_offset=_payload_int("events_byte_offset"),
            events_file_size=_payload_int("events_file_size"),
            last_seq=_payload_int("last_seq"),
            dedupe_window=list(dedupe_window) if isinstance(dedupe_window, list) else [],
            last_error=dict(last_error) if isinstance(last_error, dict) else {},
        )
